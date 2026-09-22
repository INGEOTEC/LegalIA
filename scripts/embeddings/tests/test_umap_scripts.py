"""The pure-Python logic of the UMAP scripts (issue #241), against tiny
synthetic data -- no network, no Slurm, and no real UMAP fit.

    uv run --group viz pytest scripts/embeddings/tests -q

`umap` and `pynndescent` are monkeypatched into `sys.modules` with stubs
returning known coordinates and neighbours, so this file depends on
`project_umap.py` importing cleanly and on its scaling/writing logic, never
on a fit that would take an hour. `build_umap_html.py` *is* exercised for
real: Altair's own schema validation is most of what these tests check.
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import build_umap_html  # noqa: E402
import prepare_umap_input  # noqa: E402
import project_umap  # noqa: E402
import submit_umap  # noqa: E402

import legalvec  # noqa: E402

SLUG = "qwen3-0.6b"
K = 4

#: The tiny corpus every prepare/HTML test below is built from: two
#: collections, one `text_sha1` (`shaS`) shared by two instruments *and*
#: present in both collections, which is what pins the "dedup inside a
#: collection, never across" rule.
UNITS = {
    "leyes": [
        ("a", "Ley A", "article", "art_1", "shaA1", "texto de A1"),
        ("a", "Ley A", "article", "art_2", "shaS", "transitorio compartido"),
        ("b", "Ley B", "article", "art_1", "shaB1", "texto de B1"),
        ("b", "Ley B", "article", "art_2", "shaS", "transitorio compartido"),
        ("b", "Ley B", "heading", "cap_1", "shaS", "transitorio compartido"),
    ],
    "lineamientos": [
        ("900", "Lineamientos Z", "article", "art_1", "shaS", "transitorio compartido"),
    ],
}
#: Every vector is a constant row, so a centroid is a number a test can
#: predict by hand.
VALUES = {"shaA1": 1.0, "shaB1": 3.0, "shaS": 9.0}


def write_vectors(directory: Path, name: str, hashes) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({
        "text_sha1": pa.array(list(hashes), type=pa.string()),
        "vector": pa.array([[VALUES[h]] * K for h in hashes],
                            type=pa.list_(pa.float32())),
    }), directory / name)


def write_units(directory: Path, coleccion: str, rows) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({
        "coleccion": pa.array([coleccion] * len(rows), type=pa.string()),
        "clave": pa.array([r[0] for r in rows], type=pa.string()),
        "nombre": pa.array([r[1] for r in rows], type=pa.string()),
        "unit_type": pa.array([r[2] for r in rows], type=pa.string()),
        "eId": pa.array([r[3] for r in rows], type=pa.string()),
        "text_sha1": pa.array([r[4] for r in rows], type=pa.string()),
        "text": pa.array([r[5] for r in rows], type=pa.string()),
    }), directory / "units.parquet")


@pytest.fixture
def cache(tmp_path):
    """A `legalvec` cache holding the tiny corpus above."""
    root = tmp_path / "cache"
    leyes = root / "scjn-leyes-vectors"
    write_units(leyes, "leyes", UNITS["leyes"])
    write_vectors(leyes, f"vectors-a-{SLUG}-{K}.parquet", ["shaA1"])
    write_vectors(leyes, f"vectors-b-{SLUG}-{K}.parquet", ["shaB1"])
    write_vectors(leyes, f"vectors-shared-{SLUG}-{K}.parquet", ["shaS"])

    lineamientos = root / "scjn-lineamientos-vectors"
    write_units(lineamientos, "lineamientos", UNITS["lineamientos"])
    write_vectors(lineamientos, f"vectors-900-{SLUG}-{K}.parquet", ["shaS"])
    write_vectors(lineamientos, f"vectors-shared-{SLUG}-{K}.parquet", [])
    return root


COLLECTIONS = ("leyes", "lineamientos")


# -- prepare_umap_input --------------------------------------------------- #

def test_prepare_writes_one_row_per_collection_and_text(tmp_path, cache):
    work_dir = tmp_path / "work"
    summary = prepare_umap_input.prepare(
        work_dir, collections=COLLECTIONS, cache_dir=cache, log=lambda *a: None)

    vectors = np.load(work_dir / "vectors.npy")
    assert vectors.shape == (4, K)
    assert vectors.dtype == np.float16
    assert summary["n"] == 4 and summary["k"] == K

    ids = pq.read_table(work_dir / "vector_ids.parquet").to_pydict()
    assert ids["row"] == [0, 1, 2, 3]
    # `shaS` twice: once as a leyes vector, once as a lineamientos one --
    # dedup is inside a collection, never across.
    assert list(zip(ids["coleccion"], ids["text_sha1"])) == [
        ("leyes", "shaA1"), ("leyes", "shaS"), ("leyes", "shaB1"),
        ("lineamientos", "shaS"),
    ]


def test_prepare_centroid_is_the_mean_over_unit_rows(tmp_path, cache):
    work_dir = tmp_path / "work"
    prepare_umap_input.prepare(work_dir, collections=COLLECTIONS, cache_dir=cache,
                               log=lambda *a: None)

    instruments = pq.read_table(work_dir / "instruments.parquet").to_pydict()
    assert instruments["clave"] == ["a", "b", "900"]
    assert instruments["i"] == [0, 1, 2]
    assert instruments["units"] == [2, 3, 1]
    assert instruments["distinct_texts"] == [2, 2, 1]

    centroids = np.load(work_dir / "centroid_input.npy")
    # a: (1 + 9) / 2; b: (3 + 9 + 9) / 3 -- the shared text counted once per
    # unit row that carries it; 900: 9.
    assert centroids[:, 0].tolist() == pytest.approx([5.0, 7.0, 9.0])


def test_prepare_is_a_no_op_until_forced(tmp_path, cache):
    work_dir = tmp_path / "work"
    prepare_umap_input.prepare(work_dir, collections=COLLECTIONS, cache_dir=cache,
                               log=lambda *a: None)
    (work_dir / "vectors.npy").unlink()

    prepare_umap_input.prepare(work_dir, collections=COLLECTIONS, cache_dir=cache,
                               log=lambda *a: None)
    assert not (work_dir / "vectors.npy").exists()  # skipped, marker present

    prepare_umap_input.prepare(work_dir, collections=COLLECTIONS, cache_dir=cache,
                               force=True, log=lambda *a: None)
    assert (work_dir / "vectors.npy").exists()


def test_prepare_raises_asset_not_cached_for_a_missing_vector_file(tmp_path, cache):
    (cache / "scjn-leyes-vectors" / f"vectors-b-{SLUG}-{K}.parquet").unlink()
    with pytest.raises(legalvec.AssetNotCached):
        prepare_umap_input.prepare(tmp_path / "work", collections=COLLECTIONS,
                                   cache_dir=cache, log=lambda *a: None)


# -- project_umap --------------------------------------------------------- #

class StubUMAP:
    """Coordinates a test can predict: `x = 10 + row`, `y = 20 + 2 * row`."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def fit_transform(self, matrix):
        n = matrix.shape[0]
        return np.column_stack([10.0 + np.arange(n), 20.0 + 2.0 * np.arange(n)])

    def transform(self, matrix):
        n = matrix.shape[0]
        return np.column_stack([10.0 + np.zeros(n), 20.0 + np.zeros(n)])


class StubNNDescent:
    """`k + 1` neighbours per row, the row itself always first."""

    def __init__(self, matrix, **kwargs):
        n = matrix.shape[0]
        k = kwargs["n_neighbors"]
        indices, distances = [], []
        for row in range(n):
            others = [(row + 1 + j) % n for j in range(k - 1)]
            indices.append([row] + others)
            distances.append([0.0] + [0.1 * (j + 1) for j in range(k - 1)])
        self.neighbor_graph = (np.array(indices), np.array(distances))


@pytest.fixture
def stub_umap(monkeypatch):
    monkeypatch.setitem(sys.modules, "umap",
                        SimpleNamespace(UMAP=StubUMAP, __version__="0.0.stub"))
    monkeypatch.setitem(sys.modules, "pynndescent",
                        SimpleNamespace(NNDescent=StubNNDescent))


@pytest.fixture
def projected_input(tmp_path):
    """A work directory with 20 vectors and 3 instrument centroids."""
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    np.save(work_dir / "vectors.npy", np.arange(20 * K, dtype=np.float16).reshape(20, K))
    np.save(work_dir / "centroid_input.npy", np.ones((3, K), dtype=np.float32))
    return work_dir


def test_project_scales_coordinates_to_the_unit_square(projected_input, stub_umap):
    summary = project_umap.project(projected_input, n_neighbors=15, knn=0,
                                   log=lambda *a: None)

    coordinates = pq.read_table(projected_input / "nn015" / "coordinates.parquet").to_pydict()
    assert min(coordinates["x"]) == pytest.approx(0.0)
    assert max(coordinates["x"]) == pytest.approx(1.0)
    assert min(coordinates["y"]) == pytest.approx(0.0)
    assert max(coordinates["y"]) == pytest.approx(1.0)
    assert summary["x_range"] == pytest.approx([10.0, 29.0])
    assert summary["y_range"] == pytest.approx([20.0, 58.0])
    assert summary["n"] == 20 and summary["umap_version"] == "0.0.stub"
    assert (projected_input / "nn015" / ".done").exists()


def test_project_maps_centroids_with_the_same_affine_map(projected_input, stub_umap):
    project_umap.project(projected_input, n_neighbors=50, knn=0, log=lambda *a: None)

    centroids = pq.read_table(projected_input / "nn050" / "centroids.parquet").to_pydict()
    assert centroids["i"] == [0, 1, 2]
    # The stub transforms every centroid to the raw (10, 20), which is the
    # minimum of both axes -- so the shared map sends it to (0, 0).
    assert centroids["x"] == pytest.approx([0.0, 0.0, 0.0])
    assert centroids["y"] == pytest.approx([0.0, 0.0, 0.0])


def test_project_writes_neighbours_at_the_work_dir_root_without_the_self_row(
        projected_input, stub_umap):
    project_umap.project(projected_input, n_neighbors=15, knn=15, log=lambda *a: None)

    table = pq.read_table(projected_input / "neighbors.parquet").to_pydict()
    assert len(table["row"]) == 20
    assert all(len(group) == 15 for group in table["neighbors"])
    assert all(row not in group for row, group in zip(table["row"], table["neighbors"]))
    assert all(len(group) == 15 for group in table["distances"])
    assert not (projected_input / "nn015" / "neighbors.parquet").exists()


def test_project_keeps_an_existing_neighbour_table(projected_input, stub_umap, monkeypatch):
    """The neighbours live in the embedding space, not in a projection, so a
    second sweep must not spend minutes rewriting the same table."""
    (projected_input / "neighbors.parquet").write_bytes(b"not a parquet file")

    def explode(*args, **kwargs):
        raise AssertionError("the kNN was recomputed")

    monkeypatch.setattr(project_umap, "nearest_neighbors", explode)

    summary = project_umap.project(projected_input, n_neighbors=4, knn=15,
                                   log=lambda *a: None)
    assert summary["knn"] == 0
    assert "knn" not in summary["seconds"]
    assert (projected_input / "neighbors.parquet").read_bytes() == b"not a parquet file"


def test_force_knn_recomputes_the_neighbour_table(projected_input, stub_umap):
    (projected_input / "neighbors.parquet").write_bytes(b"not a parquet file")

    summary = project_umap.project(projected_input, n_neighbors=4, knn=15,
                                   force_knn=True, log=lambda *a: None)
    assert summary["knn"] == 15
    table = pq.read_table(projected_input / "neighbors.parquet").to_pydict()
    assert len(table["row"]) == 20


def test_project_writes_no_done_marker_when_the_fit_fails(projected_input, monkeypatch):
    class Exploding(StubUMAP):
        def fit_transform(self, matrix):
            raise RuntimeError("out of memory")

    monkeypatch.setitem(sys.modules, "umap",
                        SimpleNamespace(UMAP=Exploding, __version__="0.0.stub"))
    with pytest.raises(RuntimeError):
        project_umap.project(projected_input, n_neighbors=200, knn=0, log=lambda *a: None)
    assert not (projected_input / "nn200" / ".done").exists()
    assert not (projected_input / "nn200" / "projection.json").exists()


def test_thread_count_prefers_slurms_own_number(monkeypatch):
    monkeypatch.setenv("SLURM_CPUS_ON_NODE", "60")
    assert project_umap.thread_count() == 60
    assert project_umap.thread_count(8) == 8


def test_set_thread_env_pins_numba_before_it_is_imported(monkeypatch):
    monkeypatch.delenv("NUMBA_NUM_THREADS", raising=False)
    project_umap.set_thread_env(12)
    import os
    assert os.environ["NUMBA_NUM_THREADS"] == "12"
    assert os.environ["OMP_NUM_THREADS"] == "12"


# -- submit_umap ---------------------------------------------------------- #

def test_submission_order_is_the_heaviest_configuration_first():
    assert submit_umap.submission_order((4, 8, 16, 32)) == [32, 16, 8, 4]
    assert submit_umap.submission_order((8, 4)) == [8, 4]
    assert submit_umap.submission_order(()) == []


def test_the_default_sweep_is_the_one_project_umap_declares():
    assert project_umap.DEFAULT_N_NEIGHBORS == (16, 32, 64, 128)
    assert submit_umap.DEFAULT_CONFIGS == project_umap.DEFAULT_N_NEIGHBORS
    assert submit_umap.DEFAULT_KNN_CONFIG == 16
    assert build_umap_html.DEFAULT_PROJECTIONS == ("nn016", "nn032", "nn064", "nn128")


def test_the_sbatch_wrapper_asks_for_a_whole_node_for_eight_hours():
    wrapper = (Path(submit_umap.__file__).with_name("submit_umap.sh")
               ).read_text(encoding="utf-8")
    assert "#SBATCH --exclusive" in wrapper
    assert "#SBATCH --time=8:00:00" in wrapper
    assert "#SBATCH -c 60" in wrapper
    assert "/home/mgraffg/software/LegalIA/.venv/bin/python" in wrapper


def test_dry_run_prints_the_four_configurations_heaviest_first(tmp_path):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    (work_dir / "prepare.done").write_text("")

    lines = []
    submit_umap.submit(work_dir, dry_run=True, log=lines.append)

    commands = [line for line in lines if line.startswith("sbatch")]
    assert len(commands) == 4
    assert [c.split("--n-neighbors ")[1].split()[0] for c in commands] == \
        ["128", "64", "32", "16"]
    assert all("--parsable" in c for c in commands)
    assert all("--exclude=geoint0" in c for c in commands)
    # Only the cheapest one is asked for the shared kNN table.
    assert [("--knn 15" in c) for c in commands] == [False, False, False, True]
    assert not (work_dir / "jobs.json").exists()


def test_dry_run_skips_a_configuration_that_already_finished(tmp_path):
    work_dir = tmp_path / "work"
    (work_dir / "nn016").mkdir(parents=True)
    (work_dir / "prepare.done").write_text("")
    (work_dir / "nn016" / ".done").write_text("")

    lines = []
    submit_umap.submit(work_dir, dry_run=True, log=lines.append)

    commands = [line for line in lines if line.startswith("sbatch")]
    assert len(commands) == 3
    assert not any("--n-neighbors 16 " in c for c in commands)
    assert any("nn016: .done exists, skipping" == line for line in lines)


def test_submit_refuses_to_start_without_a_prepared_input(tmp_path):
    with pytest.raises(SystemExit):
        submit_umap.submit(tmp_path / "work", dry_run=True, log=lambda *a: None)


def test_submit_records_the_job_ids(tmp_path, monkeypatch):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    (work_dir / "prepare.done").write_text("")

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout=f"{1000 + len(calls)}\n", stderr="")

    monkeypatch.setattr(submit_umap.subprocess, "run", fake_run)
    # Two of the sweep's own configurations, so the kNN rule below is the
    # real one: only `DEFAULT_KNN_CONFIG` (the cheapest, 16) asks for it.
    state = submit_umap.submit(work_dir, configs=(16, 32), log=lambda *a: None)

    assert [job["name"] for job in state["jobs"]] == ["nn032", "nn016"]
    assert [job["job_id"] for job in state["jobs"]] == ["1001", "1002"]
    assert [job["knn"] for job in state["jobs"]] == [0, 15]
    assert json.loads((work_dir / "jobs.json").read_text())["jobs"] == state["jobs"]


def test_wait_returns_when_squeue_reports_nothing(tmp_path, monkeypatch):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    (work_dir / "jobs.json").write_text(json.dumps({
        "submitted_at": 0.0, "max_wait_hours": 8.0, "exclude": "geoint0",
        "configs": [15], "jobs": [{"name": "nn015", "job_id": "1001", "knn": 15}],
    }))
    answers = ["1001\n", ""]
    monkeypatch.setattr(submit_umap.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(returncode=0,
                                                        stdout=answers.pop(0), stderr=""))
    monkeypatch.setattr(submit_umap.time, "time", lambda: 10.0)
    outcome, pending = submit_umap.wait(work_dir, poll=0, sleep=lambda s: None,
                                        log=lambda *a: None)
    assert (outcome, pending) == ("finished", [])


def test_wait_returns_still_running_when_the_chunk_elapses(tmp_path, monkeypatch):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    (work_dir / "jobs.json").write_text(json.dumps({
        "submitted_at": 0.0, "max_wait_hours": 8.0, "exclude": "geoint0",
        "configs": [15], "jobs": [{"name": "nn015", "job_id": "1001", "knn": 15}],
    }))
    monkeypatch.setattr(submit_umap.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(returncode=0, stdout="1001\n",
                                                        stderr=""))
    clock = iter([0.0, 0.0, 100.0, 100.0, 100.0])
    monkeypatch.setattr(submit_umap.time, "time", lambda: next(clock))
    outcome, pending = submit_umap.wait(work_dir, poll=0, max_wait_minutes=1,
                                        sleep=lambda s: None, log=lambda *a: None)
    assert (outcome, pending) == ("still running", ["nn015"])


def test_wait_cancels_what_is_left_after_the_ceiling(tmp_path, monkeypatch):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    (work_dir / "jobs.json").write_text(json.dumps({
        "submitted_at": 0.0, "max_wait_hours": 8.0, "exclude": "geoint0",
        "configs": [15], "jobs": [{"name": "nn015", "job_id": "1001", "knn": 15}],
    }))
    cancelled = []

    def fake_run(cmd, **kwargs):
        if cmd[0] == "scancel":
            cancelled.append(cmd)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="1001\n", stderr="")

    monkeypatch.setattr(submit_umap.subprocess, "run", fake_run)
    monkeypatch.setattr(submit_umap.time, "time", lambda: 9 * 3600.0)
    outcome, pending = submit_umap.wait(work_dir, poll=0, sleep=lambda s: None,
                                        log=lambda *a: None)
    assert (outcome, pending) == ("timed out", ["nn015"])
    assert cancelled == [["scancel", "1001"]]


def test_queued_jobs_falls_back_to_one_query_per_job(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if len(calls) == 1:
            return SimpleNamespace(returncode=1, stdout="", stderr="Invalid job id")
        return SimpleNamespace(returncode=0, stdout="1002\n" if "1002" in cmd else "",
                               stderr="")

    monkeypatch.setattr(submit_umap.subprocess, "run", fake_run)
    assert submit_umap.queued_jobs(["1001", "1002"]) == {"1002"}
    assert len(calls) == 3


def test_report_marks_a_configuration_without_done_as_failed(tmp_path, capsys):
    work_dir = tmp_path / "work"
    (work_dir / "nn015").mkdir(parents=True)
    (work_dir / "nn015" / ".done").write_text("")
    (work_dir / "nn015" / "projection.json").write_text(json.dumps({
        "seconds_total": 1234.5, "peak_rss_gb": 41.2, "node": "geoint1",
        "slurm_job_id": "1001",
    }))
    (work_dir / "nn050").mkdir()
    (work_dir / "nn050" / "slurm-9.out").write_text("boom\n")

    rows = submit_umap.report(work_dir, configs=(15, 50))
    assert [(r["name"], r["state"]) for r in rows] == [("nn015", "done"), ("nn050", "failed")]
    printed = capsys.readouterr().out
    assert "geoint1" in printed and "boom" in printed
    assert "neighbors.parquet: MISSING" in printed

    assert submit_umap.main(["--work-dir", str(work_dir), "--report",
                             "--configs", "15,50"]) == 1
    assert submit_umap.main(["--work-dir", str(work_dir), "--report",
                             "--configs", "15"]) == 0


# -- build_umap_html ------------------------------------------------------ #

@pytest.fixture
def built(tmp_path, cache):
    """A work directory with three finished projections over the tiny corpus:
    two of the current sweep, plus one left over from the first one — which is
    what makes the `--projections all` rule testable."""
    work_dir = tmp_path / "work"
    prepare_umap_input.prepare(work_dir, collections=COLLECTIONS, cache_dir=cache,
                               log=lambda *a: None)
    n = np.load(work_dir / "vectors.npy").shape[0]
    instruments = pq.read_table(work_dir / "instruments.parquet").num_rows
    for name, n_neighbors in (("nn016", 16), ("nn032", 32), ("nn050", 50)):
        out = work_dir / name
        out.mkdir()
        pq.write_table(pa.table({
            "row": pa.array(range(n), type=pa.int32()),
            "x": pa.array(np.linspace(0, 1, n), type=pa.float32()),
            "y": pa.array(np.linspace(1, 0, n), type=pa.float32()),
        }), out / "coordinates.parquet")
        pq.write_table(pa.table({
            "i": pa.array(range(instruments), type=pa.int32()),
            "x": pa.array(np.linspace(0, 1, instruments), type=pa.float32()),
            "y": pa.array(np.linspace(0, 1, instruments), type=pa.float32()),
        }), out / "centroids.parquet")
        (out / "projection.json").write_text(json.dumps(
            {"name": name, "n_neighbors": n_neighbors}))
        (out / ".done").write_text("")
    pq.write_table(pa.table({
        "row": pa.array(range(n), type=pa.int32()),
        "neighbors": pa.array([[(r + 1) % n, (r + 2) % n] for r in range(n)],
                               type=pa.list_(pa.int32())),
        "distances": pa.array([[0.1, 0.2] for _ in range(n)],
                               type=pa.list_(pa.float32())),
    }), work_dir / "neighbors.parquet")
    return work_dir


def test_finished_projections_skips_what_never_finished(built):
    (built / "nn064").mkdir()
    found = build_umap_html.finished_projections(built)
    assert [p["name"] for p in found] == ["nn016", "nn032"]
    assert [p["n_neighbors"] for p in found] == [16, 32]
    assert [p["name"] for p in build_umap_html.finished_projections(built, {"nn050"})] \
        == ["nn050"]


def test_all_reaches_the_earlier_sweeps_directories(built):
    """`nn050` is outside the current sweep: absent by default, back with
    `all`, because nothing on disk was thrown away."""
    assert [p["name"] for p in build_umap_html.finished_projections(built, "all")] == \
        ["nn016", "nn032", "nn050"]
    assert build_umap_html._wanted_projections(None) is None
    assert build_umap_html._wanted_projections("all") == "all"
    assert build_umap_html._wanted_projections("nn016,nn050") == {"nn016", "nn050"}


def test_one_point_per_unit_row(built, cache):
    found = build_umap_html.finished_projections(built)
    points, instruments = build_umap_html.load_frames(
        built, found, neighbors=2, cache_dir=cache,
        collections=COLLECTIONS, log=lambda *a: None)

    assert len(points) == 6  # 5 leyes unit rows + 1 lineamientos
    assert len(instruments) == 3
    assert set(points.columns) == {"v", "c", "i", "u", "e", "x0", "y0", "x1", "y1", "n"}
    assert sorted(points["c"].unique()) == ["L", "N"]
    # The text `shaS` carries a leyes vector row and a lineamientos one.
    assert points.loc[points["e"] == "art_1", "v"].tolist() == [0, 2, 3]
    assert all("," in value for value in points["n"])


def test_unit_types_and_text_chars_narrow_the_points(built, cache):
    found = build_umap_html.finished_projections(built)
    points, _ = build_umap_html.load_frames(
        built, found, unit_types=("article",), neighbors=0, text_chars=5,
        cache_dir=cache, collections=COLLECTIONS, log=lambda *a: None)
    assert len(points) == 5  # the `heading` row is gone
    assert "n" not in points.columns
    assert points["t"].str.len().max() == 5


def test_projection_expr_chains_the_radio_over_the_pairs():
    assert build_umap_html.projection_expr("x", 1) == "datum.x0"
    assert build_umap_html.projection_expr("x", 2) == "proj === 0 ? datum.x0 : (datum.x1)"
    assert "proj === 1 ?" in build_umap_html.projection_expr("cy", 3)


def spec_of(built, cache, **kwargs):
    found = build_umap_html.finished_projections(built)
    points, instruments = build_umap_html.load_frames(
        built, found, cache_dir=cache, collections=COLLECTIONS,
        neighbors=kwargs.get("neighbors", 2), text_chars=kwargs.get("text_chars", 0),
        log=lambda *a: None)
    chart = build_umap_html.build_chart(points, instruments, found,
                                        background_points=2, **kwargs)
    return chart, chart.to_dict()  # to_dict validates against the schema


def param_names(spec) -> set[str]:
    names = set()

    def walk(node):
        if isinstance(node, dict):
            for param in node.get("params", []):
                if isinstance(param, dict) and "name" in param:
                    names.add(param["name"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(spec)
    return names


def test_the_spec_validates_and_carries_every_interaction(built, cache):
    _, spec = spec_of(built, cache, neighbors=2)
    assert {"pick", "pick_instrument", "collections", "proj"} <= param_names(spec)
    text = json.dumps(spec)
    assert "binding_radio" not in text and '"radio"' in text
    assert "pick_instrument.i" in text          # the detail honours both selections
    assert "split(pick.n[0]" in text            # the neighbour layer
    assert '"bind": "legend"' in text           # the collection toggle
    assert '"renderer": "canvas"' in text
    assert spec["vconcat"][0]["hconcat"][0]["mark"]["type"] == "circle"


def test_neighbors_zero_drops_the_field_and_the_layer(built, cache):
    _, spec = spec_of(built, cache, neighbors=0)
    text = json.dumps(spec)
    assert "split(pick.n[0]" not in text
    assert {"pick", "pick_instrument", "collections", "proj"} <= param_names(spec)
    dataset = next(iter(spec["datasets"].values()))
    assert "n" not in dataset[0]


def test_the_overview_has_no_voronoi_layer_by_default(built, cache):
    """`nearest` is what makes Vega-Lite insert a hidden Voronoi mark that
    captures the pointer, and every tooltip evaluated on it reads
    `undefined`. The default spec must not contain one anywhere."""
    _, spec = spec_of(built, cache, neighbors=2)
    assert "voronoi" not in json.dumps(spec)
    pick = next(p for p in spec["params"] if p["name"] == "pick")
    assert pick["select"].get("nearest", False) is False
    # `pick` stays on the overview, and the mark is big enough to be clicked
    # now that the pointer has to land on it.
    overview = spec["vconcat"][0]["hconcat"][0]
    assert pick["views"] == [overview["name"]]
    assert overview["mark"] == {"type": "circle", "size": 10}

    _, with_nearest = spec_of(built, cache, neighbors=2, nearest=True)
    pick = next(p for p in with_nearest["params"] if p["name"] == "pick")
    assert pick["select"]["nearest"] is True


def test_the_instrument_view_rings_and_names_whatever_was_clicked(built, cache):
    _, spec = spec_of(built, cache, neighbors=2)
    instrument_view = spec["vconcat"][1]
    layers = instrument_view["layer"]
    assert len(layers) == 3
    # The centroids keep the selection (Vega-Lite hoists a selection to the
    # top level and points it back at its own view); the ring and the label are
    # filtered by the same predicate the detail view uses, so a click in either
    # view lands.
    pick_instrument = next(p for p in spec["params"] if p["name"] == "pick_instrument")
    assert pick_instrument["views"] == [layers[0]["name"]]
    for layer in layers[1:]:
        predicate = layer["transform"][-1]["filter"]
        assert "pick.i" in predicate and "pick_instrument.i" in predicate
    assert layers[1]["mark"]["filled"] is False
    assert layers[1]["mark"]["stroke"] == "#000000"
    assert layers[2]["mark"]["type"] == "text"
    assert layers[2]["encoding"]["text"]["field"] == "nm"


def test_the_subtitles_explain_every_mark(built, cache):
    _, spec = spec_of(built, cache, neighbors=7)
    detail_subtitle = spec["vconcat"][0]["hconcat"][1]["title"]["subtitle"]
    assert any("black diamond" in line for line in detail_subtitle)
    assert any("7 nearest neighbours" in line for line in detail_subtitle)
    assert any("shape = unit type" in line for line in detail_subtitle)
    instrument_subtitle = spec["vconcat"][1]["title"]["subtitle"]
    assert any("black ring" in line for line in instrument_subtitle)


def test_the_neighbour_line_is_absent_without_neighbours(built, cache):
    _, spec = spec_of(built, cache, neighbors=0)
    detail = spec["vconcat"][0]["hconcat"][1]["title"]
    assert not any("nearest neighbours" in line for line in detail["subtitle"])
    assert any("black diamond" in line for line in detail["subtitle"])
    # ... and the title stops promising them too.
    assert "neighbours" not in detail["text"]
    assert detail["text"] == ("click a unit or an instrument: every unit of "
                             "that instrument")


def test_neighbours_are_off_by_default_everywhere(built, cache, monkeypatch):
    """Issue #241's third pass: "quita los vecinos en este momento" -- off,
    not gone. The whole kNN path stays; only every default moved to 0."""
    import inspect

    for function in (build_umap_html.load_frames, build_umap_html.build_chart,
                     build_umap_html.build):
        assert inspect.signature(function).parameters["neighbors"].default == 0

    found = build_umap_html.finished_projections(built)
    points, instruments = build_umap_html.load_frames(
        built, found, cache_dir=cache, collections=COLLECTIONS, log=lambda *a: None)
    assert "n" not in points.columns
    spec = build_umap_html.build_chart(points, instruments, found,
                                       background_points=2).to_dict()
    assert "split(pick.n[0]" not in json.dumps(spec)

    seen = {}
    monkeypatch.setattr(build_umap_html, "build",
                        lambda *args, **kwargs: seen.update(kwargs))
    build_umap_html.main([])
    assert seen["neighbors"] == 0
    build_umap_html.main(["--neighbors", "15"])
    assert seen["neighbors"] == 15


# -- "the last click wins" ------------------------------------------------ #

def test_each_selection_is_cleared_by_a_click_in_the_other_view(built, cache):
    """The spec-level half: both `clear` streams are strings that keep
    `dblclick` and add the other view's marks."""
    _, spec = spec_of(built, cache, neighbors=2)
    pick = next(p for p in spec["params"] if p["name"] == "pick")
    pick_instrument = next(p for p in spec["params"] if p["name"] == "pick_instrument")
    assert pick["select"]["clear"] == "dblclick, @centroids_1_marks:click"
    assert pick_instrument["select"]["clear"] == "dblclick, @overview_marks:click"
    # The names those streams address are on the two clickable views -- with
    # the suffix Altair adds to a layer child inside a concat, which is why
    # the constant says `centroids_1_marks` and not `centroids_marks`.
    assert spec["vconcat"][0]["hconcat"][0]["name"] == "overview"
    assert spec["vconcat"][1]["layer"][0]["name"] == "centroids_1"
    assert build_umap_html.OVERVIEW_MARKS == "overview_marks"
    assert build_umap_html.CENTROIDS_MARKS == "centroids_1_marks"


def toy_last_click_spec():
    """The smallest chart with this page's nesting: a named unit view beside
    a layered view whose first child is named, both carrying a selection that
    the other view's marks clear. It exists to pin down *Vega-Lite's own*
    naming rule, independently of what `build_chart` happens to do."""
    import altair as alt
    import pandas as pd

    data = pd.DataFrame({"x": [0.0, 1.0], "y": [1.0, 0.0], "i": [0, 1]})
    pick = alt.selection_point(
        name="pick", fields=["i"], on="click", empty=False,
        clear=f"dblclick, @{build_umap_html.CENTROIDS_MARKS}:click")
    pick_instrument = alt.selection_point(
        name="pick_instrument", fields=["i"], on="click", empty=False,
        clear=f"dblclick, @{build_umap_html.OVERVIEW_MARKS}:click")
    unit = lambda: alt.Chart(data).mark_circle().encode(x="x:Q", y="y:Q")  # noqa: E731
    overview = unit().add_params(pick).properties(name="overview")
    centroids = unit().add_params(pick_instrument).properties(name="centroids")
    return alt.vconcat(alt.hconcat(overview, unit()),
                       alt.layer(centroids, unit(), unit())).to_dict()


def test_vega_lite_names_a_unit_view_and_a_layer_child_differently():
    """Why `CENTROIDS_MARKS` carries a `_1`: a unit spec keeps its own name,
    a layer child gets its parent's concat index appended. Measured here, on
    a toy spec, so a Vega-Lite upgrade that changes the rule fails a fast
    test rather than a two-hour build."""
    vega = build_umap_html.compiled_vega(toy_last_click_spec())
    marks = [m["name"] for m in build_umap_html._vega_marks(vega) if m.get("name")]
    assert build_umap_html.OVERVIEW_MARKS in marks
    assert build_umap_html.CENTROIDS_MARKS in marks
    assert "centroids_marks" not in marks

    clears = build_umap_html.clearing_marknames(vega)
    assert clears["pick_tuple"] == [build_umap_html.CENTROIDS_MARKS]
    assert clears["pick_instrument_tuple"] == [build_umap_html.OVERVIEW_MARKS]


def test_the_compiled_real_spec_clears_each_selection_from_the_other_view(built, cache):
    _, spec = spec_of(built, cache, neighbors=2)
    lines = []
    found = build_umap_html.check_last_click_wins(spec, log=lines.append)
    assert build_umap_html.OVERVIEW_MARKS in found["marks"]
    assert build_umap_html.CENTROIDS_MARKS in found["marks"]
    assert found["clears"]["pick_tuple"] == [build_umap_html.CENTROIDS_MARKS]
    assert found["clears"]["pick_instrument_tuple"] == [build_umap_html.OVERVIEW_MARKS]
    assert any("cleared by @" in line for line in lines)


def test_a_spec_whose_selections_never_clear_each_other_fails_the_build(built, cache):
    """Without the cross-view streams both selections stay on at once, which
    is the defect this pass exists to fix -- so the build stops instead of
    writing a 70 MB page nobody can use."""
    _, spec = spec_of(built, cache, neighbors=2)
    for param in spec["params"]:
        if param["name"] in ("pick", "pick_instrument"):
            param["select"]["clear"] = "dblclick"
    with pytest.raises(SystemExit) as raised:
        build_umap_html.check_last_click_wins(spec, log=lambda *a: None)
    assert "pick_tuple" in str(raised.value)


def test_the_build_records_the_wiring_it_checked(built, cache, tmp_path):
    measured = build_umap_html.build(built, tmp_path / "out" / "umap.html",
                                     background_points=2, collections=COLLECTIONS,
                                     cache_dir=cache, log=lambda *a: None)
    wiring = measured["last_click_wins"]
    assert wiring["clears"]["pick_tuple"] == [build_umap_html.CENTROIDS_MARKS]
    assert wiring["clears"]["pick_instrument_tuple"] == [build_umap_html.OVERVIEW_MARKS]


def test_text_chars_adds_the_text_to_the_tooltip(built, cache):
    _, spec = spec_of(built, cache, neighbors=2, text_chars=20)
    assert '"title": "text"' in json.dumps(spec)


def test_the_written_html_embeds_the_spec(built, cache, tmp_path):
    output = tmp_path / "out" / "umap.html"
    measured = build_umap_html.build(built, output, neighbors=2, background_points=2,
                                     collections=COLLECTIONS, cache_dir=cache,
                                     log=lambda *a: None)
    html = output.read_text(encoding="utf-8")
    assert "vega-embed" in html
    assert "pick_instrument" in html
    assert output.with_suffix(".vl.json").exists()
    assert measured["points"] == 6 and measured["html_bytes"] > 0
    assert measured["projections"] == ["nn016", "nn032"]


def test_the_page_says_what_produced_it(built, cache, tmp_path):
    output = tmp_path / "out" / "umap.html"
    measured = build_umap_html.build(built, output, neighbors=2, background_points=2,
                                     collections=COLLECTIONS, cache_dir=cache,
                                     argv=["build_umap_html.py", "--neighbors", "2"],
                                     log=lambda *a: None)

    html = output.read_text(encoding="utf-8")
    assert html.count("Generated by scripts/embeddings/build_umap_html.py") == 1
    assert "<footer" in html
    assert str(built.resolve()) in html
    assert "nn016, nn032" in html
    assert "build_umap_html.py --neighbors 2" in html
    # The footer goes after the chart's own div, not inside it.
    assert html.index('<div id="vis"></div>') < html.index("<footer")

    spec = json.loads(output.with_suffix(".vl.json").read_text(encoding="utf-8"))
    provenance = spec["usermeta"]["provenance"]
    assert set(provenance) == {"script", "commit", "date", "work_dir", "argv",
                               "projections"}
    assert provenance["projections"] == ["nn016", "nn032"]
    assert provenance["script"] == "scripts/embeddings/build_umap_html.py"
    assert measured["provenance"] == provenance


def test_an_unknown_commit_is_never_a_failed_build(tmp_path):
    assert build_umap_html.repository_commit(tmp_path) == "unknown"


def test_the_footer_lands_in_either_altair_template():
    """`--inline-js` only swaps the `<script>` tags for the libraries' own
    source; both templates carry the same chart `div`, which is what
    `insert_footer` anchors on -- so the footer is tested against both
    templates rather than by running the inlined path, whose only other
    requirement (`vl-convert-python`) the viz group now ships for the
    compiled-spec check."""
    footer = build_umap_html.footer_html(
        {"script": "s", "commit": "c", "date": "d", "work_dir": "w",
         "argv": "a", "projections": ["nn016"]})
    with_div = build_umap_html.insert_footer(
        '<body>\n  <div id="vis"></div>\n  <script>1</script>\n</body>', footer)
    assert with_div.index('<div id="vis"></div>') < with_div.index(footer)
    assert with_div.index(footer) < with_div.index("<script>")

    without_div = build_umap_html.insert_footer("<body></body>", footer)
    assert without_div.index(footer) < without_div.index("</body>")


def test_build_refuses_a_work_dir_with_no_finished_projection(tmp_path, cache):
    (tmp_path / "work").mkdir()
    with pytest.raises(SystemExit):
        build_umap_html.build(tmp_path / "work", tmp_path / "out.html",
                              collections=COLLECTIONS, cache_dir=cache,
                              log=lambda *a: None)

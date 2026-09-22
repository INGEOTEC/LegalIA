"""The nearest-foreign-neighbour matrix and its page (issue #242).

    uv run --group viz pytest scripts/embeddings/tests -q

Synthetic data throughout: a five-instrument toy corpus over two collections
whose vectors are written by hand, so every count in the expected matrix can
be derived on paper. No Slurm (`subprocess.run` is monkeypatched), no network,
and no real UMAP fit (a stub reducer returns known coordinates) — what is
under test is the masking rule, the tie rule, the per-unit-row multiplicity,
the Slurm plumbing's three states and the generated Vega-Lite spec.
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

import build_instrument_umap_html  # noqa: E402
import build_umap_html  # noqa: E402
import instrument_matrix  # noqa: E402
import prepare_umap_input  # noqa: E402

SLUG = "qwen3-0.6b"
K = 4
COLLECTIONS = ("leyes", "lineamientos")

#: The toy corpus, chosen so that every rule issue #242 states shows up once.
#: Directions, not magnitudes: nothing here is normalised, exactly as the
#: published vectors are not.
VECTORS = {
    "t1": [1.0, 0.0, 0.0, 0.0],    # ley `a`; the same direction as `p`'s own t1
    "t2": [0.9, 0.1, 0.0, 0.0],    # ley `b`; near t1, equally near both copies
    "t3": [0.0, 1.0, 0.0, 0.0],    # ley `c`; nearest foreign text is the shared one
    "ts": [0.5, 0.5, 0.0, 0.0],    # shared by leyes `a` and `b`, twice in `b`
    "t4": [0.2, 0.0, 1.0, 0.0],    # lineamiento `p`
    "t5": [0.0, 0.0, 0.3, 1.0],    # lineamiento `q`; nearest foreign text is t4
}

#: `(clave, nombre, unit_type, eId, text_sha1, text)`, the shape
#: `legalvec.load_units` returns.
UNITS = {
    "leyes": [
        ("a", "Ley A", "article", "art_1", "t1", "texto t1"),
        ("a", "Ley A", "article", "art_2", "ts", "transitorio compartido"),
        ("b", "Ley B", "article", "art_1", "t2", "texto t2"),
        ("b", "Ley B", "article", "art_2", "ts", "transitorio compartido"),
        # The same text a second time inside `b`: one vector row, two unit
        # rows, and issue #242 counts it twice.
        ("b", "Ley B", "heading", "cap_1", "ts", "transitorio compartido"),
        ("c", "Ley C", "article", "art_1", "t3", "texto t3"),
    ],
    "lineamientos": [
        ("900", "Lineamientos P", "article", "art_1", "t4", "texto t4"),
        # Byte-identical to ley `a`'s t1, in the other collection: dedup is
        # inside a collection, so this is its own vector row at cosine 1.
        ("900", "Lineamientos P", "article", "art_2", "t1", "texto t1"),
        ("901", "Lineamientos Q", "article", "art_1", "t5", "texto t5"),
    ],
}


def write_vectors(directory: Path, name: str, hashes) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({
        "text_sha1": pa.array(list(hashes), type=pa.string()),
        "vector": pa.array([VECTORS[h] for h in hashes], type=pa.list_(pa.float32())),
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
    """A `legalvec` cache holding the toy corpus."""
    root = tmp_path / "cache"
    leyes = root / "scjn-leyes-vectors"
    write_units(leyes, "leyes", UNITS["leyes"])
    write_vectors(leyes, f"vectors-a-{SLUG}-{K}.parquet", ["t1"])
    write_vectors(leyes, f"vectors-b-{SLUG}-{K}.parquet", ["t2"])
    write_vectors(leyes, f"vectors-c-{SLUG}-{K}.parquet", ["t3"])
    write_vectors(leyes, f"vectors-shared-{SLUG}-{K}.parquet", ["ts"])

    lineamientos = root / "scjn-lineamientos-vectors"
    write_units(lineamientos, "lineamientos", UNITS["lineamientos"])
    write_vectors(lineamientos, f"vectors-900-{SLUG}-{K}.parquet", ["t4", "t1"])
    write_vectors(lineamientos, f"vectors-901-{SLUG}-{K}.parquet", ["t5"])
    write_vectors(lineamientos, f"vectors-shared-{SLUG}-{K}.parquet", [])
    return root


@pytest.fixture
def prepared(tmp_path, cache):
    """`prepare_umap_input.py`'s own output over the toy corpus — the work
    directory this issue reads."""
    work_dir = tmp_path / "work"
    prepare_umap_input.prepare(work_dir, collections=COLLECTIONS, cache_dir=cache,
                               log=lambda *a: None)
    return work_dir


def index_of(work_dir: Path) -> dict:
    """`clave` -> `i`, so no test has to predict the order
    `prepare_umap_input.py` assigned."""
    instruments = pq.read_table(work_dir / "instruments.parquet").to_pandas()
    return dict(zip(instruments["clave"], instruments["i"].astype(int)))


def computed(work_dir: Path, cache, **kwargs) -> dict:
    summary = instrument_matrix.build_matrix(
        work_dir, collections=COLLECTIONS, cache_dir=cache,
        log=lambda *a: None, **kwargs)
    out = instrument_matrix.output_dir(work_dir)
    return {
        "summary": summary,
        "matrix": np.load(out / "matrix.npy"),
        "nearest": pq.read_table(out / "nearest.parquet").to_pandas(),
        "index": index_of(work_dir),
    }


# -- the join and the owners ---------------------------------------------- #

def test_the_join_is_build_umap_htmls_own(prepared, cache):
    """Nine unit rows over six distinct texts, with `coleccion`, `clave` and
    `unit_type` decoded back from the compact codes the shared join emits."""
    units = instrument_matrix.unit_rows(prepared, collections=COLLECTIONS,
                                        cache_dir=cache, log=lambda *a: None)
    assert len(units) == 9
    assert list(units.columns) == ["i", "coleccion", "clave", "unit_type", "eId", "row"]
    assert sorted(units["coleccion"].unique()) == ["leyes", "lineamientos"]
    assert sorted(units["clave"].unique()) == ["900", "901", "a", "b", "c"]
    assert sorted(units["unit_type"].unique()) == ["article", "heading"]
    # The shared transitorio: one vector row, three unit rows (one in `a`,
    # two in `b`).
    shared = units[units["eId"].isin(["art_2", "cap_1"]) & (units["clave"].isin(["a", "b"]))]
    assert shared["row"].nunique() == 1
    assert len(shared) == 3


def test_owners_are_the_instruments_that_carry_a_text(prepared, cache):
    units = instrument_matrix.unit_rows(prepared, collections=COLLECTIONS,
                                        cache_dir=cache, log=lambda *a: None)
    vectors = np.load(prepared / "vectors.npy")
    owners = instrument_matrix.owners_of_rows(units, vectors.shape[0])
    index = index_of(prepared)
    shared_row = int(units.loc[(units["clave"] == "a") & (units["eId"] == "art_2"), "row"].iloc[0])
    assert sorted(owners[shared_row]) == sorted([index["a"], index["b"]])
    own_row = int(units.loc[(units["clave"] == "c"), "row"].iloc[0])
    assert owners[own_row] == [index["c"]]
    assert sum(1 for group in owners if len(group) > 1) == 1


# -- the matrix ------------------------------------------------------------ #

def test_the_matrix_is_the_hand_computed_one(prepared, cache):
    """Every count below is derivable from `VECTORS` with a pen:

    * `a`/t1 -> the identical text in `900` (cosine 1, other collection).
    * `a`/ts -> the text it shares with `b` (cosine 1, same collection).
    * `b`/t2 -> t1, which exists twice at the same cosine: a tie, +1 to `a`
      *and* +1 to `900`.
    * `b`/ts twice -> `a`, twice.
    * `c`/t3 -> the shared text, owned by `a` and `b`: +1 to each.
    * `900`/t4 -> t5 in `901` (0.282, above t1's 0.196); `900`/t1 -> the
      identical t1 in `a`, at cosine 1.
    * `901`/t5 -> t4 in `900`, the same 0.282 the other way round.
    """
    result = computed(prepared, cache)
    index, matrix = result["index"], result["matrix"]
    expected = np.zeros_like(matrix)
    expected[index["a"], index["900"]] = 1
    expected[index["a"], index["b"]] = 1
    expected[index["b"], index["a"]] = 3
    expected[index["b"], index["900"]] = 1
    expected[index["c"], index["a"]] = 1
    expected[index["c"], index["b"]] = 1
    expected[index["900"], index["a"]] = 1
    expected[index["900"], index["901"]] = 1
    expected[index["901"], index["900"]] = 1
    assert matrix.dtype == np.int32
    np.testing.assert_array_equal(matrix, expected)
    assert np.trace(matrix) == 0
    assert (matrix >= 0).all()
    assert matrix.sum() >= len(result["nearest"])


def test_a_text_shared_inside_a_collection_is_its_own_nearest_foreign_neighbour(
        prepared, cache):
    """The masking rule's whole point: a column the source shares with
    another instrument stays a candidate, so the answer is that very text at
    similarity 1 — the strongest relation there is."""
    result = computed(prepared, cache)
    nearest, index = result["nearest"], result["index"]
    shared = nearest[(nearest["clave"] == "a") & (nearest["eId"] == "art_2")].iloc[0]
    assert shared["similarity"] == pytest.approx(1.0)
    assert list(shared["targets"]) == [index["b"]]


def test_an_identical_text_in_the_other_collection_wins_at_cosine_one(prepared, cache):
    result = computed(prepared, cache)
    nearest, index = result["nearest"], result["index"]
    own = nearest[(nearest["clave"] == "a") & (nearest["eId"] == "art_1")].iloc[0]
    assert own["similarity"] == pytest.approx(1.0)
    assert list(own["targets"]) == [index["900"]]


def test_a_tie_credits_every_instrument_that_owns_a_winner(prepared, cache):
    result = computed(prepared, cache)
    nearest, index = result["nearest"], result["index"]
    tied = nearest[(nearest["clave"] == "b") & (nearest["eId"] == "art_1")].iloc[0]
    assert tied["n_winners"] == 2
    assert sorted(tied["targets"]) == sorted([index["a"], index["900"]])
    assert result["summary"]["tie_rows"] == 1
    assert result["summary"]["n_winners"]["2"] == 1
    assert result["summary"]["tolerance"] == instrument_matrix.DEFAULT_TOLERANCE


def test_a_text_repeated_inside_an_instrument_counts_once_per_unit_row(prepared, cache):
    """`b` carries the shared transitorio twice (an `article` and a
    `heading`): two unit rows, one vector row, two counts."""
    result = computed(prepared, cache)
    nearest, index = result["nearest"], result["index"]
    shared_row = int(nearest.loc[(nearest["clave"] == "a")
                                 & (nearest["eId"] == "art_2"), "row"].iloc[0])
    repeats = nearest[(nearest["clave"] == "b") & (nearest["row"] == shared_row)]
    assert len(repeats) == 2
    assert result["matrix"][index["b"], index["a"]] == 3   # 2 repeats + the tie row
    assert result["summary"]["unit_rows"] == 9
    assert result["summary"]["shared_rows"] == 1


def test_the_source_instruments_own_texts_are_masked(prepared, cache):
    """`c` has exactly one unit, so without masking its own text would win at
    cosine 1 and the row would be empty of foreign counts."""
    result = computed(prepared, cache)
    nearest, index = result["nearest"], result["index"]
    alone = nearest[nearest["clave"] == "c"].iloc[0]
    assert alone["similarity"] < 1.0
    assert sorted(alone["targets"]) == sorted([index["a"], index["b"]])
    assert result["matrix"][index["c"], index["c"]] == 0


def test_blocking_is_invisible(prepared, cache):
    """`--block-rows 1` splits every instrument into one product per row; the
    matrix may not notice."""
    default = computed(prepared, cache)["matrix"]
    (instrument_matrix.output_dir(prepared) / ".done").unlink()
    blocked = computed(prepared, cache, block_rows=1)["matrix"]
    np.testing.assert_array_equal(default, blocked)


def test_matrix_json_records_what_the_run_cost(prepared, cache):
    summary = computed(prepared, cache)["summary"]
    assert set(summary["seconds"]) == {"load", "normalise", "sweep", "write"}
    # Six distinct texts, but t1 exists in both collections and dedup is
    # inside a collection -- so seven vector rows.
    assert summary["vector_rows"] == 7
    assert summary["instruments"] == 5
    assert summary["matrix_sum"] == 11
    assert summary["peak_rss_gb"] > 0
    assert summary["block_rows"] == instrument_matrix.DEFAULT_BLOCK_ROWS
    assert set(summary["n_winners"]) == {"1", "2", "3-5", "6+"}


def test_done_makes_a_rerun_a_no_op_and_force_recomputes(prepared, cache, monkeypatch):
    computed(prepared, cache)
    done = instrument_matrix.output_dir(prepared) / ".done"
    assert done.exists()

    def explode(*args, **kwargs):
        raise AssertionError("build_matrix must not run again without --force")

    monkeypatch.setattr(instrument_matrix, "build_matrix", explode)
    assert instrument_matrix.main(["--work-dir", str(prepared)]) == 0

    seen = {}
    monkeypatch.setattr(instrument_matrix, "build_matrix",
                        lambda *args, **kwargs: seen.update(kwargs))
    assert instrument_matrix.main(["--work-dir", str(prepared), "--force"]) == 0
    assert seen["tolerance"] == instrument_matrix.DEFAULT_TOLERANCE


# -- the Slurm plumbing ---------------------------------------------------- #

def test_dry_run_prints_one_sbatch_command(tmp_path):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    (work_dir / "prepare.done").write_text("")

    lines = []
    instrument_matrix.submit(work_dir, dry_run=True, log=lines.append)

    command = next(line for line in lines if line.startswith("sbatch"))
    assert "--parsable" in command
    assert "--exclude=geoint0" in command
    assert "--time=2:00:00" in command
    assert "submit_umap.sh" in command and "instrument_matrix.py" in command
    # Slurm chdirs into its own spool directory, so every path is absolute.
    assert f"--work-dir {work_dir.resolve()}" in command
    assert not (instrument_matrix.output_dir(work_dir) / "job.json").exists()


def test_submit_refuses_a_work_dir_that_was_never_prepared(tmp_path):
    with pytest.raises(SystemExit):
        instrument_matrix.submit(tmp_path / "work", dry_run=True, log=lambda *a: None)


def test_submit_records_the_job_id(tmp_path, monkeypatch):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    (work_dir / "prepare.done").write_text("")
    monkeypatch.setattr(instrument_matrix.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(returncode=0, stdout="4242\n",
                                                        stderr=""))
    state = instrument_matrix.submit(work_dir, log=lambda *a: None)
    assert state["job_id"] == "4242"
    written = json.loads(
        (instrument_matrix.output_dir(work_dir) / "job.json").read_text(encoding="utf-8"))
    assert written["job_id"] == "4242"


def submitted(work_dir: Path, job_id: str = "4242") -> None:
    out = instrument_matrix.output_dir(work_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "job.json").write_text(json.dumps(
        {"job_id": job_id, "submitted_at": 0.0, "argv": "sbatch ...", "exclude": "geoint0"}))


def test_wait_returns_still_running_while_the_job_is_in_the_queue(tmp_path, monkeypatch):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    submitted(work_dir)
    monkeypatch.setattr(instrument_matrix, "queued_jobs", lambda ids: {"4242"})
    outcome = instrument_matrix.wait(work_dir, poll=0, max_wait_minutes=1e-9,
                                     sleep=lambda *a: None, log=lambda *a: None)
    assert outcome == "still running"
    assert instrument_matrix.main(["--work-dir", str(work_dir), "--wait",
                                   "--max-wait-minutes", "1e-9", "--poll", "0"]) \
        == instrument_matrix.EXIT_STILL_RUNNING


def test_wait_returns_finished_once_done_is_there(tmp_path, monkeypatch):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    submitted(work_dir)
    (instrument_matrix.output_dir(work_dir) / ".done").write_text("")
    monkeypatch.setattr(instrument_matrix, "queued_jobs", lambda ids: {"4242"})
    assert instrument_matrix.wait(work_dir, poll=0, sleep=lambda *a: None,
                                  log=lambda *a: None) == "finished"


def test_wait_reports_a_job_that_vanished_without_a_done(tmp_path, monkeypatch):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    submitted(work_dir)
    (instrument_matrix.output_dir(work_dir) / "slurm-4242.out").write_text(
        "Traceback\nMemoryError\n")
    monkeypatch.setattr(instrument_matrix, "queued_jobs", lambda ids: set())
    lines = []
    assert instrument_matrix.wait(work_dir, poll=0, sleep=lambda *a: None,
                                  log=lines.append) == "failed"
    # The tail of the job's own output is the only thing that says why.
    assert any("MemoryError" in line for line in lines)
    assert instrument_matrix.main(["--work-dir", str(work_dir), "--wait"]) == 1


def test_report_works_offline(prepared, cache, capsys):
    assert instrument_matrix.main(["--work-dir", str(prepared), "--report"]) == 1
    computed(prepared, cache)
    assert instrument_matrix.main(["--work-dir", str(prepared), "--report"]) == 0
    printed = capsys.readouterr().out
    assert "matrix.npy: present" in printed
    assert "done" in printed


# -- build_instrument_umap_html -------------------------------------------- #

class StubUMAP:
    """A reducer that returns a known, non-degenerate embedding, so the
    scaling and the writing are what is under test rather than UMAP."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def fit_transform(self, data):
        n = data.shape[0]
        offset = self.kwargs["n_neighbors"]
        return np.stack([np.arange(n, dtype=np.float32) + offset,
                         np.arange(n, dtype=np.float32)[::-1] * 2.0], axis=1)


@pytest.fixture
def stub_umap(monkeypatch):
    monkeypatch.setitem(sys.modules, "umap",
                        SimpleNamespace(UMAP=StubUMAP, __version__="stub"))


@pytest.fixture
def with_matrix(prepared, cache):
    instrument_matrix.build_matrix(prepared, collections=COLLECTIONS, cache_dir=cache,
                                   log=lambda *a: None)
    return prepared


def test_rows_are_l2_normalised(with_matrix):
    matrix = np.load(instrument_matrix.output_dir(with_matrix) / "matrix.npy")
    normalised = build_instrument_umap_html.normalise_rows(matrix)
    assert normalised.dtype == np.float32
    np.testing.assert_allclose(np.linalg.norm(normalised, axis=1),
                               np.ones(matrix.shape[0]), rtol=1e-6)


def test_a_zero_row_is_a_failure_not_a_nan(with_matrix):
    matrix = np.load(instrument_matrix.output_dir(with_matrix) / "matrix.npy")
    matrix[2] = 0
    with pytest.raises(SystemExit):
        build_instrument_umap_html.normalise_rows(matrix)


def test_the_four_projections_are_scaled_into_the_unit_square(with_matrix, stub_umap):
    summary = build_instrument_umap_html.project(with_matrix, log=lambda *a: None)
    table = pq.read_table(
        instrument_matrix.output_dir(with_matrix) / "umap.parquet").to_pandas()
    assert len(table) == 5
    assert list(table.columns) == ["i", "x4", "y4", "x8", "y8", "x16", "y16",
                                  "x32", "y32"]
    for column in table.columns[1:]:
        assert table[column].min() == pytest.approx(0.0)
        assert table[column].max() == pytest.approx(1.0)
    assert [fit["n_neighbors"] for fit in summary["fits"]] == [4, 8, 16, 32]
    assert all(fit["random_state"] == 0 and fit["metric"] == "cosine"
               and fit["min_dist"] == 0.1 for fit in summary["fits"])


def test_project_is_a_no_op_until_forced(with_matrix, stub_umap, monkeypatch):
    build_instrument_umap_html.project(with_matrix, log=lambda *a: None)
    written = (instrument_matrix.output_dir(with_matrix) / "umap.parquet").read_bytes()
    monkeypatch.setattr(build_instrument_umap_html, "fit_one",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("refitted")))
    build_instrument_umap_html.project(with_matrix, log=lambda *a: None)
    assert (instrument_matrix.output_dir(with_matrix) / "umap.parquet").read_bytes() \
        == written


def test_the_point_table_carries_both_directions_and_the_targets(with_matrix, stub_umap):
    build_instrument_umap_html.project(with_matrix, log=lambda *a: None)
    points = build_instrument_umap_html.instrument_points(with_matrix,
                                                          log=lambda *a: None)
    index = index_of(with_matrix)
    row = points[points["clave"] == "b"].iloc[0]
    # `b` sends three counts to `a` and one to `900`; `a` sends it one back.
    assert row["out"] == 4
    assert points[points["clave"] == "a"].iloc[0]["in"] == 3 + 1 + 1
    assert row["top"].splitlines()[0].startswith("Ley A (3)")
    assert str(index["a"]) in row["t"].split(",")


def test_the_spec_carries_every_interaction(with_matrix, stub_umap):
    build_instrument_umap_html.project(with_matrix, log=lambda *a: None)
    points = build_instrument_umap_html.instrument_points(with_matrix,
                                                          log=lambda *a: None)
    spec = build_instrument_umap_html.build_chart(points).to_dict()
    text = json.dumps(spec)

    radio = next(p for p in spec["params"] if p["name"] == "nn")
    assert radio["bind"]["options"] == [4, 8, 16, 32]
    assert radio["value"] == 16
    pick = next(p for p in spec["params"] if p["name"] == "pick")
    assert pick["select"].get("nearest", False) is False
    assert "voronoi" not in text
    assert '"bind": "legend"' in text
    assert '"renderer": "canvas"' in text
    assert "split(pick.t[0]" in text                 # the red rings
    for field in ("nombre", "clave", "coleccion", "units", "out", "in", "top"):
        assert f'"field": "{field}"' in text
    layers = spec["layer"]
    assert any(layer["mark"].get("stroke") == "#d62728" for layer in layers)
    assert any(layer["mark"].get("stroke") == "#000000" for layer in layers)
    assert any(layer["mark"].get("type") == "text" for layer in layers)
    assert any("black ring" in line for line in spec["title"]["subtitle"])


def test_the_written_page_says_what_produced_it(with_matrix, stub_umap, tmp_path):
    output = tmp_path / "out" / "umap-instruments.html"
    measured = build_instrument_umap_html.build(
        with_matrix, output, argv=["build_instrument_umap_html.py"],
        log=lambda *a: None)

    html = output.read_text(encoding="utf-8")
    assert html.count("Generated by scripts/embeddings/build_instrument_umap_html.py") == 1
    assert "vega-embed" in html
    assert output.with_suffix(".vl.json").exists()
    assert measured["instruments"] == 5

    spec = json.loads(output.with_suffix(".vl.json").read_text(encoding="utf-8"))
    provenance = spec["usermeta"]["provenance"]
    assert provenance["script"] == "scripts/embeddings/build_instrument_umap_html.py"
    assert provenance["n_neighbors"] == [4, 8, 16, 32]
    assert measured["provenance"] == provenance


def test_the_page_refuses_to_build_without_a_finished_matrix(tmp_path):
    (tmp_path / "work").mkdir()
    with pytest.raises(SystemExit):
        build_instrument_umap_html.build(tmp_path / "work", tmp_path / "out.html",
                                         log=lambda *a: None)

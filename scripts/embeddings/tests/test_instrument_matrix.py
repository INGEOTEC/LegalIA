"""The nearest-foreign-neighbour matrix and its page (issue #242).

    uv run --group viz pytest scripts/embeddings/tests -q

Synthetic data throughout: a six-instrument toy corpus over two collections
whose vectors are written by hand, so every weight in the expected matrix can
be derived on paper. No Slurm (`subprocess.run` is monkeypatched), no network,
and no real UMAP fit (a stub reducer returns known coordinates) — what is
under test is the masking rule, the tie rule, the `1/m` weighting, the
per-unit-row multiplicity, the Slurm plumbing's three states and the generated
Vega-Lite spec.
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
    # The boilerplate case this issue's second pass is about: one text carried
    # by *three* leyes at once. Its direction is the opposite of t1, so its
    # cosine against every other text here is 0 or negative and it never wins
    # anything it is not itself part of.
    "tb": [-1.0, 0.0, 0.0, 0.0],
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
        # One boilerplate text in three leyes: one vector row with three
        # owners, which is how "Se deroga." behaves in the real corpus.
        ("a", "Ley A", "article", "art_3", "tb", "Se deroga."),
        ("b", "Ley B", "article", "art_3", "tb", "Se deroga."),
        ("c", "Ley C", "article", "art_2", "tb", "Se deroga."),
    ],
    "lineamientos": [
        ("900", "Lineamientos P", "article", "art_1", "t4", "texto t4"),
        # Byte-identical to ley `a`'s t1, in the other collection: dedup is
        # inside a collection, so this is its own vector row at cosine 1.
        ("900", "Lineamientos P", "article", "art_2", "t1", "texto t1"),
        ("901", "Lineamientos Q", "article", "art_1", "t5", "texto t5"),
        # The same boilerplate, in the other collection: its own vector row,
        # so this instrument's single unit wins the leyes' copy outright (one
        # winning row, `n_winners == 1`) and credits its three owners `1/3`
        # each.
        ("902", "Lineamientos R", "article", "art_1", "tb", "Se deroga."),
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
    write_vectors(leyes, f"vectors-shared-{SLUG}-{K}.parquet", ["ts", "tb"])

    lineamientos = root / "scjn-lineamientos-vectors"
    write_units(lineamientos, "lineamientos", UNITS["lineamientos"])
    write_vectors(lineamientos, f"vectors-900-{SLUG}-{K}.parquet", ["t4", "t1"])
    write_vectors(lineamientos, f"vectors-901-{SLUG}-{K}.parquet", ["t5"])
    write_vectors(lineamientos, f"vectors-902-{SLUG}-{K}.parquet", ["tb"])
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
    """Thirteen unit rows over seven distinct texts, with `coleccion`, `clave`
    and `unit_type` decoded back from the compact codes the shared join
    emits."""
    units = instrument_matrix.unit_rows(prepared, collections=COLLECTIONS,
                                        cache_dir=cache, log=lambda *a: None)
    assert len(units) == 13
    assert list(units.columns) == ["i", "coleccion", "clave", "unit_type", "eId", "row"]
    assert sorted(units["coleccion"].unique()) == ["leyes", "lineamientos"]
    assert sorted(units["clave"].unique()) == ["900", "901", "902", "a", "b", "c"]
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
    own_row = int(units.loc[(units["clave"] == "c") & (units["eId"] == "art_1"),
                            "row"].iloc[0])
    assert owners[own_row] == [index["c"]]
    # The boilerplate row, the one with three owners.
    boilerplate = int(units.loc[(units["clave"] == "c") & (units["eId"] == "art_2"),
                                "row"].iloc[0])
    assert sorted(owners[boilerplate]) == sorted([index["a"], index["b"], index["c"]])
    assert sum(1 for group in owners if len(group) > 1) == 2


# -- the matrix ------------------------------------------------------------ #

def test_the_matrix_is_the_hand_computed_one(prepared, cache):
    """Every weight below is derivable from `VECTORS` with a pen — each unit
    row hands out a total of 1, split over the instruments it credits:

    * `a`/t1 -> the identical text in `900` (cosine 1, other collection): 1.
    * `a`/ts -> the text it shares with `b` (cosine 1, same collection): 1.
    * `a`/tb -> the boilerplate row (shared, so not masked) at cosine 1, and
      `902`'s identical copy at cosine 1 too: `b`, `c` and `902`, 1/3 each.
    * `b`/t2 -> t1, which exists twice at the same cosine: a tie, 1/2 to `a`
      *and* 1/2 to `900`.
    * `b`/ts twice -> `a`, twice, 1 each.
    * `b`/tb, `c`/tb -> the same three-way split `a`/tb got.
    * `c`/t3 -> the shared text, owned by `a` and `b`: 1/2 to each.
    * `900`/t4 -> t5 in `901` (0.282, above t1's 0.196); `900`/t1 -> the
      identical t1 in `a`, at cosine 1.
    * `901`/t5 -> t4 in `900`, the same 0.282 the other way round.
    * `902`/tb -> the leyes' boilerplate row, one winning row owned by three
      instruments: 1/3 to `a`, `b` and `c`.
    """
    result = computed(prepared, cache)
    index, matrix = result["index"], result["matrix"]
    third = 1.0 / 3.0
    expected = np.zeros_like(matrix)
    expected[index["a"], index["900"]] = 1
    expected[index["a"], index["b"]] = 1 + third
    expected[index["a"], index["c"]] = third
    expected[index["a"], index["902"]] = third
    expected[index["b"], index["a"]] = 0.5 + 1 + 1 + third
    expected[index["b"], index["900"]] = 0.5
    expected[index["b"], index["c"]] = third
    expected[index["b"], index["902"]] = third
    expected[index["c"], index["a"]] = 0.5 + third
    expected[index["c"], index["b"]] = 0.5 + third
    expected[index["c"], index["902"]] = third
    expected[index["900"], index["a"]] = 1
    expected[index["900"], index["901"]] = 1
    expected[index["901"], index["900"]] = 1
    expected[index["902"], index["a"]] = third
    expected[index["902"], index["b"]] = third
    expected[index["902"], index["c"]] = third
    assert matrix.dtype == np.float32
    np.testing.assert_allclose(matrix, expected, atol=1e-6)
    assert np.trace(matrix) == 0
    assert (matrix >= 0).all()
    # The identity the `1/m` rule exists for: one unit row, one unit of weight.
    assert abs(matrix.sum() - len(result["nearest"])) < 1e-4
    units_per_instrument = result["nearest"].groupby("i").size()
    np.testing.assert_allclose(matrix.sum(axis=1),
                               units_per_instrument.reindex(range(matrix.shape[0]),
                                                            fill_value=0).to_numpy(),
                               atol=1e-3)


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


def test_a_tie_credits_every_instrument_that_owns_a_winner_with_half_each(
        prepared, cache):
    result = computed(prepared, cache)
    nearest, index, matrix = result["nearest"], result["index"], result["matrix"]
    tied = nearest[(nearest["clave"] == "b") & (nearest["eId"] == "art_1")].iloc[0]
    assert tied["n_winners"] == 2
    assert sorted(tied["targets"]) == sorted([index["a"], index["900"]])
    assert tied["m"] == 2
    assert tied["weight"] == pytest.approx(0.5)
    # `b` points at `900` through this row alone, so the cell is the weight.
    assert matrix[index["b"], index["900"]] == pytest.approx(0.5)
    # Four rows tie: this one, plus each ley's own boilerplate row, which wins
    # both the shared leyes copy and `902`'s identical one at cosine 1.
    assert result["summary"]["tie_rows"] == 4
    assert result["summary"]["n_winners"]["2"] == 4
    assert result["summary"]["tolerance"] == instrument_matrix.DEFAULT_TOLERANCE


def test_a_winner_owned_by_three_instruments_gives_a_third_to_each(prepared, cache):
    """The boilerplate case this second pass is about: `902`'s single unit
    finds *one* winning vector row — no tie at all — which three leyes own
    because they all carry "Se deroga.". Under the first pass's rule that row
    added +1 to each of the three; now it adds 1/3, and the unit still weighs
    exactly one."""
    result = computed(prepared, cache)
    nearest, index, matrix = result["nearest"], result["index"], result["matrix"]
    boilerplate = nearest[nearest["clave"] == "902"].iloc[0]
    assert boilerplate["similarity"] == pytest.approx(1.0)
    assert boilerplate["n_winners"] == 1          # one vector row ...
    assert boilerplate["m"] == 3                  # ... owned by three instruments
    assert boilerplate["weight"] == pytest.approx(1 / 3)
    assert sorted(boilerplate["targets"]) == sorted([index["a"], index["b"], index["c"]])
    for clave in ("a", "b", "c"):
        assert matrix[index["902"], index[clave]] == pytest.approx(1 / 3, abs=1e-6)
    assert matrix[index["902"]].sum() == pytest.approx(1.0, abs=1e-6)
    assert result["summary"]["max_m"] == 3
    assert result["summary"]["m"]["3-5"] == 4     # `902`'s row and the three tb rows


def test_every_unit_row_carries_its_own_weight(prepared, cache):
    """`weight` is `1/m` on every row, and the rule is named in
    `matrix.json`."""
    result = computed(prepared, cache)
    nearest, summary = result["nearest"], result["summary"]
    assert (nearest["m"] >= 1).all()
    np.testing.assert_allclose(nearest["weight"].to_numpy(),
                               1.0 / nearest["m"].to_numpy(), rtol=1e-6)
    assert summary["weighting"] == "1/m"
    assert summary["matrix_dtype"] == "float32"
    assert summary["row_sums_equal_units"] is True
    assert set(summary["m"]) == {"1", "2", "3-5", "6-20", "21-100", "101+"}
    assert summary["matrix_sum"] == pytest.approx(summary["unit_rows"], abs=1e-3)


def test_a_text_repeated_inside_an_instrument_counts_once_per_unit_row(prepared, cache):
    """`b` carries the shared transitorio twice (an `article` and a
    `heading`): two unit rows, one vector row, two counts."""
    result = computed(prepared, cache)
    nearest, index = result["nearest"], result["index"]
    shared_row = int(nearest.loc[(nearest["clave"] == "a")
                                 & (nearest["eId"] == "art_2"), "row"].iloc[0])
    repeats = nearest[(nearest["clave"] == "b") & (nearest["row"] == shared_row)]
    assert len(repeats) == 2
    np.testing.assert_allclose(repeats["weight"].to_numpy(), 1.0)   # one target each
    # 2 repeats at 1, the tie row at 1/2, the boilerplate row at 1/3.
    assert result["matrix"][index["b"], index["a"]] == pytest.approx(2 + 0.5 + 1 / 3,
                                                                    abs=1e-6)
    assert result["summary"]["unit_rows"] == 13
    assert result["summary"]["shared_rows"] == 2


def test_the_source_instruments_own_texts_are_masked(prepared, cache):
    """`c`'s t3 is its own exclusively, so without masking it would win
    against itself at cosine 1 and that unit row would credit nobody."""
    result = computed(prepared, cache)
    nearest, index = result["nearest"], result["index"]
    alone = nearest[(nearest["clave"] == "c") & (nearest["eId"] == "art_1")].iloc[0]
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
    # Seven distinct texts, but t1 and tb exist in both collections and dedup
    # is inside a collection -- so nine vector rows.
    assert summary["vector_rows"] == 9
    assert summary["instruments"] == 6
    assert summary["matrix_sum"] == pytest.approx(13.0, abs=1e-3)
    assert summary["nonzero_cells"] == 17
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
    assert not command.endswith("--force")
    assert not (instrument_matrix.output_dir(work_dir) / "job.json").exists()


def test_submit_forwards_force_into_the_job(tmp_path):
    """The `.done` of a previous run is checked *inside* the job, so
    recomputing a finished directory needs the flag on the far side of
    `sbatch` too."""
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    (work_dir / "prepare.done").write_text("")

    lines = []
    instrument_matrix.submit(work_dir, dry_run=True, force=True, log=lines.append)
    command = next(line for line in lines if line.startswith("sbatch"))
    assert command.endswith("--force")


def test_a_forced_submit_drops_the_previous_runs_done(tmp_path, monkeypatch):
    """Otherwise `--wait` reads the marker of the run being replaced and calls
    a job that has barely been queued finished."""
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    (work_dir / "prepare.done").write_text("")
    done = instrument_matrix.output_dir(work_dir)
    done.mkdir(parents=True, exist_ok=True)
    (done / ".done").write_text("")
    monkeypatch.setattr(instrument_matrix.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(returncode=0, stdout="4243\n",
                                                        stderr=""))
    instrument_matrix.submit(work_dir, force=True, log=lambda *a: None)
    assert not (done / ".done").exists()


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
    assert len(table) == 6
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
    # `out` is the row sum, which under the `1/m` rule *is* the unit count:
    # `b` has four unit rows, and they weigh 2 + 1/2 + 1/3 towards `a`, 1/2
    # towards `900` and 1/3 each towards `c` and `902`.
    assert row["out"] == pytest.approx(4.0, abs=0.05)
    assert row["out"] == pytest.approx(float(row["units"]), abs=0.05)
    # What points *at* `a`: `b`'s 2.833, `c`'s 0.833, `900`'s 1 and `902`'s
    # 0.333.
    assert points[points["clave"] == "a"].iloc[0]["in"] == pytest.approx(5.0, abs=0.05)
    # One decimal, weight first, because a weight is a sum of fractions and
    # a long name must not push it out of the tooltip.
    assert row["top1"] == "2.8  Ley A"
    assert str(index["a"]) in row["t"].split(",")
    assert "top" not in points.columns


def test_strongest_targets_are_non_zero_heaviest_first_ties_by_id():
    matrix = np.array([[0.0, 1.0, 3.0, 0.0, 1.0, 2.0, 0.5, 0.0]], dtype=np.float32)
    strongest = build_instrument_umap_html.strongest_targets
    assert strongest(matrix, 0) == [2, 5, 1, 4, 6]       # 1 and 4 tie: 1 first
    assert strongest(matrix, 0, limit=3) == [2, 5, 1]
    # Fewer non-zero weights than the limit: only those, never a zero id.
    assert strongest(matrix, 0, limit=20) == [2, 5, 1, 4, 6]
    assert strongest(np.zeros((1, 4)), 0) == []


def test_target_rows_are_always_five_padded_with_an_em_dash():
    import pandas as pd

    instruments = pd.DataFrame({"nombre": ["Zero", "One", "Two", "Three"]})
    matrix = np.array([[0.0, 0.25, 11.3, 0.0]], dtype=np.float32)
    rows = build_instrument_umap_html.target_rows(matrix, instruments, 0)
    assert rows == ["11.3  Two", "0.2  One", "\u2014", "\u2014", "\u2014"]
    assert len(rows) == build_instrument_umap_html.TOP_TARGETS == 5


def test_the_rings_are_exactly_the_tooltips_targets(with_matrix, stub_umap):
    build_instrument_umap_html.project(with_matrix, log=lambda *a: None)
    points = build_instrument_umap_html.instrument_points(with_matrix,
                                                          log=lambda *a: None)
    matrix = np.load(instrument_matrix.output_dir(with_matrix) / "matrix.npy")
    nombre = dict(zip(points["i"].astype(int), points["nombre"]))
    columns = [f"top{rank}" for rank in range(1, 6)]
    for _, row in points.iterrows():
        ids = [int(j) for j in row["t"].split(",") if j]
        named = [row[c] for c in columns if row[c] != build_instrument_umap_html.NO_TARGET]
        assert [text.split("  ", 1)[1] for text in named] == [nombre[j] for j in ids]
        assert all(matrix[int(row["i"]), j] > 0 for j in ids)
        # The placeholders only ever trail.
        assert all(row[c] == build_instrument_umap_html.NO_TARGET
                   for c in columns[len(named):])
    # `b` points at four instruments, so its fifth row is the placeholder.
    b = points[points["clave"] == "b"].iloc[0]
    assert len(b["t"].split(",")) == 4
    assert b["top5"] == "\u2014"


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
    for field in ("nombre", "clave", "coleccion", "units", "out", "in",
                  "top1", "top2", "top3", "top4", "top5"):
        assert f'"field": "{field}"' in text
    assert '"field": "top"' not in text
    scatter = next(layer for layer in spec["layer"] if layer["mark"]["type"] == "circle")
    titles = [entry["title"] for entry in scatter["encoding"]["tooltip"]]
    assert titles == ["instrument", "clave", "collection", "units", "units pointing out",
                      "foreign units pointing here", "target 1", "target 2",
                      "target 3", "target 4", "target 5"]
    layers = spec["layer"]
    assert any(layer["mark"].get("stroke") == "#d62728" for layer in layers)
    assert any(layer["mark"].get("stroke") == "#000000" for layer in layers)
    assert any(layer["mark"].get("type") == "text" for layer in layers)
    assert any("black ring" in line for line in spec["title"]["subtitle"])
    assert any("5 instruments named in the tooltip" in line
               for line in spec["title"]["subtitle"])


def test_the_written_page_says_what_produced_it(with_matrix, stub_umap, tmp_path):
    output = tmp_path / "out" / "umap-instruments.html"
    measured = build_instrument_umap_html.build(
        with_matrix, output, argv=["build_instrument_umap_html.py"],
        log=lambda *a: None)

    html = output.read_text(encoding="utf-8")
    assert html.count("Generated by scripts/embeddings/build_instrument_umap_html.py") == 1
    assert "vega-embed" in html
    assert output.with_suffix(".vl.json").exists()
    assert measured["instruments"] == 6

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

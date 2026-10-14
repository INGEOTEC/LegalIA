"""The five closest foreign texts of every searched excerpt (issue #288).

    uv run --group viz pytest scripts/embeddings/tests/test_nearest_texts.py -q

The toy corpora of `test_instrument_matrix.py` (its fixtures imported, not
copied). The expected answer is an independent brute force over the stacked
vectors, written in this file with plain `numpy`, plus a few facts derived on
paper from `VECTORS`. No Slurm (`subprocess.run` is monkeypatched), no network.
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import instrument_matrix  # noqa: E402
import nearest_texts  # noqa: E402
from test_instrument_matrix import (  # noqa: E402,F401
    COLLECTIONS, SLUG, bm25_computed, cache, computed, index_of, prepared,
    transitorio_work)


def quiet(*args):
    pass


def run(work_dir, cache, **kwargs):
    return nearest_texts.build_nearest_texts(work_dir, collections=COLLECTIONS,
                                             cache_dir=cache, log=quiet, **kwargs)


def read_table(work_dir):
    return pq.read_table(nearest_texts.output_dir(work_dir) / nearest_texts.TABLE).to_pandas()


@pytest.fixture
def result(prepared, cache):
    """The matrix, then this script over it."""
    matrix = computed(prepared, cache)
    summary = run(prepared, cache)
    return {"matrix": matrix, "summary": summary, "table": read_table(prepared),
            "index": matrix["index"], "work_dir": prepared}


def brute_force(work_dir, cache, *, top, collections=COLLECTIONS):
    """Per `(i, vector row)` source, the `top` best foreign vector rows by
    `(-similarity, row)`, from nothing but the stacked vectors and the join."""
    all_units = instrument_matrix.unit_rows(work_dir, collections=collections,
                                            cache_dir=cache, log=quiet)
    units = all_units[instrument_matrix.searched_mask(all_units)]
    vectors = np.load(work_dir / "vectors.npy").astype(np.float64)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    owners = {}
    for i, row in units[["i", "row"]].drop_duplicates().itertuples(index=False):
        owners.setdefault(int(row), set()).add(int(i))
    expected = {}
    for i, row in units[["i", "row"]].drop_duplicates().itertuples(index=False):
        scores = vectors @ vectors[int(row)]
        eligible = [c for c in range(len(vectors))
                    if c in owners and owners[c] != {int(i)}]
        eligible.sort(key=lambda c: (-round(float(scores[c]), 6), c))
        expected[(int(i), int(row))] = [(c, float(scores[c]), sorted(owners[c] - {int(i)}))
                                        for c in eligible[:top]]
    return expected


def check_against_brute_force(table, expected):
    assert set(zip(table["i"], table["row"])) == set(expected)
    for (i, row), group in table.groupby(["i", "row"]):
        want = expected[(int(i), int(row))]
        assert list(group["rank"]) == list(range(1, len(want) + 1))
        # Same similarities, rank by rank; the rows agree wherever the scores
        # do not tie, and a tie is cut by the lower row (the brute force sorts
        # on the rounded score, so only an exact tie can reorder anything).
        np.testing.assert_allclose(group["similarity"], [w[1] for w in want], atol=1e-5)
        assert list(group["target_row"]) == [w[0] for w in want]
        assert [list(t) for t in group["targets"]] == [w[2] for w in want]


# -- the sweep ---------------------------------------------------------------- #

def test_the_table_is_the_brute_force_top_five(result, cache):
    table = result["table"]
    assert list(table.columns) == ["i", "coleccion", "clave", "row", "rank",
                                   "target_row", "similarity", "targets"]
    check_against_brute_force(table, brute_force(result["work_dir"], cache,
                                                 top=nearest_texts.TOP_TEXTS))
    assert nearest_texts.TOP_TEXTS == 5
    assert table["rank"].max() == 5
    assert table["i"].dtype == np.int32 and table["similarity"].dtype == np.float32
    # Sorted by (i, row, rank).
    keys = list(zip(table["i"], table["row"], table["rank"]))
    assert keys == sorted(keys)


def test_a_top_larger_than_the_candidates_is_allowed_and_recorded(prepared, cache):
    """Ten searched vector rows against at most eight foreign candidates each:
    nobody gets fifty."""
    computed(prepared, cache)
    summary = run(prepared, cache, top=50)
    table = read_table(prepared)
    check_against_brute_force(table, brute_force(prepared, cache, top=50))
    assert summary["top"] == 50
    assert summary["sources_short_of_top"] == summary["source_texts"]
    assert summary["rows_written"] == len(table)
    assert table.groupby(["i", "row"]).size().max() < 50


def test_the_summary_counts_sources_and_texts(result):
    summary = result["summary"]
    # Thirteen searched unit rows (16, minus three headings); the table is keyed
    # by the distinct `(i, row)`, one source text however many unit rows carry it.
    nearest = result["matrix"]["nearest"]
    assert summary["sources"] == len(nearest) == 13
    assert summary["source_texts"] == len(nearest[["i", "row"]].drop_duplicates())
    assert summary["rows_written"] == len(result["table"])
    assert summary["agrees_with_nearest"] is True
    assert summary["method"] == "dense" and summary["tolerance"] == 1e-6
    assert summary["block_rows"] == instrument_matrix.DEFAULT_BLOCK_ROWS
    for key in ("threads", "node", "seconds", "seconds_total", "peak_rss_gb", "slurm_job_id"):
        assert key in summary
    on_disk = json.loads((nearest_texts.output_dir(result["work_dir"]) / "summary.json")
                         .read_text(encoding="utf-8"))
    assert on_disk == summary
    assert (nearest_texts.output_dir(result["work_dir"]) / ".done").exists()


def test_the_exclusive_and_unowned_rows_never_appear_as_a_target(result, cache):
    work_dir = result["work_dir"]
    all_units = instrument_matrix.unit_rows(work_dir, collections=COLLECTIONS,
                                            cache_dir=cache, log=quiet)
    units = all_units[instrument_matrix.searched_mask(all_units)]
    owners = instrument_matrix.owners_of_rows(units, np.load(work_dir / "vectors.npy").shape[0])
    unowned = {c for c, group in enumerate(owners) if not group}
    # `c`'s heading text `th` is carried by a heading alone.
    assert unowned
    table = result["table"]
    assert not set(table["target_row"]) & unowned
    own = instrument_matrix.own_rows_of(units)
    for i, group in table.groupby("i"):
        exclusive = {c for c in own[i] if len(owners[c]) == 1}
        assert not set(group["target_row"]) & exclusive
        assert i not in {t for targets in group["targets"] for t in targets}


def test_a_text_shared_with_another_instrument_is_a_candidate_at_similarity_one(result):
    """`a`/ts (the transitorio `a` shares with `b`) has, as its nearest
    foreign text, that very text — owned by `b` too — at cosine 1."""
    table, index = result["table"], result["index"]
    nearest = result["matrix"]["nearest"]
    source = nearest[(nearest["clave"] == "a") & (nearest["eId"] == "art_2")].iloc[0]
    first = table[(table["i"] == index["a"]) & (table["row"] == source["row"])
                  & (table["rank"] == 1)].iloc[0]
    assert first["target_row"] == source["row"]
    assert first["similarity"] == pytest.approx(1.0, abs=1e-6)
    assert list(first["targets"]) == [index["b"]]


def test_rank_one_is_the_matrixs_winner_and_a_tie_is_two_ranks(result):
    """`b`/t2 ties between the two copies of `t1` (`a` and `900`): the matrix has
    one source with two winners, this table has them as ranks 1 and 2, the lower
    vector row first."""
    table, index = result["table"], result["index"]
    nearest = result["matrix"]["nearest"]
    tied = nearest[(nearest["clave"] == "b") & (nearest["eId"] == "art_1")].iloc[0]
    assert tied["n_winners"] == 2
    group = table[(table["i"] == index["b"]) & (table["row"] == tied["row"])]
    first, second = group.iloc[0], group.iloc[1]
    assert first["similarity"] == pytest.approx(second["similarity"], abs=1e-6)
    assert first["target_row"] < second["target_row"]
    assert sorted(set(first["targets"]) | set(second["targets"])) \
        == sorted(tied["targets"])


def test_every_matrix_source_has_a_rank_one_row(result):
    nearest = result["matrix"]["nearest"]
    table = result["table"]
    first = table[table["rank"] == 1]
    assert set(zip(nearest["i"], nearest["row"])) == set(zip(first["i"], first["row"]))


def test_a_tie_at_the_cut_is_broken_by_the_lower_row():
    similarity = np.array([[0.1, 0.9, 0.9, 0.9, 0.9, 0.2],
                           [0.5, 0.4, 0.3, 0.2, 0.1, 0.0]], dtype=np.float32)
    columns, values = nearest_texts.top_of_block(similarity, 3)
    assert columns.tolist() == [[1, 2, 3], [0, 1, 2]]
    assert values.tolist() == [pytest.approx([0.9, 0.9, 0.9]), pytest.approx([0.5, 0.4, 0.3])]
    # A masked column is a -inf that never outranks a real one.
    similarity[0, 1:5] = -np.inf
    columns, values = nearest_texts.top_of_block(similarity, 3)
    assert columns[0].tolist()[:2] == [5, 0]
    assert values[0][2] == -np.inf


def test_blocking_is_invisible(prepared, cache):
    """Which texts, in which order, owned by whom: identical for any block size.
    The one thing a block's shape can change is the last bit of a `float32`
    similarity (a different matrix product is a different summation order), so
    that column is compared at the matrix's own tolerance, not byte for byte."""
    computed(prepared, cache)
    run(prepared, cache)
    path = nearest_texts.output_dir(prepared) / nearest_texts.TABLE
    default = pq.read_table(path).to_pandas()
    run(prepared, cache, block_rows=1)
    other = pq.read_table(path).to_pandas()
    assert list(other.columns) == list(default.columns)
    for column in ("i", "coleccion", "clave", "row", "rank", "target_row"):
        assert other[column].tolist() == default[column].tolist()
    assert [list(t) for t in other["targets"]] == [list(t) for t in default["targets"]]
    np.testing.assert_allclose(other["similarity"], default["similarity"],
                               atol=instrument_matrix.DEFAULT_TOLERANCE)


# -- headings and transitorios --------------------------------------------- #

def test_headings_and_transitorios_are_neither_sources_nor_candidates(transitorio_work):
    work_dir, cache = transitorio_work
    instrument_matrix.build_matrix(work_dir, collections=("leyes",), cache_dir=cache, log=quiet)
    nearest_texts.build_nearest_texts(work_dir, collections=("leyes",), cache_dir=cache,
                                      log=quiet)
    table = read_table(work_dir)
    all_units = instrument_matrix.unit_rows(work_dir, collections=("leyes",),
                                            cache_dir=cache, log=quiet)
    searched = all_units[instrument_matrix.searched_mask(all_units)]
    out = all_units[~instrument_matrix.searched_mask(all_units)]
    assert len(out) == 9
    searched_pairs = set(zip(searched["i"], searched["row"]))
    assert set(zip(table["i"], table["row"])) == searched_pairs
    # A text a transitorio shares with an article (`e1`: `p`, `q`, `s` and the
    # article of `r`) stays a candidate, owned by `r` alone; a text only
    # transitorios and headings carry is never a target.
    vectors = np.load(work_dir / "vectors.npy").shape[0]
    owners = instrument_matrix.owners_of_rows(searched, vectors)
    unowned = {c for c, group in enumerate(owners) if not group}
    assert unowned and not set(table["target_row"]) & unowned
    index = index_of(work_dir)
    shared = table[table["target_row"].map(lambda c: owners[int(c)] == [index["r"]])]
    assert len(shared)
    check_against_brute_force(table, brute_force(work_dir, cache, top=5,
                                                 collections=("leyes",)))


# -- the agreement check -------------------------------------------------------- #

def doctor(work_dir, change):
    path = instrument_matrix.output_dir(work_dir) / "nearest.parquet"
    frame = pq.read_table(path).to_pandas()
    change(frame)
    pq.write_table(pa.table({
        "i": pa.array(frame["i"].to_numpy(), type=pa.int32()),
        "coleccion": pa.array(frame["coleccion"]),
        "clave": pa.array(frame["clave"]),
        "unit_type": pa.array(frame["unit_type"]),
        "eId": pa.array(frame["eId"]),
        "row": pa.array(frame["row"].to_numpy(), type=pa.int32()),
        "similarity": pa.array(frame["similarity"].to_numpy(), type=pa.float32()),
        "n_winners": pa.array(frame["n_winners"].to_numpy(), type=pa.int32()),
        "targets": pa.array(list(frame["targets"]), type=pa.list_(pa.int32())),
        "m": pa.array(frame["m"].to_numpy(), type=pa.int32()),
        "weight": pa.array(frame["weight"].to_numpy(), type=pa.float32()),
        "counted": pa.array(frame["counted"].to_numpy(), type=pa.bool_()),
        "drop_reason": pa.array(frame["drop_reason"], type=pa.string()),
    }), path)


def test_a_doctored_similarity_stops_the_script_naming_the_source(prepared, cache):
    matrix = computed(prepared, cache)
    victim = matrix["nearest"].iloc[3]

    def change(frame):
        frame.loc[3, "similarity"] = frame.loc[3, "similarity"] - 0.01

    doctor(prepared, change)
    with pytest.raises(SystemExit) as error:
        run(prepared, cache)
    assert f"instrument {int(victim['i'])}, vector row {int(victim['row'])}" in str(error.value)
    assert not (nearest_texts.output_dir(prepared) / ".done").exists()


def test_doctored_targets_stop_the_script(prepared, cache):
    matrix = computed(prepared, cache)
    victim = matrix["nearest"].iloc[0]

    def change(frame):
        frame["targets"] = [[99] if position == 0 else list(targets)
                            for position, targets in enumerate(frame["targets"])]

    doctor(prepared, change)
    with pytest.raises(SystemExit, match=f"instrument {int(victim['i'])}, vector row "
                                         f"{int(victim['row'])}"):
        run(prepared, cache)


def test_a_source_the_matrix_does_not_have_is_refused(prepared, cache):
    matrix = computed(prepared, cache)
    nearest = matrix["nearest"]
    victim = nearest.iloc[0]

    def change(frame):
        frame.drop(index=frame[(frame["i"] == victim["i"])
                               & (frame["row"] == victim["row"])].index, inplace=True)
        frame.reset_index(drop=True, inplace=True)

    doctor(prepared, change)
    with pytest.raises(SystemExit, match="does not have"):
        run(prepared, cache)


def test_the_script_needs_a_finished_matrix(prepared, cache):
    with pytest.raises(SystemExit, match="nearest.parquet|instrument-matrix"):
        run(prepared, cache)
    with pytest.raises(SystemExit):
        nearest_texts.main(["--work-dir", str(prepared)])


# -- .done, --force ------------------------------------------------------------- #

def test_done_makes_a_rerun_a_no_op_and_force_recomputes(prepared, cache, monkeypatch):
    computed(prepared, cache)
    run(prepared, cache)
    done = nearest_texts.output_dir(prepared) / ".done"
    assert done.exists()

    def explode(*args, **kwargs):
        raise AssertionError("must not recompute")

    monkeypatch.setattr(nearest_texts, "build_nearest_texts", explode)
    assert nearest_texts.main(["--work-dir", str(prepared)]) == 0
    seen = {}
    monkeypatch.setattr(nearest_texts, "build_nearest_texts",
                        lambda *args, **kwargs: seen.update(kwargs))
    assert nearest_texts.main(["--work-dir", str(prepared), "--force", "--top", "3"]) == 0
    assert seen["top"] == 3 and seen["tolerance"] == instrument_matrix.DEFAULT_TOLERANCE


# -- the Slurm plumbing ------------------------------------------------------------ #

def ready(tmp_path):
    work_dir = tmp_path / "work"
    (work_dir / "instrument-matrix").mkdir(parents=True)
    (work_dir / "prepare.done").write_text("")
    (work_dir / "instrument-matrix" / ".done").write_text("")
    (work_dir / "instrument-matrix" / "nearest.parquet").write_text("")
    return work_dir


def test_dry_run_prints_one_sbatch_command(tmp_path, capsys):
    work_dir = ready(tmp_path)
    assert nearest_texts.main(["--work-dir", str(work_dir), "--dry-run"]) == 0
    lines = [line for line in capsys.readouterr().out.splitlines()
             if line.startswith("sbatch")]
    assert len(lines) == 1
    command = lines[0]
    assert "--parsable" in command and "--job-name=nearest-texts" in command
    assert "--exclude=geoint0" in command and "--time=2:00:00" in command
    assert "submit_umap.sh" in command and "nearest_texts.py" in command
    assert f"--work-dir {work_dir.resolve()}" in command
    assert "--top 5" in command and "--block-rows 1024" in command
    assert f"--output={work_dir.resolve() / 'nearest-texts' / 'slurm-%j.out'}" in command
    assert not (nearest_texts.output_dir(work_dir) / "job.json").exists()


def test_submit_records_the_job_and_forwards_force(tmp_path, monkeypatch):
    work_dir = ready(tmp_path)
    done = nearest_texts.output_dir(work_dir)
    done.mkdir(parents=True, exist_ok=True)
    (done / ".done").write_text("")
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="4242\n", stderr="")

    monkeypatch.setattr(instrument_matrix.subprocess, "run", fake_run)
    assert nearest_texts.main(["--work-dir", str(work_dir), "--submit", "--force"]) == 0
    assert calls[0][-1] == "--force"
    # The previous run's marker goes before the new job starts.
    assert not (done / ".done").exists()
    state = json.loads((done / "job.json").read_text(encoding="utf-8"))
    assert state["job_id"] == "4242"
    # The matrix's own directory is untouched.
    assert not (work_dir / "instrument-matrix" / "job.json").exists()


@pytest.mark.parametrize("missing", ["prepare.done", "instrument-matrix/.done",
                                     "instrument-matrix/nearest.parquet"])
def test_submit_refuses_a_work_dir_without_a_finished_matrix(tmp_path, missing):
    work_dir = ready(tmp_path)
    (work_dir / missing).unlink()
    with pytest.raises(SystemExit, match="missing"):
        nearest_texts.main(["--work-dir", str(work_dir), "--submit", "--dry-run"])


def submitted(work_dir, job_id="4242"):
    out = nearest_texts.output_dir(work_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "job.json").write_text(json.dumps(
        {"job_id": job_id, "submitted_at": 0.0, "argv": "sbatch ...", "exclude": "geoint0"}))
    return out


def test_wait_returns_75_while_queued(tmp_path, monkeypatch):
    work_dir = tmp_path / "work"
    submitted(work_dir)
    monkeypatch.setattr(instrument_matrix, "queued_jobs", lambda ids: {"4242"})
    assert nearest_texts.main(["--work-dir", str(work_dir), "--wait",
                               "--max-wait-minutes", "1e-9", "--poll", "0"]) \
        == nearest_texts.EXIT_STILL_RUNNING == 75


def test_wait_returns_zero_once_done_exists(tmp_path, monkeypatch, capsys):
    work_dir = tmp_path / "work"
    (submitted(work_dir) / ".done").write_text("")
    monkeypatch.setattr(instrument_matrix, "queued_jobs", lambda ids: {"4242"})
    assert nearest_texts.main(["--work-dir", str(work_dir), "--wait"]) == 0
    assert "nearest-texts: done" in capsys.readouterr().out


def test_wait_reports_a_job_that_vanished_without_a_done(tmp_path, monkeypatch, capsys):
    work_dir = tmp_path / "work"
    (submitted(work_dir) / "slurm-4242.out").write_text("Traceback\nMemoryError\n")
    monkeypatch.setattr(instrument_matrix, "queued_jobs", lambda ids: set())
    assert nearest_texts.main(["--work-dir", str(work_dir), "--wait"]) == 1
    output = capsys.readouterr().out
    assert "left the queue" in output and "MemoryError" in output


def test_report_is_offline_and_says_whether_it_agrees(result, capsys):
    assert nearest_texts.main(["--work-dir", str(result["work_dir"]), "--report"]) == 0
    output = capsys.readouterr().out
    assert "agrees_with_nearest: true" in output
    assert nearest_texts.main(["--work-dir", str(result["work_dir"].parent / "nowhere"),
                               "--report"]) == 1


def test_instrument_matrixs_own_slurm_line_is_unchanged(tmp_path):
    command = instrument_matrix.sbatch_command(tmp_path)
    assert "--job-name=instrument-matrix" in command
    assert any(part.endswith("instrument_matrix.py") for part in command)
    assert f"--output={tmp_path.resolve() / 'instrument-matrix' / 'slurm-%j.out'}" in command
    assert "--top" not in command


# -- a lexical scorer ----------------------------------------------------------------- #

def test_a_bm25_work_directory_writes_no_zero_score_row(tmp_path, prepared, cache):
    matrix = bm25_computed(tmp_path, prepared, cache)
    work_dir = tmp_path / "bm25"
    assert matrix["summary"]["method"] == "bm25"
    summary = run(work_dir, cache)
    table = read_table(work_dir)
    assert summary["method"] == "bm25" and summary["agrees_with_nearest"] is True
    assert (table["similarity"] > 0).all()
    assert summary["sources_without_rows"] == 0
    # Rank 1 is the matrix's best, relative tolerance.
    first = table[table["rank"] == 1]
    nearest = matrix["nearest"].drop_duplicates(["i", "row"])
    merged = first.merge(nearest, on=["i", "row"], suffixes=("_t", "_m"))
    np.testing.assert_allclose(merged["similarity_t"], merged["similarity_m"], rtol=1e-5)
    # `texto` is all most sources share with anything: ties at the cut are
    # kept apart by the lower row.
    for _, group in table.groupby(["i", "row"]):
        keys = list(zip(-group["similarity"].round(6), group["target_row"]))
        assert keys == sorted(keys)


def test_nearest_texts_dry_run_on_a_shared_node(tmp_path, capsys):
    work_dir = ready(tmp_path)
    assert nearest_texts.main(["--work-dir", str(work_dir), "--dry-run", "--shared-node",
                               "--cpus", "8", "--mem", "32G"]) == 0
    command = next(line for line in capsys.readouterr().out.splitlines()
                   if line.startswith("sbatch"))
    assert "submit_cpu.sh" in command and "nearest_texts.py" in command
    assert "--cpus-per-task=8" in command and "--mem=32G" in command
    assert "--exclude" not in command and "--top 5" in command

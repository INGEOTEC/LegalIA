"""Scoring the Atlas data sets against the strong-link gold (issue #272).

    uv run --group viz pytest scripts/embeddings/tests -q

Hand-built matrices with known answers: no real work directory, no vectors, no
network. A work directory here is only `instruments.parquet` and
`instrument-matrix/matrix.npy`, which is all `evaluate_links` reads.
"""

import json
import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import evaluate_links  # noqa: E402

INSTRUMENTS = [["leyes", "a"], ["leyes", "b"], ["reglamentos", "1"], ["reglamentos", "2"]]


def write_work_dir(path: Path, matrix, instruments=INSTRUMENTS) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({
        "i": pa.array(range(len(instruments)), type=pa.int32()),
        "coleccion": [c for c, _ in instruments],
        "clave": [k for _, k in instruments],
        "nombre": [f"{c} {k}" for c, k in instruments],
    }), path / "instruments.parquet")
    if matrix is not None:
        (path / "instrument-matrix").mkdir(exist_ok=True)
        np.save(path / "instrument-matrix" / "matrix.npy", np.asarray(matrix, dtype=np.float32))
    return path


def gold_record(entries=None) -> dict:
    """Instrument 2 develops law 0 (A); instrument 3 develops laws 0 and 1 (B)."""
    entries = entries or [
        {"i": 2, "coleccion": "reglamentos", "clave": "1", "laws_A": [0], "laws_B": [],
         "laws": [0], "signals": ["A"]},
        {"i": 3, "coleccion": "reglamentos", "clave": "2", "laws_A": [], "laws_B": [0, 1],
         "laws": [0, 1], "signals": ["B"]},
    ]
    return {"instruments": INSTRUMENTS, "entries": entries, "summary": {}, "provenance": {}}


# -- the four metrics, by hand ------------------------------------------------ #

def test_closest_top5_rank_and_share_of_a_clear_row():
    result = evaluate_links.row_metrics(np.array([0.0, 3.0, 1.0, 0.0]), [1])
    assert result == {"closest": True, "top5": True, "mrr": 1.0, "share": 0.75,
                      "tied_at_top": False}


def test_the_best_ranked_gold_law_sets_the_rank():
    result = evaluate_links.row_metrics(np.array([4.0, 1.0, 3.0, 2.0]), [1, 3])
    assert result["closest"] is False and result["top5"] is True
    assert result["mrr"] == pytest.approx(1 / 3)               # 4, 3 and 2 beat 1; 2 beats nobody
    assert result["share"] == pytest.approx(3 / 10)


def test_rank_is_the_competition_rank_under_a_tie():
    """Two instruments share the largest weight: both are rank 1, so a gold law
    among them is `closest` whatever its column (argmax would pick column 0)."""
    row = np.array([2.0, 2.0, 1.0, 0.0])
    for gold in ([0], [1]):
        result = evaluate_links.row_metrics(row, gold)
        assert result["closest"] is True and result["mrr"] == 1.0
        assert result["tied_at_top"] is True
    below = evaluate_links.row_metrics(np.array([2.0, 2.0, 1.0, 1.0]), [3])
    assert below["mrr"] == pytest.approx(1 / 3)                # two instruments strictly greater


def test_two_gold_laws_tied_with_each_other_are_not_a_tie_with_a_stranger():
    result = evaluate_links.row_metrics(np.array([2.0, 2.0, 0.0, 1.0]), [0, 1])
    assert result["closest"] is True and result["tied_at_top"] is False


def test_a_gold_law_with_zero_weight_has_infinite_rank():
    result = evaluate_links.row_metrics(np.array([0.0, 0.0, 2.0, 1.0]), [1])
    assert result == {"closest": False, "top5": False, "mrr": 0.0, "share": 0.0,
                      "tied_at_top": False}


def test_top5_is_rank_five_or_better():
    row = np.array([6.0, 5.0, 4.0, 3.0, 2.0, 1.0])
    assert evaluate_links.row_metrics(row, [4])["top5"] is True
    assert evaluate_links.row_metrics(row, [5])["top5"] is False
    assert evaluate_links.row_metrics(row, [5])["mrr"] == pytest.approx(1 / 6)


def test_a_zero_row_is_not_scored():
    assert evaluate_links.row_metrics(np.zeros(4), [0]) is None


def test_the_instrument_with_a_zero_row_in_any_model_is_skipped_in_all():
    first = np.array([[0, 0, 0, 0], [0, 0, 0, 0], [3, 1, 0, 0], [1, 2, 0, 0]], dtype=float)
    second = first.copy()
    second[3] = 0
    scorable, arrays, skipped = evaluate_links.metric_table(
        {"x": first, "y": second}, {2: [0], 3: [1]})
    assert scorable == [2] and skipped == {"x": 0, "y": 1}
    assert all(len(arrays[m]["closest"]) == 1 for m in arrays)


# -- significance ------------------------------------------------------------ #

def indices_for(n, resamples=2000, seed=0):
    return np.random.default_rng(seed).integers(0, n, size=(resamples, n))


def test_identical_columns_are_a_tie():
    a = np.array([1, 0, 1, 1, 0, 1, 0, 0, 1, 1], dtype=float)
    result = evaluate_links.compare(a, a.copy(), True, indices_for(10))
    assert result["verdict"] == "tie"
    assert result["difference"] == 0.0 and result["p"] == 1.0


def test_all_hits_against_all_misses_is_a_real_difference():
    n = 30
    result = evaluate_links.compare(np.ones(n), np.zeros(n), True, indices_for(n),
                                    names=("good", "bad"))
    assert result["verdict"] == "good better"
    assert result["p"] < 0.05 and result["interval"][0] > 0
    reverse = evaluate_links.compare(np.zeros(n), np.ones(n), True, indices_for(n),
                                     names=("bad", "good"))
    assert reverse["verdict"] == "good better"


def test_a_binary_difference_needs_mcnemar_as_well_as_the_interval():
    """Seven wins against none: the paired bootstrap interval excludes 0 but the
    exact test (p = 2 * 0.5**7 = 0.0156) agrees, so it is real; four against none
    (p = 0.125) is a tie even though the interval of the means can exclude 0."""
    n = 40
    a = np.zeros(n)
    b = np.zeros(n)
    a[:7] = 1
    assert evaluate_links.compare(a, b, True, indices_for(n))["verdict"] != "tie"
    a = np.zeros(n)
    a[:4] = 1
    four = evaluate_links.compare(a, b, True, indices_for(n))
    assert four["p"] == pytest.approx(0.125)
    assert four["verdict"] == "tie"


def test_mcnemar_exact_counts_the_discordant_pairs():
    a = np.array([1, 1, 1, 0, 0, 1], dtype=float)
    b = np.array([1, 0, 0, 0, 1, 1], dtype=float)
    assert evaluate_links.mcnemar_exact(a, b) == {"only_first": 2, "only_second": 1,
                                                  "p": pytest.approx(1.0)}
    assert evaluate_links.mcnemar_exact(a, a)["p"] == 1.0


def test_a_continuous_metric_ignores_mcnemar():
    n = 30
    result = evaluate_links.compare(np.full(n, 0.9), np.full(n, 0.1), False, indices_for(n))
    assert result["p"] is None and result["verdict"].endswith("better")


def test_the_bootstrap_is_seeded():
    a = np.random.default_rng(1).random(25)
    first = evaluate_links.paired_bootstrap(a, indices_for(25))
    assert first == evaluate_links.paired_bootstrap(a, indices_for(25))
    assert first[0] < a.mean() < first[1]


# -- the instrument check ------------------------------------------------------ #

def test_a_work_directory_listing_other_instruments_is_refused(tmp_path):
    other = [["leyes", "a"], ["leyes", "zzz"], ["reglamentos", "1"], ["reglamentos", "2"]]
    write_work_dir(tmp_path / "w", np.eye(4), instruments=other)
    with pytest.raises(SystemExit, match="instrument 1"):
        evaluate_links.load_matrix("M", tmp_path / "w", gold_record())


def test_a_work_directory_with_another_instrument_count_is_refused(tmp_path):
    write_work_dir(tmp_path / "w", np.eye(3), instruments=INSTRUMENTS[:3])
    with pytest.raises(SystemExit, match="3 instruments"):
        evaluate_links.load_matrix("M", tmp_path / "w", gold_record())


def test_a_missing_matrix_is_refused_naming_the_path_and_nothing_is_built(tmp_path):
    write_work_dir(tmp_path / "w", None)
    with pytest.raises(SystemExit, match="matrix.npy"):
        evaluate_links.load_matrix("M", tmp_path / "w", gold_record())
    assert not (tmp_path / "w" / "instrument-matrix").exists()


def test_a_matrix_of_the_wrong_shape_is_refused(tmp_path):
    write_work_dir(tmp_path / "w", np.eye(3))
    with pytest.raises(SystemExit, match="shape"):
        evaluate_links.load_matrix("M", tmp_path / "w", gold_record())


def test_a_missing_gold_is_refused(tmp_path):
    with pytest.raises(SystemExit, match="gold.json"):
        evaluate_links.load_gold(tmp_path / "gold.json")


# -- end to end ---------------------------------------------------------------- #

GOOD = [[0, 0, 0, 0], [0, 0, 0, 0], [3, 1, 0, 0], [1, 3, 0, 0]]     # both link to their laws
BAD = [[0, 0, 0, 0], [0, 0, 0, 0], [0, 1, 3, 0], [0, 0, 2, 1]]      # neither does


def test_the_gold_sets_are_a_alone_and_the_union(tmp_path):
    gold = gold_record()
    assert evaluate_links.gold_sets(gold, "A") == {2: [0]}
    assert evaluate_links.gold_sets(gold, "A+B") == {2: [0], 3: [0, 1]}


def test_main_writes_the_json_and_the_markdown(tmp_path, capsys):
    gold_path = tmp_path / "gold-links" / "gold.json"
    gold_path.parent.mkdir()
    gold_path.write_text(json.dumps(gold_record()), encoding="utf-8")
    write_work_dir(tmp_path / "good", GOOD)
    write_work_dir(tmp_path / "bad", BAD)
    assert evaluate_links.main([
        "--gold", str(gold_path), "--work-dir", f"good={tmp_path / 'good'}",
        "--work-dir", f"bad={tmp_path / 'bad'}", "--resamples", "500"]) == 0
    out = json.loads((gold_path.parent / "evaluation.json").read_text(encoding="utf-8"))
    assert (gold_path.parent / "evaluation.md").read_text(encoding="utf-8").startswith("# ")
    assert out["thresholds"]["seed"] == 0 and out["thresholds"]["p_threshold"] == 0.05
    both = out["signal_sets"]["A+B"]
    assert both["scored"] == 2 and both["models"]["good"]["closest"]["count"] == 2
    assert both["models"]["bad"]["closest"]["count"] == 0
    assert {p["metric"] for p in both["pairs"]} == {"closest", "top5", "mrr", "share"}
    assert set(out["signal_sets"]) == {"A", "A+B"}
    capsys.readouterr()
    assert evaluate_links.main(["--gold", str(gold_path), "--report"]) == 0
    assert "closest is a gold law" in capsys.readouterr().out


def test_signals_narrows_the_sets(tmp_path):
    gold_path = tmp_path / "gold.json"
    gold_path.write_text(json.dumps(gold_record()), encoding="utf-8")
    write_work_dir(tmp_path / "x", GOOD)
    write_work_dir(tmp_path / "y", BAD)
    evaluate_links.main(["--gold", str(gold_path), "--work-dir", f"x={tmp_path / 'x'}",
                         "--work-dir", f"y={tmp_path / 'y'}", "--signals", "A",
                         "--resamples", "100"])
    out = json.loads((tmp_path / "evaluation.json").read_text(encoding="utf-8"))
    assert list(out["signal_sets"]) == ["A"]


def test_work_dir_needs_label_and_two_models(tmp_path):
    with pytest.raises(SystemExit, match="LABEL=PATH"):
        evaluate_links.parse_work_dirs(["emb-run-atlas"])
    with pytest.raises(SystemExit, match="at least two"):
        evaluate_links.parse_work_dirs(["a=emb-run-atlas"])
    assert list(evaluate_links.parse_work_dirs(None)) == ["0.6B", "4B", "BM25"]

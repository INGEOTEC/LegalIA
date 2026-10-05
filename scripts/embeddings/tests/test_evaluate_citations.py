"""The row-level evaluation against explicit citations (issue #273).

    uv run --group viz pytest scripts/embeddings/tests -q

The toy corpus of `test_instrument_matrix.py` (fixtures imported, not copied): its
dense work directory and its BM25 sibling are re-scored row by row, and every
answer is checked against an independent oracle written here from the toy
vectors and unit table -- including the matrix's own mask.
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import evaluate_citations  # noqa: E402
import evaluate_links  # noqa: E402
import instrument_matrix  # noqa: E402
import scoring  # noqa: E402
from test_instrument_matrix import (COLLECTIONS, UNITS, VECTORS, cache, computed,  # noqa: E402,F401
                                    index_of, prepared)
from test_prepare_bm25_input import bm25_dir  # noqa: E402,F401


def quiet(*args):
    pass


# -- the rank, by hand ------------------------------------------------------------ #

def test_competition_rank_counts_the_scores_strictly_above():
    scores = np.array([0.9, 0.5, 0.5, 0.1, -np.inf])
    assert evaluate_citations.competition_rank(scores, 0.5, tolerance=1e-6, relative=False) == 2
    assert evaluate_citations.competition_rank(scores, 0.9, tolerance=1e-6, relative=False) == 1
    assert evaluate_citations.competition_rank(scores, 0.1, tolerance=1e-6, relative=False) == 4


def test_a_tie_within_the_tolerance_is_not_above():
    scores = np.array([0.5 + 5e-7, 0.5])
    assert evaluate_citations.competition_rank(scores, 0.5, tolerance=1e-6, relative=False) == 1
    assert evaluate_citations.competition_rank(scores, 0.5, tolerance=1e-8, relative=False) == 2
    lexical = np.array([10.0 * (1 + 5e-7), 4.0])
    assert evaluate_citations.competition_rank(lexical, 10.0, tolerance=1e-6, relative=True) == 1


def test_a_law_with_no_score_has_no_rank():
    scores = np.array([2.0, 1.0])
    assert evaluate_citations.competition_rank(scores, -np.inf, tolerance=1e-6,
                                               relative=False) is None
    assert evaluate_citations.competition_rank(scores, 0.0, tolerance=1e-6, relative=True) is None
    # A dense cosine of 0 is a real score.
    assert evaluate_citations.competition_rank(scores, 0.0, tolerance=1e-6, relative=False) == 3


def test_the_instrument_layout_reduces_by_the_best_owned_text():
    owners = [[2], [0, 2], [], [0]]                 # vector rows 0..3
    pair_rows, starts, instruments = evaluate_citations.instrument_layout(owners)
    scores = np.array([[5.0, 3.0, 99.0, 1.0]])
    reduced = np.maximum.reduceat(scores[:, pair_rows], starts, axis=1)
    assert instruments.tolist() == [0, 2]
    assert reduced.tolist() == [[3.0, 5.0]]          # row 2 is owned by nobody: never read


# -- the metrics ------------------------------------------------------------------ #

def test_metric_values_on_a_hand_built_rank_table():
    ranks = [1, 3, 5, 10, 11, None]
    values = evaluate_citations.metric_values(ranks, cap=100)
    assert values["recall1"].tolist() == [1, 0, 0, 0, 0, 0]
    assert values["recall5"].tolist() == [1, 1, 1, 0, 0, 0]
    assert values["recall10"].tolist() == [1, 1, 1, 1, 0, 0]
    assert values["mrr"].tolist() == pytest.approx([1, 1 / 3, 1 / 5, 1 / 10, 1 / 11, 0])
    assert values["median_rank"].tolist() == [1, 3, 5, 10, 11, 100]
    summary = evaluate_citations.summarise(values)
    assert summary["recall5"] == {"count": 3, "mean": 0.5}
    assert summary["median_rank"] == {"median": 7.5}
    assert summary["mrr"]["mean"] == pytest.approx(sum([1, 1 / 3, 1 / 5, 1 / 10, 1 / 11]) / 6)


def test_a_model_that_ranks_everything_first_beats_one_that_ranks_nothing():
    n = 40
    good = evaluate_citations.metric_values([1] * n, cap=100)
    bad = evaluate_citations.metric_values([None] * n, cap=100)
    pairs = evaluate_citations.compare_models({"good": good, "bad": bad}, seed=0, resamples=500)
    verdicts = {p["metric"]: p["verdict"] for p in pairs}
    assert set(verdicts) == {"recall1", "recall5", "recall10", "mrr", "median_rank"}
    assert set(verdicts.values()) == {"good better"}


def test_identical_rank_tables_tie_on_every_metric():
    values = evaluate_citations.metric_values([1, 4, 20, None, 2, 9, 1, 30], cap=100)
    pairs = evaluate_citations.compare_models({"x": values, "y": dict(values)}, seed=0,
                                              resamples=500)
    assert {p["verdict"] for p in pairs} == {"tie"}


def test_the_median_rank_is_better_when_lower():
    first = np.full(30, 2.0)
    second = np.full(30, 50.0)
    indices = np.random.default_rng(0).integers(0, 30, size=(500, 30))
    result = evaluate_citations.median_compare(first, second, indices, names=("a", "b"))
    assert result["verdict"] == "a better" and result["difference"] == -48.0


# -- the mask and the join, against an oracle -------------------------------------- #

def oracle(prepared, source: str, text: str, law: str):
    """The cited instrument's competition rank by independent code: cosine over the
    toy vectors, the source's exclusively owned texts and the texts only headings
    own removed, one score per other instrument (its best owned text)."""
    index = index_of(prepared)
    searched = {}                                            # text -> owning instruments
    for coleccion in COLLECTIONS:
        for clave, _, unit_type, _, sha, _ in UNITS[coleccion]:
            if unit_type != "heading":
                searched.setdefault((coleccion, sha), set()).add(clave)
    own = {key for key, claves in searched.items() if claves == {source}}
    vectors = {k: np.array(v) / np.linalg.norm(v) for k, v in VECTORS.items()}
    # `source`'s row for `text` is the vector of its own collection.
    query = vectors[text]
    best = {}
    for (coleccion, sha), claves in searched.items():
        if (coleccion, sha) in own:
            continue
        for clave in claves - {source}:
            best[clave] = max(best.get(clave, -2.0), float(query @ vectors[sha]))
    mine = best[law]
    return 1 + sum(1 for other, score in best.items() if score > mine + 1e-6), best, index


def row_frame(prepared, cache):
    units = instrument_matrix.unit_rows(prepared, collections=COLLECTIONS, cache_dir=cache,
                                        log=quiet)
    return units


def test_rank_rows_reproduces_the_matrix_and_an_independent_oracle(prepared, cache):
    """Every searched `(instrument, text)` against every other instrument: the
    rank equals the oracle's, and the best foreign score equals what the matrix
    recorded in `nearest.parquet` (`verify_against_nearest` raises otherwise)."""
    answers = computed(prepared, cache)
    units = row_frame(prepared, cache)
    searched = units[instrument_matrix.searched_mask(units)]
    index = index_of(prepared)
    by_index = {i: clave for clave, i in index.items()}
    vector_ids = pq.read_table(prepared / "vector_ids.parquet").to_pandas()
    sha_of = {(c, r): s for c, r, s in zip(vector_ids["coleccion"], vector_ids["row"],
                                           vector_ids["text_sha1"])}
    pairs = searched[["i", "coleccion", "row"]].drop_duplicates()
    frames = []
    for _, pair in pairs.iterrows():
        for law in index.values():
            if law != pair["i"]:
                frames.append({"i": int(pair["i"]), "row": int(pair["row"]),
                               "law": int(law), "coleccion": pair["coleccion"]})
    rows = pd.DataFrame(frames)
    scorer = scoring.scorer_for(prepared)
    ranked = evaluate_citations.rank_rows(scorer, units, rows, block_rows=2, log=quiet)
    nearest = answers["nearest"].drop_duplicates(["i", "row"])
    # one `best` per (i, row): the matrix's own answer.
    worst = evaluate_citations.verify_against_nearest(rows, ranked, nearest, scorer, 1e-6)
    assert worst < 1e-6
    for n, record in rows.iterrows():
        source = by_index[int(record["i"])]
        text = sha_of[(record["coleccion"], int(record["row"]))]
        expected, _, _ = oracle(prepared, source, text, by_index[int(record["law"])])
        assert ranked["rank"][n] == expected, (source, text, by_index[int(record["law"])])


def test_a_text_shared_with_the_cited_law_ranks_it_first(prepared, cache):
    """`ts` is owned by leyes `a` and `b`: for source `a` it is not masked (two owners),
    so law `b` owns a word-for-word copy of the row and ranks first."""
    units = row_frame(prepared, cache)
    index = index_of(prepared)
    vector_ids = pq.read_table(prepared / "vector_ids.parquet").to_pandas()
    row = int(vector_ids[(vector_ids["text_sha1"] == "ts")
                         & (vector_ids["coleccion"] == "leyes")]["row"].iloc[0])
    rows = pd.DataFrame([{"i": index["a"], "row": row, "law": index["b"]}])
    ranked = evaluate_citations.rank_rows(scoring.scorer_for(prepared), units, rows, log=quiet)
    assert ranked["rank"] == [1] and ranked["law_owns_text"] == [True]


def test_the_exclusive_text_of_the_source_is_masked(prepared, cache):
    """Source `c`'s only own text is `t3`: unmasked, `c` would be its own best match,
    and a perfect score would not be foreign. The best foreign score must be less
    than 1 for the dense scorer."""
    units = row_frame(prepared, cache)
    index = index_of(prepared)
    vector_ids = pq.read_table(prepared / "vector_ids.parquet").to_pandas()
    row = int(vector_ids[vector_ids["text_sha1"] == "t3"]["row"].iloc[0])
    rows = pd.DataFrame([{"i": index["c"], "row": row, "law": index["a"]}])
    ranked = evaluate_citations.rank_rows(scoring.scorer_for(prepared), units, rows, log=quiet)
    assert ranked["best"][0] < 1.0 - 1e-6


def test_a_disagreement_with_nearest_is_a_system_exit(prepared, cache):
    answers = computed(prepared, cache)
    nearest = answers["nearest"].drop_duplicates(["i", "row"]).copy()
    nearest["similarity"] = nearest["similarity"] + 0.01
    units = row_frame(prepared, cache)
    scorer = scoring.scorer_for(prepared)
    first = nearest.iloc[0]
    rows = pd.DataFrame([{"i": int(first["i"]), "row": int(first["row"]),
                          "law": next(j for j in index_of(prepared).values()
                                      if j != int(first["i"]))}])
    ranked = evaluate_citations.rank_rows(scorer, units, rows, log=quiet)
    with pytest.raises(SystemExit, match="does not reproduce the matrix"):
        evaluate_citations.verify_against_nearest(rows, ranked, nearest, scorer, 1e-6)


# -- end to end, both scorers ------------------------------------------------------ #

def write_gold(prepared, tmp_path, cache):
    """A `gold.json` (only its instrument list is read) and a `citations.parquet`
    over the toy corpus: three single-law rows, one row citing two laws."""
    index = index_of(prepared)
    instruments = pq.read_table(prepared / "instruments.parquet").to_pandas().sort_values("i")
    gold_dir = tmp_path / "gold-links"
    gold_dir.mkdir()
    gold = {"instruments": [[c, str(k)] for c, k in zip(instruments["coleccion"],
                                                        instruments["clave"])],
            "entries": [], "summary": {}, "provenance": {},
            "law_names": {str(index[k]): f"Ley {k.upper()}" for k in ("a", "b", "c")}}
    (gold_dir / "gold.json").write_text(json.dumps(gold), encoding="utf-8")
    rows = [("900", "t4", "a", True), ("901", "t5", "c", True), ("902", "t6", "b", True),
            ("900", "t1", "a", False), ("900", "t1", "b", False)]
    pq.write_table(pa.table({
        "i": pa.array([index[k] for k, _, _, _ in rows], type=pa.int32()),
        "coleccion": ["lineamientos"] * len(rows),
        "clave": [k for k, _, _, _ in rows],
        "text_sha1": [t for _, t, _, _ in rows],
        "eId": ["art_1"] * len(rows), "unit_type": ["article"] * len(rows),
        "article": ["1"] * len(rows),
        "law": pa.array([index[law] for _, _, law, _ in rows], type=pa.int32()),
        "span": ["articulo 1 de la ley"] * len(rows), "hits": pa.array([1] * len(rows), type=pa.int32()),
        "single_law": [s for _, _, _, s in rows],
    }), gold_dir / "citations.parquet")
    return gold, gold_dir


def test_evaluate_scores_both_scorers_on_the_same_rows(prepared, bm25_dir, cache, tmp_path):
    computed(prepared, cache)
    computed(bm25_dir, cache)
    gold, gold_dir = write_gold(prepared, tmp_path, cache)
    evaluation = evaluate_citations.evaluate(
        gold, gold_dir, {"dense": prepared, "bm25": bm25_dir}, cache_dir=cache,
        collections=COLLECTIONS, resamples=200, log=quiet)
    cites = evaluation["citations"]
    assert cites["citing_rows"] == 4 and cites["single_law_rows"] == 3
    assert cites["multi_law_rows_not_scored"] == 1
    assert cites["removed_by_the_matrix_rules"] == {"dense": {}, "bm25": {}}
    assert cites["scored_rows"] == 3
    assert evaluation["models"]["dense"]["method"] == "dense"
    assert evaluation["models"]["bm25"]["method"] == "bm25"
    assert evaluation["models"]["dense"]["max_difference_from_nearest"] < 1e-6
    assert set(evaluation["metrics"]) == {"dense", "bm25"}
    assert {p["metric"] for p in evaluation["pairs"]} == {m for m, _, _ in evaluate_citations.METRICS}
    assert set(evaluation["per_collection"]) == {"lineamientos"}
    assert "Without the Constitution" in evaluate_citations.render_markdown(evaluation)


def test_main_writes_and_reports(prepared, bm25_dir, cache, tmp_path, capsys, monkeypatch):
    computed(prepared, cache)
    computed(bm25_dir, cache)
    gold, gold_dir = write_gold(prepared, tmp_path, cache)
    real = evaluate_citations.evaluate
    monkeypatch.setattr(evaluate_citations, "evaluate",
                        lambda *a, **k: real(*a, collections=COLLECTIONS, **k))
    assert evaluate_citations.main([
        "--gold-dir", str(gold_dir), "--work-dir", f"dense={prepared}",
        "--work-dir", f"bm25={bm25_dir}", "--cache-dir", str(cache), "--resamples", "100"]) == 0
    assert (gold_dir / "evaluation-rows.json").is_file()
    assert (gold_dir / "evaluation-rows.md").read_text(encoding="utf-8").startswith("# ")
    capsys.readouterr()
    assert evaluate_citations.main(["--gold-dir", str(gold_dir), "--report"]) == 0
    assert "recall@5" in capsys.readouterr().out


# -- refusals ---------------------------------------------------------------------- #

def test_a_missing_citations_table_is_refused_naming_it(tmp_path):
    with pytest.raises(SystemExit, match="citations.parquet"):
        evaluate_citations.load_citations(tmp_path, {})


def test_a_work_directory_without_nearest_is_refused_naming_the_path(prepared):
    with pytest.raises(SystemExit, match="nearest.parquet"):
        evaluate_citations.load_nearest("M", prepared)


def test_a_work_directory_of_other_instruments_is_refused(prepared, cache, tmp_path):
    gold, gold_dir = write_gold(prepared, tmp_path, cache)
    gold["instruments"][0] = ["leyes", "not-an-instrument"]
    with pytest.raises(SystemExit, match="instrument 0"):
        evaluate_citations.evaluate(gold, gold_dir, {"x": prepared, "y": prepared},
                                    cache_dir=cache, collections=COLLECTIONS, log=quiet)


def test_report_without_an_evaluation_is_refused(tmp_path):
    with pytest.raises(SystemExit, match="evaluation-rows.json"):
        evaluate_citations.main(["--gold-dir", str(tmp_path), "--report"])

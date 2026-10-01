"""The BM25 tuning toward the 4B (issue #267's review fix).

    uv run --group viz pytest scripts/embeddings/tests -q

The toy corpus of `test_instrument_matrix.py` (its fixtures imported, not
copied): a dense work directory plays the 4B, its BM25 sibling the candidate.
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import instrument_matrix  # noqa: E402
import scoring  # noqa: E402
import tune_bm25  # noqa: E402
from test_instrument_matrix import COLLECTIONS, cache, computed, prepared  # noqa: E402,F401
from test_prepare_bm25_input import bm25_dir  # noqa: E402,F401


def quiet(*args):
    pass


# -- the grid ---------------------------------------------------------------- #

def test_the_grid_is_the_issues_search_space():
    configs = tune_bm25.grid()
    assert len(configs) == 6 * 5 * 5 * 2
    assert len({c.slug for c in configs}) == len(configs)
    assert {c.k1 for c in configs} == {0.5, 0.9, 1.2, 1.5, 2.0, 3.0}
    assert {c.b for c in configs} == {0.0, 0.25, 0.5, 0.75, 1.0}
    assert {c.method for c in configs} == {"lucene", "robertson", "atire", "bm25l", "bm25+"}
    assert {c.weighting for c in configs} == {"binary", "tf-saturated"}
    assert tune_bm25.default_config() in configs


def test_dry_run_prints_the_grid_and_computes_nothing(tmp_path, capsys, monkeypatch):
    def refused(*args, **kwargs):
        raise AssertionError("--dry-run must not load or compute anything")

    monkeypatch.setattr(tune_bm25, "Context", refused)
    monkeypatch.setattr(tune_bm25, "run_grid", refused)
    work_dir = tmp_path / "bm25"
    assert tune_bm25.main(["--dry-run", "--work-dir", str(work_dir)]) == 0
    out = capsys.readouterr().out
    assert "300 configurations" in out
    assert "bm25+" in out and "tf-saturated" in out
    assert "sbatch" in out and "--run-grid" in out
    assert not work_dir.exists()


# -- the metrics, by hand ------------------------------------------------------- #

def test_hit_jaccard_over_a_hand_built_pair_of_tables():
    ref = [[1], [2, 3], [4], [5], [6]]
    cand = [[1, 9], [3], [7], [], [6]]
    counted = [True, True, True, True, False]       # the last row is not measured
    result = tune_bm25.agreement(ref, cand, counted)
    # Row 0 hits (J 1/2), row 1 hits (J 1/2), row 2 misses (J 0), row 3 has no
    # candidate at all: a miss (J 0).
    assert result["rows"] == 4 and result["hits"] == 2
    assert result["hit_rate"] == pytest.approx(0.5)
    assert result["jaccard"] == pytest.approx((0.5 + 0.5 + 0 + 0) / 4)


def test_an_empty_reference_measures_nothing():
    assert tune_bm25.agreement([], [], [])["hit_rate"] == 0.0
    assert tune_bm25.agreement([[1]], [[1]], [False])["rows"] == 0


def test_closest_instrument_and_top5_overlap_over_hand_built_matrices():
    ref = np.zeros((3, 3), dtype=np.float32)
    ref[0, 1], ref[0, 2] = 5, 2
    ref[1, 0] = 4
    ref[2, 0], ref[2, 1] = 3, 1
    cand = np.zeros((3, 3), dtype=np.float32)
    cand[0, 2], cand[0, 1] = 6, 1        # closest differs (2 against 1); top-5 same set
    cand[1, 0] = 9                       # same closest, same set
    cand[2, 1] = 1                       # closest differs; shares 1 of 2
    result = tune_bm25.instrument_agreement(ref, cand)
    assert result["instruments"] == 3
    assert result["closest_agree"] == 1
    assert result["top5_overlap"] == pytest.approx((2 + 1 + 1) / 3)


def test_the_per_row_counts():
    counts = tune_bm25.answer_counts(m=[0, 1, 2, 1, 3], n_winners=[0, 1, 2, 3, 1],
                                     identical=[False, False, True, True, False])
    assert counts["tie_rows"] == 2
    assert counts["no_match_rows"] == 1
    # identical and shared by several instruments (m > 1): row 2 only.
    assert counts["identical_shared_dropped"] == 1
    assert counts["mean_m"] == pytest.approx((1 + 2 + 1 + 3) / 4)


# -- the weights ----------------------------------------------------------------- #

def test_the_recomputed_default_weights_are_the_saved_indexs(bm25_dir):
    scorer = scoring.scorer_for(bm25_dir)
    counts = scoring.Bm25Counts.load(bm25_dir)
    weights, _ = counts.weights(tune_bm25.default_config())
    assert weights.shape == scorer.W.shape
    assert (weights != scorer.W).nnz == 0


@pytest.mark.parametrize("method", scoring.BM25_METHODS)
def test_every_method_is_the_librarys_own_index(tmp_path, method):
    """Weights recomputed from token counts equal what bm25s saves for the same
    parameters -- for every method, not only the default."""
    import bm25s
    import shutil

    from test_scoring import bm25_work_dir

    texts = ["aa bb aa", "bb cc", "aa", "cc cc dd aa bb", "ee"]
    work_dir, _, _ = bm25_work_dir(tmp_path, texts)
    # A fresh tokenisation: indexing adds an empty token to the vocabulary it is given.
    tokenized = bm25s.tokenize(texts, lower=True, stopwords=None, stemmer=None,
                               return_ids=True, show_progress=False)
    config = scoring.Bm25Config(method=method, k1=1.2, b=0.5)
    retriever = bm25s.BM25(method=config.method, k1=config.k1, b=config.b, delta=config.delta)
    retriever.index(tokenized, show_progress=False)
    shutil.rmtree(work_dir / scoring.INDEX_DIR)
    retriever.save(work_dir / scoring.INDEX_DIR, show_progress=False)
    counts = scoring.Bm25Counts.load(work_dir)
    weights, _ = counts.weights(config)
    saved = scoring.scorer_for(work_dir)
    assert (weights != saved.W).nnz == 0
    assert (scoring.Bm25Scorer.for_config(counts, config).W != saved.W).nnz == 0


def test_tf_saturated_is_binary_when_every_query_term_occurs_once(tmp_path):
    from test_scoring import bm25_work_dir

    work_dir, _, _ = bm25_work_dir(tmp_path, ["aa bb cc", "bb dd", "cc ee ff"])
    counts = scoring.Bm25Counts.load(work_dir)
    binary = counts.queries(scoring.Bm25Config(weighting="binary"))
    saturated = counts.queries(scoring.Bm25Config(weighting="tf-saturated"))
    np.testing.assert_allclose(saturated.toarray(), binary.toarray(), rtol=1e-6)


def test_tf_saturated_weighs_a_repeated_term_by_its_saturated_count(tmp_path):
    from test_scoring import bm25_work_dir

    work_dir, _, tokenized = bm25_work_dir(tmp_path, ["aa aa aa bb", "bb"])
    counts = scoring.Bm25Counts.load(work_dir)
    k1 = 1.5
    queries = counts.queries(scoring.Bm25Config(k1=k1, weighting="tf-saturated")).toarray()
    aa, bb = tokenized.vocab["aa"], tokenized.vocab["bb"]
    assert queries[0, aa] == pytest.approx(3 * (k1 + 1) / (3 + k1))
    assert queries[0, bb] == pytest.approx(1.0)
    binary = counts.queries(scoring.Bm25Config(weighting="binary")).toarray()
    assert binary[0, aa] == 1.0


# -- the sample and the sweeps -------------------------------------------------------- #

@pytest.fixture
def tuning(tmp_path, prepared, cache, bm25_dir):
    """A dense "4B" with its matrix, and the BM25 directory over the same corpus."""
    computed(prepared, cache)
    ctx = tune_bm25.Context(bm25_dir, prepared, cache_dir=cache, collections=COLLECTIONS,
                            log=quiet)
    return ctx


def test_the_context_lines_up_with_the_reference(tuning):
    assert len(tuning.units) == len(tuning.ref)
    assert tuning.ref_matrix.shape[0] == tuning.units["i"].max() + 1


def test_the_stratified_sample_takes_a_share_of_every_collection(tuning):
    positions = tune_bm25.stratified_sample(tuning.units, 0.5, 0)
    assert list(positions) == sorted(set(positions))
    chosen = tuning.units.loc[positions]
    for _, group in tuning.units.groupby("coleccion"):
        assert 1 <= chosen["coleccion"].eq(group["coleccion"].iloc[0]).sum() <= len(group)
    again = tune_bm25.stratified_sample(tuning.units, 0.5, 0)
    assert list(again) == list(positions)


def test_a_sample_of_everything_gives_the_default_runs_answers(tuning, bm25_dir):
    """The sample sweep is the full sweep's own code: over every row, with the
    default configuration, it gives the table a normal BM25 run writes."""
    instrument_matrix.build_matrix(bm25_dir, collections=COLLECTIONS, cache_dir=tuning_cache(tuning),
                                   log=quiet)
    out = instrument_matrix.output_dir(bm25_dir)
    full = pd.read_parquet(out / "nearest.parquet")
    result = tune_bm25.evaluate_sample(tuning, tune_bm25.default_config(),
                                       np.arange(len(tuning.units)))
    expected = tune_bm25.agreement(list(tuning.ref["targets"]), list(full["targets"]),
                                   tuning.ref_counted)
    assert result["hit_rate"] == pytest.approx(expected["hit_rate"])
    assert result["jaccard"] == pytest.approx(expected["jaccard"])
    assert result["no_match_rows"] == int((full["m"] == 0).sum())
    assert result["tie_rows"] == int((full["n_winners"] > 1).sum())


def tuning_cache(ctx):
    return ctx.cache_dir


def test_a_partial_sample_still_masks_the_instruments_own_texts(tuning):
    """An instrument's own text outside the sample must stay masked, or it
    wins against itself and the row reads as "no match"."""
    everything = np.arange(len(tuning.units))
    full = tune_bm25.evaluate_sample(tuning, tune_bm25.default_config(), everything)
    first = everything[:1]
    part = tune_bm25.evaluate_sample(tuning, tune_bm25.default_config(), first)
    assert part["no_match_rows"] <= full["no_match_rows"]
    # One row's winners are the full run's for that row.
    row = tuning.units.loc[first[0]]
    scorer = scoring.Bm25Scorer.for_config(tuning.counts, tune_bm25.default_config())
    answers = instrument_matrix.sweep_answers(
        scorer, tuning.owners, tuning.unowned, tuning.units.loc[[0], ["i", "row"]],
        own_rows=tuning.own_rows, log=quiet)
    whole = instrument_matrix.sweep_answers(
        scorer, tuning.owners, tuning.unowned, tuning.units[["i", "row"]], log=quiet)
    match = whole[(whole["i"] == row["i"]) & (whole["row"] == row["row"])].iloc[0]
    assert list(answers["targets"].iloc[0]) == list(match["targets"])


def test_the_grid_resumes_and_collects(tuning, bm25_dir, prepared, monkeypatch):
    monkeypatch.setattr(tune_bm25, "grid", lambda: SMALL)
    monkeypatch.setattr(tune_bm25, "Context", lambda *a, **k: tuning)
    done = tune_bm25.run_grid(bm25_dir, prepared, fraction=0.5, seed=0, shard=(0, 1),
                              processes=2, log=quiet)
    assert done == len(SMALL)
    assert tune_bm25.run_grid(bm25_dir, prepared, fraction=0.5, seed=0, shard=(0, 1),
                              processes=2, log=quiet) == 0
    sample = pd.read_parquet(bm25_dir / "tune" / "sample.parquet")
    assert len(sample) >= 1 and set(sample["seed"]) == {0}
    results = tune_bm25.collect_grid(bm25_dir)
    assert [r["slug"] for r in results] == [c.slug for c in SMALL]


SMALL = tune_bm25.grid()[:4]


def test_collect_grid_names_what_is_missing(tmp_path):
    with pytest.raises(SystemExit, match="300 of 300"):
        tune_bm25.collect_grid(tmp_path)


# -- ranking and the report --------------------------------------------------------------- #

def fake(slug_config, hit, jaccard=0.0):
    return {**slug_config.record(), "slug": slug_config.slug, "hit_rate": hit,
            "jaccard": jaccard}


def test_confirm_configs_are_the_top_three_and_the_defaults():
    configs = tune_bm25.grid()
    results = [fake(c, 0.1 + 0.001 * n) for n, c in enumerate(configs)]
    chosen = tune_bm25.confirm_configs(results)
    assert len(chosen) == 4
    assert chosen[-1] == tune_bm25.default_config()
    assert [c.slug for c in chosen[:3]] == [configs[-1].slug, configs[-2].slug, configs[-3].slug]


def test_the_defaults_are_never_among_the_three_only_the_control():
    configs = tune_bm25.grid()
    results = [fake(c, 0.9 if c == tune_bm25.default_config() else 0.1) for c in configs]
    chosen = tune_bm25.confirm_configs(results)
    assert chosen.count(tune_bm25.default_config()) == 1


def test_results_are_written_and_reported_offline(tmp_path, capsys):
    configs = tune_bm25.grid()[:3]
    sample = [fake(c, 0.2 + n / 10) for n, c in enumerate(configs)]
    tune_bm25.write_results(tmp_path, sample, [], {"sample_rows": 10})
    assert (tmp_path / "tune" / "results.parquet").exists()
    assert tune_bm25.main(["--report", "--work-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "hit_rate" in out and configs[2].method in out
    data = json.loads((tmp_path / "tune" / "results.json").read_text(encoding="utf-8"))
    assert data["sample"][0]["slug"] == configs[2].slug


def test_report_without_results_names_the_file(tmp_path):
    with pytest.raises(SystemExit, match="results.json"):
        tune_bm25.report(tmp_path)

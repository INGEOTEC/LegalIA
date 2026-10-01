"""The text-to-text scorers of the Atlas comparison (issue #267).

    uv run --group viz pytest scripts/embeddings/tests -q

The dense scorer is checked against the product computed by hand over the toy
corpus of `test_instrument_matrix.py` (its fixtures imported, not copied); the
BM25 scorer against a three-document corpus whose Lucene scores are worked out
below with a pen, and against `bm25s.BM25.get_scores`, the library's own
numbers.
"""

import json
import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import scoring  # noqa: E402
from test_instrument_matrix import cache, prepared  # noqa: E402,F401

K1, B = 1.5, 0.75


def bm25_work_dir(tmp_path, texts, *, extra_rows=0):
    """A BM25 work directory over `texts`, one document per vector row (then
    `extra_rows` vector rows that are no document), written the way
    `prepare_bm25_input.py` writes it."""
    import bm25s

    work_dir = tmp_path / "bm25work"
    work_dir.mkdir()
    tokenized = bm25s.tokenize(texts, lower=True, stopwords=None, stemmer=None,
                               return_ids=True, show_progress=False)
    retriever = bm25s.BM25(method="lucene", k1=K1, b=B)
    retriever.index(tokenized, show_progress=False)
    retriever.save(work_dir / scoring.INDEX_DIR, show_progress=False)
    n = len(texts) + extra_rows
    pq.write_table(pa.table({
        "row": pa.array(range(len(texts)), type=pa.int32()),
        "token_ids": pa.array([list(ids) for ids in tokenized.ids], type=pa.list_(pa.int32())),
    }), work_dir / scoring.TOKENS)
    pq.write_table(pa.table({
        "row": pa.array(range(n), type=pa.int32()),
        "coleccion": pa.array(["leyes"] * n, type=pa.string()),
        "text_sha1": pa.array([f"sha{i}" for i in range(n)], type=pa.string()),
    }), work_dir / "vector_ids.parquet")
    (work_dir / "input.json").write_text(json.dumps({"method": "bm25"}), encoding="utf-8")
    return work_dir, retriever, tokenized


# -- the dense scorer ------------------------------------------------------- #

def test_the_dense_scorer_is_the_normalised_product(prepared):
    vectors = np.load(prepared / "vectors.npy").astype(np.float32)
    unit = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
    scorer = scoring.scorer_for(prepared)
    assert isinstance(scorer, scoring.DenseScorer)
    assert scorer.n_rows == len(vectors)
    rows = np.array([2, 0, 3])
    block = scorer.scores(rows)
    assert block.dtype == np.float32 and block.shape == (3, len(vectors))
    np.testing.assert_allclose(block, unit[rows] @ unit.T, atol=1e-6)
    assert block[0, 2] == pytest.approx(1.0, abs=1e-6)
    # A subset against explicit candidates is the same product's columns.
    np.testing.assert_allclose(scorer.scores_against(rows, np.array([1, 4])),
                               block[:, [1, 4]], atol=1e-6)
    assert scorer.method == "dense" and not scorer.relative


def test_the_lazy_dense_scorer_agrees_with_the_full_one(prepared):
    full = scoring.scorer_for(prepared)
    lazy = scoring.scorer_for(prepared, full=False)
    rows, candidates = np.array([0, 3, 4]), np.array([1, 2, 5])
    np.testing.assert_allclose(lazy.scores_against(rows, candidates),
                               full.scores_against(rows, candidates), atol=1e-6)
    with pytest.raises(RuntimeError):
        lazy.scores(rows)


def test_dense_identity_is_a_cosine_of_one_within_tolerance(prepared):
    scorer = scoring.scorer_for(prepared)
    assert scorer.identical(0, 1.0, np.array([1]), 1e-6)
    assert scorer.identical(0, 1.0 - 5e-7, np.array([1]), 1e-6)
    assert not scorer.identical(0, 0.999, np.array([1]), 1e-6)


# -- BM25, by hand ----------------------------------------------------------- #

#: Three documents, N = 3, average length 2. Lucene BM25 (bm25s):
#:   idf(df)  = ln(1 + (N - df + 0.5) / (df + 0.5))
#:   tfc      = tf / (tf + k1 * (1 - b + b * dl / avgdl)),  k1 = 1.5, b = 0.75
#:   idf: aa, bb (df 2) = ln 1.6 = 0.47000; cc (df 1) = ln(8/3) = 0.98083
#:   (two-letter words: the tokeniser drops one-character ones)
#:   d0 "aa bb aa" (dl 3, norm 2.0625): aa 0.47000 * 2/4.0625 = 0.23139,
#:                                   bb 0.47000 * 1/3.0625 = 0.15347
#:   d1 "bb cc"   (dl 2, norm 1.5):    b 0.47000 * 1/2.5 = 0.18800,
#:                                   c 0.98083 * 1/2.5 = 0.39233
#:   d2 "aa"     (dl 1, norm 0.9375): a 0.47000 * 1/1.9375 = 0.24258
TOY_TEXTS = ["aa bb aa", "bb cc", "aa"]
W_BY_HAND = np.array([[0.23139, 0.15347, 0.0],
                      [0.0, 0.18800, 0.39233],
                      [0.24258, 0.0, 0.0]])


def test_the_bm25_scorer_is_lucene_bm25_worked_out_by_hand(tmp_path):
    work_dir, _, tokenized = bm25_work_dir(tmp_path, TOY_TEXTS)
    scorer = scoring.scorer_for(work_dir)
    assert isinstance(scorer, scoring.Bm25Scorer)
    assert scorer.relative and scorer.n_rows == 3
    vocab = tokenized.vocab
    order = [vocab["aa"], vocab["bb"], vocab["cc"]]
    np.testing.assert_allclose(scorer.W.toarray()[:, order], W_BY_HAND, atol=1e-4)
    # Query d0 = {a, b} (binary: the second "a" does not count twice).
    block = scorer.scores(np.array([0, 1, 2]))
    assert block.dtype == np.float32 and block.shape == (3, 3)
    expected = np.array([[0.38486, 0.18800, 0.24258],    # d0 {a, b}
                         [0.15347, 0.58033, 0.0],        # d1 {b, c}
                         [0.23139, 0.0, 0.24258]])       # d2 {a}
    np.testing.assert_allclose(block, expected, atol=1e-4)


def test_the_bm25_scorer_matches_the_librarys_own_scores(tmp_path):
    texts = ["el estado de mexico", "de la federacion y de los estados", "el el el estado",
             "ley de la federacion", "mexico mexico"]
    work_dir, retriever, tokenized = bm25_work_dir(tmp_path, texts)
    scorer = scoring.scorer_for(work_dir)
    block = scorer.scores(np.arange(len(texts)))
    for row, ids in enumerate(tokenized.ids):
        distinct = sorted(set(ids))
        np.testing.assert_allclose(block[row], retriever.get_scores(distinct), rtol=1e-5)


def test_scores_against_are_the_columns_of_scores(tmp_path):
    texts = ["el estado de mexico", "de la federacion", "el estado", "ley de la federacion"]
    work_dir, _, _ = bm25_work_dir(tmp_path, texts)
    scorer = scoring.scorer_for(work_dir)
    rows, candidates = np.array([0, 3]), np.array([1, 2, 3])
    np.testing.assert_array_equal(scorer.scores_against(rows, candidates),
                                  scorer.scores(rows)[:, candidates])


def test_the_same_token_multiset_ties_exactly(tmp_path):
    """Case, punctuation and order do not change the tokens, so two such
    documents have identical columns of `W` and bit-identical scores."""
    texts = ["El Estado de México.", "méxico, de estado EL", "ley federal", "estado de"]
    work_dir, _, _ = bm25_work_dir(tmp_path, texts)
    scorer = scoring.scorer_for(work_dir)
    block = scorer.scores(np.array([3]))                 # "estado de"
    assert block[0, 0] == block[0, 1] > 0
    dense = scorer.W.toarray()
    np.testing.assert_array_equal(dense[0], dense[1])


def test_a_document_with_no_term_scores_zero_everywhere(tmp_path):
    work_dir, _, _ = bm25_work_dir(tmp_path, ["ley federal", "1 — 3", "ley estatal"])
    scorer = scoring.scorer_for(work_dir)
    assert (scorer.scores(np.array([1])) == 0).all()
    assert scorer.Q[1].nnz == 0
    # ... and it is nobody's match either: no document contains its terms.
    assert (scorer.scores(np.array([0, 2]))[:, 1] == 0).all()


def test_a_vector_row_that_is_no_document_is_an_empty_row(tmp_path):
    work_dir, _, _ = bm25_work_dir(tmp_path, ["ley federal", "ley estatal"], extra_rows=2)
    scorer = scoring.scorer_for(work_dir)
    assert scorer.n_rows == 4 and scorer.documents == 2
    block = scorer.scores(np.array([0, 1]))
    assert block.shape == (2, 4) and (block[:, 2:] == 0).all()


def test_bm25_identity_is_the_sources_text_sha1_in_any_collection(tmp_path):
    work_dir, _, _ = bm25_work_dir(tmp_path, ["ley federal", "ley federal", "ley estatal"])
    ids = pq.read_table(work_dir / "vector_ids.parquet").to_pandas()
    ids["text_sha1"] = ["same", "same", "other"]
    ids["coleccion"] = ["leyes", "lineamientos", "leyes"]
    pq.write_table(pa.Table.from_pandas(ids, preserve_index=False),
                   work_dir / "vector_ids.parquet")
    scorer = scoring.scorer_for(work_dir)
    assert scorer.identical(0, 0.3, np.array([1]), 1e-6)
    assert not scorer.identical(0, 0.3, np.array([2]), 1e-6)
    assert scorer.identical(0, 0.3, np.array([2, 1]), 1e-6)


# -- the tie rule ----------------------------------------------------------- #

def test_tie_mask_absolute_is_best_minus_tolerance():
    scores = np.array([[0.9, 0.8999999, 0.89, -np.inf]], dtype=np.float32)
    best = scores.max(axis=1)
    assert scoring.tie_mask(scores, best, 1e-6).tolist() == [[True, True, False, False]]


def test_tie_mask_relative_scales_with_the_best():
    scores = np.array([[100.0, 99.99999, 99.9, 0.0],
                       [0.5, 0.4999999, 0.49, 0.0]], dtype=np.float32)
    best = scores.max(axis=1)
    mask = scoring.tie_mask(scores, best, 1e-6, relative=True)
    assert mask.tolist() == [[True, True, False, False], [True, True, False, False]]
    # The same absolute slack on the 100-scale would be far too tight.
    assert scoring.tie_mask(scores[:1], best[:1], 1e-6, relative=False).tolist() \
        == [[True, False, False, False]]


def test_tie_mask_relative_has_no_winner_when_the_best_is_not_positive():
    scores = np.array([[0.0, 0.0, -np.inf]], dtype=np.float32)
    assert not scoring.tie_mask(scores, scores.max(axis=1), 1e-6, relative=True).any()
    assert scoring.tie_mask(scores, scores.max(axis=1), 1e-6, relative=False).tolist() \
        == [[True, True, False]]


def test_agrees_is_absolute_or_relative():
    assert scoring.agrees(1.0, 1.0 + 5e-7, 1e-6, relative=False)
    assert not scoring.agrees(100.0, 100.0 + 5e-5, 1e-6, relative=False)
    assert scoring.agrees(100.0, 100.0 + 5e-5, 1e-6, relative=True)
    assert not scoring.agrees(100.0, 100.01, 1e-6, relative=True)


# -- choosing a scorer ------------------------------------------------------ #

def test_a_work_dir_without_a_method_is_dense(prepared):
    record = json.loads((prepared / "input.json").read_text(encoding="utf-8"))
    assert "method" not in record
    assert scoring.method_of(prepared) == "dense"
    assert scoring.method_of(prepared / "nowhere") == "dense"
    assert isinstance(scoring.scorer_for(prepared), scoring.DenseScorer)


def test_an_unknown_method_is_refused(prepared):
    record = json.loads((prepared / "input.json").read_text(encoding="utf-8"))
    record["method"] = "tfidf"
    (prepared / "input.json").write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(SystemExit, match="tfidf"):
        scoring.scorer_for(prepared)


def test_a_missing_input_is_a_system_exit_naming_it(tmp_path, prepared):
    (prepared / "vectors.npy").unlink()
    with pytest.raises(SystemExit, match="vectors.npy"):
        scoring.scorer_for(prepared)
    work_dir, _, _ = bm25_work_dir(tmp_path, TOY_TEXTS)
    (work_dir / scoring.TOKENS).unlink()
    with pytest.raises(SystemExit, match="tokens.parquet"):
        scoring.scorer_for(work_dir)


def test_an_index_and_tokens_from_different_runs_are_refused(tmp_path):
    work_dir, _, _ = bm25_work_dir(tmp_path, TOY_TEXTS)
    tokens = pq.read_table(work_dir / scoring.TOKENS)
    pq.write_table(tokens.slice(0, 2), work_dir / scoring.TOKENS)
    with pytest.raises(SystemExit, match="different runs"):
        scoring.scorer_for(work_dir)

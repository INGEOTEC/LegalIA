"""How two texts are scored in the Atlas comparison (issue #267).

`instrument_matrix.py` asks, for every searched unit row, which *other*
instrument owns the nearest text. Until issue #267 "nearest" meant one thing,
the exact cosine of two Qwen3-Embedding vectors, computed inline in the sweep.
This module is the one place a text-to-text score is computed, so a second
method (BM25, a classical lexical baseline) can be swapped in without touching
the mask, the `1/m` weighting, the identical-shared rule or anything downstream:

    scorer = scorer_for(work_dir)          # chosen by input.json's `method`
    block = scorer.scores(rows)            # (len(rows), n_rows) float32
    one = scorer.scores_against(rows, candidates)

Row `r` of `scores(rows)` is the score of every vector row against source
vector row `rows[r]`. A larger score is always a nearer text; the scale is the
scorer's own (a cosine lies in [-1, 1], a BM25 score is unbounded above), which
is why the *tie rule* lives here too: `tie_mask`.

* **`DenseScorer`** is the code `instrument_matrix.py` and
  `export_atlas_pairs.py` used to carry, moved and not rewritten: `vectors.npy`
  as `float32`, L2-normalised once, `vectors[rows] @ vectors.T`. A tie is an
  *absolute* slack (`score >= best - tolerance`) and a word-for-word match is a
  score of at least `1 - tolerance`. A work directory whose `input.json` has no
  `method` key is dense, so the two work directories already published keep
  working unchanged and their matrices are reproduced to the byte.
* **`Bm25Scorer`** reads the BM25 index `prepare_bm25_input.py` saved
  (`bm25-index/`, bm25s' own files) and the token ids of every document
  (`tokens.parquet`). `W` is the `(vector rows x vocabulary)` sparse matrix of
  bm25s' scores (each document's weight of each of its terms: the IDF times the
  length-normalised term frequency); `Q` is the query side, **binary over
  distinct terms** — Lucene's convention, and bm25s' own, which scores a
  repeated query term once per distinct token id. A row's score against a
  document is then `Q[row] . W[document]`, exactly what `bm25s.BM25.get_scores`
  returns for the same terms (a test pins that against the library's own
  numbers). A tie is a *relative* slack (`score >= best * (1 - tolerance)`) —
  BM25 scores are unbounded, so an absolute 1e-6 means nothing on them — and
  a word-for-word match is a winner whose `text_sha1` equals the source's.

  Ties of identical texts are exact under either scorer: two documents with the
  same token multiset have identical columns of `W`, and the sparse product
  accumulates a row's terms in the same (query) order for every document, so
  their dot products are bit-identical float32 numbers.

  A source row can have **no foreign match at all** under BM25 — it has no
  term (nothing but one-character words and digits), or every term it has lies
  only in texts the source owns alone and are masked. Its best foreign score
  is 0, `tie_mask` returns no winner for it, and `instrument_matrix.py` records
  it as `drop_reason == "no_match"` and does not count it. Cosine always has a
  nearest text, so the dense path keeps raising when a row finds none.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

#: What `input.json` records for a vector work directory written before issue
#: #267 (no `method` key).
DENSE = "dense"
BM25 = "bm25"

#: Where `prepare_bm25_input.py` saves the bm25s index, and the sidecar with
#: every document's token ids, inside the work directory.
INDEX_DIR = "bm25-index"
TOKENS = "tokens.parquet"

#: bm25s' own file names (`BM25.save`'s defaults), checked by name so a missing
#: one is a `SystemExit` naming it rather than an `OSError` from numpy.
BM25_FILES = ("data.csc.index.npy", "indices.csc.index.npy", "indptr.csc.index.npy",
              "vocab.index.json", "params.index.json")


def method_of(work_dir: Path) -> str:
    """The scoring method `input.json` records: `"dense"` when the key (or the
    whole file) is absent."""
    record = Path(work_dir) / "input.json"
    if record.exists():
        return json.loads(record.read_text(encoding="utf-8")).get("method", DENSE)
    return DENSE


def tie_threshold(best, tolerance: float, relative: bool):
    """The lowest score that still ties with `best` (a scalar or an array).

    Absolute: `best - tolerance`. Relative: `best * (1 - tolerance)`.
    """
    if relative:
        return best * (1.0 - tolerance)
    return best - tolerance


def tie_mask(block_scores, best, tolerance: float, relative: bool = False):
    """Which columns of `block_scores` tie with each row's `best`.

    `block_scores` is `(rows, columns)`, `best` its per-row maximum (`(rows,)`);
    the result is a boolean array of the same shape as `block_scores`. Masked
    columns (`-inf`) never tie. Under the relative rule a row whose best is not
    positive has **no** winner: its threshold would be 0 (or below), and every
    unmasked column — all scoring 0 — would tie, which is no match at all.
    """
    import numpy as np

    best = np.asarray(best)
    threshold = tie_threshold(best, tolerance, relative)
    mask = block_scores >= np.reshape(threshold, (-1, 1))
    if relative:
        mask &= np.reshape(best > 0, (-1, 1))
    return mask


def agrees(best: float, recorded: float, tolerance: float, relative: bool) -> bool:
    """Whether a recomputed best score is the one `nearest.parquet` recorded:
    within `tolerance`, absolute or relative to the larger of the two."""
    if relative:
        return abs(best - recorded) <= tolerance * max(abs(best), abs(recorded))
    return abs(best - recorded) <= tolerance


class DenseScorer:
    """Exact cosine over the stacked embedding vectors.

    `full=True` (the matrix sweep) loads `vectors.npy` as `float32` and
    normalises it once, in place — the published vectors are not unit length
    (norms run 92 to 121), so a dot product is not a cosine until then.
    `full=False` (the pair export) memory-maps the file and normalises only the
    rows each call asks for; `scores` is not available then.
    """

    method = DENSE
    relative = False
    may_have_no_match = False
    required = ("vectors.npy",)

    def __init__(self, work_dir: Path, *, full: bool = True):
        import numpy as np

        work_dir = Path(work_dir)
        self.timings: dict[str, float] = {}
        mark = time.time()
        if full:
            self.vectors = np.load(work_dir / "vectors.npy").astype(np.float32)
            self.timings["load"] = round(time.time() - mark, 1)
            mark = time.time()
            # Once, in place.
            norms = np.linalg.norm(self.vectors, axis=1, keepdims=True)
            np.divide(self.vectors, np.where(norms > 0, norms, 1.0), out=self.vectors)
            self.timings["normalise"] = round(time.time() - mark, 1)
        else:
            self.vectors = np.load(work_dir / "vectors.npy", mmap_mode="r")
            self.timings["load"] = round(time.time() - mark, 1)
        self.full = full
        self.n_rows = int(self.vectors.shape[0])

    def _take(self, rows):
        import numpy as np

        block = np.asarray(self.vectors[rows], dtype=np.float32)
        if self.full:
            return block
        norms = np.linalg.norm(block, axis=1, keepdims=True)
        return block / np.where(norms > 0, norms, 1.0)

    def scores(self, rows):
        if not self.full:
            raise RuntimeError("DenseScorer(full=False) only scores against explicit candidates")
        return self.vectors[rows] @ self.vectors.T

    def scores_against(self, rows, candidates):
        return self._take(rows) @ self._take(candidates).T

    def identical(self, source_row: int, best: float, winners, tolerance: float) -> bool:
        """A word-for-word match scores a cosine of at least `1 - tolerance`.
        Compared in `float32`, exactly as the `float32` `similarity` column
        always was."""
        import numpy as np

        return bool(np.float32(best) >= np.float32(1.0 - tolerance))

    def describe(self) -> dict:
        return {"method": DENSE, "tolerance_relative": False}


class Bm25Scorer:
    """BM25 (bm25s, Lucene variant) over the candidate texts; see the module
    docstring for the query convention, the tie rule and the identity rule."""

    method = BM25
    relative = True
    may_have_no_match = True
    required = tuple(f"{INDEX_DIR}/{name}" for name in BM25_FILES) + (TOKENS, "vector_ids.parquet")

    def __init__(self, work_dir: Path, *, full: bool = True):
        import bm25s
        import numpy as np
        import pyarrow.parquet as pq
        from scipy import sparse

        work_dir = Path(work_dir)
        mark = time.time()
        index_dir = work_dir / INDEX_DIR
        retriever = bm25s.BM25.load(index_dir, load_corpus=False)
        self.params = json.loads((index_dir / "params.index.json").read_text(encoding="utf-8"))

        ids = pq.read_table(work_dir / "vector_ids.parquet").to_pandas()
        self.n_rows = int(len(ids))
        self.text_sha1 = ids["text_sha1"].to_numpy()

        tokens = pq.read_table(work_dir / TOKENS).to_pandas()
        doc_rows = tokens["row"].to_numpy(dtype=np.int64)
        scores = retriever.scores
        # bm25s appends an empty "" token to the vocabulary it saves, so the
        # index's own width (`indptr`) is the vocabulary, not `len(vocab_dict)`.
        vocabulary = len(scores["indptr"]) - 1
        documents = int(scores["num_docs"])
        if documents != len(doc_rows):
            raise SystemExit(f"{index_dir} indexes {documents} documents, {work_dir / TOKENS} "
                             f"lists {len(doc_rows)}: the index and its tokens are from "
                             "different runs, rerun prepare_bm25_input.py --force")
        if len(doc_rows) and (doc_rows.min() < 0 or doc_rows.max() >= self.n_rows):
            raise SystemExit(f"{work_dir / TOKENS} names a vector row outside "
                             f"vector_ids.parquet's {self.n_rows}")

        # bm25s keeps postings per token id (CSC over documents x vocabulary);
        # re-addressing the document axis by vector row gives a matrix indexed
        # the way every other script indexes texts. Vector rows that are no
        # document (nothing searched carries them) are empty rows: score 0.
        data = np.asarray(scores["data"], dtype=np.float32)
        indices = doc_rows[np.asarray(scores["indices"])]
        self.W = sparse.csc_matrix((data, indices, np.asarray(scores["indptr"])),
                                   shape=(self.n_rows, vocabulary))
        self.Wt = self.W.T            # (vocabulary x n_rows), CSR, shares the arrays
        self._W_rows = None

        # The query side: one 1 per distinct term of the document's own tokens.
        lengths = np.array([len(t) for t in tokens["token_ids"]], dtype=np.int64)
        flat = (np.concatenate([np.asarray(t, dtype=np.int64) for t in tokens["token_ids"]])
                if lengths.sum() else np.empty(0, dtype=np.int64))
        owner = np.repeat(doc_rows, lengths)
        query = sparse.csr_matrix((np.ones(len(flat), dtype=np.float32), (owner, flat)),
                                  shape=(self.n_rows, vocabulary))
        query.sum_duplicates()
        query.data[:] = 1.0
        self.Q = query
        self.documents = documents
        self.vocabulary = vocabulary
        self.timings = {"load": round(time.time() - mark, 1)}

    def scores(self, rows):
        return (self.Q[rows] @ self.Wt).toarray().astype("float32", copy=False)

    def scores_against(self, rows, candidates):
        if self._W_rows is None:
            self._W_rows = self.W.tocsr()
        block = self.Q[rows] @ self._W_rows[candidates].T
        return block.toarray().astype("float32", copy=False)

    def identical(self, source_row: int, best: float, winners, tolerance: float) -> bool:
        """A word-for-word match is a winner carrying the source's own
        `text_sha1` (in any collection) — what a cosine of 1 means for the
        embeddings."""
        return bool((self.text_sha1[winners] == self.text_sha1[source_row]).any())

    def describe(self) -> dict:
        return {
            "method": BM25,
            "tolerance_relative": True,
            "bm25": {
                "library": "bm25s",
                "library_version": self.params.get("version"),
                "method": self.params.get("method"),
                "k1": self.params.get("k1"),
                "b": self.params.get("b"),
                "query": "binary over distinct terms",
                "identical": "text_sha1",
                "vocabulary": self.vocabulary,
                "documents": self.documents,
            },
        }


def scorer_class(work_dir: Path):
    method = method_of(work_dir)
    if method == DENSE:
        return DenseScorer
    if method == BM25:
        return Bm25Scorer
    raise SystemExit(f"{Path(work_dir) / 'input.json'} records method {method!r}: "
                     f"this repository scores {DENSE!r} and {BM25!r}")


def required_files(work_dir: Path) -> list[Path]:
    """What the scorer of `work_dir` reads, relative to it."""
    return [Path(work_dir) / name for name in scorer_class(work_dir).required]


def check_inputs(work_dir: Path) -> None:
    """A `SystemExit` naming the first scorer input that is not on disk."""
    for path in required_files(work_dir):
        if not path.exists():
            raise SystemExit(f"{path} is missing -- the {method_of(work_dir)} scorer reads "
                             "what the work directory's prepare script wrote")


def scorer_for(work_dir: Path, *, full: bool = True):
    """The scorer `work_dir`'s `input.json` names."""
    check_inputs(work_dir)
    return scorer_class(work_dir)(work_dir, full=full)

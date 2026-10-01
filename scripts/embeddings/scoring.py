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
  length-normalised term frequency); `Q` is the query side, by default **binary
  over distinct terms** (`Bm25Config.weighting`; the tuning of
  `tune_bm25.py` also tries a saturated term count) — Lucene's convention, and bm25s' own, which scores a
  repeated query term once per distinct token id. A row's score against a
  document is then `Q[row] . W[document]`, exactly what `bm25s.BM25.get_scores`
  returns for the same terms (a test pins that against the library's own
  numbers). The method, `k1`, `b` and weighting are a `Bm25Config`, read back
  from `bm25-index/` and `input.json`; `Bm25Counts` recomputes `W` and `Q` for
  any configuration from `tokens.parquet` alone (bm25s' own formulas, no
  re-tokenising and no new index), which is what makes the tuning's grid cheap.
  A tie is a *relative* slack (`score >= best * (1 - tolerance)`) —
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
from dataclasses import dataclass
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


#: bm25s' scoring variants and the two query-side weightings the tuning
#: (issue #267, review fix-1) searches. `lucene` is what the data set was first
#: built with.
BM25_METHODS = ("lucene", "robertson", "atire", "bm25l", "bm25+")
QUERY_WEIGHTINGS = ("binary", "tf-saturated")

#: What `input.json`'s `bm25.query` says for each weighting (kept as prose for a
#: reader; the machine-readable name is `bm25.weighting`).
QUERY_LABELS = {
    "binary": "binary over distinct terms",
    "tf-saturated": "term count saturated with k1",
}


@dataclass(frozen=True)
class Bm25Config:
    """One BM25 configuration: the bm25s scoring `method`, its `k1`, `b` and
    (for `bm25l`/`bm25+`) `delta`, and the query-side `weighting`.

    `weighting` is how a query term counts. `binary` (Lucene's and bm25s' own
    convention) gives every *distinct* term of the query a weight of 1.
    `tf-saturated` gives a term that occurs `c` times in the query the weight
    `c * (k1 + 1) / (c + k1)` -- its own count saturated with the same `k1`,
    with no length normalisation on the query side -- which is 1 for `c == 1`,
    so the two coincide whenever every query term occurs once.
    """

    method: str = "lucene"
    k1: float = 1.5
    b: float = 0.75
    delta: float = 0.5
    weighting: str = "binary"

    def __post_init__(self):
        if self.method not in BM25_METHODS:
            raise ValueError(f"unknown BM25 method {self.method!r}")
        if self.weighting not in QUERY_WEIGHTINGS:
            raise ValueError(f"unknown query weighting {self.weighting!r}")

    @property
    def slug(self) -> str:
        return (f"{self.method.replace('+', 'plus')}-k{self.k1:g}-b{self.b:g}-"
                f"{self.weighting.replace('-', '')}")

    def record(self) -> dict:
        """The `bm25` block of `input.json` / `matrix.json`."""
        return {"method": self.method, "k1": self.k1, "b": self.b, "delta": self.delta,
                "weighting": self.weighting, "query": QUERY_LABELS[self.weighting]}


def bm25_config_of(record: dict) -> Bm25Config:
    """The configuration a `bm25` block (or bm25s' own `params.index.json`)
    records. A block written before the tuning has no `weighting`: binary."""
    return Bm25Config(method=record.get("method", "lucene"), k1=float(record.get("k1", 1.5)),
                      b=float(record.get("b", 0.75)), delta=float(record.get("delta", 0.5)),
                      weighting=record.get("weighting", "binary"))


class Bm25Counts:
    """Every document's term counts, from the `tokens.parquet` that
    `prepare_bm25_input.py` saved, and from them any BM25 configuration's
    sparse weights -- recomputed with bm25s' own formulas (never re-tokenised,
    never re-indexed), which is what makes a parameter sweep affordable.

    `tf` is `(documents x vocabulary)` term frequencies in document order,
    `doc_rows[d]` the vector row of document `d`, and `doc_len[d]` its token
    count (repeats included). `n_rows` is the number of vector rows.
    """

    def __init__(self, doc_rows, tf, doc_len, n_rows: int, text_sha1):
        self.doc_rows, self.tf, self.doc_len = doc_rows, tf, doc_len
        self.n_rows, self.text_sha1 = int(n_rows), text_sha1
        self.documents, self.vocabulary = tf.shape

    @classmethod
    def load(cls, work_dir: Path, *, vocabulary: int | None = None) -> "Bm25Counts":
        import numpy as np
        import pyarrow.parquet as pq
        from scipy import sparse

        work_dir = Path(work_dir)
        ids = pq.read_table(work_dir / "vector_ids.parquet", columns=["text_sha1"])
        text_sha1 = ids.column("text_sha1").to_numpy(zero_copy_only=False)
        tokens = pq.read_table(work_dir / TOKENS)
        doc_rows = tokens.column("row").to_numpy().astype(np.int64)
        column = tokens.column("token_ids").combine_chunks()
        offsets = column.offsets.to_numpy().astype(np.int64)
        flat = column.values.to_numpy().astype(np.int64)
        lengths = np.diff(offsets)
        if vocabulary is None:
            vocabulary = int(flat.max()) + 1 if len(flat) else 0
        documents = len(doc_rows)
        tf = sparse.csr_matrix(
            (np.ones(len(flat), dtype=np.float32), (np.repeat(np.arange(documents), lengths), flat)),
            shape=(documents, vocabulary))
        tf.sum_duplicates()
        if len(doc_rows) and (doc_rows.min() < 0 or doc_rows.max() >= len(text_sha1)):
            raise SystemExit(f"{work_dir / TOKENS} names a vector row outside "
                             f"vector_ids.parquet's {len(text_sha1)}")
        return cls(doc_rows, tf, lengths, len(text_sha1), text_sha1)

    def idf(self, method: str):
        """bm25s' inverse document frequency of every term, `float32`."""
        import numpy as np
        from bm25s.scoring import _select_idf_scorer

        df = self.tf.getnnz(axis=0)
        compute = _select_idf_scorer(method)
        out = np.zeros(self.vocabulary, dtype=np.float32)
        for term in np.flatnonzero(df):
            out[term] = compute(int(df[term]), N=self.documents)
        return out

    def weights(self, config: Bm25Config):
        """`W`, a `(vector rows x vocabulary)` CSC matrix of `float32` weights --
        bm25s' index re-addressed by vector row, exactly what `Bm25Scorer` reads
        from `bm25-index/` for the same configuration -- and the length-`vocabulary`
        non-occurrence vector (all zeros but for `bm25l`/`bm25+`).

        For those two variants bm25s scores a term a document lacks too: a
        query-only constant `sum(nonoccurrence[t])` added to every document, and
        stores `idf * tfc - nonoccurrence` for the terms it has. The constant
        changes no ranking, so it is **dropped here** (a document sharing no term
        scores 0, as under the other variants): kept, it would give every unrelated
        text the same positive score, a row with no real match would tie the whole
        corpus, and the relative tie rule would turn that into noise.
        """
        import numpy as np
        from bm25s.scoring import _select_tfc_scorer
        from scipy import sparse

        tf = self.tf
        rows = np.repeat(np.arange(self.documents), np.diff(tf.indptr))
        idf = self.idf(config.method)
        compute = _select_tfc_scorer(config.method)
        average = np.float64(self.doc_len.mean()) if self.documents else np.float64(1.0)
        tfc = compute(tf_array=tf.data, l_d=self.doc_len[rows], l_avg=average, k1=config.k1,
                      b=config.b, delta=config.delta)
        data = idf[tf.indices] * tfc
        nonoccurrence = np.zeros(self.vocabulary, dtype=np.float32)
        if config.method in ("bm25l", "bm25+"):
            zero = compute(tf_array=0, l_d=average, l_avg=average, k1=config.k1, b=config.b,
                           delta=config.delta)
            nonoccurrence = (idf * zero).astype(np.float32)
            data = data - nonoccurrence[tf.indices]
        w = sparse.csc_matrix((data.astype(np.float32), (self.doc_rows[rows], tf.indices)),
                              shape=(self.n_rows, self.vocabulary))
        return w, nonoccurrence

    def queries(self, config: Bm25Config):
        """`Q`, the `(vector rows x vocabulary)` CSR query matrix: row `r` holds
        the weight of each term of vector row `r`'s own text (see
        `Bm25Config`). Vector rows that are no document are empty."""
        import numpy as np
        from scipy import sparse

        tf = self.tf
        rows = np.repeat(self.doc_rows, np.diff(tf.indptr))
        if config.weighting == "binary":
            data = np.ones(len(tf.data), dtype=np.float32)
        else:
            count = tf.data.astype(np.float64)
            data = (count * (config.k1 + 1.0) / (count + config.k1)).astype(np.float32)
        return sparse.csr_matrix((data, (rows, tf.indices)), shape=(self.n_rows, self.vocabulary))


class Bm25Scorer:
    """BM25 (bm25s) over the candidate texts; see the module docstring for the
    query convention, the tie rule and the identity rule. The configuration
    (`method`, `k1`, `b`, `delta`, query weighting) is read from the work
    directory's `input.json`, never fixed here."""

    method = BM25
    relative = True
    may_have_no_match = True
    required = tuple(f"{INDEX_DIR}/{name}" for name in BM25_FILES) + (TOKENS, "vector_ids.parquet")

    def __init__(self, work_dir: Path, *, full: bool = True):
        import bm25s
        import numpy as np

        work_dir = Path(work_dir)
        mark = time.time()
        index_dir = work_dir / INDEX_DIR
        retriever = bm25s.BM25.load(index_dir, load_corpus=False)
        self.params = json.loads((index_dir / "params.index.json").read_text(encoding="utf-8"))
        record = json.loads((work_dir / "input.json").read_text(encoding="utf-8")) \
            if (work_dir / "input.json").exists() else {}
        config = bm25_config_of({**self.params, **{key: value for key, value in
                                                   record.get("bm25", {}).items()
                                                   if key in ("weighting", "delta")}})

        scores = retriever.scores
        documents = int(scores["num_docs"])
        # bm25s appends an empty "" token to the vocabulary it saves, so the
        # index's own width (`indptr`) is the vocabulary, not `len(vocab_dict)`.
        vocabulary = len(scores["indptr"]) - 1
        counts = Bm25Counts.load(work_dir, vocabulary=vocabulary)
        if documents != counts.documents:
            raise SystemExit(f"{index_dir} indexes {documents} documents, {work_dir / TOKENS} "
                             f"lists {counts.documents}: the index and its tokens are from "
                             "different runs, rerun prepare_bm25_input.py --force")
        # bm25s keeps postings per token id (CSC over documents x vocabulary);
        # re-addressing the document axis by vector row gives a matrix indexed
        # the way every other script indexes texts. Vector rows that are no
        # document (nothing searched carries them) are empty rows: score 0.
        from scipy import sparse
        data = np.asarray(scores["data"], dtype=np.float32)
        indices = counts.doc_rows[np.asarray(scores["indices"])]
        w = sparse.csc_matrix((data, indices, np.asarray(scores["indptr"])),
                              shape=(counts.n_rows, vocabulary))
        self._setup(counts, config, w, vocabulary)
        self.timings = {"load": round(time.time() - mark, 1)}

    @classmethod
    def for_config(cls, counts: Bm25Counts, config: Bm25Config) -> "Bm25Scorer":
        """A scorer for `config` built straight from the term counts, with no
        saved index: what the tuning sweeps (`tune_bm25.py`)."""
        scorer = object.__new__(cls)
        w, _ = counts.weights(config)
        scorer.params = config.record()
        scorer._setup(counts, config, w, counts.vocabulary)
        scorer.timings = {"load": 0.0}
        return scorer

    def _setup(self, counts: Bm25Counts, config: Bm25Config, w, vocabulary: int) -> None:
        self.counts, self.config = counts, config
        self.n_rows = counts.n_rows
        self.text_sha1 = counts.text_sha1
        self.W = w
        self.Wt = w.T            # (vocabulary x n_rows), CSR, shares the arrays
        self._W_rows = None
        self.Q = counts.queries(config)
        self.documents = counts.documents
        self.vocabulary = vocabulary

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
                **self.config.record(),
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

"""Cluster the Atlas instruments into topics (issue #285).

    uv run --group viz python scripts/embeddings/cluster_instruments.py --work-dir emb-run-atlas
    uv run --group viz python scripts/embeddings/cluster_instruments.py --work-dir emb-run-atlas \\
        --choose louvain:1.0 --force

The Atlas draws the 2,676 unique instruments through a directed matrix `A`
(`instrument-matrix/matrix.npy`, issue #242): row *i* says how the counted
excerpts of instrument *i* distribute over the other instruments that own the
text nearest to each of them. Nothing groups the instruments, and nothing names
what a region of the map is about. This script groups them — **a clustering of
the L2-normalised rows of `A`** (zero rows, if any, are reported and assigned
noise), so a cluster is a region of the map, not of the excerpt centroids — and
labels every cluster by the words that distinguish its texts and by its most
representative instruments. It reads a prepared work directory with a computed
matrix, takes seconds to minutes of one CPU (no Slurm, no GPU, no network) and
writes everything under `<work-dir>/topics/` (gitignored):

* **Four algorithms, one sweep of one tuning parameter each** (`SWEEPS`, 21
  configurations): HDBSCAN on a 5-dimensional UMAP of the normalised rows
  (`min_cluster_size`; the BERTopic pipeline, with an explicit noise label);
  Ward agglomerative clustering and k-means on the normalised rows (`n_clusters`;
  Euclidean distance on unit vectors is monotone in cosine); and Louvain
  communities of the undirected graph `A + Aᵀ` (`resolution`; the raw matrix, not
  the normalised rows, with a zero diagonal). Every run is deterministic under
  the seeds in `SEEDS`; a configuration that raises is recorded as failed and the
  sweep goes on (no retry, no fallback).
* **Five measures per configuration** (`comparison.json`/`comparison.md`): the
  number of clusters (noise excluded), the noise fraction, the largest cluster's
  share of the instruments, the cosine silhouette over the normalised rows
  (noise rows excluded) and the **strong-link co-membership** — the fraction of
  the gold's links (`gold-links/gold.json`, issue #272 and #283's review fix: an
  instrument's `targets`, `A_or_B`) whose two ends carry the same non-noise label.
* **One selection rule, fixed before the run and never tuned after it**
  (`select`): among the configurations that finished, have 20 to 80 clusters, at
  most 15 % noise and a largest cluster of at most 25 % of the instruments
  (`GATES` — co-membership alone is maximised by one giant cluster, so the gates
  keep the result a topic map), take the highest co-membership; break a tie by
  silhouette, then by fewer clusters, then by the algorithm's name. When no
  configuration passes, the script writes the comparison and exits non-zero naming
  the gate each one failed: that is a finding for a human, not something to
  relax. `--choose <algorithm>:<parameter>` overrides the rule, and `meta` records
  that it did.
* **Labels** for the chosen configuration: BERTopic's class-based TF-IDF
  (c-TF-IDF) over unigrams and bigrams of the searched unit rows (headings and
  transitorios left out through `instrument_matrix.searched_mask`) of the
  cluster's instruments, tokenised like `prepare_bm25_input.py`. A cluster's term
  frequency is relative to its own total (BERTopic's L1 normalisation) and
  multiplied by `log(1 + A / f_t)`, `A` the mean number of terms per cluster and
  `f_t` the term's count over every cluster; the noise cluster is a class like any
  other, as in BERTopic. Terms seen in fewer than two instruments are dropped.
  A cluster's three **representatives** are the instruments nearest (cosine) to
  the cluster's mean normalised row, and its members are ordered by that same
  distance.
* **Outputs**, each atomic (`_atomic.py`): `labels.parquet` (`i`, `algorithm`,
  `parameter`, `label` for every finished configuration), `comparison.json`,
  `comparison.md`, `topics.json` (the chosen configuration: `meta`, then one entry
  per cluster with `id`, `size`, `terms`, `representatives` and `members` as
  `[i, distance]`, `collections`; clusters by size, the noise cluster `id: -1`
  last) and the `.done` marker. An existing `.done` returns; `--force` recomputes.

No hierarchy is exported, no topic is named by a language model, and nothing here
is committed. The 4B and BM25 work directories are accepted through `--work-dir`
but only the 0.6B is measured (the README's "Measured, topics" section).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402

import build_umap_html as html  # noqa: E402
import instrument_matrix  # noqa: E402
from _atomic import atomic_write_table, atomic_write_text  # noqa: E402
from prepare_bm25_input import TOKENISER  # noqa: E402

SUBDIR = "topics"
DONE_MARKER = ".done"
GOLD_JSON = Path("gold-links") / "gold.json"

#: Every random choice of the run, spelled out: UMAP's, k-means' and Louvain's.
#: HDBSCAN and Ward are deterministic. A fixed UMAP seed makes the fit
#: single-threaded, which is a few seconds at this size.
SEEDS = {"umap": 0, "kmeans": 0, "louvain": 0}

#: The UMAP reduction HDBSCAN runs on, fitted once and reused across its sweep.
UMAP_PARAMS = {"n_components": 5, "n_neighbors": 15, "min_dist": 0.0, "metric": "cosine",
               "random_state": SEEDS["umap"]}

#: `algorithm -> (name of its one tuning parameter, the values swept)`.
SWEEPS = {
    "hdbscan_umap": ("min_cluster_size", (5, 10, 15, 20, 30)),
    "agglomerative": ("n_clusters", (20, 30, 40, 50, 60, 80)),
    "kmeans": ("n_clusters", (20, 30, 40, 50, 60, 80)),
    "louvain": ("resolution", (0.5, 1.0, 1.5, 2.0, 3.0)),
}

#: The selection rule's gates, fixed in issue #285 and never tuned afterwards.
GATES = {"min_clusters": 20, "max_clusters": 80, "max_noise": 0.15, "max_largest": 0.25}

#: How many terms label a cluster, and how many representatives it names.
TOP_TERMS = 10
TOP_REPRESENTATIVES = 3

#: c-TF-IDF's vocabulary: unigrams and bigrams, of terms seen in at least two
#: instruments.
NGRAM_RANGE = (1, 2)
MIN_INSTRUMENTS = 2

#: Spanish function words dropped before the n-grams are formed, for every
#: cluster alike (issue #285's rule: allowed only if most clusters' top terms are
#: the same boilerplate). The first measured run, with no list, put `de`, `la`,
#: `el`, `en`, `los`, `que` among the six leading terms of 27 of 32 clusters, which
#: names no topic. Words of one character are already gone from the tokeniser.
#: Domain words (`artículo`, `ley`, `estado`, `parte`) are deliberately not here:
#: where a cluster's terms are the same legal boilerplate, that *is* the finding.
STOPWORDS: tuple[str, ...] = (
    "al", "como", "con", "cada", "cual", "cuales", "cuando", "de", "del", "desde", "donde",
    "el", "en", "entre", "es", "esa", "ese", "esta", "este", "estas", "estos", "ha", "han",
    "hasta", "la", "las", "le", "les", "lo", "los", "mas", "más", "mismo", "misma", "ni",
    "no", "para", "pero", "por", "que", "quien", "quienes", "se", "sea", "sean", "ser",
    "si", "sin", "sobre", "son", "su", "sus", "también", "un", "una", "ya", "así",
)

_TOKEN = re.compile(TOKENISER["token_pattern"])


def tokenize(text: str) -> list[str]:
    """`prepare_bm25_input.py`'s tokeniser: lowercase words of two or more
    characters (its own `token_pattern`, no stopwords, no stemming)."""
    return _TOKEN.findall(text.lower())


def output_dir(work_dir: Path) -> Path:
    return Path(work_dir) / SUBDIR


# -- loading -------------------------------------------------------------- #

def load_inputs(work_dir: Path) -> dict:
    """What the run reads, after refusing a work directory without it: the
    instruments, the raw matrix `A`, `matrix.json`, `input.json` and the gold."""
    import pyarrow.parquet as pq

    work_dir = Path(work_dir)
    needed = [work_dir / "instruments.parquet",
              instrument_matrix.output_dir(work_dir) / "matrix.npy",
              instrument_matrix.output_dir(work_dir) / "matrix.json",
              work_dir / "input.json",
              work_dir / GOLD_JSON]
    for path in needed:
        if not path.exists():
            hint = ("; run instrument_matrix.py first" if path.name.startswith("matrix")
                    else "; run gold_links.py first" if path.name == "gold.json" else "")
            raise SystemExit(f"{path} is missing{hint}")
    instruments = pq.read_table(work_dir / "instruments.parquet").to_pandas()
    matrix = np.load(needed[1])
    if matrix.shape != (len(instruments),) * 2:
        raise SystemExit(f"{needed[1]} is {matrix.shape[0]} x {matrix.shape[1]} but "
                         f"instruments.parquet lists {len(instruments)} instruments")
    return {
        "instruments": instruments,
        "matrix": matrix,
        "matrix_json": json.loads(needed[2].read_text(encoding="utf-8")),
        "input": json.loads(needed[3].read_text(encoding="utf-8")),
        "gold": json.loads(needed[4].read_text(encoding="utf-8")),
    }


def normalised_rows(matrix):
    """`(X, nonzero)`: the rows of `A` scaled to unit L2 norm in `float32`, and the
    boolean mask of the rows that are not all zero (a zero row stays zero)."""
    matrix = np.asarray(matrix, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1)
    nonzero = norms > 0
    scaled = np.zeros_like(matrix)
    scaled[nonzero] = matrix[nonzero] / norms[nonzero, None]
    return scaled, nonzero


def gold_pairs(gold: dict) -> list[tuple[int, int]]:
    """The gold's strong links as sorted `(i, target)` pairs: every entry's `targets`
    (signal A or B), once each."""
    return sorted({(int(entry["i"]), int(target))
                   for entry in gold["entries"] for target in entry["targets"]})


# -- the four algorithms -------------------------------------------------- #

def canonical_labels(labels) -> np.ndarray:
    """Relabel clusters `0..n-1` by size, largest first, a tie going to the
    cluster whose first member comes first; noise (`-1`) is kept. Makes a label
    depend on the partition and not on an algorithm's own numbering."""
    labels = np.asarray(labels)
    out = np.full(len(labels), -1, dtype=np.int32)
    clusters = sorted((label for label in set(labels.tolist()) if label >= 0),
                      key=lambda label: (-int((labels == label).sum()),
                                         int(np.flatnonzero(labels == label)[0])))
    for new, label in enumerate(clusters):
        out[labels == label] = new
    return out


def umap_embedding(X):
    """The 5-dimensional UMAP of the normalised rows (`UMAP_PARAMS`)."""
    import umap

    return umap.UMAP(**UMAP_PARAMS).fit_transform(X)


def hdbscan_labels(embedding, min_cluster_size: int) -> np.ndarray:
    from sklearn.cluster import HDBSCAN

    return HDBSCAN(min_cluster_size=int(min_cluster_size),
                   metric="euclidean", copy=True).fit_predict(embedding)


def agglomerative_labels(X, n_clusters: int) -> np.ndarray:
    from sklearn.cluster import AgglomerativeClustering

    return AgglomerativeClustering(n_clusters=int(n_clusters), linkage="ward").fit_predict(X)


def kmeans_labels(X, n_clusters: int) -> np.ndarray:
    from sklearn.cluster import KMeans

    return KMeans(n_clusters=int(n_clusters), n_init=10,
                  random_state=SEEDS["kmeans"]).fit_predict(X)


def louvain_labels(matrix, resolution: float) -> np.ndarray:
    """Modularity communities of the undirected weighted graph of `A + Aᵀ` (the raw
    matrix, zero diagonal), with networkx's Louvain."""
    import networkx as nx

    symmetric = np.asarray(matrix, dtype=np.float64)
    symmetric = symmetric + symmetric.T
    np.fill_diagonal(symmetric, 0.0)
    graph = nx.from_numpy_array(symmetric)
    labels = np.full(len(symmetric), -1, dtype=np.int32)
    communities = nx.community.louvain_communities(
        graph, weight="weight", resolution=float(resolution), seed=SEEDS["louvain"])
    for label, members in enumerate(communities):
        labels[sorted(members)] = label
    return labels


# -- the measures --------------------------------------------------------- #

def silhouette(X, labels):
    """The cosine silhouette of the clustered (non-noise) rows, or `None` when it is
    undefined: fewer than two clusters, or as many clusters as rows."""
    from sklearn.metrics import silhouette_score

    labels = np.asarray(labels)
    kept = labels >= 0
    clusters = len(set(labels[kept].tolist()))
    if clusters < 2 or clusters > int(kept.sum()) - 1:
        return None
    return float(silhouette_score(X[kept], labels[kept], metric="cosine", sample_size=None,
                                  random_state=0))


def comembership(labels, pairs) -> float | None:
    """The fraction of `pairs` whose two ends carry the same non-noise label, or
    `None` when there are no pairs."""
    if not pairs:
        return None
    labels = np.asarray(labels)
    same = sum(1 for i, j in pairs if labels[i] >= 0 and labels[i] == labels[j])
    return same / len(pairs)


def measures(X, labels, pairs) -> dict:
    labels = np.asarray(labels)
    sizes = np.bincount(labels[labels >= 0]) if (labels >= 0).any() else np.array([0])
    return {
        "clusters": int(len(set(labels[labels >= 0].tolist()))),
        "noise": float((labels < 0).sum() / len(labels)),
        "largest": float(sizes.max() / len(labels)),
        "silhouette": silhouette(X, labels),
        "comembership": comembership(labels, pairs),
    }


# -- the sweeps and the rule ---------------------------------------------- #

def configurations(sweeps=None) -> list[tuple[str, str, float]]:
    """`(algorithm, parameter name, value)` for every configuration, in order."""
    sweeps = SWEEPS if sweeps is None else sweeps
    return [(algorithm, name, value) for algorithm, (name, values) in sweeps.items()
            for value in values]


def parameter_text(value) -> str:
    """How a parameter is spelled in `--choose` and in the tables: `10`, `1.0`."""
    return str(value)


def run_sweeps(inputs: dict, *, sweeps=None, log=print) -> list[dict]:
    """Run every configuration and measure it. Each record holds `algorithm`,
    `parameter_name`, `parameter`, `status` and either the measures (and the
    private `labels`, one per instrument) or the `error`. Rows of `A` that are all
    zero are left out of every fit and labelled noise."""
    matrix = inputs["matrix"]
    X, nonzero = normalised_rows(matrix)
    pairs = gold_pairs(inputs["gold"])
    n = len(matrix)
    Xn, An = X[nonzero], np.asarray(matrix)[np.ix_(nonzero, nonzero)]
    embedding = None
    embedding_error = None
    records = []
    for algorithm, name, value in configurations(sweeps):
        record = {"algorithm": algorithm, "parameter_name": name, "parameter": value}
        try:
            if algorithm == "hdbscan_umap":
                if embedding is None and embedding_error is None:
                    try:
                        embedding = umap_embedding(Xn)
                    except Exception as error:  # noqa: BLE001 - recorded, never retried
                        embedding_error = error
                if embedding_error is not None:
                    raise embedding_error
                fitted = hdbscan_labels(embedding, value)
            elif algorithm == "agglomerative":
                fitted = agglomerative_labels(Xn, value)
            elif algorithm == "kmeans":
                fitted = kmeans_labels(Xn, value)
            elif algorithm == "louvain":
                fitted = louvain_labels(An, value)
            else:
                raise ValueError(f"unknown algorithm {algorithm!r}")
            labels = np.full(n, -1, dtype=np.int32)
            labels[nonzero] = fitted
            labels = canonical_labels(labels)
            record.update(status="ok", **measures(X, labels, pairs))
            record["labels"] = labels
        except Exception as error:  # noqa: BLE001 - a failed configuration is a recorded result
            record.update(status="failed", error=f"{type(error).__name__}: {error}")
        log(describe(record))
        records.append(record)
    return records


def gate_failures(record: dict, gates=None) -> list[str]:
    """Which gates a finished configuration fails, spelled out; empty when it passes."""
    gates = GATES if gates is None else gates
    if record["status"] != "ok":
        return [f"failed to run ({record['error']})"]
    failed = []
    if not gates["min_clusters"] <= record["clusters"] <= gates["max_clusters"]:
        failed.append(f"{record['clusters']} clusters, outside "
                      f"{gates['min_clusters']}-{gates['max_clusters']}")
    if record["noise"] > gates["max_noise"]:
        failed.append(f"noise {record['noise']:.1%} over {gates['max_noise']:.0%}")
    if record["largest"] > gates["max_largest"]:
        failed.append(f"largest cluster {record['largest']:.1%} over {gates['max_largest']:.0%}")
    return failed


def select(records: list[dict], gates=None) -> dict | None:
    """The configuration the rule picks, or `None` when none passes the gates:
    highest co-membership, then highest silhouette, then fewer clusters, then the
    algorithm's name (then the parameter, only to stay total)."""
    passing = [r for r in records if not gate_failures(r, gates)]
    if not passing:
        return None

    def key(record):
        silhouette_value = record["silhouette"]
        return (-(record["comembership"] if record["comembership"] is not None else -1.0),
                -(silhouette_value if silhouette_value is not None else float("-inf")),
                record["clusters"], record["algorithm"], record["parameter"])

    return min(passing, key=key)


def parse_choice(text: str, records: list[dict]) -> dict:
    """The configuration `--choose algorithm:parameter` names, or a `SystemExit`
    listing what exists."""
    algorithm, _, parameter = text.partition(":")
    for record in records:
        if record["algorithm"] == algorithm and parameter_text(record["parameter"]) == parameter:
            if record["status"] != "ok":
                raise SystemExit(f"--choose {text}: that configuration failed "
                                 f"({record['error']})")
            return record
    known = ", ".join(f"{r['algorithm']}:{parameter_text(r['parameter'])}" for r in records)
    raise SystemExit(f"--choose {text}: no such configuration; the sweeps are {known}")


def describe(record: dict) -> str:
    label = f"{record['algorithm']} {record['parameter_name']}={parameter_text(record['parameter'])}"
    if record["status"] != "ok":
        return f"{label}: FAILED {record['error']}"
    sil = "n/a" if record["silhouette"] is None else f"{record['silhouette']:.3f}"
    com = "n/a" if record["comembership"] is None else f"{record['comembership']:.3f}"
    return (f"{label}: {record['clusters']} clusters, noise {record['noise']:.1%}, largest "
            f"{record['largest']:.1%}, silhouette {sil}, co-membership {com}")


# -- the labels ------------------------------------------------------------ #

def instrument_documents(work_dir: Path, instruments, *, collections, cache_dir=None,
                         log=print) -> list[str]:
    """One document per instrument, in `i` order: the text of its *searched* unit rows
    (every row, not every distinct text — an article repeated counts as often as it
    is written), joined by newlines. The join is `build_umap_html.load_frames`' own,
    narrowed by `instrument_matrix.searched_mask`, like `prepare_bm25_input.py`'s."""
    import pandas as pd

    points, _ = html.load_frames(work_dir, [], neighbors=0, collections=collections,
                                 cache_dir=cache_dir, extra_columns=("path", "text"), log=log)
    names = dict(enumerate(html.UNIT_TYPES))
    units = pd.DataFrame({
        "unit_type": points["u"].map(names),
        "transitorio": [instrument_matrix.is_transitorio_path(path) for path in points["path"]],
    })
    searched = instrument_matrix.searched_mask(units)
    texts = pd.Series(["" if text is None or text != text else str(text)
                       for text in points["text"].to_numpy()[searched]],
                      index=points["i"].to_numpy()[searched])
    joined = texts.groupby(level=0).agg("\n".join)
    log(f"{int(searched.sum())} searched unit rows of {joined.shape[0]} instruments "
        f"({len(points) - int(searched.sum())} headings and transitorios left out)")
    return [joined.get(int(i), "") for i in instruments["i"]]


def term_counts(documents: list[str], stop_words=None):
    """`(counts, vocabulary)`: the sparse instruments-by-terms count matrix over unigrams
    and bigrams seen in at least `MIN_INSTRUMENTS` instruments, and the term list
    (sorted, so a tie in a score is broken by the term itself)."""
    from sklearn.feature_extraction.text import CountVectorizer

    stop_words = list(STOPWORDS) if stop_words is None else list(stop_words)
    vectorizer = CountVectorizer(tokenizer=tokenize, token_pattern=None, lowercase=False,
                                 ngram_range=NGRAM_RANGE, min_df=MIN_INSTRUMENTS,
                                 stop_words=stop_words or None)
    counts = vectorizer.fit_transform(documents)
    return counts.tocsr(), np.asarray(vectorizer.get_feature_names_out())


def ctfidf_terms(counts, vocabulary, labels, *, top: int = TOP_TERMS) -> dict[int, list[str]]:
    """The top terms of every cluster (noise `-1` included, as BERTopic treats it) by
    class-based TF-IDF: a cluster's term counts (the sum over its instruments),
    relative to the cluster's own total, times `log(1 + A / f_t)` with `A` the mean
    number of terms per cluster and `f_t` the term's count over all clusters.
    Descending by score, a tie going to the term that sorts first."""
    import scipy.sparse as sp

    labels = np.asarray(labels)
    classes = sorted(set(labels.tolist()))
    indicator = sp.csr_matrix(
        (np.ones(len(labels)), (np.searchsorted(classes, labels), np.arange(len(labels)))),
        shape=(len(classes), len(labels)))
    per_class = (indicator @ counts).tocsr().astype(np.float64)
    totals = np.asarray(per_class.sum(axis=1)).ravel()
    frequency = np.asarray(per_class.sum(axis=0)).ravel()
    average = totals.mean()
    idf = np.log1p(average / np.maximum(frequency, 1.0))
    terms: dict[int, list[str]] = {}
    for row, label in enumerate(classes):
        scores = per_class[row].toarray().ravel() / max(totals[row], 1.0) * idf
        order = np.lexsort((np.arange(len(scores)), -scores))
        terms[int(label)] = [str(vocabulary[j]) for j in order[:top] if scores[j] > 0]
    return terms


def cluster_geometry(X, nonzero, labels) -> dict[int, list[list]]:
    """Per cluster (noise included) its members as `[i, distance]`, nearest first:
    the cosine distance to the cluster's mean normalised row, `1.0` for a zero row
    (a cosine undefined there)."""
    labels = np.asarray(labels)
    members: dict[int, list[list]] = {}
    for label in sorted(set(labels.tolist())):
        idx = np.flatnonzero(labels == label)
        usable = idx[nonzero[idx]]
        mean = X[usable].mean(axis=0) if len(usable) else np.zeros(X.shape[1], dtype=np.float32)
        norm = float(np.linalg.norm(mean))
        center = mean / norm if norm > 0 else mean
        distances = np.where(nonzero[idx], 1.0 - X[idx] @ center, 1.0) if norm > 0 \
            else np.ones(len(idx))
        ordered = sorted(zip(idx.tolist(), distances.tolist()), key=lambda pair: (pair[1], pair[0]))
        members[int(label)] = [[int(i), round(float(d), 4)] for i, d in ordered]
    return members


# -- the outputs ------------------------------------------------------------ #

def comparison_table(records: list[dict], chosen: dict, gates=None) -> str:
    """`comparison.md`: every configuration with its measures and whether it passed."""
    lines = ["| algorithm | parameter | clusters | noise | largest | silhouette | co-membership "
             "| gates | chosen |", "|---|---|---:|---:|---:|---:|---:|---|---|"]
    for record in records:
        label = (record["algorithm"], f"{record['parameter_name']} {parameter_text(record['parameter'])}")
        if record["status"] != "ok":
            lines.append(f"| {label[0]} | {label[1]} | failed | | | | | {record['error']} | |")
            continue
        failed = gate_failures(record, gates)
        sil = "–" if record["silhouette"] is None else f"{record['silhouette']:.3f}"
        com = "–" if record["comembership"] is None else f"{record['comembership']:.3f}"
        lines.append(
            f"| {label[0]} | {label[1]} | {record['clusters']} | {record['noise']:.1%} "
            f"| {record['largest']:.1%} | {sil} | {com} | "
            f"{'pass' if not failed else 'fail: ' + '; '.join(failed)} "
            f"| {'**yes**' if record is chosen else ''} |")
    return "\n".join(lines) + "\n"


def public(record: dict) -> dict:
    """A record without its private `labels`, as `comparison.json` holds it."""
    return {key: value for key, value in record.items() if key != "labels"}


def labels_table(records: list[dict]):
    import pyarrow as pa

    rows = [(i, r["algorithm"], parameter_text(r["parameter"]), int(label))
            for r in records if r["status"] == "ok" for i, label in enumerate(r["labels"])]
    return pa.table({
        "i": pa.array([row[0] for row in rows], type=pa.int32()),
        "algorithm": pa.array([row[1] for row in rows], type=pa.string()),
        "parameter": pa.array([row[2] for row in rows], type=pa.string()),
        "label": pa.array([row[3] for row in rows], type=pa.int32()),
    })


def library_versions() -> dict:
    import networkx
    import sklearn
    import umap

    return {"scikit-learn": sklearn.__version__, "umap-learn": umap.__version__,
            "networkx": networkx.__version__}


def build_topics(inputs: dict, chosen: dict, documents: list[str], *, selection: dict,
                 records: list[dict], gates=None, now=None, commit=None) -> dict:
    """`topics.json`'s content for the chosen configuration."""
    matrix = inputs["matrix"]
    instruments = inputs["instruments"]
    X, nonzero = normalised_rows(matrix)
    labels = np.asarray(chosen["labels"])
    counts, vocabulary = term_counts(documents)
    terms = ctfidf_terms(counts, vocabulary, labels)
    members = cluster_geometry(X, nonzero, labels)
    collection_of = instruments["coleccion"].to_numpy()
    topics = []
    for label in sorted(members, key=lambda label: (label < 0, label)):
        listed = members[label]
        composition: dict[str, int] = {}
        for i, _ in listed:
            composition[str(collection_of[i])] = composition.get(str(collection_of[i]), 0) + 1
        topics.append({
            "id": int(label),
            "size": len(listed),
            "terms": terms[label],
            "representatives": listed[:TOP_REPRESENTATIVES],
            "members": listed,
            "collections": dict(sorted(composition.items())),
        })
    meta = {
        "generated": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        "commit": commit if commit is not None else html.repository_commit(),
        "model": inputs["input"].get("model"),
        "method": inputs["input"].get("method", "dense"),
        "algorithm": chosen["algorithm"],
        "parameter_name": chosen["parameter_name"],
        "parameter": chosen["parameter"],
        "selection": selection,
        "seeds": dict(SEEDS),
        "umap": dict(UMAP_PARAMS) if chosen["algorithm"] == "hdbscan_umap" else None,
        "measures": {key: chosen[key] for key in
                     ("clusters", "noise", "largest", "silhouette", "comembership")},
        "instruments": int(len(instruments)),
        "zero_rows": int((~nonzero).sum()),
        "clusters": int(chosen["clusters"]),
        "noise_instruments": int((labels < 0).sum()),
        "gold_links": len(gold_pairs(inputs["gold"])),
        "gates": dict(GATES if gates is None else gates),
        "configurations": len(records),
        "labels": {"method": "c-TF-IDF", "ngram_range": list(NGRAM_RANGE),
                   "min_instruments": MIN_INSTRUMENTS, "top_terms": TOP_TERMS,
                   "representatives": TOP_REPRESENTATIVES, "stopwords": list(STOPWORDS),
                   "vocabulary": int(len(vocabulary)), "tokeniser": dict(TOKENISER)},
        "versions": library_versions(),
    }
    return {"meta": meta, "topics": topics}


def cluster(work_dir: Path, *, choose: str | None = None, force: bool = False,
            cache_dir=None, sweeps=None, gates=None, now=None, log=print) -> dict | None:
    """The whole run: sweeps, rule, labels, outputs. Returns `topics.json`'s content, or
    `None` when `.done` already stands and `force` is off. Raises `SystemExit` when
    no configuration passes the gates (the comparison is written first)."""
    work_dir = Path(work_dir)
    out = output_dir(work_dir)
    if (out / DONE_MARKER).exists() and not force:
        log(f"{out / DONE_MARKER} exists; nothing to do (--force recomputes)")
        return None
    inputs = load_inputs(work_dir)
    (out / DONE_MARKER).unlink(missing_ok=True)
    n = len(inputs["instruments"])
    zero_rows = int((np.linalg.norm(inputs["matrix"], axis=1) == 0).sum())
    log(f"{n} instruments, {zero_rows} zero row(s) of A, "
        f"{len(gold_pairs(inputs['gold']))} strong links")
    records = run_sweeps(inputs, sweeps=sweeps, log=log)

    if choose:
        chosen = parse_choice(choose, records)
        selection = {"mode": "choose", "choice": choose, "rule_would_pick": None}
        picked = select(records, gates)
        if picked is not None:
            selection["rule_would_pick"] = f"{picked['algorithm']}:{parameter_text(picked['parameter'])}"
    else:
        chosen = select(records, gates)
        selection = {"mode": "rule"}
    ranking = {"finished": sum(r["status"] == "ok" for r in records),
               "passing_the_gates": sum(not gate_failures(r, gates) for r in records)}
    selection.update(ranking)

    comparison = {"gates": dict(GATES if gates is None else gates), "selection": selection,
                  "chosen": None if chosen is None else
                  f"{chosen['algorithm']}:{parameter_text(chosen['parameter'])}",
                  "configurations": [public(r) for r in records]}
    atomic_write_table(out / "labels.parquet", labels_table(records))
    atomic_write_text(out / "comparison.json", json.dumps(comparison, indent=1) + "\n")
    atomic_write_text(out / "comparison.md", comparison_table(records, chosen, gates))
    if chosen is None:
        reasons = "\n".join(f"  {describe(r)} -> " + "; ".join(gate_failures(r, gates))
                            for r in records)
        raise SystemExit("no configuration passes the gates "
                         f"({gates or GATES}); the comparison is in {out}:\n{reasons}")

    log(f"chosen ({selection['mode']}): {describe(chosen)}")
    collections = [name for name in inputs["input"]["collections"]]
    documents = instrument_documents(work_dir, inputs["instruments"], collections=collections,
                                     cache_dir=cache_dir, log=log)
    topics = build_topics(inputs, chosen, documents, selection=selection, records=records,
                          gates=gates, now=now)
    atomic_write_text(out / "topics.json", json.dumps(topics, indent=1, ensure_ascii=False) + "\n")
    atomic_write_text(out / DONE_MARKER, topics["meta"]["generated"] + "\n")
    return topics


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--work-dir", type=Path, required=True,
                        help="a prepared work directory with a computed matrix and gold "
                             "(emb-run-atlas)")
    parser.add_argument("--choose", metavar="ALGORITHM:PARAMETER",
                        help="override the selection rule with an explicit, recorded choice "
                             "(e.g. louvain:1.0, kmeans:40)")
    parser.add_argument("--force", action="store_true", help="recompute over an existing .done")
    parser.add_argument("--cache-dir", type=Path, default=None,
                        help="the legalvec cache holding the units.parquet files "
                             "(default: $LEGALVEC_CACHE_DIR)")
    args = parser.parse_args(argv)
    topics = cluster(args.work_dir, choose=args.choose, force=args.force, cache_dir=args.cache_dir)
    if topics is not None:
        meta = topics["meta"]
        print(f"{meta['algorithm']} {meta['parameter_name']}={meta['parameter']}: "
              f"{meta['clusters']} topics, {meta['noise_instruments']} instruments in none; "
              f"measures {json.dumps(meta['measures'])}")
        print(f"written to {output_dir(args.work_dir)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

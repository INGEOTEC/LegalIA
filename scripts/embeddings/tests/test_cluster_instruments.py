"""Clustering the Atlas instruments into topics (issue #285).

    uv run --group viz pytest scripts/embeddings/tests -q

Synthetic data throughout: a two-collection, eight-instrument toy work directory
whose matrix and gold are written by hand (two obvious groups, so every measure
is derivable on paper), plus two-block random matrices for the algorithms
themselves. No Slurm, no network, no real UMAP fit (a stub returns the rows
themselves) — what is under test is the sweeps, the measures, the selection rule,
the c-TF-IDF labels and the outputs' contract.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cluster_instruments  # noqa: E402
import instrument_matrix  # noqa: E402
import prepare_umap_input  # noqa: E402
from test_instrument_matrix import (  # noqa: E402
    K, SLUG, index_of, stub_corpus_reader, write_units)

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
COLLECTIONS = ("leyes", "lineamientos")

#: Group one is about water, group two about health; each instrument carries two
#: articles with its own words, a word its group shares, and a heading every
#: instrument shares (a heading is never part of a label).
WATER = ("a", "b", "900", "901")
HEALTH = ("c", "d", "902", "903")
GROUP_WORDS = {"water": "agua", "health": "salud"}


def article_rows(clave: str, nombre: str, word: str):
    return [
        (clave, nombre, "article", "art_1", f"{clave}-1",
         f"El {word} es un bien comun {clave}uno"),
        (clave, nombre, "article", "art_2", f"{clave}-2",
         f"Todo {word} debe cuidarse segun {clave}dos"),
        (clave, nombre, "heading", "cap_1", "heading", "ENCABEZADO GENERAL"),
    ]


UNITS = {
    "leyes": [row for clave, nombre in (("a", "Ley A"), ("b", "Ley B"))
              for row in article_rows(clave, nombre, "agua")]
    + [row for clave, nombre in (("c", "Ley C"), ("d", "Ley D"))
       for row in article_rows(clave, nombre, "salud")],
    "lineamientos": [row for clave, nombre in (("900", "Lineamientos P"), ("901", "Lineamientos Q"))
                     for row in article_rows(clave, nombre, "agua")]
    + [row for clave, nombre in (("902", "Lineamientos R"), ("903", "Lineamientos S"))
       for row in article_rows(clave, nombre, "salud")],
}


def write_toy_vectors(directory: Path, name: str, hashes) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(len(name))
    pq.write_table(pa.table({
        "text_sha1": pa.array(list(hashes), type=pa.string()),
        "vector": pa.array([rng.normal(size=K).astype("float32").tolist() for _ in hashes],
                           type=pa.list_(pa.float32())),
    }), directory / name)


@pytest.fixture
def cache(tmp_path):
    root = tmp_path / "cache"
    for coleccion, tag in (("leyes", "scjn-leyes-vectors"),
                           ("lineamientos", "scjn-lineamientos-vectors")):
        directory = root / tag
        write_units(directory, coleccion, UNITS[coleccion])
        claves = sorted({row[0] for row in UNITS[coleccion]})
        for clave in claves:
            write_toy_vectors(directory, f"vectors-{clave}-{SLUG}-{K}.parquet",
                              [f"{clave}-1", f"{clave}-2", "heading"] if clave == claves[0]
                              else [f"{clave}-1", f"{clave}-2"])
        write_toy_vectors(directory, f"vectors-shared-{SLUG}-{K}.parquet", [])
    return root


def two_blocks(size: int = 30, seed: int = 0, noise: float = 0.02) -> np.ndarray:
    """A `size x size` matrix of two equal blocks: weights 1-3 inside a block, a
    little noise across, a zero diagonal."""
    rng = np.random.default_rng(seed)
    matrix = rng.random((size, size)) * noise
    half = size // 2
    for block in (slice(0, half), slice(half, size)):
        matrix[block, block] += rng.integers(1, 4, size=(half, half))
    np.fill_diagonal(matrix, 0.0)
    return matrix.astype(np.float32)


TRUTH = np.array([0] * 15 + [1] * 15)


@pytest.fixture
def toy_work(tmp_path, cache):
    """`prepare_umap_input.py`'s own output over the toy corpus, then a hand-written matrix
    (the two groups, each row weighing its three group mates) and gold."""
    work_dir = tmp_path / "work"
    prepare_umap_input.prepare(work_dir, collections=COLLECTIONS, cache_dir=cache,
                               unique_names=True, corpus_reader=stub_corpus_reader(),
                               log=lambda *a: None)
    index = index_of(work_dir)
    matrix = np.zeros((8, 8), dtype=np.float32)
    for group in (WATER, HEALTH):
        for number, left in enumerate(group):
            for other, right in enumerate(group):
                if left != right:
                    # Slightly different weights, so no two rows are identical.
                    matrix[index[left], index[right]] = 1.0 + 0.1 * ((number + other) % 3)
    out = instrument_matrix.output_dir(work_dir)
    out.mkdir(parents=True, exist_ok=True)
    np.save(out / "matrix.npy", matrix)
    (out / "matrix.json").write_text(json.dumps({"counted_rows": 16}), encoding="utf-8")
    # The gold lives outside the work directory, as the real one does (review fix-1
    # of #285: `emb-run-atlas-4b/` has no `gold-links/`).
    gold = gold_of(work_dir)
    gold.parent.mkdir()
    gold.write_text(json.dumps({"provenance": {"commit": "abc1234", "instruments": 8}, "entries": [
        {"i": index["a"], "targets": [index["b"], index["c"]]},   # one inside, one across
        {"i": index["c"], "targets": [index["902"]]},             # inside
    ]}), encoding="utf-8")
    return work_dir


def gold_of(work_dir: Path) -> Path:
    """Where the toy gold is written: next to the work directory, not in it."""
    return work_dir.parent / "gold" / "gold.json"


#: The toy run's sweeps and gates: two or three clusters of eight, no noise.
TOY_SWEEPS = {"agglomerative": ("n_clusters", (2, 4))}
TOY_GATES = {"min_clusters": 2, "max_clusters": 4, "max_noise": 0.5, "max_largest": 0.6}


@pytest.fixture(autouse=True)
def stub_umap(monkeypatch):
    """No real UMAP fit: the 'embedding' is the normalised rows."""
    monkeypatch.setattr(cluster_instruments, "umap_embedding", lambda X: np.asarray(X))


def run(work_dir, cache, **kwargs):
    kwargs.setdefault("sweeps", TOY_SWEEPS)
    kwargs.setdefault("gates", TOY_GATES)
    kwargs.setdefault("now", NOW)
    kwargs.setdefault("gold", gold_of(work_dir))
    return cluster_instruments.cluster(work_dir, cache_dir=cache, log=lambda *a: None, **kwargs)


# -- the spec, pinned -------------------------------------------------------- #

def test_the_sweeps_are_issue_285s_configurations():
    # The issue says 21; its own five + six + six + five values make 22.
    configurations = cluster_instruments.configurations()
    assert len(configurations) == 22
    assert cluster_instruments.SWEEPS == {
        "hdbscan_umap": ("min_cluster_size", (5, 10, 15, 20, 30)),
        "agglomerative": ("n_clusters", (20, 30, 40, 50, 60, 80)),
        "kmeans": ("n_clusters", (20, 30, 40, 50, 60, 80)),
        "louvain": ("resolution", (0.5, 1.0, 1.5, 2.0, 3.0)),
    }
    assert cluster_instruments.GATES == {"min_clusters": 20, "max_clusters": 80,
                                         "max_noise": 0.15, "max_largest": 0.25}
    assert cluster_instruments.UMAP_PARAMS == {"n_components": 5, "n_neighbors": 15,
                                               "min_dist": 0.0, "metric": "cosine",
                                               "random_state": 0}


# -- the four algorithms ------------------------------------------------------ #

@pytest.fixture
def blocks():
    matrix = two_blocks()
    X, _ = cluster_instruments.normalised_rows(matrix)
    return matrix, X


def test_every_algorithm_recovers_the_two_groups(blocks):
    matrix, X = blocks
    found = {
        "hdbscan": cluster_instruments.hdbscan_labels(X, 5),
        "agglomerative": cluster_instruments.agglomerative_labels(X, 2),
        "kmeans": cluster_instruments.kmeans_labels(X, 2),
        "louvain": cluster_instruments.louvain_labels(matrix, 1.0),
    }
    for name, labels in found.items():
        assert len(labels) == 30, name
        assert cluster_instruments.canonical_labels(labels).tolist() == TRUTH.tolist(), name


def test_canonical_labels_number_by_size_and_keep_noise():
    labels = cluster_instruments.canonical_labels([5, 5, -1, 9, 9, 9, 2])
    assert labels.tolist() == [1, 1, -1, 0, 0, 0, 2]
    # A tie in size goes to the cluster whose first member comes first.
    assert cluster_instruments.canonical_labels([7, 3, 7, 3]).tolist() == [0, 1, 0, 1]


def test_louvain_uses_the_raw_matrix_and_ignores_the_diagonal_and_direction():
    matrix = np.zeros((4, 4), dtype=np.float32)
    matrix[0, 1] = 5.0          # only one direction of the pair is written
    matrix[2, 3] = 5.0
    matrix[0, 0] = 99.0         # a self weight must not matter
    labels = cluster_instruments.louvain_labels(matrix, 1.0)
    assert labels[0] == labels[1] and labels[2] == labels[3] and labels[0] != labels[2]


def test_zero_rows_stay_zero_and_are_flagged():
    matrix = np.array([[0, 3, 4], [0, 0, 0], [1, 0, 0]], dtype=np.float32)
    X, nonzero = cluster_instruments.normalised_rows(matrix)
    assert nonzero.tolist() == [True, False, True]
    assert X.dtype == np.float32
    assert np.allclose(np.linalg.norm(X, axis=1), [1.0, 0.0, 1.0])
    assert np.allclose(X[0], [0, 0.6, 0.8])


# -- the measures --------------------------------------------------------------- #

def test_the_measures_are_computed_as_defined(blocks):
    matrix, X = blocks
    labels = np.array([0] * 15 + [1] * 15)
    pairs = [(0, 1), (2, 20), (16, 17), (3, 25)]      # inside, across, inside, across
    got = cluster_instruments.measures(X, labels, pairs)
    assert got["clusters"] == 2
    assert got["noise"] == 0.0
    assert got["largest"] == 0.5
    assert got["comembership"] == 0.5
    assert got["silhouette"] > 0.3          # two separated blocks

    noisy = labels.copy()
    noisy[1] = -1                           # the pair (0, 1) is no longer together
    got = cluster_instruments.measures(X, noisy, pairs)
    assert got["noise"] == 1 / 30
    assert got["comembership"] == 0.25      # only (16, 17) is left
    assert got["clusters"] == 2


def test_comembership_counts_a_noise_pair_as_apart():
    labels = np.array([-1, -1, 0, 0])
    assert cluster_instruments.comembership(labels, [(0, 1), (2, 3)]) == 0.5
    assert cluster_instruments.comembership(labels, []) is None


def test_silhouette_is_undefined_for_one_cluster(blocks):
    _, X = blocks
    assert cluster_instruments.silhouette(X, np.zeros(30, dtype=int)) is None
    assert cluster_instruments.silhouette(X, np.full(30, -1)) is None


def test_gold_pairs_are_the_entries_targets_once_each():
    gold = {"entries": [{"i": 1, "targets": [2, 3]}, {"i": 1, "targets": [2]},
                        {"i": 4, "targets": []}]}
    assert cluster_instruments.gold_pairs(gold) == [(1, 2), (1, 3)]


# -- the selection rule -------------------------------------------------------- #

def record(algorithm="kmeans", parameter=20, clusters=30, noise=0.0, largest=0.1,
           silhouette=0.05, comembership=0.5, status="ok"):
    if status != "ok":
        return {"algorithm": algorithm, "parameter_name": "n_clusters", "parameter": parameter,
                "status": status, "error": "boom"}
    return {"algorithm": algorithm, "parameter_name": "n_clusters", "parameter": parameter,
            "status": "ok", "clusters": clusters, "noise": noise, "largest": largest,
            "silhouette": silhouette, "comembership": comembership}


def test_the_gates_decide_who_can_win():
    records = [
        record("kmeans", 5, clusters=10, comembership=0.99),                    # too few clusters
        record("kmeans", 6, clusters=90, comembership=0.99),                    # too many
        record("kmeans", 7, noise=0.2, comembership=0.99),                      # too much noise
        record("kmeans", 8, largest=0.5, comembership=0.99),                    # one giant cluster
        record("kmeans", 9, status="failed"),
        record("louvain", 10, comembership=0.40),
    ]
    assert cluster_instruments.select(records)["parameter"] == 10
    failures = [cluster_instruments.gate_failures(r) for r in records]
    assert all(failures[:5]) and failures[5] == []
    assert "10 clusters" in failures[0][0] and "noise" in failures[2][0]
    assert "largest" in failures[3][0] and "failed" in failures[4][0]


def test_a_gate_is_inclusive():
    exactly = record(clusters=20, noise=0.15, largest=0.25)
    assert cluster_instruments.gate_failures(exactly) == []
    assert cluster_instruments.gate_failures(record(clusters=80)) == []


def test_the_highest_comembership_wins_then_silhouette_then_fewer_clusters_then_name():
    pick = cluster_instruments.select
    assert pick([record("kmeans", 1, comembership=0.5), record("louvain", 2, comembership=0.6)]
                )["algorithm"] == "louvain"
    assert pick([record("kmeans", 1, silhouette=0.1), record("louvain", 2, silhouette=0.2)]
                )["algorithm"] == "louvain"
    assert pick([record("kmeans", 1, clusters=40), record("louvain", 2, clusters=30)]
                )["algorithm"] == "louvain"
    assert pick([record("louvain", 1), record("agglomerative", 2)]
                )["algorithm"] == "agglomerative"
    assert pick([record("agglomerative", 5, silhouette=None, comembership=0.5),
                 record("kmeans", 6, silhouette=-0.1, comembership=0.5)])["algorithm"] == "kmeans"


def test_no_configuration_passing_is_none():
    assert cluster_instruments.select([record(clusters=5), record(status="failed")]) is None
    assert cluster_instruments.select([]) is None


def test_choose_names_one_configuration_and_refuses_the_rest():
    records = [record("kmeans", 20), record("louvain", 1.0, comembership=0.9),
               record("kmeans", 30, status="failed")]
    assert cluster_instruments.parse_choice("louvain:1.0", records) is records[1]
    with pytest.raises(SystemExit, match="no such configuration"):
        cluster_instruments.parse_choice("louvain:9", records)
    with pytest.raises(SystemExit, match="failed"):
        cluster_instruments.parse_choice("kmeans:30", records)


# -- the sweeps ---------------------------------------------------------------- #

def test_a_failed_configuration_is_recorded_and_the_sweep_goes_on(toy_work, monkeypatch):
    def boom(X, n_clusters):
        raise RuntimeError("no memory")

    monkeypatch.setattr(cluster_instruments, "kmeans_labels", boom)
    inputs = cluster_instruments.load_inputs(toy_work, gold_of(toy_work))
    records = cluster_instruments.run_sweeps(
        inputs, sweeps={"kmeans": ("n_clusters", (2,)), "agglomerative": ("n_clusters", (2,))},
        log=lambda *a: None)
    assert [r["status"] for r in records] == ["failed", "ok"]
    assert records[0]["error"] == "RuntimeError: no memory"
    assert "labels" not in records[0] and len(records[1]["labels"]) == 8


def test_every_run_is_deterministic(toy_work):
    inputs = cluster_instruments.load_inputs(toy_work, gold_of(toy_work))
    sweeps = {"kmeans": ("n_clusters", (2,)), "louvain": ("resolution", (1.0,)),
              "hdbscan_umap": ("min_cluster_size", (2,))}
    first = cluster_instruments.run_sweeps(inputs, sweeps=sweeps, log=lambda *a: None)
    second = cluster_instruments.run_sweeps(inputs, sweeps=sweeps, log=lambda *a: None)
    for one, other in zip(first, second):
        assert one["status"] == other["status"]
        if one["status"] == "ok":
            assert one["labels"].tolist() == other["labels"].tolist()


def test_a_zero_row_is_noise_in_every_algorithm(toy_work):
    index = index_of(toy_work)
    out = instrument_matrix.output_dir(toy_work)
    matrix = np.load(out / "matrix.npy")
    matrix[index["903"], :] = 0.0
    matrix[:, index["903"]] = 0.0
    np.save(out / "matrix.npy", matrix)
    inputs = cluster_instruments.load_inputs(toy_work, gold_of(toy_work))
    records = cluster_instruments.run_sweeps(
        inputs, sweeps={"agglomerative": ("n_clusters", (2,)), "louvain": ("resolution", (1.0,))},
        log=lambda *a: None)
    for result in records:
        assert result["status"] == "ok"
        assert result["labels"][index["903"]] == -1
        assert result["noise"] == 1 / 8


# -- the labels --------------------------------------------------------------- #

def test_ctfidf_ranks_the_distinctive_word_first():
    vocabulary = np.array(["agua", "bien", "salud"])
    #                  agua bien salud
    counts = np.array([[6, 3, 0],      # instruments 0, 1: cluster 0
                       [4, 3, 0],
                       [0, 3, 7],      # instruments 2, 3: cluster 1
                       [0, 3, 5]])
    import scipy.sparse as sp

    terms = cluster_instruments.ctfidf_terms(sp.csr_matrix(counts), vocabulary,
                                             [0, 0, 1, 1], top=2)
    assert terms[0][0] == "agua" and terms[1][0] == "salud"
    # `bien` is in every cluster: its idf is the lowest, whatever its raw count.
    assert terms[0][1] == "bien"


def test_the_tokeniser_is_the_bm25_one():
    assert cluster_instruments.tokenize("El AGUA, a 2 de 15 Mil!") == ["el", "agua", "de", "15", "mil"]


def test_instrument_documents_leave_headings_out(toy_work, cache):
    from cluster_instruments import instrument_documents
    import pyarrow.parquet as pq_

    instruments = pq_.read_table(toy_work / "instruments.parquet").to_pandas()
    documents = instrument_documents(toy_work, instruments, collections=COLLECTIONS,
                                     cache_dir=cache, log=lambda *a: None)
    assert len(documents) == 8
    index = index_of(toy_work)
    assert "bien comun auno" in documents[index["a"]]
    assert all("ENCABEZADO" not in document for document in documents)


# -- the run ------------------------------------------------------------------ #

def test_the_toy_run_picks_the_two_groups_and_labels_them(toy_work, cache):
    topics = run(toy_work, cache)
    meta = topics["meta"]
    assert (meta["algorithm"], meta["parameter"]) == ("agglomerative", 2)
    assert meta["selection"] == {"mode": "rule", "finished": 2, "passing_the_gates": 2}
    assert meta["measures"]["comembership"] == pytest.approx(2 / 3)   # (a, c) is across
    assert meta["clusters"] == 2 and meta["noise_instruments"] == 0 and meta["zero_rows"] == 0
    assert meta["generated"] == NOW.isoformat(timespec="seconds")
    assert meta["method"] == "dense" and meta["model"] == "Qwen/Qwen3-Embedding-0.6B"
    assert meta["labels"]["stopwords"] == list(cluster_instruments.STOPWORDS)

    index = index_of(toy_work)
    by_term = {topic["terms"][0]: topic for topic in topics["topics"]}
    assert set(by_term) == {"agua", "salud"}
    assert {i for i, _ in by_term["agua"]["members"]} == {index[c] for c in WATER}
    assert {i for i, _ in by_term["salud"]["members"]} == {index[c] for c in HEALTH}
    assert by_term["agua"]["collections"] == {"leyes": 2, "lineamientos": 2}
    for topic in topics["topics"]:
        distances = [distance for _, distance in topic["members"]]
        assert distances == sorted(distances)
        assert topic["representatives"] == topic["members"][:3]
        assert topic["size"] == len(topic["members"]) == 4
        assert "encabezado" not in topic["terms"]
        assert not set(topic["terms"]) & set(cluster_instruments.STOPWORDS)


def test_every_instrument_is_in_exactly_one_cluster(toy_work, cache):
    topics = run(toy_work, cache)
    members = [i for topic in topics["topics"] for i, _ in topic["members"]]
    assert sorted(members) == list(range(8))
    assert sum(topic["size"] for topic in topics["topics"]) == 8


def test_choose_overrides_the_rule_and_meta_records_it(toy_work, cache):
    topics = run(toy_work, cache, choose="agglomerative:4")
    meta = topics["meta"]
    assert (meta["algorithm"], meta["parameter"], meta["clusters"]) == ("agglomerative", 4, 4)
    assert meta["selection"]["mode"] == "choose"
    assert meta["selection"]["choice"] == "agglomerative:4"
    assert meta["selection"]["rule_would_pick"] == "agglomerative:2"


def test_the_outputs_exist_and_the_comparison_has_one_record_per_configuration(toy_work, cache):
    run(toy_work, cache)
    out = cluster_instruments.output_dir(toy_work)
    assert sorted(path.name for path in out.iterdir()) == [
        ".done", "comparison.json", "comparison.md", "labels.parquet", "topics.json"]
    comparison = json.loads((out / "comparison.json").read_text(encoding="utf-8"))
    assert [(c["algorithm"], c["parameter"]) for c in comparison["configurations"]] == [
        ("agglomerative", 2), ("agglomerative", 4)]
    assert all("labels" not in c for c in comparison["configurations"])
    assert comparison["chosen"] == "agglomerative:2"
    table = pq.read_table(out / "labels.parquet").to_pandas()
    assert list(table.columns) == ["i", "algorithm", "parameter", "label"]
    assert len(table) == 16 and sorted(table["parameter"].unique()) == ["2", "4"]
    markdown = (out / "comparison.md").read_text(encoding="utf-8")
    assert markdown.count("\n") == 4 and "**yes**" in markdown


def test_a_second_run_is_byte_identical_apart_from_generated(toy_work, cache):
    out = cluster_instruments.output_dir(toy_work)
    run(toy_work, cache)
    first = json.loads((out / "topics.json").read_text(encoding="utf-8"))
    later = datetime(2027, 1, 1, tzinfo=timezone.utc)
    run(toy_work, cache, force=True, now=later)
    second = json.loads((out / "topics.json").read_text(encoding="utf-8"))
    assert second["meta"].pop("generated") != first["meta"].pop("generated")
    assert json.dumps(first) == json.dumps(second)


def test_done_returns_and_force_recomputes(toy_work, cache):
    out = cluster_instruments.output_dir(toy_work)
    run(toy_work, cache)
    before = (out / "topics.json").read_text(encoding="utf-8")
    assert run(toy_work, cache, now=datetime(2030, 1, 1, tzinfo=timezone.utc)) is None
    assert (out / "topics.json").read_text(encoding="utf-8") == before
    assert run(toy_work, cache, force=True, now=datetime(2030, 1, 1, tzinfo=timezone.utc))
    assert (out / "topics.json").read_text(encoding="utf-8") != before


def test_an_interrupted_run_leaves_no_done_and_no_partial_file(toy_work, cache, monkeypatch):
    out = cluster_instruments.output_dir(toy_work)
    run(toy_work, cache)

    def boom(*args, **kwargs):
        raise RuntimeError("killed")

    monkeypatch.setattr(cluster_instruments, "build_topics", boom)
    with pytest.raises(RuntimeError):
        run(toy_work, cache, force=True)
    assert not (out / ".done").exists()      # a stale marker would mislead the next run
    assert not list(out.glob("*.parcial"))


def test_no_configuration_passing_the_gates_is_a_finding_not_a_softening(toy_work, cache):
    out = cluster_instruments.output_dir(toy_work)
    with pytest.raises(SystemExit) as stopped:
        run(toy_work, cache, gates={**TOY_GATES, "min_clusters": 20, "max_clusters": 80})
    message = str(stopped.value)
    assert "no configuration passes the gates" in message
    assert "agglomerative n_clusters=2" in message and "outside 20-80" in message
    # The comparison is the evidence; there is no topics.json and no `.done`.
    assert (out / "comparison.json").exists() and (out / "comparison.md").exists()
    assert not (out / "topics.json").exists() and not (out / ".done").exists()
    chosen = json.loads((out / "comparison.json").read_text(encoding="utf-8"))["chosen"]
    assert chosen is None


def test_a_zero_row_is_the_noise_cluster_in_topics_json(toy_work, cache):
    index = index_of(toy_work)
    out = instrument_matrix.output_dir(toy_work)
    matrix = np.load(out / "matrix.npy")
    matrix[index["903"], :] = 0.0
    matrix[:, index["903"]] = 0.0
    np.save(out / "matrix.npy", matrix)
    topics = run(toy_work, cache)
    assert topics["meta"]["zero_rows"] == 1
    noise = topics["topics"][-1]
    assert noise["id"] == -1 and noise["size"] == 1
    assert noise["members"] == [[index["903"], 1.0]]
    assert [topic["id"] for topic in topics["topics"][:-1]] == list(range(len(topics["topics"]) - 1))
    assert sum(topic["size"] for topic in topics["topics"]) == 8


def test_a_work_directory_without_a_matrix_is_refused_by_name(toy_work, cache):
    (instrument_matrix.output_dir(toy_work) / "matrix.npy").unlink()
    with pytest.raises(SystemExit, match="matrix.npy is missing"):
        run(toy_work, cache)


def test_a_missing_gold_is_refused_by_name(toy_work, cache):
    gold_of(toy_work).unlink()
    with pytest.raises(SystemExit, match="gold.json is missing"):
        run(toy_work, cache)


def test_a_gold_built_for_other_instruments_is_refused(toy_work, cache):
    """Review fix-1 of #285: the gold is shared by every work directory, so its
    instrument count must be this one's, or its `i` would mean other instruments."""
    record = json.loads(gold_of(toy_work).read_text(encoding="utf-8"))
    record["provenance"]["instruments"] = 1303
    gold_of(toy_work).write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(SystemExit, match="built for 1303 instruments but .* lists 8"):
        run(toy_work, cache)
    record.pop("provenance")
    gold_of(toy_work).write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(SystemExit, match="built for None instruments"):
        run(toy_work, cache)


def test_the_default_gold_is_the_one_evaluate_links_reads():
    import evaluate_links

    assert cluster_instruments.DEFAULT_GOLD == evaluate_links.DEFAULT_GOLD == Path(
        "emb-run-atlas/gold-links/gold.json")


def test_the_gold_path_and_commit_are_recorded_and_no_gold_is_needed_in_the_work_dir(
        toy_work, cache):
    assert not (toy_work / "gold-links").exists()
    meta = run(toy_work, cache)["meta"]
    assert meta["gold"] == {"path": str(gold_of(toy_work)), "commit": "abc1234",
                            "instruments": 8}
    assert meta["gold_links"] == 3


def test_a_matrix_of_the_wrong_size_is_refused(toy_work, cache):
    out = instrument_matrix.output_dir(toy_work)
    np.save(out / "matrix.npy", np.zeros((3, 3), dtype=np.float32))
    with pytest.raises(SystemExit, match="3 x 3"):
        run(toy_work, cache)


def test_the_command_line_prints_the_chosen_configuration(toy_work, cache, capsys, monkeypatch):
    monkeypatch.setattr(cluster_instruments, "SWEEPS", TOY_SWEEPS)
    monkeypatch.setattr(cluster_instruments, "GATES", TOY_GATES)
    assert cluster_instruments.main(["--work-dir", str(toy_work), "--cache-dir", str(cache),
                                     "--gold", str(gold_of(toy_work))]) == 0
    printed = capsys.readouterr().out
    assert "chosen (rule): agglomerative n_clusters=2" in printed
    assert "2 topics, 0 instruments in none" in printed

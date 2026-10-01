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
import pandas as pd
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
    # Issue #259's fix: a text only a *heading* carries. It is nearly `t2`
    # (cosine 0.9999), so were headings candidates it would beat `t1` as `b`/t2's
    # nearest foreign text, and `c` would be credited.
    "th": [0.9, 0.11, 0.0, 0.0],
    # `902`'s second unit: nearest to the shared `ts` (0.99), not identical.
    "t6": [0.8, 0.6, 0.0, 0.0],
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
        # Headings are out of the comparison (issue #259's fix): `c`'s carries a
        # text nobody else has, `a`'s carries `b`'s own `t2`.
        ("c", "Ley C", "heading", "cap_1", "th", "CAPITULO I"),
        ("a", "Ley A", "heading", "cap_9", "t2", "texto t2"),
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
        ("902", "Lineamientos R", "article", "art_2", "t6", "texto t6"),
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
    write_vectors(leyes, f"vectors-c-{SLUG}-{K}.parquet", ["t3", "th"])
    write_vectors(leyes, f"vectors-shared-{SLUG}-{K}.parquet", ["ts", "tb"])

    lineamientos = root / "scjn-lineamientos-vectors"
    write_units(lineamientos, "lineamientos", UNITS["lineamientos"])
    write_vectors(lineamientos, f"vectors-900-{SLUG}-{K}.parquet", ["t4", "t1"])
    write_vectors(lineamientos, f"vectors-901-{SLUG}-{K}.parquet", ["t5"])
    write_vectors(lineamientos, f"vectors-902-{SLUG}-{K}.parquet", ["tb", "t6"])
    write_vectors(lineamientos, f"vectors-shared-{SLUG}-{K}.parquet", [])
    return root


def stub_corpus_reader(dates=None):
    """`unique_instruments.first_publication_dates`' `reader` hook: every
    instrument has one snapshot, dated `dates[clave]` (2000 by default), so no
    tarball of the SCJN release is needed."""
    dates = dates or {}

    def reader(coleccion):
        def read(clave, cache_dir=None):
            return {"id_ordenamiento": clave,
                    "snapshots": [{"fecha_publicacion": dates.get(clave, "01-01-2000")}]}
        return read
    return reader


@pytest.fixture
def prepared(tmp_path, cache):
    """`prepare_umap_input.py`'s own output over the toy corpus — the work
    directory this issue reads, prepared the way the Atlas is (`--unique-names`,
    issue #259; no toy instrument shares a name, so nothing is dropped)."""
    work_dir = tmp_path / "work"
    prepare_umap_input.prepare(work_dir, collections=COLLECTIONS, cache_dir=cache,
                               unique_names=True, corpus_reader=stub_corpus_reader(),
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
    """Sixteen unit rows (headings included: `unit_rows` is the join, the
    exclusion happens in `build_matrix`), with `coleccion`, `clave`
    and `unit_type` decoded back from the compact codes the shared join
    emits."""
    units = instrument_matrix.unit_rows(prepared, collections=COLLECTIONS,
                                        cache_dir=cache, log=lambda *a: None)
    assert len(units) == 16
    assert list(units.columns) == ["i", "coleccion", "clave", "unit_type", "eId", "row",
                                   "transitorio"]
    assert not units["transitorio"].any()      # the toy table has no `path`
    assert sorted(units["coleccion"].unique()) == ["leyes", "lineamientos"]
    assert sorted(units["clave"].unique()) == ["900", "901", "902", "a", "b", "c"]
    assert sorted(units["unit_type"].unique()) == ["article", "heading"]
    # The shared transitorio: one vector row, three unit rows (one in `a`,
    # two in `b`).
    shared = units[units["eId"].isin(["art_2", "cap_1"]) & (units["clave"].isin(["a", "b"]))]
    assert shared["row"].nunique() == 1
    assert len(shared) == 3


def searched_units(prepared, cache):
    """The unit rows `build_matrix` searches: everything but the headings."""
    units = instrument_matrix.unit_rows(prepared, collections=COLLECTIONS,
                                        cache_dir=cache, log=lambda *a: None)
    return units[~units["unit_type"].isin(instrument_matrix.EXCLUDED_UNIT_TYPES)]


def test_owners_are_the_instruments_that_carry_a_text(prepared, cache):
    units = searched_units(prepared, cache)
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


def test_a_text_owned_by_a_heading_and_an_article_is_owned_by_the_article_only(
        prepared, cache):
    """`a` carries `t2` in a heading, `b` in an article: only `b` owns it."""
    all_units = instrument_matrix.unit_rows(prepared, collections=COLLECTIONS,
                                            cache_dir=cache, log=lambda *a: None)
    vectors = np.load(prepared / "vectors.npy")
    index = index_of(prepared)
    row = int(all_units.loc[(all_units["clave"] == "b") & (all_units["eId"] == "art_1"),
                            "row"].iloc[0])
    with_headings = instrument_matrix.owners_of_rows(all_units, vectors.shape[0])
    assert sorted(with_headings[row]) == sorted([index["a"], index["b"]])
    owners = instrument_matrix.owners_of_rows(searched_units(prepared, cache),
                                              vectors.shape[0])
    assert owners[row] == [index["b"]]


def test_a_text_only_headings_own_has_no_owner(prepared, cache):
    all_units = instrument_matrix.unit_rows(prepared, collections=COLLECTIONS,
                                            cache_dir=cache, log=lambda *a: None)
    vectors = np.load(prepared / "vectors.npy")
    row = int(all_units.loc[(all_units["clave"] == "c")
                            & (all_units["unit_type"] == "heading"), "row"].iloc[0])
    owners = instrument_matrix.owners_of_rows(searched_units(prepared, cache),
                                              vectors.shape[0])
    assert owners[row] == []


# -- the matrix ------------------------------------------------------------ #

def test_the_matrix_is_the_hand_computed_one(prepared, cache):
    """Every weight below is derivable from `VECTORS` with a pen — each
    *counted* unit row hands out a total of 1, split over the instruments it
    credits. Sixteen unit rows: three headings are not searched, and four
    word-for-word matches shared by several instruments are not counted.

    * `a`/t1 -> the identical text in `900` (cosine 1, other collection,
      `m == 1`): 1.
    * `a`/ts -> the text it shares with `b` (cosine 1, `m == 1`): 1.
    * `a`/tb, `b`/tb, `c`/tb, `902`/tb -> the boilerplate row, at cosine 1,
      owned by three instruments (`m == 3`): **dropped**, adds nothing.
    * `b`/t2 -> t1, which exists twice at the same cosine (0.994): a tie, 1/2 to
      `a` *and* 1/2 to `900`. `c`'s heading text `th` (0.99998) would win if
      headings were candidates; it is not one.
    * `b`/ts (article) -> `a`: 1.
    * `c`/t3 -> the shared text, owned by `a` and `b`: 1/2 to each.
    * `900`/t4 -> t5 in `901` (0.282, above t1's 0.196); `900`/t1 -> the
      identical t1 in `a`: 1.
    * `901`/t5 -> t4 in `900`, the same 0.282 the other way round: 1.
    * `902`/t6 -> the shared `ts` (0.99, not identical), owned by `a` and `b`:
      1/2 to each.
    """
    result = computed(prepared, cache)
    index, matrix = result["index"], result["matrix"]
    expected = np.zeros_like(matrix)
    expected[index["a"], index["900"]] = 1
    expected[index["a"], index["b"]] = 1
    expected[index["b"], index["a"]] = 0.5 + 1
    expected[index["b"], index["900"]] = 0.5
    expected[index["c"], index["a"]] = 0.5
    expected[index["c"], index["b"]] = 0.5
    expected[index["900"], index["a"]] = 1
    expected[index["900"], index["901"]] = 1
    expected[index["901"], index["900"]] = 1
    expected[index["902"], index["a"]] = 0.5
    expected[index["902"], index["b"]] = 0.5
    assert matrix.dtype == np.float32
    np.testing.assert_allclose(matrix, expected, atol=1e-6)
    assert np.trace(matrix) == 0
    assert (matrix >= 0).all()
    # The identity: one *counted* unit row, one unit of weight.
    nearest = result["nearest"]
    counted = nearest[nearest["counted"]]
    assert abs(matrix.sum() - len(counted)) < 1e-4
    np.testing.assert_allclose(matrix.sum(axis=1),
                               counted.groupby("i").size().reindex(
                                   range(matrix.shape[0]), fill_value=0).to_numpy(),
                               atol=1e-3)


def test_headings_are_neither_sources_nor_winners(prepared, cache):
    result = computed(prepared, cache)
    nearest, index, matrix = result["nearest"], result["index"], result["matrix"]
    assert "heading" not in set(nearest["unit_type"])
    assert len(nearest) == 13                      # 16 unit rows, 3 headings
    # `th`, the text only `c`'s heading carries, is nearly `t2`: `b`/t2's
    # winners are still the two copies of t1, and `c` is credited nothing by it.
    tied = nearest[(nearest["clave"] == "b") & (nearest["eId"] == "art_1")].iloc[0]
    assert sorted(tied["targets"]) == sorted([index["a"], index["900"]])
    assert tied["similarity"] < 0.999
    assert matrix[index["b"], index["c"]] == 0
    summary = result["summary"]
    assert summary["excluded_unit_types"] == ["heading"]
    assert summary["heading_rows_excluded"] == 3
    assert summary["unit_rows"] == 16
    assert summary["searched_rows"] == 13


def test_a_winner_owned_by_three_instruments_and_identical_is_dropped(prepared, cache):
    """`902`/tb finds *one* winning vector row — no tie at all — which three
    leyes own because they all carry "Se deroga.". It is a word-for-word match
    (cosine 1) shared by several instruments (`m == 3`): it is kept in
    `nearest.parquet` as evidence but adds nothing to `A`, and is not
    re-credited to a next-nearest text."""
    result = computed(prepared, cache)
    nearest, index, matrix = result["nearest"], result["index"], result["matrix"]
    boilerplate = nearest[(nearest["clave"] == "902") & (nearest["eId"] == "art_1")].iloc[0]
    assert boilerplate["similarity"] == pytest.approx(1.0)
    assert boilerplate["n_winners"] == 1          # one vector row ...
    assert boilerplate["m"] == 3                  # ... owned by three instruments
    assert boilerplate["weight"] == pytest.approx(1 / 3)
    assert not boilerplate["counted"]
    assert sorted(boilerplate["targets"]) == sorted([index["a"], index["b"], index["c"]])
    for clave in ("a", "b", "c"):
        assert matrix[index["902"], index[clave]] == pytest.approx(0.5 if clave != "c" else 0)
    assert matrix[index["902"]].sum() == pytest.approx(1.0, abs=1e-6)   # only `t6`
    dropped = nearest[~nearest["counted"]]
    assert sorted(dropped["clave"]) == ["902", "a", "b", "c"]
    assert (dropped["similarity"] >= 1 - 1e-6).all() and (dropped["m"] > 1).all()
    summary = result["summary"]
    assert summary["identical_shared_dropped"] == 4
    assert summary["counted_rows"] == 9
    assert summary["max_m"] == 3
    assert summary["m"]["3-5"] == 4


def test_an_identical_text_owned_by_one_other_instrument_still_counts(prepared, cache):
    result = computed(prepared, cache)
    nearest, index = result["nearest"], result["index"]
    for clave, eid, target in (("a", "art_1", "900"), ("a", "art_2", "b"),
                               ("900", "art_2", "a")):
        row = nearest[(nearest["clave"] == clave) & (nearest["eId"] == eid)].iloc[0]
        assert row["similarity"] == pytest.approx(1.0)
        assert row["m"] == 1 and row["counted"]
        assert list(row["targets"]) == [index[target]]
        assert result["matrix"][index[clave], index[target]] >= 1


def test_a_non_identical_tie_over_two_instruments_still_gives_half_each(prepared, cache):
    result = computed(prepared, cache)
    nearest, index, matrix = result["nearest"], result["index"], result["matrix"]
    row = nearest[(nearest["clave"] == "902") & (nearest["eId"] == "art_2")].iloc[0]
    assert row["similarity"] < 1.0 - 1e-3
    assert row["m"] == 2 and row["counted"]
    assert matrix[index["902"], index["a"]] == pytest.approx(0.5)
    assert matrix[index["902"], index["b"]] == pytest.approx(0.5)


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


def test_every_unit_row_carries_its_own_weight(prepared, cache):
    """`weight` is `1/m` on every searched row, and the rule is named in
    `matrix.json`."""
    result = computed(prepared, cache)
    nearest, summary = result["nearest"], result["summary"]
    assert (nearest["m"] >= 1).all()
    np.testing.assert_allclose(nearest["weight"].to_numpy(),
                               1.0 / nearest["m"].to_numpy(), rtol=1e-6)
    assert summary["weighting"] == "1/m"
    assert summary["matrix_dtype"] == "float32"
    assert summary["row_sums_equal_counted_rows"] is True
    assert "row_sums_equal_units" not in summary
    assert set(summary["m"]) == {"1", "2", "3-5", "6-20", "21-100", "101+"}
    assert summary["matrix_sum"] == pytest.approx(summary["counted_rows"], abs=1e-3)
    assert summary["counted_rows"] == summary["searched_rows"] - summary[
        "identical_shared_dropped"]


def test_a_text_repeated_inside_an_instrument_counts_once_per_unit_row(prepared, cache):
    """`b` carries the shared transitorio in two unit rows of the same text
    (an `article` and a `heading`): only the article is searched, so it counts
    once — the heading is out of the comparison altogether."""
    result = computed(prepared, cache)
    nearest, index = result["nearest"], result["index"]
    shared_row = int(nearest.loc[(nearest["clave"] == "a")
                                 & (nearest["eId"] == "art_2"), "row"].iloc[0])
    repeats = nearest[(nearest["clave"] == "b") & (nearest["row"] == shared_row)]
    assert len(repeats) == 1
    np.testing.assert_allclose(repeats["weight"].to_numpy(), 1.0)   # one target
    assert result["matrix"][index["b"], index["a"]] == pytest.approx(1 + 0.5, abs=1e-6)
    assert result["summary"]["unit_rows"] == 16
    assert result["summary"]["shared_rows"] == 2


def test_a_repeated_article_text_counts_once_per_unit_row(tmp_path, cache):
    """The per-unit-row rule itself: give `b` a second *article* carrying `ts`."""
    units = pq.read_table(cache / "scjn-leyes-vectors" / "units.parquet").to_pylist()
    extra = dict(next(u for u in units if u["clave"] == "b" and u["eId"] == "art_2"),
                 eId="art_9")
    rows = [(u["clave"], u["nombre"], u["unit_type"], u["eId"], u["text_sha1"], u["text"])
            for u in units + [extra]]
    write_units(cache / "scjn-leyes-vectors", "leyes", rows)
    work_dir = tmp_path / "work2"
    prepare_umap_input.prepare(work_dir, collections=COLLECTIONS, cache_dir=cache,
                               unique_names=True, corpus_reader=stub_corpus_reader(),
                               log=lambda *a: None)
    result = computed(work_dir, cache)
    index = result["index"]
    assert result["matrix"][index["b"], index["a"]] == pytest.approx(2 + 0.5, abs=1e-6)


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
    # Nine distinct texts, but t1 and tb exist in both collections and dedup
    # is inside a collection; `th` and `t6` are one more each -- eleven rows.
    assert summary["vector_rows"] == 11
    assert summary["instruments"] == 6
    assert summary["matrix_sum"] == pytest.approx(9.0, abs=1e-3)
    assert summary["nonzero_cells"] == 11
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
    # `out` is the row sum, which under the `1/m` rule *is* the counted unit
    # rows: `b` has four unit rows, one a heading and one an identical
    # boilerplate shared by three instruments, so two count -- 1 + 1/2
    # towards `a`, 1/2 towards `900`.
    assert row["units"] == 4
    assert row["out"] == pytest.approx(2.0, abs=0.05)
    # What points *at* `a`: `b`'s 1.5, `c`'s 0.5, `900`'s 1 and `902`'s 0.5.
    assert points[points["clave"] == "a"].iloc[0]["in"] == pytest.approx(3.5, abs=0.05)
    # One decimal, weight first, because a weight is a sum of fractions and
    # a long name must not push it out of the tooltip.
    assert row["top1"] == "1.5  Ley A"
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
    # `b` points at two instruments, so its other rows are the placeholder.
    b = points[points["clave"] == "b"].iloc[0]
    assert len(b["t"].split(",")) == 2
    assert b["top3"] == b["top5"] == "\u2014"


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


def test_unit_rows_equal_the_instruments_own_unit_counts(prepared, cache):
    """Issue #259: whatever `prepare_umap_input.py --unique-names` removed, the
    join's unit rows are exactly what `instruments.parquet` lists."""
    summary = computed(prepared, cache)["summary"]
    instruments = pq.read_table(prepared / "instruments.parquet").to_pandas()
    assert summary["unit_rows"] == int(instruments["units"].sum())
    assert summary["matrix_sum"] == pytest.approx(summary["counted_rows"], abs=1e-3)
    assert summary["counted_rows"] < summary["unit_rows"]


def test_an_instruments_table_that_disagrees_with_the_join_is_refused(prepared, cache):
    table = pq.read_table(prepared / "instruments.parquet")
    units = table["units"].to_numpy().copy()
    units[0] += 1
    table = table.set_column(table.schema.get_field_index("units"), "units",
                             pa.array(units, type=pa.int32()))
    pq.write_table(table, prepared / "instruments.parquet")
    with pytest.raises(SystemExit, match="instruments.parquet lists"):
        computed(prepared, cache)


# -- transitorios are out of the comparison (issue #264) ------------------ #

TRANSITORIO = ["TRANSITORIOS"]

#: `(clave, unit_type, eId, text_sha1, path)`; every unit is a `leyes` unit.
#: Unit-length vectors, so every cosine below is the dot product on paper.
#: Eight rows lie in a transitorios section (`w`, `x`, `y`, `z`, `u`, `p`, `q`,
#: `s`) and are excluded; the article rows that are searched are `w`/`x`/`v`/
#: `r`/`n` (counted) and `g`/`h`/`k` (a word-for-word text shared by three).
TRANSITORIO_VECTORS = {
    "a1": [1.0, 0.0, 0.0, 0.0],
    "a2": [0.0, 1.0, 0.0, 0.0],
    "b1": [0.0, 0.995, 0.0998749, 0.0],        # cosine 0.995 to a2
    "b2": [0.995, 0.0, 0.0, 0.0998749],        # cosine 0.995 to a1
    "c1": [0.0, 0.0, 0.0, 1.0],
    "c2": [0.0, 0.0, 0.19899749, 0.98],        # cosine 0.98 to c1
    "d1": [-1.0, 0.0, 0.0, 0.0],
    "d2": [-0.9, 0.0, 0.0, -0.43588989],       # cosine 0.9 to d1
    "e1": [0.0, -1.0, 0.0, 0.0],               # a transitorio of p, q, s and an article of r
    "f1": [-0.3, 0.0, 0.9539392, 0.0],         # one article text, three instruments
    "n1": [0.0, -0.9, 0.43588989, 0.0],        # cosine 0.9 to e1, 0.42 to f1
    "h1": [0.0, 0.0, 0.0, -1.0],               # a heading inside a transitorios section
}
TRANSITORIO_UNITS = [
    ("w", "article", "art_1", "a1", []),
    ("w", "article", "art_t", "a2", ["TRANSITORIOS 18 DE MARZO DE 1980"]),
    ("w", "heading", "head_t", "h1", TRANSITORIO),
    ("x", "article", "art_t", "b1", TRANSITORIO),
    ("x", "article", "art_1", "b2", []),
    ("y", "article", "art_t", "c1", TRANSITORIO),
    ("z", "article", "art_t", "c2", TRANSITORIO),
    ("u", "article", "art_t", "d1", ["TITULO PRIMERO", "TRANSITORIOS"]),
    ("v", "article", "art_1", "d2", []),
    ("p", "article", "art_t", "e1", TRANSITORIO),
    ("q", "article", "art_t", "e1", TRANSITORIO),
    ("s", "article", "art_t", "e1", TRANSITORIO),
    ("r", "article", "art_1", "e1", []),
    ("n", "article", "art_1", "n1", []),
    ("g", "article", "art_1", "f1", []),
    ("h", "article", "art_1", "f1", []),
    ("k", "article", "art_1", "f1", []),
]
SHARED_TEXTS = ("e1", "f1")


@pytest.fixture
def transitorio_work(tmp_path):
    """A `leyes`-only corpus with a `path` column, prepared and ready for
    `build_matrix`; returns `(work_dir, cache)`."""
    cache = tmp_path / "tcache"
    leyes = cache / "scjn-leyes-vectors"
    leyes.mkdir(parents=True)
    pq.write_table(pa.table({
        "coleccion": pa.array(["leyes"] * len(TRANSITORIO_UNITS), type=pa.string()),
        "clave": pa.array([u[0] for u in TRANSITORIO_UNITS], type=pa.string()),
        "nombre": pa.array([f"Ley {u[0].upper()}" for u in TRANSITORIO_UNITS], type=pa.string()),
        "unit_type": pa.array([u[1] for u in TRANSITORIO_UNITS], type=pa.string()),
        "eId": pa.array([u[2] for u in TRANSITORIO_UNITS], type=pa.string()),
        "text_sha1": pa.array([u[3] for u in TRANSITORIO_UNITS], type=pa.string()),
        "text": pa.array([f"texto {u[3]}" for u in TRANSITORIO_UNITS], type=pa.string()),
        "path": pa.array([u[4] for u in TRANSITORIO_UNITS], type=pa.list_(pa.string())),
    }), leyes / "units.parquet")

    def write(name, hashes):
        pq.write_table(pa.table({
            "text_sha1": pa.array(hashes, type=pa.string()),
            "vector": pa.array([TRANSITORIO_VECTORS[h] for h in hashes],
                               type=pa.list_(pa.float32())),
        }), leyes / name)

    for clave in dict.fromkeys(u[0] for u in TRANSITORIO_UNITS):
        write(f"vectors-{clave}-{SLUG}-{K}.parquet",
              [u[3] for u in TRANSITORIO_UNITS if u[0] == clave and u[3] not in SHARED_TEXTS])
    write(f"vectors-shared-{SLUG}-{K}.parquet", list(SHARED_TEXTS))
    work_dir = tmp_path / "twork"
    prepare_umap_input.prepare(work_dir, collections=("leyes",), cache_dir=cache,
                               log=lambda *a: None)
    return work_dir, cache


def transitorio_result(transitorio_work, **kwargs):
    work_dir, cache = transitorio_work
    summary = instrument_matrix.build_matrix(
        work_dir, collections=("leyes",), cache_dir=cache, log=lambda *a: None, **kwargs)
    out = instrument_matrix.output_dir(work_dir)
    nearest = pq.read_table(out / "nearest.parquet").to_pandas()
    return {"summary": summary, "matrix": np.load(out / "matrix.npy"),
            "nearest": nearest, "index": index_of(work_dir)}


def row_of_unit(result, clave, eid):
    nearest = result["nearest"]
    return nearest[(nearest["clave"] == clave) & (nearest["eId"] == eid)].iloc[0]


@pytest.mark.parametrize("path, expected", [
    (["TRANSITORIOS"], True),
    (["TRANSITORIOS 18 DE MARZO DE 1980"], True),
    (["TITULO PRIMERO", "TRANSITORIOS"], True),                  # nested
    (["TRANSITORIOS 18 DE MARZO DE 1980", "ARTICULO 3"], True),
    (["DISPOSICIONES TRANSITORIOS"], False),                     # the word elsewhere
    (["CAPITULO II", "Del régimen TRANSITORIOS de"], False),
    (["TRANSITORIOSX"], False),
    ([], False),
    (None, False),
    (np.array(["TRANSITORIOS 1"]), True),                        # as pandas hands it back
    (float("nan"), False),
])
def test_the_transitorio_predicate_reads_the_path(path, expected):
    assert instrument_matrix.is_transitorio_path(path) is expected


def test_a_transitorio_is_never_a_source_row(transitorio_work):
    """Whatever its similarity (0.995, 0.98, cosine 1 with `m == 2`, or no
    neighbour at all), a transitorio has no row in `nearest.parquet`."""
    result = transitorio_result(transitorio_work)
    nearest = result["nearest"]
    assert sorted(zip(nearest["clave"], nearest["eId"])) == sorted([
        ("w", "art_1"), ("x", "art_1"), ("v", "art_1"), ("r", "art_1"), ("n", "art_1"),
        ("g", "art_1"), ("h", "art_1"), ("k", "art_1")])
    assert "transitorio" not in nearest.columns
    index = result["index"]
    # The two instruments that only have transitorios (`y`, `z`, `u`, `p`, `q`,
    # `s`) weigh nothing in either direction.
    for clave in ("y", "z", "u", "p", "q", "s"):
        assert result["matrix"][index[clave]].sum() == 0
        assert result["matrix"][:, index[clave]].sum() == 0


def test_an_article_at_0995_still_counts(transitorio_work):
    result = transitorio_result(transitorio_work)
    index = result["index"]
    for clave, target in (("w", "x"), ("x", "w")):
        row = row_of_unit(result, clave, "art_1")
        assert row["counted"] and pd.isna(row["drop_reason"])
        assert row["similarity"] == pytest.approx(0.995, abs=1e-4)
        assert result["matrix"][index[clave], index[target]] == pytest.approx(1.0)


def test_a_transitorio_text_is_not_a_candidate(transitorio_work):
    """`v`'s article was nearest to `u`'s transitorio (0.9); that text is not a
    candidate any more, so it goes to the next-nearest non-transitorio text:
    `f1`, owned by `g`, `h` and `k`."""
    result = transitorio_result(transitorio_work)
    index = result["index"]
    row = row_of_unit(result, "v", "art_1")
    assert list(row["targets"]) == sorted(index[c] for c in ("g", "h", "k"))
    assert row["m"] == 3 and row["counted"]
    assert row["similarity"] == pytest.approx(0.27, abs=1e-4)
    assert result["matrix"][index["v"], index["u"]] == 0
    assert result["matrix"][index["v"], index["g"]] == pytest.approx(1 / 3)


def test_a_text_shared_by_a_transitorio_and_an_article_belongs_to_the_article(transitorio_work):
    """`e1` is a transitorio of `p`, `q` and `s` and an article of `r`: `n`'s
    article wins it at 0.9, and only `r` answers for it (`m == 1`, a whole
    1) — it was `p`, `q`, `r` and `s` before."""
    result = transitorio_result(transitorio_work)
    index = result["index"]
    row = row_of_unit(result, "n", "art_1")
    assert list(row["targets"]) == [index["r"]] and row["m"] == 1
    assert row["similarity"] == pytest.approx(0.9, abs=1e-4)
    assert result["matrix"][index["n"], index["r"]] == pytest.approx(1.0)
    for clave in ("p", "q", "s"):
        assert result["matrix"][index["n"], index[clave]] == 0
    # And `r` itself: its own `e1` is exclusive, so it looks past it.
    assert list(row_of_unit(result, "r", "art_1")["targets"]) == [index["n"]]


def test_a_text_only_transitorios_own_never_wins(transitorio_work):
    """`a2`, `b1`, `c1`, `c2` and `d1` are owned by transitorios alone: no
    row's winner is any of them, although `b1` is 0.995 to `a2` and `d1` is 0.9
    to `d2`."""
    result = transitorio_result(transitorio_work)
    index = result["index"]
    targets = {int(j) for row in result["nearest"]["targets"] for j in row}
    assert targets.isdisjoint({index[c] for c in ("y", "z", "u", "p", "q", "s")})


def test_the_identical_shared_rule_still_drops_a_shared_article(transitorio_work):
    result = transitorio_result(transitorio_work)
    nearest, index = result["nearest"], result["index"]
    for clave in ("g", "h", "k"):
        row = row_of_unit(result, clave, "art_1")
        assert row["m"] == 2 and row["similarity"] == pytest.approx(1.0)
        assert not row["counted"] and row["drop_reason"] == "identical_shared"
        assert result["matrix"][index[clave]].sum() == 0
    assert set(nearest.loc[~nearest["counted"], "drop_reason"]) == {"identical_shared"}
    assert nearest.loc[nearest["counted"], "drop_reason"].isna().all()


def test_the_counts_and_the_identity(transitorio_work):
    result = transitorio_result(transitorio_work)
    summary, nearest, matrix = result["summary"], result["nearest"], result["matrix"]
    assert summary["unit_rows"] == 17
    # The heading inside a transitorios section is counted once, as a heading.
    assert summary["heading_rows_excluded"] == 1
    assert summary["transitorio_rows_excluded"] == 8
    assert summary["searched_rows"] == len(nearest) == 8
    assert (summary["searched_rows"] + summary["heading_rows_excluded"]
            + summary["transitorio_rows_excluded"]) == summary["unit_rows"]
    assert summary["excluded_unit_types"] == ["heading"]
    assert summary["excluded_transitorios"] is True
    assert summary["identical_shared_dropped"] == 3
    assert summary["counted_rows"] == 5
    assert summary["row_sums_equal_counted_rows"] is True
    assert matrix.sum() == pytest.approx(5.0)
    assert int(nearest["counted"].sum()) == 5


def test_the_near_copy_rule_is_gone(transitorio_work, tmp_path):
    result = transitorio_result(transitorio_work)
    for key in ("transitorio_similarity", "transitorio_near_identical_dropped"):
        assert key not in result["summary"]
    assert not hasattr(instrument_matrix, "TRANSITORIO_SIMILARITY")
    with pytest.raises(TypeError):
        instrument_matrix.build_matrix(tmp_path, transitorio_similarity=0.99)
    command = instrument_matrix.sbatch_command(tmp_path)
    assert "--transitorio-similarity" not in command
    with pytest.raises(SystemExit):
        instrument_matrix.main(["--work-dir", str(tmp_path), "--transitorio-similarity", "0.9"])


def test_searched_mask_is_the_one_definition_of_a_searched_row(transitorio_work):
    work_dir, cache = transitorio_work
    units = instrument_matrix.unit_rows(work_dir, collections=("leyes",), cache_dir=cache,
                                        log=lambda *a: None)
    mask = instrument_matrix.searched_mask(units)
    assert mask.dtype == bool and int(mask.sum()) == 8
    assert not units.loc[mask, "transitorio"].any()
    assert (units.loc[mask, "unit_type"] != "heading").all()
    # Nine rows lie in a transitorios section: the eight articles and a heading.
    assert units["transitorio"].sum() == 9 and not mask[units["transitorio"]].any()

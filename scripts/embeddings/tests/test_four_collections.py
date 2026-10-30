"""The Atlas chain over a fourth collection, `tratados` (issue #282).

    uv run --group viz pytest scripts/embeddings/tests -q

The shared toy corpus of `test_instrument_matrix.py` stays two collections and
six instruments, because a dozen tests pin its matrix weight by weight. This
module adds one *treaty* to a copy of it instead: one instrument keyed by an
integer id, its `slug`/`codNota` null like every id-keyed collection, whose single
article sits just beside `902`'s `t6` — so the treaty is a source (it points at
`902`) and a candidate (`902` now points at it) at once.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_instrument_umap_html  # noqa: E402
import build_umap_html  # noqa: E402
import export_atlas_data  # noqa: E402
import export_atlas_pairs  # noqa: E402
import instrument_matrix  # noqa: E402
import prepare_bm25_input  # noqa: E402
import prepare_umap_input  # noqa: E402
import unique_instruments  # noqa: E402
import legalvec.cache  # noqa: E402
from test_instrument_matrix import (  # noqa: E402,F401
    COLLECTIONS as THREE_TOY, K, SLUG, VECTORS, cache, index_of, stub_corpus_reader,
    stub_umap, write_units, write_vectors)

COLLECTIONS = THREE_TOY + ("tratados",)
NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
TREATY = "7001"

#: Nearest to `t6` (`902`'s second article, [0.8, 0.6, 0, 0]) at cosine 0.9987,
#: above the shared `ts` (0.99) that used to be `t6`'s nearest foreign text.
VECTORS["tt1"] = [0.8, 0.6, 0.0, 0.05]


def quiet(*args):
    pass


@pytest.fixture
def cache4(cache):
    """The toy `legalvec` cache plus a `scjn-tratados-vectors` release."""
    treaties = cache / "scjn-tratados-vectors"
    write_units(treaties, "tratados",
                [(TREATY, "Tratado T", "article", "art_1", "tt1", "texto tt1")])
    write_vectors(treaties, f"vectors-{TREATY}-{SLUG}-{K}.parquet", ["tt1"])
    write_vectors(treaties, f"vectors-shared-{SLUG}-{K}.parquet", [])
    return cache


@pytest.fixture
def prepared4(tmp_path, cache4):
    work_dir = tmp_path / "work4"
    prepare_umap_input.prepare(work_dir, collections=COLLECTIONS, cache_dir=cache4,
                               unique_names=True, corpus_reader=stub_corpus_reader(),
                               log=quiet)
    return work_dir


@pytest.fixture
def matrix4(prepared4, cache4):
    instrument_matrix.build_matrix(prepared4, collections=COLLECTIONS, cache_dir=cache4,
                                   log=quiet)
    return prepared4


def test_the_three_registries_name_the_same_collections_in_the_same_order():
    """`prepare_umap_input` stacks them, `build_umap_html` gates them and `legalvec`
    publishes them: a collection missing from one is silently absent downstream."""
    assert prepare_umap_input.COLLECTIONS == tuple(build_umap_html.COLLECTION_CODES)
    assert tuple(build_umap_html.COLLECTION_CODES) == tuple(legalvec.cache.RELEASE_TAGS)
    assert prepare_umap_input.COLLECTIONS[-1] == "tratados"      # stacked last
    assert len(set(build_umap_html.COLLECTION_CODES.values())) == 4
    assert build_umap_html.COLLECTION_CODES["tratados"] == "T"
    assert set(build_umap_html.COLLECTION_COLORS) == set(build_umap_html.COLLECTION_CODES.values())
    assert list(build_instrument_umap_html.COLLECTION_COLORS) == \
        list(build_umap_html.COLLECTION_CODES)


def test_treaties_are_not_grouped_by_name():
    assert unique_instruments.GROUPED_COLLECTIONS == ("reglamentos", "lineamientos")
    twins = [{"coleccion": "tratados", "clave": str(n), "nombre": "TRATADO X",
              "first_publication": "01-01-2000"} for n in (1, 2)]
    kept, dropped = unique_instruments.select_unique(twins)
    assert len(kept) == 2 and dropped == []


def test_a_unique_names_prepare_records_the_treaty_with_nothing_dropped(prepared4):
    report = json.loads((prepared4 / "unique-instruments.json").read_text(encoding="utf-8"))
    assert report["tratados"] == {"before": 1, "kept": 1, "dropped": 0,
                                  "dropped_instruments": []}
    instruments = pq.read_table(prepared4 / "instruments.parquet").to_pandas()
    assert list(instruments["coleccion"]) == ["leyes"] * 3 + ["lineamientos"] * 3 + ["tratados"]
    assert instruments.iloc[-1]["clave"] == TREATY


def test_the_treaty_is_a_source_and_a_candidate(matrix4, cache4):
    out = instrument_matrix.output_dir(matrix4)
    matrix = np.load(out / "matrix.npy")
    index = index_of(matrix4)
    assert matrix.shape == (7, 7)
    # Source: its one article's nearest foreign text is `902`'s t6.
    assert matrix[index[TREATY], index["902"]] == pytest.approx(1)
    assert matrix[index[TREATY]].sum() == pytest.approx(1)
    # Candidate: `902`'s t6 now finds the treaty before the shared `ts`, so `a`
    # and `b` lose the half each it used to credit them.
    assert matrix[index["902"], index[TREATY]] == pytest.approx(1)
    assert matrix[index["902"], index["a"]] == 0 and matrix[index["902"], index["b"]] == 0
    summary = json.loads((out / "matrix.json").read_text(encoding="utf-8"))
    assert summary["counted_rows"] == 10 and matrix.sum() == pytest.approx(10)
    assert summary["row_sums_equal_counted_rows"] is True
    nearest = pq.read_table(out / "nearest.parquet").to_pandas()
    assert (nearest["i"] == index[TREATY]).sum() == 1


def test_the_atlas_export_lists_the_treaty(matrix4, stub_umap, tmp_path):
    output = tmp_path / "atlas.json"
    export_atlas_data.export(matrix4, output, now=NOW, log=quiet)
    data = json.loads(output.read_text(encoding="utf-8"))
    meta = data["meta"]
    assert meta["collections"] == {"leyes": 3, "lineamientos": 3, "tratados": 1}
    assert meta["instruments"] == 7 and meta["counted_rows"] == 10
    assert "scjn-tratados" in meta["sources"] and "scjn-tratados-vectors" in meta["sources"]
    assert "tratados" not in meta["duplicates_dropped"]
    treaty = [entry for entry in data["instruments"] if entry["c"] == "tratados"]
    assert [(e["k"], e["n"], e["p"], e["pc"]) for e in treaty] == [(TREATY, "Tratado T", 1, 1)]
    assert len(data["projections"]["16"]) == 7


def test_the_pair_export_covers_the_treaty_and_refuses_an_unknown_collection(
        matrix4, cache4, stub_umap):
    manifest = export_atlas_pairs.export(matrix4, cache_dir=cache4, now=NOW, log=quiet)
    index = index_of(matrix4)
    out = export_atlas_pairs.output_dir(matrix4) / "pairs"
    names = sorted(path.name for path in out.iterdir())
    assert f"{index[TREATY]}-{index['902']}.json" in names
    assert f"{index['902']}-{index[TREATY]}.json" in names
    assert manifest["pairs"] == len(names)

    instruments = pq.read_table(matrix4 / "instruments.parquet").to_pandas()
    instruments.loc[instruments["clave"] == TREATY, "coleccion"] = "normas"
    pq.write_table(__import__("pyarrow").Table.from_pandas(instruments, preserve_index=False),
                   matrix4 / "instruments.parquet")
    with pytest.raises(SystemExit, match="normas"):
        export_atlas_pairs.export(matrix4, cache_dir=cache4, now=NOW, log=quiet,
                                  out_dir=matrix4 / "elsewhere")


def test_the_bm25_input_indexes_the_treaty(matrix4, cache4, tmp_path):
    bm25 = tmp_path / "bm25"
    prepare_bm25_input.prepare(bm25, matrix4, cache_dir=cache4, log=quiet)
    record = json.loads((bm25 / "input.json").read_text(encoding="utf-8"))
    assert list(record["collections"]) == list(COLLECTIONS)
    # Ten searched vector rows before the treaty, one more with it.
    assert record["documents"] == 11
    built = instrument_matrix.build_matrix(bm25, collections=COLLECTIONS, cache_dir=cache4,
                                           log=quiet)
    index = index_of(bm25)
    matrix = np.load(instrument_matrix.output_dir(bm25) / "matrix.npy")
    assert matrix.shape == (7, 7) and built["counted_rows"] == 10
    assert matrix[index[TREATY]].sum() == pytest.approx(1)
    assert matrix[:, index[TREATY]].sum() > 0


# -- the same treaty filed under two ids (review fix-2 of #300 and #301) ------ #

TWIN = "7002"


def twin_reader():
    """`7001` is the treaty's original publication (one snapshot, its text); `7002` was
    published earlier with another text and has the same one as its latest snapshot."""
    def reader(coleccion):
        def read(clave, cache_dir=None):
            if coleccion != "tratados":
                return {"snapshots": [{"fecha_publicacion": "01-01-2000", "markdown": "x"}]}
            snapshots = {
                TREATY: [("01-01-2010", "texto tt1")],
                TWIN: [("01-01-1990", "texto anterior"), ("01-01-2010", "texto tt1")],
            }[clave]
            return {"snapshots": [{"fecha_publicacion": d, "markdown": f"---\nid: {clave}\n---\n\n{body}\n"}
                                  for d, body in snapshots]}
        return read
    return reader


@pytest.fixture
def cache_twins(cache4):
    treaties = cache4 / "scjn-tratados-vectors"
    write_units(treaties, "tratados", [
        (TREATY, "Tratado T", "article", "art_1", "tt1", "texto tt1"),
        (TWIN, "Tratado T bis", "heading", "tit_1", "th", "TÍTULO"),
        (TWIN, "Tratado T bis", "article", "art_1", "tt1", "texto tt1"),
    ])
    write_vectors(treaties, f"vectors-{TWIN}-{SLUG}-{K}.parquet", ["th"])
    return cache4


def test_a_treaty_whose_compared_text_is_anothers_is_left_out_with_its_reason(tmp_path, cache_twins):
    work_dir = tmp_path / "work-twins"
    prepare_umap_input.prepare(work_dir, collections=COLLECTIONS, cache_dir=cache_twins,
                               unique_names=True, corpus_reader=twin_reader(), log=quiet)
    instruments = pq.read_table(work_dir / "instruments.parquet").to_pandas()
    treaties = list(instruments[instruments["coleccion"] == "tratados"]["clave"])
    assert treaties == [TREATY]
    report = json.loads((work_dir / "unique-instruments.json").read_text(encoding="utf-8"))
    assert report["tratados"] == {
        "before": 2, "kept": 1, "dropped": 1,
        "dropped_instruments": [{"clave": TWIN, "nombre": "Tratado T bis",
                                 "first_publication": "01-01-1990", "replaced_by": TREATY,
                                 "reason": "identical-text"}],
    }
    # the heading the twin has of its own does not make it another text: headings are not compared
    assert export_atlas_data.identical_text_dropped(work_dir) == {
        "tratados": [{"k": TWIN, "replaced_by": TREATY}]}
    assert export_atlas_data.duplicates_dropped(work_dir) == {"lineamientos": 0}

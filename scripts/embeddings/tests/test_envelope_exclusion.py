"""An instrument's envelope is neither a source nor a candidate (issue #301).

    uv run --group viz pytest scripts/embeddings/tests/test_envelope_exclusion.py -q

A three-law toy corpus with an `envelope` column in `units.parquet`: the text a
promulgation decree repeats word for word (`e1`, an envelope row of `a` and of `b`) would
have been `b`'s nearest neighbour; with the envelope out it is not a candidate, no
envelope row is a source, and the counters say how many rows were left out.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import instrument_matrix  # noqa: E402
import nearest_texts  # noqa: E402
import prepare_umap_input  # noqa: E402
from test_instrument_matrix import K, SLUG, index_of  # noqa: E402

VECTORS = {
    "v1": [0.0, 1.0, 0.0, 0.0],      # `a`'s article
    "e1": [1.0, 0.0, 0.0, 0.0],      # the decree text `a` and `b` both carry (envelope)
    "v2": [0.95, 0.05, 0.0, 0.0],    # `b`'s article: nearest to e1 (0.9987), then to c
    "e2": [0.9, 0.1, 0.0, 0.0],      # `b`'s own envelope row
    "v3": [0.3, 0.9, 0.0, 0.0],      # `c`'s article
}
#: `(clave, unit_type, eId, text_sha1, envelope)`
UNITS = [
    ("a", "article", "art_1", "v1", False),
    ("a", "intro", "hc_PROMULGACION__intro", "e1", True),
    ("b", "article", "art_1", "v2", False),
    ("b", "intro", "hc_PROMULGACION__intro", "e1", True),
    ("b", "preamble", "preamble", "e2", True),
    ("c", "article", "art_1", "v3", False),
]
SHARED = ("e1",)


def build(tmp_path, *, with_column=True):
    cache = tmp_path / "ecache"
    leyes = cache / "scjn-leyes-vectors"
    leyes.mkdir(parents=True)
    columns = {
        "coleccion": pa.array(["leyes"] * len(UNITS), type=pa.string()),
        "clave": pa.array([u[0] for u in UNITS], type=pa.string()),
        "nombre": pa.array([f"Ley {u[0].upper()}" for u in UNITS], type=pa.string()),
        "unit_type": pa.array([u[1] for u in UNITS], type=pa.string()),
        "eId": pa.array([u[2] for u in UNITS], type=pa.string()),
        "text_sha1": pa.array([u[3] for u in UNITS], type=pa.string()),
        "text": pa.array([f"texto {u[3]}" for u in UNITS], type=pa.string()),
    }
    if with_column:
        columns["envelope"] = pa.array([u[4] for u in UNITS], type=pa.bool_())
    pq.write_table(pa.table(columns), leyes / "units.parquet")

    def write(name, hashes):
        pq.write_table(pa.table({
            "text_sha1": pa.array(hashes, type=pa.string()),
            "vector": pa.array([VECTORS[h] for h in hashes], type=pa.list_(pa.float32())),
        }), leyes / name)

    for clave in dict.fromkeys(u[0] for u in UNITS):
        write(f"vectors-{clave}-{SLUG}-{K}.parquet",
              [u[3] for u in UNITS if u[0] == clave and u[3] not in SHARED])
    write(f"vectors-shared-{SLUG}-{K}.parquet", list(SHARED))
    work_dir = tmp_path / "ework"
    prepare_umap_input.prepare(work_dir, collections=("leyes",), cache_dir=cache,
                               log=lambda *a: None)
    return work_dir, cache


def matrix(work_dir, cache):
    summary = instrument_matrix.build_matrix(
        work_dir, collections=("leyes",), cache_dir=cache, log=lambda *a: None)
    out = instrument_matrix.output_dir(work_dir)
    return summary, np.load(out / "matrix.npy"), pq.read_table(out / "nearest.parquet").to_pandas()


def test_the_envelope_rows_are_not_sources_and_their_text_is_not_a_candidate(tmp_path):
    work_dir, cache = build(tmp_path)
    summary, a, nearest = matrix(work_dir, cache)
    index = index_of(work_dir)
    assert sorted(zip(nearest["clave"], nearest["eId"])) == [
        ("a", "art_1"), ("b", "art_1"), ("c", "art_1")]
    # `b`'s article was 0.9987 from the decree text `a` carries; with the envelope out
    # it goes to the next-nearest text, `c`'s article (0.366).
    row = nearest[nearest["clave"] == "b"].iloc[0]
    assert list(row["targets"]) == [index["c"]]
    assert row["similarity"] == pytest.approx(0.366, abs=1e-3)
    assert a[index["b"], index["a"]] == 0
    # `a`'s only searched text is its article; its envelope text is nobody's candidate.
    assert a[index["a"], index["b"]] + a[index["a"], index["c"]] == pytest.approx(1.0)


def test_the_counters_are_taken_after_headings_and_transitorios(tmp_path):
    work_dir, cache = build(tmp_path)
    summary, _, nearest = matrix(work_dir, cache)
    assert summary["unit_rows"] == 6
    assert summary["envelope_rows_excluded"] == 3
    assert summary["heading_rows_excluded"] == 0 and summary["transitorio_rows_excluded"] == 0
    assert summary["searched_rows"] == len(nearest) == 3
    assert summary["envelope_column"] is True


def test_searched_mask_reads_the_envelope_column(tmp_path):
    work_dir, cache = build(tmp_path)
    units = instrument_matrix.unit_rows(work_dir, collections=("leyes",), cache_dir=cache,
                                        log=lambda *a: None)
    assert units["envelope"].sum() == 3
    mask = instrument_matrix.searched_mask(units)
    assert int(mask.sum()) == 3 and not units.loc[mask, "envelope"].any()
    # a heading that is also envelope is one excluded row, and a frame with no column
    # (a toy table) has none
    frame = pd.DataFrame({"unit_type": ["heading", "article"], "transitorio": [False, False],
                          "envelope": [True, False]})
    assert instrument_matrix.searched_mask(frame).tolist() == [False, True]
    assert instrument_matrix.searched_mask(frame.drop(columns="envelope")).tolist() == [False, True]


def test_a_cache_without_the_column_compares_everything_and_says_so(tmp_path):
    work_dir, cache = build(tmp_path, with_column=False)
    summary, _, nearest = matrix(work_dir, cache)
    assert summary["envelope_column"] is False and summary["envelope_rows_excluded"] == 0
    assert len(nearest) == len(UNITS)


def test_collections_that_disagree_about_the_column_stop_the_run():
    points = pd.DataFrame({"envelope": [True, None, False]})
    with pytest.raises(SystemExit, match="envelope"):
        instrument_matrix.envelope_flags(points)
    flags, present = instrument_matrix.envelope_flags(pd.DataFrame({"envelope": [None, None]}))
    assert present is False and not flags.any()


def test_the_nearest_texts_pipeline_leaves_the_envelope_out_both_ways(tmp_path):
    work_dir, cache = build(tmp_path)
    matrix(work_dir, cache)
    nearest_texts.build_nearest_texts(work_dir, collections=("leyes",), cache_dir=cache,
                                      log=lambda *a: None)
    table = pq.read_table(nearest_texts.output_dir(work_dir) / nearest_texts.TABLE).to_pandas()
    index = index_of(work_dir)
    units = instrument_matrix.unit_rows(work_dir, collections=("leyes",), cache_dir=cache,
                                        log=lambda *a: None)
    envelope_rows = {int(r) for r in units.loc[units["envelope"], "row"]}
    article_rows = {int(r) for r in units.loc[~units["envelope"], "row"]}
    # Sources: only the three articles' texts. Candidates (`target` is a vector row):
    # never a text only envelope rows own.
    assert set(int(r) for r in table["row"]) == article_rows
    assert not set(int(r) for r in table["target_row"]) & envelope_rows
    assert table["i"].nunique() == 3 and set(table["i"]) == set(index.values())

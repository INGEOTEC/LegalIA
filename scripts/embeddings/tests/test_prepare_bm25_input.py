"""The BM25 work directory (issue #267).

    uv run --group viz pytest scripts/embeddings/tests -q

The toy corpus of `test_instrument_matrix.py` (its fixtures imported, not
copied): every document the index should hold, and every term it should know,
can be listed from `UNITS` with a pen.
"""

import json
import sys
from pathlib import Path

import pyarrow.parquet as pq
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import prepare_bm25_input  # noqa: E402
import scoring  # noqa: E402
from test_instrument_matrix import (  # noqa: E402,F401
    COLLECTIONS, cache, prepared, transitorio_work)


def quiet(*args):
    pass


@pytest.fixture
def bm25_dir(tmp_path, prepared, cache):
    work_dir = tmp_path / "bm25"
    prepare_bm25_input.prepare(work_dir, prepared, cache_dir=cache, log=quiet)
    return work_dir


def vocabulary(work_dir):
    return json.loads((work_dir / scoring.INDEX_DIR / "vocab.index.json")
                      .read_text(encoding="utf-8"))


def test_the_row_tables_are_copied_byte_for_byte(bm25_dir, prepared):
    for name in ("vector_ids.parquet", "instruments.parquet", "unique-instruments.json"):
        assert (bm25_dir / name).read_bytes() == (prepared / name).read_bytes()


def test_nothing_here_is_a_vector(bm25_dir):
    assert not (bm25_dir / "vectors.npy").exists()
    assert not (bm25_dir / "centroid_input.npy").exists()


def test_the_index_holds_exactly_the_searched_texts(bm25_dir):
    """Ten searched vector rows (eleven vectors: the heading-only text
    "CAPITULO I" is the one left out), so its words are not in the vocabulary."""
    record = json.loads((bm25_dir / "input.json").read_text(encoding="utf-8"))
    assert record["documents"] == 10
    tokens = pq.read_table(bm25_dir / scoring.TOKENS).to_pandas()
    assert len(tokens) == 10 and tokens["row"].is_monotonic_increasing
    words = vocabulary(bm25_dir)
    assert "capitulo" not in words
    assert {"texto", "t1", "t2", "t3", "t4", "t5", "t6", "transitorio", "compartido",
            "se", "deroga"} <= set(words)


def test_input_json_records_what_the_issue_lists(bm25_dir, prepared):
    record = json.loads((bm25_dir / "input.json").read_text(encoding="utf-8"))
    source = json.loads((prepared / "input.json").read_text(encoding="utf-8"))
    assert record["method"] == "bm25"
    assert record["model"] == "bm25" and record["model_slug"] == "bm25"
    assert record["k"] is None
    assert record["bm25"]["library"] == "bm25s"
    assert record["bm25"]["library_version"]
    assert (record["bm25"]["method"], record["bm25"]["k1"], record["bm25"]["b"]) \
        == ("lucene", 1.5, 0.75)
    assert record["bm25"]["query"] == "binary over distinct terms"
    assert record["tokeniser"] == {"lower": True, "stopwords": None, "stemmer": None,
                                   "token_pattern": r"(?u)\b\w\w+\b"}
    assert record["vocabulary"] == 11
    assert record["documents_without_terms"] == 0
    assert record["unique_names"] is True
    for key in ("n", "instruments", "collections", "legalvec_version"):
        assert record[key] == source[key]
    assert record["seconds"] >= 0


def test_the_index_reloads_as_a_scorer(bm25_dir):
    scorer = scoring.scorer_for(bm25_dir)
    assert isinstance(scorer, scoring.Bm25Scorer)
    assert scorer.n_rows == 11 and scorer.documents == 10
    assert scorer.params["method"] == "lucene"
    # The vector row nobody searched (the heading-only text) is an empty row.
    ids = pq.read_table(bm25_dir / "vector_ids.parquet").to_pandas()
    assert scorer.Q.getnnz(axis=1).tolist().count(0) == 1
    assert len(ids) == 11


def test_headings_and_transitorios_are_not_documents(transitorio_work, tmp_path):
    work_dir, cache = transitorio_work
    record = json.loads((work_dir / "input.json").read_text(encoding="utf-8"))
    record["unique_names"] = True            # this fixture is not about duplicates
    (work_dir / "input.json").write_text(json.dumps(record), encoding="utf-8")
    (work_dir / "unique-instruments.json").write_text("{}", encoding="utf-8")
    target = tmp_path / "bm25"
    prepare_bm25_input.prepare(target, work_dir, cache_dir=cache, log=quiet)
    words = set(vocabulary(target))
    # Articles are documents; a text only a transitorio or a heading carries is
    # not, and neither are its words; a text a transitorio shares with an
    # article ("e1", also r's article) is.
    assert {"a1", "b2", "d2", "e1", "f1", "n1"} <= words
    assert not words & {"a2", "b1", "c1", "c2", "d1", "h1"}


def test_a_source_that_was_never_prepared_is_refused(tmp_path, prepared, cache):
    (prepared / "prepare.done").unlink()
    with pytest.raises(SystemExit, match="prepare.done"):
        prepare_bm25_input.prepare(tmp_path / "bm25", prepared, cache_dir=cache, log=quiet)


def test_a_source_without_unique_names_is_refused(tmp_path, prepared, cache):
    record = json.loads((prepared / "input.json").read_text(encoding="utf-8"))
    record["unique_names"] = False
    (prepared / "input.json").write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(SystemExit, match="unique-names"):
        prepare_bm25_input.prepare(tmp_path / "bm25", prepared, cache_dir=cache, log=quiet)


def test_done_makes_a_rerun_a_no_op_and_force_redoes(bm25_dir, prepared, cache):
    assert (bm25_dir / "prepare.done").exists()
    stamp = (bm25_dir / "input.json").stat().st_mtime_ns
    again = prepare_bm25_input.prepare(bm25_dir, prepared, cache_dir=cache, log=quiet)
    assert again["method"] == "bm25"
    assert (bm25_dir / "input.json").stat().st_mtime_ns == stamp
    (bm25_dir / scoring.INDEX_DIR / "stale.txt").write_text("x")
    prepare_bm25_input.prepare(bm25_dir, prepared, cache_dir=cache, force=True, log=quiet)
    assert (bm25_dir / "input.json").stat().st_mtime_ns != stamp
    assert not (bm25_dir / scoring.INDEX_DIR / "stale.txt").exists()
    assert (bm25_dir / "prepare.done").exists()

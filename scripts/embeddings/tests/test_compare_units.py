"""`compare_units.py` (issue #291): what a rule change did to `units.parquet`."""

import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import compare_units  # noqa: E402


def table(rows, coleccion="tratados"):
    """`rows`: `(clave, nombre, unit_type, eId, piece, text_sha1)`."""
    return pa.table({
        "coleccion": [coleccion] * len(rows),
        "clave": [r[0] for r in rows],
        "nombre": [r[1] for r in rows],
        "unit_type": [r[2] for r in rows],
        "eId": [r[3] for r in rows],
        "piece": pa.array([r[4] for r in rows], pa.int32()),
        "text_sha1": [r[5] for r in rows],
    })


PUBLISHED = table([
    ("1", "Same", "article", "art_1", 0, "a"),
    ("2", "Relabelled", "loose", "cap_I__p_content", 0, "b"),
    ("3", "Retexted", "article", "art_1", 0, "c"),
    ("3", "Retexted", "loose", "cap_I__p_content", 0, "d"),
])
REBUILT = table([
    ("1", "Same", "article", "art_1", 0, "a"),
    ("2", "Relabelled", "intro", "cap_I__intro", 0, "b"),
    ("3", "Retexted", "article", "art_1.1", 0, "c"),
    ("3", "Retexted", "table", "cap_I__intro__table_1", 1, "e"),
    ("3", "Retexted", "table", "cap_I__intro__table_1", 2, "e"),
])


def test_counts_and_classification():
    result = compare_units.compare(PUBLISHED, REBUILT)
    assert result["instruments_published"] == result["instruments_rebuilt"] == 3
    assert (result["instruments_changed"], result["instruments_unchanged"]) == (2, 1)
    assert result["instruments_relabelled_only"] == 1
    assert result["instruments_with_new_text"] == 1
    assert result["distinct_new_texts"] == 1      # "e", shared by two rows
    assert result["distinct_dropped_texts"] == 1  # "d"
    assert (result["rows_published"], result["rows_rebuilt"]) == (4, 5)
    assert result["rows_by_unit_type_after"] == {"article": 2, "intro": 1, "table": 2}
    by_clave = {c["clave"]: c for c in result["changed"]}
    assert by_clave["2"]["only_relabelled"] and by_clave["2"]["new_texts"] == 0
    assert by_clave["3"]["new_texts"] == 1 and not by_clave["3"]["only_relabelled"]


def test_identical_tables_have_no_change():
    result = compare_units.compare(PUBLISHED, PUBLISHED)
    assert result["instruments_changed"] == 0 and result["distinct_new_texts"] == 0
    assert result["changed"] == []


def test_a_text_new_to_the_collection_only_counts_once_and_not_if_published_elsewhere():
    # "a" is published (instrument 1), so a rebuilt instrument 2 reusing it
    # needs no vector.
    rebuilt = table([("1", "Same", "article", "art_1", 0, "a"),
                     ("2", "Relabelled", "article", "art_9", 0, "a")])
    published = table([("1", "Same", "article", "art_1", 0, "a"),
                       ("2", "Relabelled", "article", "art_9", 0, "z")])
    result = compare_units.compare(published, rebuilt)
    assert result["distinct_new_texts"] == 0
    assert result["instruments_changed"] == 1 and result["instruments_with_new_text"] == 0


def test_cli_is_read_only_and_writes_the_outputs(tmp_path, capsys):
    pub, new = tmp_path / "pub", tmp_path / "new"
    pub.mkdir(), new.mkdir()
    pq.write_table(PUBLISHED, pub / "units.parquet")
    pq.write_table(REBUILT, new / "units.parquet")
    before = (pub / "units.parquet").read_bytes()
    out_json, out_md = tmp_path / "c.json", tmp_path / "c.md"
    code = compare_units.main(["--published", str(pub), "--rebuilt", str(new),
                               "--json", str(out_json), "--markdown", str(out_md), "--list-changed"])
    assert code == 0
    assert (pub / "units.parquet").read_bytes() == before
    assert "Distinct new texts: **1**" in out_md.read_text()
    assert "| 2 | Relabelled |" in out_md.read_text()
    assert '"instruments_changed": 2' in out_json.read_text()
    assert "tratados" in capsys.readouterr().out

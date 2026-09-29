"""The unique-instrument rule (issue #259), on toy records and a stub reader.

    uv run --group viz pytest scripts/embeddings/tests -q
"""

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unique_instruments  # noqa: E402
from unique_instruments import first_publication_dates, name_key, parse_date, select_unique  # noqa: E402


def record(coleccion, clave, nombre, first="01-01-2000"):
    return {"coleccion": coleccion, "clave": clave, "nombre": nombre, "first_publication": first}


def claves(records):
    return [r["clave"] for r in records]


def test_the_newest_original_publication_wins_and_names_its_replacement():
    kept, dropped = select_unique([
        record("reglamentos", "1", "REGLAMENTO X", "01-01-1990"),
        record("reglamentos", "2", "REGLAMENTO X", "01-01-2010"),
        record("reglamentos", "3", "REGLAMENTO Y", "01-01-1990"),
    ])
    assert claves(kept) == ["2", "3"]
    assert claves(dropped) == ["1"]
    assert dropped[0]["replaced_by"] == "2"


def test_a_three_member_group_keeps_one_and_all_point_at_it():
    kept, dropped = select_unique([
        record("lineamientos", "5", "L", "01-01-2001"),
        record("lineamientos", "6", "L", "01-01-2015"),
        record("lineamientos", "7", "L", "01-01-2008"),
    ])
    assert claves(kept) == ["6"]
    assert claves(dropped) == ["5", "7"]
    assert {d["replaced_by"] for d in dropped} == {"6"}


def test_accents_case_and_whitespace_do_not_make_a_name_different():
    assert name_key("REGLAMENTO  DEL CONCURSO PROGOL Ñ") == name_key("Reglamento del  concurso progol ñ ")
    assert name_key("Ley de Amnistía") == name_key("LEY DE AMNISTIA")
    kept, dropped = select_unique([
        record("reglamentos", "31947", "REGLAMENTO  DEL CONCURSO PROGOL", "01-01-2000"),
        record("reglamentos", "31971", "REGLAMENTO DEL CONCURSO PROGOL", "01-01-2005"),
    ])
    assert claves(kept) == ["31971"] and claves(dropped) == ["31947"]


def test_a_date_tie_goes_to_the_larger_integer_id():
    kept, dropped = select_unique([
        record("reglamentos", "999", "R", "05-05-2005"),
        record("reglamentos", "1000", "R", "05-05-2005"),
    ])
    # "999" > "1000" as strings; the id is compared as an integer.
    assert claves(kept) == ["1000"] and claves(dropped) == ["999"]


def test_dates_are_parsed_never_compared_as_strings():
    assert "02-04-2014" < "30-08-2004"
    assert parse_date("02-04-2014") > parse_date("30-08-2004")
    kept, _ = select_unique([
        record("reglamentos", "1", "R", "30-08-2004"),
        record("reglamentos", "2", "R", "02-04-2014"),
    ])
    assert claves(kept) == ["2"]
    kept, _ = select_unique([
        record("reglamentos", "1", "R", date(2014, 4, 2)),
        record("reglamentos", "2", "R", date(2004, 8, 30)),
    ])
    assert claves(kept) == ["1"]


def test_leyes_are_never_grouped():
    kept, dropped = select_unique([
        record("leyes", "lamn", "LEY DE AMNISTÍA"),
        record("leyes", "lamni", "LEY DE AMNISTÍA"),
    ])
    assert claves(kept) == ["lamn", "lamni"] and dropped == []


def test_the_same_name_in_two_collections_is_not_merged():
    kept, dropped = select_unique([
        record("reglamentos", "1", "LINEAMIENTOS GENERALES"),
        record("lineamientos", "2", "LINEAMIENTOS GENERALES"),
    ])
    assert claves(kept) == ["1", "2"] and dropped == []


def test_kept_records_keep_their_input_order():
    kept, _ = select_unique([
        record("reglamentos", "3", "C"),
        record("reglamentos", "1", "A", "01-01-1990"),
        record("reglamentos", "2", "A", "01-01-2000"),
    ])
    assert claves(kept) == ["3", "2"]


def test_the_first_publication_is_the_oldest_snapshot_whatever_the_order():
    def reader(coleccion):
        def read(clave, cache_dir=None):
            return {"snapshots": [{"fecha_publicacion": "02-04-2014"},
                                  {"fecha_publicacion": "30-08-2004"},
                                  {"fecha_publicacion": "01-01-2020"}]}
        return read

    out = first_publication_dates([record("reglamentos", "1", "R", None)], reader=reader)
    assert out[0]["first_publication"] == date(2004, 8, 30)


def test_only_grouped_collections_are_looked_up():
    def reader(coleccion):
        raise AssertionError("leyes must not be looked up")

    leyes = [{"coleccion": "leyes", "clave": "cpeum", "nombre": "CONSTITUCIÓN"}]
    assert first_publication_dates(leyes, reader=reader) == leyes


def test_an_uncached_tarball_names_the_download_command():
    import scjn

    def reader(coleccion):
        def read(clave, cache_dir=None):
            raise scjn.AssetNotCached(f"{clave}.tgz", Path("/cache"), coleccion=coleccion)
        return read

    with pytest.raises(scjn.AssetNotCached, match="scjn download --coleccion reglamentos --id 77"):
        first_publication_dates([record("reglamentos", "77", "R", None)], reader=reader)


def test_the_default_reader_is_scjns_own():
    import scjn

    assert unique_instruments._default_reader("reglamentos") is scjn.download_scjn_reglamentos_corpus
    assert unique_instruments._default_reader("lineamientos") is scjn.download_scjn_lineamientos_corpus

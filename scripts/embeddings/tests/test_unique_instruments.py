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


# -- identical texts (review fix-2 of #300 and #301) ------------------------- #

from unique_instruments import (  # noqa: E402
    original_is_current, select_identical, snapshot_body,
)

TEXT = ["a", "b", "c"]


def text_record(coleccion, clave, compared=TEXT):
    return {"coleccion": coleccion, "clave": clave, "nombre": f"N{clave}", "compared": list(compared)}


def snapshots_reader(by_clave):
    """`reader(coleccion)(clave, cache_dir=None)` over ``{clave: [(date, body), ...]}``."""
    def reader(coleccion):
        def read(clave, cache_dir=None):
            return {"snapshots": [
                {"fecha_publicacion": d, "markdown": f"---\nfuente: scjn\nid: {clave}\n---\n\n{body}\n"}
                for d, body in by_clave[clave]]}
        return read
    return reader


def test_the_instrument_whose_original_publication_is_the_text_is_kept():
    """tratados 11151, 16145 and 23742: the 1950 Convention is the latest snapshot of all
    three, and the original publication of 11151 only."""
    reader = snapshots_reader({
        "11151": [("19-06-1956", "convención 1950")],
        "16145": [("25-01-1936", "convención 1921"), ("19-10-1949", "protocolo"),
                  ("19-06-1956", "convención 1950")],
        "23742": [("21-06-1938", "convención 1933"), ("19-06-1956", "convención 1950")],
    })
    kept, dropped = select_identical(
        [text_record("tratados", k) for k in ("11151", "16145", "23742")], reader=reader)
    assert claves(kept) == ["11151"]
    assert claves(dropped) == ["16145", "23742"]
    assert {d["replaced_by"] for d in dropped} == {"11151"}
    assert {d["reason"] for d in dropped} == {"identical-text"}
    assert [d["first_publication"] for d in dropped] == [date(1936, 1, 25), date(1938, 6, 21)]


def test_the_kept_instrument_does_not_depend_on_the_id_when_one_is_the_original():
    reader = snapshots_reader({
        "5": [("01-01-1950", "viejo"), ("01-01-1960", "texto")],
        "9": [("01-01-1960", "texto")],
    })
    for order in (("5", "9"), ("9", "5")):
        kept, _ = select_identical([text_record("tratados", k) for k in order], reader=reader)
        assert claves(kept) == ["9"]
    reader = snapshots_reader({"5": [("01-01-1960", "texto")], "9": [("01-01-1950", "viejo"), ("01-01-1960", "texto")]})
    kept, dropped = select_identical([text_record("tratados", "5"), text_record("tratados", "9")],
                                     reader=reader)
    assert claves(kept) == ["5"] and claves(dropped) == ["9"]


def test_a_tie_between_originals_goes_to_the_larger_id_as_an_integer():
    reader = snapshots_reader({"999": [("01-01-1960", "t")], "1000": [("01-01-1960", "t")]})
    kept, dropped = select_identical(
        [text_record("tratados", "999"), text_record("tratados", "1000")], reader=reader)
    assert claves(kept) == ["1000"] and claves(dropped) == ["999"]


def test_a_group_in_which_nobody_is_the_original_goes_to_the_larger_id():
    reader = snapshots_reader({
        "20": [("01-01-1950", "a"), ("01-01-1960", "texto")],
        "30": [("01-01-1940", "b"), ("01-01-1960", "texto")],
    })
    kept, _ = select_identical(
        [text_record("tratados", "20"), text_record("tratados", "30")], reader=reader)
    assert claves(kept) == ["30"]


def test_the_header_and_the_markers_are_not_part_of_the_text():
    a = "---\nid: 1\nfecha: x\n---\n\n<!-- scjn:block orden=1 numero=0 kind=other label=\"A\" -->\n\nTexto.\n"
    b = "---\nid: 2\n---\n\n<!-- scjn:block orden=7 numero=0 kind=other label=\"B\" -->\n\nTexto.\n"
    assert snapshot_body(a) == snapshot_body(b) == "Texto."
    assert original_is_current([{"fecha_publicacion": "01-01-1950", "markdown": a},
                                {"fecha_publicacion": "01-01-1960", "markdown": b}])


def test_different_or_empty_compared_texts_are_never_grouped():
    def reader(coleccion):
        raise AssertionError("no group, no lookup")

    records = [text_record("tratados", "1", ["a"]), text_record("tratados", "2", ["b"]),
               text_record("tratados", "3", []), text_record("tratados", "4", [])]
    kept, dropped = select_identical(records, reader=reader)
    assert claves(kept) == ["1", "2", "3", "4"] and dropped == []
    # the same set of texts but a different multiset is another text
    kept, dropped = select_identical(
        [text_record("tratados", "1", ["a", "a", "b"]), text_record("tratados", "2", ["a", "b", "b"])],
        reader=reader)
    assert claves(kept) == ["1", "2"]


def test_the_rule_is_applied_inside_every_collection_and_never_across_them():
    reader = snapshots_reader({"1": [("01-01-1960", "t")], "2": [("01-01-1960", "t")],
                               "x": [("01-01-1960", "t")], "y": [("01-01-1960", "t")]})
    records = [text_record("tratados", "1"), text_record("tratados", "2"),
               text_record("leyes", "x"), text_record("leyes", "y"),
               text_record("reglamentos", "1")]
    kept, dropped = select_identical(records, reader=reader)
    assert [(r["coleccion"], r["clave"]) for r in kept] == [
        ("tratados", "2"), ("leyes", "y"), ("reglamentos", "1")]
    assert [(r["coleccion"], r["clave"]) for r in dropped] == [("tratados", "1"), ("leyes", "x")]


def test_real_11151_is_the_original_of_the_treaty_filed_three_times():
    """The three ids the SCJN files the 1950 Lake Success Convention under: only 11151 was
    originally published as that text (its single snapshot, 19-06-1956); 16145 and 23742
    are the 1921 and 1933 conventions the 1956 text superseded."""
    import scjn

    try:
        snapshots = {k: scjn.download_scjn_tratados_corpus(k)["snapshots"]
                     for k in ("11151", "16145", "23742")}
    except scjn.AssetNotCached:
        pytest.skip("scjn-tratados is not cached locally")
    assert {k: original_is_current(v) for k, v in snapshots.items()} == {
        "11151": True, "16145": False, "23742": False}
    kept, dropped = select_identical(
        [text_record("tratados", k) for k in ("16145", "11151", "23742")])
    assert claves(kept) == ["11151"] and claves(dropped) == ["16145", "23742"]

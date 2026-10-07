"""The gold set of strong reglamento -> law links (issue #272).

    uv run --group viz pytest scripts/embeddings/tests -q

A tiny corpus written by hand (laws, a reglamento per rule, a lineamiento): no
vectors, no network. `gold_links.build` only reads `instruments.parquet` and
`legalvec`'s cached `units.parquet`, so those are the only two files made.
"""

import hashlib
import json
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gold_links  # noqa: E402

#: `(coleccion, clave, nombre)`, in `i` order.
INSTRUMENTS = [
    ("leyes", "lft", "LEY Federal del Trabajo"),                        # 0
    ("leyes", "lgs", "LEY General de Salud"),                           # 1
    ("leyes", "lsm", "LEY de Salud Mental"),                            # 2
    ("leyes", "cpeum", "CONSTITUCIÓN Política de los Estados Unidos Mexicanos"),  # 3
    ("leyes", "lamn", "LEY de Amnistía"),                               # 4
    ("leyes", "lamni", "LEY de Amnistía"),                              # 5
    ("leyes", "lgspn", "LEY General de Salud Pública Nacional"),        # 6 (longer than lgs)
    ("reglamentos", "10", "REGLAMENTO de la Ley Federal del Trabajo"),  # 7: A
    ("reglamentos", "11", "REGLAMENTO de la Ley Forestal"),             # 8: unresolved
    ("reglamentos", "12", "REGLAMENTO Interior de la Comisión X"),      # 9: B only
    ("reglamentos", "13", "REGLAMENTO de la Ley de Amnistía"),          # 10: ambiguous
    ("reglamentos", "14", "REGLAMENTO de la Ley General de Salud Pública Nacional"),  # 11: nested
    ("reglamentos", "15", "REGLAMENTO de la Comisión Y"),               # 12: B decoys only
    ("lineamientos", "20", "LINEAMIENTOS para el Registro"),            # 13: B
]


def unit(clave, e_id, text, unit_type="article", path=()):
    return (clave, unit_type, e_id, list(path), text)


UNITS = {
    "leyes": [("lft", "article", "art_1", [], "Texto de la ley.")],
    "reglamentos": [
        unit("12", "art_1", "El presente Reglamento tiene por objeto reglamentar la Ley General "
                            "de Salud. Para lo no previsto se aplicará la Ley Federal del Trabajo."),
        unit("12", "art_2", "El presente Reglamento tiene por objeto organizar la Comisión Y. "
                            "Sin perjuicio de lo dispuesto en la Ley de Salud Mental, se "
                            "aplicará la Ley Federal del Trabajo."),
        unit("15", "pre", "El presente Reglamento tiene por objeto regular la Ley Federal del "
                          "Trabajo.", unit_type="preamble"),
        unit("15", "cap_1", "El presente Reglamento tiene por objeto regular la Ley Federal del "
                            "Trabajo.", unit_type="heading"),
        unit("15", "t_1", "El presente Reglamento tiene por objeto regular la Ley Federal del "
                          "Trabajo.", path=["TRANSITORIOS 1 DE ENERO DE 2000"]),
        unit("15", "art_9", "La Comisión tiene por objeto coordinar la Ley General de Salud."),
        unit("10", "art_1", "El presente Reglamento tiene por objeto reglamentar la Ley Federal "
                            "del Trabajo y la Constitución Política de los Estados Unidos "
                            "Mexicanos."),
        # Signal C: one law (single), two laws (not single), a reglamento's own
        # article (not the law), and a law that is not in the corpus.
        unit("10", "art_2", "Se pagará conforme al artículo 123 de la Ley Federal del Trabajo."),
        unit("10", "art_3", "Los artículos 5 y 7 Bis de la Ley General de Salud, y el artículo "
                            "1o. de la Ley Federal del Trabajo, aplican; no el artículo 3 del "
                            "Reglamento de la Ley Federal del Trabajo."),
        unit("10", "art_4", "Conforme al artículo 9 de la Ley Forestal."),
    ],
    "lineamientos": [
        unit("20", "art_1", "Los presentes Lineamientos tienen por objeto establecer las reglas "
                            "de la LEY de Salud Mental."),
    ],
}


def write_units(directory: Path, coleccion: str, rows) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({
        "coleccion": pa.array([coleccion] * len(rows), type=pa.string()),
        "clave": pa.array([r[0] for r in rows], type=pa.string()),
        "unit_type": pa.array([r[1] for r in rows], type=pa.string()),
        "eId": pa.array([r[2] for r in rows], type=pa.string()),
        "path": pa.array([r[3] for r in rows], type=pa.list_(pa.string())),
        "text": pa.array([r[4] for r in rows], type=pa.string()),
        "text_sha1": pa.array([hashlib.sha1(r[4].encode()).hexdigest() for r in rows],
                              type=pa.string()),
    }), directory / "units.parquet")


@pytest.fixture
def world(tmp_path):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    pq.write_table(pa.table({
        "i": pa.array(range(len(INSTRUMENTS)), type=pa.int32()),
        "coleccion": [c for c, _, _ in INSTRUMENTS],
        "clave": [k for _, k, _ in INSTRUMENTS],
        "nombre": [n for _, _, n in INSTRUMENTS],
    }), work_dir / "instruments.parquet")
    cache = tmp_path / "cache"
    for coleccion, rows in UNITS.items():
        write_units(cache / f"scjn-{coleccion}-vectors", coleccion, rows)
    return work_dir, cache


@pytest.fixture
def record(world):
    work_dir, cache = world
    return gold_links.build(work_dir, cache_dir=cache, log=lambda *a: None)


def entry(record, clave):
    return next(e for e in record["entries"] if e["clave"] == clave)


# -- folding and law matching ------------------------------------------------ #

def test_fold_drops_accents_case_whitespace_and_markdown_emphasis():
    assert gold_links.fold("**ARTÍCULO  1o.-** La  Ley") == "articulo 1o.- la ley"
    assert gold_links.fold(None) == ""


def test_longest_law_name_wins_and_nested_matches_are_dropped():
    matcher = gold_links.LawMatcher([(1, "LEY General de Salud"),
                                     (2, "LEY General de Salud Pública Nacional")])
    found = matcher.find(gold_links.fold("Reglamento de la Ley General de Salud Pública Nacional"))
    assert found == [("ley general de salud publica nacional", [2])]
    found = matcher.find(gold_links.fold("la Ley General de Salud y la Ley General de Salud "
                                         "Pública Nacional"))
    assert [ids for _, ids in found] == [[1], [2]]


def test_a_name_must_match_whole_words():
    matcher = gold_links.LawMatcher([(1, "LEY de Salud")])
    assert matcher.find("la ley de saludable") == []
    assert matcher.find("la ley de salud.") == [("ley de salud", [1])]


# -- signal A ------------------------------------------------------------- #

def test_signal_a_names_the_law(record):
    e = entry(record, "10")
    assert e["laws_A"] == [0]
    assert "A" in e["signals"]
    assert e["evidence"][0] == {"law": 0, "signal": "A"}


def test_nested_law_name_in_an_instrument_name_gives_only_the_longest(record):
    assert entry(record, "14")["laws_A"] == [6]


def test_an_unknown_law_is_unresolved_never_gold(record):
    assert [u["clave"] for u in record["unresolved"]] == ["11"]
    assert all(e["clave"] != "11" for e in record["entries"])


def test_a_name_two_laws_share_is_ambiguous_and_gives_no_link(record):
    assert [a["clave"] for a in record["ambiguous"]] == ["13"]
    assert record["ambiguous"][0]["laws"] == [4, 5]
    assert all(e["clave"] != "13" for e in record["entries"])
    assert record["unresolved"] and all(u["clave"] != "13" for u in record["unresolved"])


# -- signal B ------------------------------------------------------------- #

def test_signal_b_reads_the_law_in_the_same_sentence_only(record):
    e = entry(record, "12")
    assert e["laws_B"] == [1]            # General de Salud; not Federal del Trabajo (next sentence)
    assert e["laws_A"] == []
    [evidence] = [x for x in e["evidence"] if x["signal"] == "B"]
    assert evidence["eId"] == "art_1"
    assert evidence["sentence"].endswith("Ley General de Salud.")


def test_signal_b_ignores_headings_preambles_transitorios_and_other_subjects(record):
    """`15` has the phrase in a heading, a preamble, a transitorio and a
    commission's own objeto (its subject is not the instrument)."""
    assert all(e["clave"] != "15" for e in record["entries"])


def test_the_constitution_is_never_a_signal_b_law(record):
    assert 3 not in entry(record, "10")["laws_B"]
    assert entry(record, "10")["laws_B"] == [0]


def test_a_lineamiento_is_a_source_and_the_union_is_a_sorted_set(record):
    e = entry(record, "20")
    assert e["coleccion"] == "lineamientos" and e["laws"] == [2] and e["signals"] == ["B"]
    both = entry(record, "10")
    assert both["signals"] == ["A", "B"] and both["laws"] == [0]


def test_leyes_are_never_sources(record):
    assert all(e["coleccion"] != "leyes" for e in record["entries"])


def test_every_b_link_carries_an_eid_and_a_sentence(record):
    assert record["summary"]["B_links_without_evidence"] == 0
    for e in record["entries"]:
        for law in e["laws_B"]:
            assert any(x["law"] == law and x["signal"] == "B" and x["eId"] and x["sentence"]
                       for x in e["evidence"])


def test_the_summary_counts(record):
    s = record["summary"]
    assert s["A"]["instruments"] == 2          # `10` and `14`
    assert s["B"]["instruments"] == 3          # `10`, `12`, `20`
    assert s["A_or_B"]["instruments"] == 4
    assert s["A_and_B"]["instruments"] == 1
    assert s["B_only"]["instruments"] == 2
    assert s["links"] == {"A": 2, "B": 3, "A_or_B": 4, "B_only": 2}
    assert s["unresolved"] == 1 and s["ambiguous"] == 1


# -- files ------------------------------------------------------------------- #

def test_write_and_report(world, capsys):
    work_dir, cache = world
    record = gold_links.build(work_dir, cache_dir=cache, log=lambda *a: None)
    out = gold_links.write(work_dir, record)
    saved = json.loads((out / "gold.json").read_text(encoding="utf-8"))
    assert saved["instruments"][7] == ["reglamentos", "10"]
    assert saved["provenance"]["instruments"] == len(INSTRUMENTS)
    markdown = (out / "gold.md").read_text(encoding="utf-8")
    assert "LEY General de Salud" in markdown          # `12`'s B-only link
    assert "REGLAMENTO de la Ley Federal del Trabajo" not in markdown  # A-linked `10` is not B-only
    gold_links.report(work_dir)
    assert "unresolved" in capsys.readouterr().out


def test_links_md_has_one_row_per_link_with_its_signal(world):
    work_dir, cache = world
    record = gold_links.build(work_dir, cache_dir=cache, log=lambda *a: None)
    out = gold_links.write(work_dir, record)
    lines = (out / "links.md").read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("<!--") and lines[0].endswith("-->")
    assert lines[2:4] == ["| Instrument | Law | Signal |", "|---|---|---|"]
    rows = [tuple(cell.strip() for cell in line.strip("|").split("|")) for line in lines[4:]]
    links = sum(len(e["laws"]) for e in record["entries"])
    assert len(rows) == links == record["summary"]["links"]["A_or_B"]
    assert rows == sorted(rows, key=lambda r: (r[0].casefold(), r[1].casefold(), r[2]))
    # `10` names the Ley Federal del Trabajo (A) and says so in its objeto sentence (B);
    # it also names the Constitution, which is never a signal B law.
    assert ("REGLAMENTO de la Ley Federal del Trabajo", "LEY Federal del Trabajo",
            "A and B") in rows
    # `12` is a B-only instrument.
    assert ("REGLAMENTO Interior de la Comisión X", "LEY General de Salud", "B") in rows
    signals = {row[2] for row in rows}
    assert signals <= {"A", "B", "A and B"} and "A" in signals
    assert sum("A" in r[2] for r in rows) == record["summary"]["links"]["A"]
    assert sum("B" in r[2] for r in rows) == record["summary"]["links"]["B"]


def test_a_missing_work_directory_is_a_system_exit_naming_the_file(tmp_path):
    with pytest.raises(SystemExit, match="instruments.parquet"):
        gold_links.build(tmp_path / "nowhere", cache_dir=tmp_path)


def test_report_without_a_gold_is_a_system_exit(tmp_path):
    with pytest.raises(SystemExit, match="gold.json"):
        gold_links.report(tmp_path)


# -- signal C: explicit citations (issue #273) -------------------------------------- #

@pytest.fixture
def matcher():
    return gold_links.LawMatcher([(0, "LEY Federal del Trabajo"), (1, "LEY General de Salud"),
                                  (3, "CONSTITUCIÓN Política de los Estados Unidos Mexicanos"),
                                  (7, "CÓDIGO Fiscal de la Federación")])


def cited(matcher, text):
    return [(c["law"], c["article"]) for c in gold_links.find_citations(matcher, text)]


def test_an_article_of_a_named_law_is_a_citation(matcher):
    assert cited(matcher, "Conforme al artículo 123 de la Ley Federal del Trabajo.") == [(0, "123")]
    assert cited(matcher, "El Artículo 5o. de la LEY GENERAL DE SALUD aplica.") == [(1, "5o")]


def test_del_before_a_codigo_and_the_constitution_count(matcher):
    assert cited(matcher, "el artículo 27 del Código Fiscal de la Federación") == [(7, "27")]
    assert cited(matcher, "el artículo 123, apartado B, de la Constitución Política de los "
                          "Estados Unidos Mexicanos") == [(3, "123")]


def test_bis_ranges_and_lists_keep_the_first_number(matcher):
    assert cited(matcher, "los artículos 5 y 7 Bis de la Ley General de Salud") == [(1, "5")]
    assert cited(matcher, "el artículo 44 bis de la Ley General de Salud") == [(1, "44 bis")]


def test_a_gap_that_says_reglamento_is_not_a_citation_of_the_law(matcher):
    assert cited(matcher, "el artículo 3 del Reglamento de la Ley Federal del Trabajo") == []


def test_a_full_stop_or_a_semicolon_ends_the_reach(matcher):
    assert cited(matcher, "el artículo 5. Se aplicará de la Ley Federal del Trabajo") == []
    assert cited(matcher, "el artículo 5; y de la Ley Federal del Trabajo") == []
    far = "el artículo 5 " + "x" * 90 + " de la Ley Federal del Trabajo"
    assert cited(matcher, far) == []


def test_a_law_that_is_not_in_the_corpus_is_no_citation(matcher):
    assert cited(matcher, "el artículo 9 de la Ley Forestal") == []


def test_a_sentence_naming_two_laws_gives_two_citations(matcher):
    text = "el artículo 2 de la Ley General de Salud y el artículo 3 de la Ley Federal del Trabajo"
    assert cited(matcher, text) == [(1, "2"), (0, "3")]


def test_the_citations_table_groups_by_text_and_law(record):
    rows = {(r["eId"], r["law"]): r for r in record["citations"]}
    assert set(rows) == {("art_2", 0), ("art_3", 1), ("art_3", 0)}
    assert rows[("art_2", 0)]["single_law"] is True and rows[("art_2", 0)]["article"] == "123"
    assert rows[("art_3", 1)]["single_law"] is False and rows[("art_3", 0)]["single_law"] is False
    assert rows[("art_3", 0)]["article"] == "1o"
    assert all(r["clave"] == "10" and r["coleccion"] == "reglamentos" for r in rows.values())


def test_the_citation_summary(record):
    assert record["summary"]["citations"] == {
        "hits": 3, "distinct_rows": 2, "instruments": 1, "laws": 2, "single_law_rows": 1,
        "links": 3}


def test_headings_and_transitorios_are_not_read_for_citations(world):
    work_dir, cache = world
    write_units(cache / "scjn-reglamentos-vectors", "reglamentos",
                UNITS["reglamentos"] + [
                    unit("10", "cap_9", "artículo 8 de la Ley General de Salud", "heading"),
                    unit("10", "t_9", "artículo 8 de la Ley General de Salud",
                         path=["TRANSITORIOS 1 DE ENERO DE 2000"])])
    record = gold_links.build(work_dir, cache_dir=cache, log=lambda *a: None)
    assert {(r["eId"], r["law"]) for r in record["citations"]} == {
        ("art_2", 0), ("art_3", 1), ("art_3", 0)}


def test_write_puts_citations_in_a_parquet_not_in_gold_json(world):
    work_dir, cache = world
    record = gold_links.build(work_dir, cache_dir=cache, log=lambda *a: None)
    out = gold_links.write(work_dir, record)
    assert "citations" not in json.loads((out / "gold.json").read_text(encoding="utf-8"))
    table = pq.read_table(out / gold_links.CITATIONS).to_pandas()
    assert len(table) == 3 and set(table["law"]) == {0, 1}
    assert set(table.columns) == {"i", "coleccion", "clave", "text_sha1", "eId", "unit_type",
                                  "article", "law", "span", "hits", "single_law"}

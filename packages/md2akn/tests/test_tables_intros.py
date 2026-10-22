"""Rules 11, 12 and 13 (issue #291): decimal article numbers, a container's
`intro`, and `table` nodes.

Found browsing the Instruments page: the T-MEC's `Artículo 1.1:` all read as
article `1`, the *Notas.* under a LIGIE section/chapter were `loose` and glued
to whatever came next, and the tariff rows `scjn.api` writes one per paragraph
since #253 fell into `loose` excerpts cut every 2,000 characters with the
column header in only one piece. The fixtures are small, built from real lines
of `scjn-tratados/137355/29-06-2020.md` and `ligie-2022/31-12-2025.md`.
"""

import pytest

from md2akn import coverage, max_unit_chars, parse_markdown, text_units, validate
from md2akn.patterns import ARTICULO, ETIQUETA_TABLA, FILA_TABLA
from md2akn.segmenter import iter_blocks
from md2akn.structure import es_fila_tabla, modo_sin_articulos

HEADER = "| CÓDIGO | DESCRIPCIÓN | UNIDAD | CUOTA |"
LABEL = "**(ARANCEL)**"
SUBHEADER = "|  | IMPUESTO DE IMP. | IMPUESTO DE EXP. |"


def _rows(n, *, start=1, width=60):
    return [
        f"| 0101.{start + i:02d}.01 | {'Reproductores de raza pura. ' * (width // 28 + 1)}| Cbza | 10 | Ex. |"
        for i in range(n)
    ]


def _ligie(rows, *, label=True):
    """A LIGIE-shaped document: a section with notes, a chapter with notes and
    a tariff, then the next chapter and an article."""
    parts = [
        "Sección I",
        "**ANIMALES VIVOS Y PRODUCTOS DEL REINO ANIMAL**",
        "Notas.",
        "1. En esta Sección, cualquier referencia a un género se aplica también a los jóvenes.",
        "Capítulo 01",
        "Animales vivos",
        "Nota.",
        "1. Este Capítulo comprende todos los animales vivos, excepto:",
        "**a)** los peces, los crustáceos, moluscos y demás invertebrados acuáticos;",
        HEADER,
    ]
    if label:
        parts.append(LABEL)
    parts.append(SUBHEADER)
    parts.extend(rows)
    parts.extend(["Capítulo 02", "Carne", "**Artículo 1o.-** Uno."])
    return "\n\n".join(parts) + "\n"


def _units(text, **kw):
    kw.setdefault("split_articles", False)
    return text_units(text, **kw)


# -- rule 11: decimal article numbers ------------------------------------- #

def _num(line):
    m = ARTICULO.match(line.replace("**", ""))
    return None if m is None else m.group("num")


@pytest.mark.parametrize(
    "line, expected",
    [
        ("Artículo 1.1: Establecimiento de una zona de libre comercio", "1.1"),
        ("**Artículo 1.1**: Establecimiento", "1.1"),
        ("**Artículo 12.3.-** Texto.", "12.3"),
        ("Artículo 3.2.1: Texto.", "3.2.1"),
        ("Artículo 1.- Texto.", "1"),
        ("**Artículo 1.** Texto.", "1"),
        ("**Artículo 1o.-** Texto.", "1o"),
        ("**Artículo 27 Bis.-** Texto.", "27 Bis"),
        ("**ARTÍCULO IV**", "IV"),
    ],
)
def test_the_article_number(line, expected):
    assert _num(line) == expected


TMEC = (
    "**CAPÍTULO 1**\n\n**DISPOSICIONES INICIALES Y DEFINICIONES GENERALES**\n\n"
    "**Artículo 1.1**: Establecimiento de una zona de libre comercio\n\n"
    "Las Partes del presente Tratado, de conformidad con el Artículo XXIV del GATT.\n\n"
    "**Artículo 1.2**: Objetivos\n\nEl presente Tratado tiene por objetivos.\n\n"
    "**CAPÍTULO 2**\n\n**TRATO NACIONAL**\n\n"
    "**Artículo 2.1**: Definiciones\n\nPara los efectos de este Capítulo.\n"
)


def test_decimal_articles_are_distinct_articles():
    tree = parse_markdown(TMEC)
    articles = [n for n in tree.walk() if n.akn_type == "article"]
    assert [a.eId for a in articles] == [
        "cap_1__art_1.1", "cap_1__art_1.2", "cap_2__art_2.1",
    ]
    assert [a.num for a in articles] == ["1.1", "1.2", "2.1"]
    assert validate(tree).ok


def test_decimal_articles_are_article_units():
    units = _units(TMEC)
    articles = [u for u in units if u.unit_type == "article"]
    assert [u.eId for u in articles] == ["cap_1__art_1.1", "cap_1__art_1.2", "cap_2__art_2.1"]
    assert len({u.eId for u in articles}) == len(articles)
    tree = parse_markdown(TMEC)
    assert coverage(tree, units).uncovered_chars == 0


def test_the_gate_of_rule_8_sees_a_decimal_article():
    # A document that writes `Artículo N.M` numbers its articles the ordinary
    # way: rule 8 must stay off, even if its paragraphs read `1.`, `2.`.
    text = (
        "**Artículo 1.1**: Uno.\n\n1. Primero.\n\n2. Segundo.\n\n3. Tercero.\n\n"
        "4. Cuarto.\n\n5. Quinto.\n\n6. Sexto.\n"
    )
    assert modo_sin_articulos(list(iter_blocks(text))) is None


def test_a_decimal_article_is_not_an_acuerdo_numeral():
    text = "**Artículo 2.1**: Uno.\n\n**Artículo 2.2**: Dos.\n"
    tree = parse_markdown(text)
    assert [n.eId for n in tree.walk() if n.akn_type == "article"] == ["art_2.1", "art_2.2"]


# -- rule 12: a container's intro ----------------------------------------- #

def test_the_notes_under_a_section_are_its_intro():
    tree = parse_markdown(_ligie(_rows(3)))
    intro = tree.find("sec_I__intro")
    assert intro is not None and intro.akn_type == "intro"
    assert intro.parent.eId == "sec_I"
    assert [c.akn_type for c in intro.children] == ["content", "content"]
    assert intro.text.startswith("Notas.")
    assert "Capítulo" not in intro.text


def test_the_notes_under_a_chapter_are_its_intro_and_the_table_is_apart():
    tree = parse_markdown(_ligie(_rows(3)))
    intro = tree.find("cap_01__intro")
    assert intro is not None and intro.parent.eId == "cap_01"
    assert [c.akn_type for c in intro.children] == ["content"] * 3 + ["table"]
    assert intro.children[0].eId == "cap_01__intro__p_1"
    assert intro.children[-1].eId == "cap_01__intro__table_1"


def test_the_intro_units():
    units = _units(_ligie(_rows(3)))
    intros = [u for u in units if u.unit_type == "intro"]
    assert [(u.eId, u.piece) for u in intros] == [("sec_I__intro", 0), ("cap_01__intro", 0)]
    assert intros[1].text.startswith("CAPÍTULO 01 Animales vivos Nota. 1. Este Capítulo")
    assert all("|" not in u.text for u in intros)
    assert not [u for u in units if u.unit_type == "loose"]


def test_the_intro_carries_its_container_path():
    units = _units(_ligie(_rows(1)))
    intro = next(u for u in units if u.eId == "cap_01__intro")
    assert intro.path[-1].startswith("CAPÍTULO 01")


def test_the_container_heading_ends_where_the_intro_starts():
    units = _units(_ligie(_rows(1)))
    heading = next(u for u in units if u.eId == "cap_01" and u.unit_type == "heading")
    intro = next(u for u in units if u.eId == "cap_01__intro")
    assert heading.end_char <= intro.start_char
    assert "Nota." not in heading.text


def test_an_intro_over_the_cap_is_cut_and_numbered():
    paragraphs = "\n\n".join(f"Párrafo número {n} con algo de texto." for n in range(1, 8))
    text = f"**CAPITULO I**\n\n{paragraphs}\n\n**Artículo 1o.** Uno.\n"
    units = _units(text, cap=120)
    intros = [u for u in units if u.unit_type == "intro"]
    assert len(intros) > 1
    assert [u.piece for u in intros] == list(range(1, len(intros) + 1))
    assert {u.eId for u in intros} == {"cap_I__intro"}
    assert all(len(u.text) <= 120 for u in intros)
    assert coverage(parse_markdown(text), units).uncovered_chars == 0


def test_text_before_any_container_keeps_its_old_treatment():
    text = "Un preámbulo.\n\n**Artículo 1o.** Uno.\n"
    kinds = [u.unit_type for u in _units(text)]
    assert kinds == ["preamble", "article"]
    assert "intro" not in kinds


def test_no_intro_for_a_container_that_opens_with_an_article():
    tree = parse_markdown("**CAPITULO I**\n\n**Artículo 1o.** Uno.\n")
    assert not [n for n in tree.walk() if n.akn_type == "intro"]


def test_an_annotation_before_an_intro_stays_covered():
    text = (
        "**CAPITULO I**\n\n**(REFORMADO, D.O.F. 9 DE DICIEMBRE DE 2019)**\n\n"
        "Una nota del capítulo.\n\n**Artículo 1o.** Uno.\n"
    )
    tree = parse_markdown(text)
    assert validate(tree).ok
    units = _units(text)
    assert coverage(tree, units).uncovered_chars == 0
    assert [u.unit_type for u in units] == ["heading", "intro", "article"]


# -- rule 13: tables ------------------------------------------------------ #

def test_row_and_label_patterns():
    assert FILA_TABLA.match(HEADER)
    assert FILA_TABLA.match(SUBHEADER)
    assert not FILA_TABLA.match("Una frase | con una barra")
    assert not FILA_TABLA.match("| solo abre")
    assert ETIQUETA_TABLA.match("**(ARANCEL)**")
    assert ETIQUETA_TABLA.match("(ARANCEL)")
    assert not ETIQUETA_TABLA.match("**(REFORMADO, D.O.F. 7 DE NOVIEMBRE DE 2025)**")
    assert not ETIQUETA_TABLA.match("Sub-Sector:")


def test_es_fila_tabla_reads_a_block():
    block = next(iter_blocks(HEADER + "\n"))
    assert es_fila_tabla(block)


def test_consecutive_rows_are_one_table_with_the_label_inside():
    tree = parse_markdown(_ligie(_rows(5)))
    tables = [n for n in tree.walk() if n.akn_type == "table"]
    assert [t.eId for t in tables] == ["cap_01__intro__table_1"]
    assert tables[0].text.startswith(HEADER)
    assert LABEL in tables[0].text and SUBHEADER in tables[0].text
    assert tables[0].text.rstrip().endswith("| Ex. |")
    assert validate(tree).ok


def test_without_the_tolerated_label_the_table_is_still_one():
    tree = parse_markdown(_ligie(_rows(5), label=False))
    assert len([n for n in tree.walk() if n.akn_type == "table"]) == 1


def test_another_block_between_rows_ends_the_table():
    text = (
        "**CAPITULO I**\n\n"
        + "\n\n".join([HEADER, _rows(1)[0], "Sub-Sector:", _rows(1, start=5)[0]])
        + "\n\n**Artículo 1o.** Uno.\n"
    )
    tree = parse_markdown(text)
    tables = [n for n in tree.walk() if n.akn_type == "table"]
    assert [t.eId for t in tables] == ["cap_I__intro__table_1", "cap_I__intro__table_2"]
    assert coverage(tree, _units(text)).uncovered_chars == 0


def test_a_reform_annotation_between_rows_ends_the_table():
    text = (
        "**CAPITULO I**\n\n"
        + "\n\n".join([HEADER, _rows(1)[0], "**(REFORMADO, D.O.F. 7 DE NOVIEMBRE DE 2025)**",
                       _rows(1, start=5)[0]])
        + "\n\n**Artículo 1o.** Uno.\n"
    )
    tree = parse_markdown(text)
    assert len([n for n in tree.walk() if n.akn_type == "table"]) == 2
    assert validate(tree).ok
    assert coverage(tree, _units(text)).uncovered_chars == 0


def test_a_table_is_never_a_container_epigraph():
    text = f"**CAPITULO I**\n\n{HEADER}\n\n{_rows(1)[0]}\n\n**Artículo 1o.** Uno.\n"
    tree = parse_markdown(text)
    assert tree.find("cap_I").heading is None
    assert tree.find("cap_I__intro__table_1") is not None


def test_a_short_table_is_one_table_unit_and_never_loose():
    units = _units(_ligie(_rows(3)))
    tables = [u for u in units if u.unit_type == "table"]
    assert [(u.eId, u.piece) for u in tables] == [("cap_01__intro__table_1", 0)]
    assert tables[0].text.startswith("CAPÍTULO 01 Animales vivos | CÓDIGO |")
    assert not [u for u in units if u.unit_type == "loose"]


def test_a_long_table_is_cut_at_rows_with_the_header_repeated():
    rows = _rows(80)
    text = _ligie(rows)
    units = _units(text)
    tables = [u for u in units if u.unit_type == "table"]
    assert len(tables) > 2
    assert [u.piece for u in tables] == list(range(1, len(tables) + 1))
    assert {u.eId for u in tables} == {"cap_01__intro__table_1"}
    prefix = "CAPÍTULO 01 Animales vivos"
    assert tables[0].text.startswith(f"{prefix} {HEADER}")
    for unit in tables[1:]:
        assert unit.text.startswith(f"{prefix} {HEADER} | 0101.")
    for unit in tables:
        assert len(unit.text) <= 2000
    tree = parse_markdown(text)
    assert coverage(tree, units).uncovered_chars == 0
    report = max_unit_chars(tree, units, split_articles=False)
    assert report.splittable == 0 and report.over_cap == 0


def test_the_pieces_of_a_table_partition_its_rows():
    rows = _rows(60)
    text = _ligie(rows)
    tables = [u for u in _units(text) if u.unit_type == "table"]
    seen = []
    for unit in tables:
        seen.extend(line for line in text[unit.start_char:unit.end_char].split("\n") if line.strip())
    expected = [HEADER, LABEL, SUBHEADER, *rows]
    assert seen == expected


def test_a_single_row_over_the_cap_is_unsplittable_and_counted():
    huge = "| 9999.99.99 | " + "palabra " * 400 + "| Pza | 1 | Ex. |"
    text = f"**CAPITULO I**\n\n{HEADER}\n\n{huge}\n\n{_rows(1)[0]}\n\n**Artículo 1o.** Uno.\n"
    tree = parse_markdown(text)
    units = _units(text)
    over = [u for u in units if u.unit_type == "table" and len(u.text) > 2000]
    assert len(over) == 1 and "palabra" in over[0].text
    report = max_unit_chars(tree, units, split_articles=False)
    assert report.over_cap == 1 and report.unsplittable == 1 and report.splittable == 0
    assert coverage(tree, units).uncovered_chars == 0


def test_split_over_cap_false_gives_one_unit_per_table():
    units = _units(_ligie(_rows(80)), split_over_cap=False)
    tables = [u for u in units if u.unit_type == "table"]
    assert [(u.eId, u.piece) for u in tables] == [("cap_01__intro__table_1", 0)]


def test_a_table_inside_an_article_stays_article_text():
    text = (
        "**Artículo 1o.-** Se establece la siguiente tarifa:\n\n"
        f"{HEADER}\n\n{_rows(2)[0]}\n\n{_rows(2)[1]}\n\n**Artículo 2o.-** Dos.\n"
    )
    tree = parse_markdown(text)
    assert not [n for n in tree.walk() if n.akn_type == "table"]
    units = _units(text)
    assert [u.unit_type for u in units] == ["article", "article"]
    assert "| CÓDIGO |" in units[0].text and "0101.02.01" in units[0].text
    assert coverage(tree, units).uncovered_chars == 0


def test_a_table_under_the_transitorios_is_a_table_unit():
    text = (
        "**Artículo 1o.-** Uno.\n\n## Transitorios\n\n"
        f"{HEADER}\n\n{_rows(1)[0]}\n\n**Primero.-** Entra en vigor.\n"
    )
    tree = parse_markdown(text)
    tables = [n for n in tree.walk() if n.akn_type == "table"]
    assert len(tables) == 1 and tables[0].parent.refers_to == "#transitorios"
    assert validate(tree).ok
    units = _units(text)
    assert [u.unit_type for u in units] == ["article", "heading", "table", "article"]
    assert coverage(tree, units).uncovered_chars == 0


def test_the_document_order_of_units_is_kept_around_a_table():
    text = (
        f"**CAPITULO I**\n\nNota previa.\n\n{HEADER}\n\n{_rows(1)[0]}\n\nNota posterior.\n\n"
        "**Artículo 1o.** Uno.\n"
    )
    units = _units(text)
    assert [(u.unit_type, u.piece) for u in units] == [
        ("heading", 0), ("intro", 1), ("table", 0), ("intro", 2), ("article", 0),
    ]
    starts = [u.start_char for u in units]
    assert starts == sorted(starts)
    assert coverage(parse_markdown(text), units).uncovered_chars == 0


# -- default output stays what it was ------------------------------------- #

def test_a_document_without_any_of_it_is_unchanged():
    text = (
        "**TÍTULO PRIMERO**\n\n**Del objeto**\n\n**CAPÍTULO I**\n\n"
        "**Artículo 1o.-** Uno.\n\n**Artículo 2o.-** Dos.\n"
    )
    assert [(u.unit_type, u.eId) for u in text_units(text)] == [
        ("heading", "tit_PRIMERO"), ("heading", "tit_PRIMERO__cap_I"),
        ("article", "tit_PRIMERO__cap_I__art_1o"), ("article", "tit_PRIMERO__cap_I__art_2o"),
    ]


def test_validate_accepts_every_fixture():
    for text in (_ligie(_rows(80)), TMEC):
        assert validate(parse_markdown(text)).ok

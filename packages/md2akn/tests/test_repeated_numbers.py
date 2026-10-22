"""Rules 14-18 (review fix-1 of #291, a finding made after the fact): an
instrument's articles must not repeat a number because of how `md2akn` read it.

209 of the 2,676 instruments of the Atlas carried a repeated article number.
Each fragment below is the shape of a real instrument that did (named in its
test), cut down to what shows the cause; the fixtures live in
`fixtures_units/`.
"""

from collections import Counter
from pathlib import Path

import pytest

from md2akn import parse_markdown
from md2akn.patterns import ARTICULO
from md2akn.units import coverage, text_units

FIXTURES = Path(__file__).parent / "fixtures_units"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _walk(node):
    yield node
    for child in node.children:
        yield from _walk(child)


def _articles(tree, *, outside_transitorios=True):
    return [
        n
        for n in _walk(tree)
        if n.akn_type == "article" and not (outside_transitorios and "transitorios" in n.eId)
    ]


def _repeated(tree):
    return {n for n, c in Counter(str(a.num).strip() for a in _articles(tree)).items() if c > 1}


def _num(line: str):
    m = ARTICULO.match(line.replace("**", ""))
    return None if m is None else (m.group("num"), m.group("sep").strip())


# -- rule 14: an article's number is read whole ----------------------------


@pytest.mark.parametrize(
    "line, expected",
    [
        # LIVA / LIEPS: the letter after the ordinal's own separator, then its suffix
        ("ARTICULO 1o.-A BIS.- Los contribuyentes.", ("1o.-A BIS", ".-")),
        ("Artículo 5o.-A BIS.- Las personas.", ("5o.-A BIS", ".-")),
        # reglamento 173: the suffix after the ordinal's `.-`
        ("ARTICULO 3o.- Bis. Las entidades.", ("3o.- Bis", ".")),
        # the LGTOC: a lower-case letter, alone and before a suffix
        ("ARTICULO 228 a.- Los certificados.", ("228 a", ".-")),
        ("ARTICULO 228 a BIS.- Los certificados.", ("228 a BIS", ".-")),
        ("ARTICULO 228 A BIS.- Los certificados.", ("228 A BIS", ".-")),
        # spellings of the Latin suffixes the corpus uses: LAC, LFPED, LGDNNA,
        # LGV, reglamento 11120
        ("Artículo 78 Septdecies. La persona prestadora.", ("78 Septdecies", ".")),
        ("ARTICULO 73 OCTODECIES. Las personas.", ("73 OCTODECIES", ".")),
        ("Artículo 15 Novenus.- Las instancias.", ("15 Novenus", ".-")),
        ("Artículo 135 Sexties. Las autoridades.", ("135 Sexties", ".")),
        ("Artículo 84 Sexíes. La Junta.", ("84 Sexíes", ".")),
        ("ARTÍCULO 41 QUARTER.- La Dirección.", ("41 QUARTER", ".-")),
        # a letter followed by `(SIC)` (LIEPS 8o.-B)
        ("ARTICULO 8o.-B (SIC).- (DEROGADO, D.O.F.", ("8o.-B", "")),
        # an ordinal word with its suffix (reglamento 39766), a letter after `.-` (LIEPS)
        ("ARTICULO VIGESIMO CUARTO BIS.- Si por causas.", ("VIGESIMO CUARTO BIS", ".-")),
        ("ARTICULO 18.-A.- Para los efectos de esta ley.", ("18.-A", ".-")),
        # a word after the number is not part of it
        ("Artículo 5 a las personas que...", ("5", "")),
        ("ARTICULO 1o.-A falta de pago.", ("1o", ".-")),
        ("ARTICULO 3o.- Las entidades señaladas.", ("3o", ".-")),
    ],
)
def test_number_is_read_whole(line, expected):
    assert _num(line) == expected


def test_a_letter_suffix_does_not_repeat_its_base_article():
    # lgtoc: `ARTICULO 228` and `228 a`, `228 a BIS`, `228 b` ... are different articles
    text = (
        "**ARTICULO 228.-** Son aplicables a las obligaciones.\n\n"
        "ARTICULO 228 a.- Los certificados de participación.\n\n"
        "ARTICULO 228 a BIS.- Los certificados de vivienda.\n\n"
        "ARTICULO 228 b.- Los certificados serán bienes.\n\n"
        "**ARTICULO 1o.-** Está obligado.\n\n"
        "ARTICULO 1o.-A BIS.- Los contribuyentes.\n"
    )
    tree = parse_markdown(text)
    assert [a.num for a in _articles(tree)] == [
        "228", "228 a", "228 a BIS", "228 b", "1o", "1o.-A BIS"
    ]
    assert not _repeated(tree)


# -- rules 15 and 16: a table of contents, and PARTE/ANEXO -------------------


def test_index_parts_and_appendices_of_the_imdg_code():
    # tratados/114738: the code's index repeats every heading of its body, and
    # its seven parts hold chapters `1.1`, `2.1`, ... that restart per part
    text = _read("codigo_indice_partes.md")
    tree = parse_markdown(text)
    types = [n.akn_type for n in _walk(tree)]
    assert types.count("toc") == 1
    assert [n.num for n in _walk(tree) if n.akn_type == "part"] == ["1", "2"]
    assert [n.num for n in _walk(tree) if n.akn_type == "attachment"] == ["A", "B"]
    assert not _repeated(tree)
    # the body's chapters sit inside their Parte, the index entries are no article
    chapters = {n.eId for n in _walk(tree) if n.akn_type == "chapter"}
    assert chapters == {"part_1__cap_1.1", "part_2__cap_2.1"}
    units = text_units(text, split_articles=False)
    assert [u.unit_type for u in units].count("toc") == 1
    assert [u.eId for u in units if u.unit_type == "article"][0] == "part_1__cap_1.1__art_1.1.0"
    assert coverage(tree, units).uncovered_chars == 0


def test_a_marker_alone_never_swallows_text():
    text = "**ÍNDICE**\n\nTexto del índice.\n\n**Artículo 1o.** Uno.\n"
    tree = parse_markdown(text)
    assert not [n for n in _walk(tree) if n.akn_type == "toc"]
    assert [a.num for a in _articles(tree)] == ["1o"]


# -- rule 17: recitals are not provisions ------------------------------------


def test_numbered_recitals_are_not_articles():
    # lineamientos/109459 and friends: `PRIMERO.-` .. `TERCERO.-` under
    # CONSIDERANDO are the preamble; the same words under ACUERDA are articles
    tree = parse_markdown(_read("acuerdo_considerando_ordinal.md"))
    assert [a.num for a in _articles(tree)] == ["PRIMERO", "SEGUNDO", "TERCERO", "CUARTO", "QUINTO"]
    assert not _repeated(tree)


def test_unnumbered_recitals_leave_the_numbered_paragraphs_to_be_articles():
    text = (
        "**CONSIDERANDO**\n\nQue la ley lo prevé.\n\nQue es necesario.\n\n"
        "**ACUERDO**\n\n1. Uno.\n\n2. Dos.\n\n3. Tres.\n\n4. Cuatro.\n\n5. Cinco.\n"
    )
    tree = parse_markdown(text)
    assert [a.num for a in _articles(tree)] == ["1", "2", "3", "4", "5"]


# -- rule 18: a numeral that starts over is a list item -----------------------


def test_restarted_numerals_are_list_items_of_the_provision_above():
    # lineamientos/93817: `1.`..`4.` inside `2.2`, `1.-`..`3.-` inside `4.1`
    tree = parse_markdown(_read("lineamientos_numerales_reinicio.md"))
    assert [a.num for a in _articles(tree)] == [
        "1", "2", "2.1", "2.2", "3", "3.1", "4", "4.1", "5", "6"
    ]
    assert not _repeated(tree)
    units = text_units(_read("lineamientos_numerales_reinicio.md"), split_articles=False)
    item = next(u for u in units if u.eId == "art_2.2")
    assert "Aplicar recursos económicos" in item.text and "Apoyar a las personas" in item.text


def test_numerals_that_only_grow_are_untouched():
    text = "\n\n".join(f"{n}. Disposición número {n}." for n in range(1, 8)) + "\n"
    tree = parse_markdown(text)
    assert [a.num for a in _articles(tree)] == [str(n) for n in range(1, 8)]


@pytest.mark.parametrize(
    "heading",
    [
        "**DISPOSICIONES TRANSITORIAS:**",  # LMEUM, reglamento 429
        "**T R A N S I T O R I O S :**",  # reglamento 1027
        "**TRANSITORIO (SIC)**",  # reglamento 1791
    ],
)
def test_the_transitorios_headings_the_corpus_spells_unusually(heading):
    # their `ARTICULO 1°` is a transitory article, never the instrument's own
    text = (
        "**ARTICULO 1°.-** Uno.\n\n**ARTICULO 2°.-** Dos.\n\n"
        f"{heading}\n\n**ARTICULO 1°.-** Este reglamento entra en vigor.\n"
    )
    tree = parse_markdown(text)
    assert [a.num for a in _articles(tree)] == ["1°", "2°"]
    assert not _repeated(tree)

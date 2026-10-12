"""Rule 10 (issue #279): a roman-numbered article opener is an article.

International treaties number their own articles `ARTÍCULO I`, `ARTÍCULO II`,
... — 6,646 such openers over 489 of the cached `scjn-tratados`. The rule is
part of `NUM_ARTICULO`, so the builder, `modo_sin_articulos` and `clasifica`
all see it at once, and it is unconditional.
"""

from pathlib import Path

import pytest

from md2akn import parse_markdown
from md2akn.patterns import ARTICULO
from md2akn.segmenter import iter_blocks
from md2akn.structure import modo_sin_articulos
from md2akn.units import coverage, text_units

FIXTURE = Path(__file__).parent / "fixtures_units" / "tratado_romano.md"


def _num(line: str):
    # `clasifica` matches against the line with its bold markers removed.
    m = ARTICULO.match(line.replace("**", ""))
    return None if m is None else (m.group("num"), m.group("sep").strip())


@pytest.mark.parametrize(
    "line, expected",
    [
        ("ARTÍCULO I", ("I", "")),
        ("**ARTÍCULO IV**", ("IV", "")),
        ("ARTÍCULO I.", ("I", ".")),
        ("**ARTÍCULO I.**", ("I", ".")),
        ("ARTÍCULO I.-", ("I", ".-")),
        ("**Artículo XIV.-** Texto.", ("XIV", ".-")),
        ("ARTICULO X**", ("X", "")),
        ("ARTÍCULO XV BIS", ("XV BIS", "")),
        ("ARTÍCULO XXX. Texto", ("XXX", ".")),
    ],
)
def test_roman_opener_shapes_match(line, expected):
    assert _num(line) == expected


@pytest.mark.parametrize(
    "line",
    [
        "Artículo del Convenio se aplicará",
        "ARTÍCULO MIL",
        "ARTÍCULO CIVIL",
        "ARTÍCULO IIII",
        "Artículo Ito",
    ],
)
def test_prose_and_malformed_numerals_do_not_match(line):
    assert _num(line) is None


def test_ordinal_word_is_still_an_ordinal_word():
    # `V` + `IGÉSIMO` would be a roman reading; the lookahead forbids it.
    # A bare tens word was never an article number (`ORDINAL_PALABRA` wants a
    # unit after it); it still is not, and it is not `V` either.
    assert _num("ARTÍCULO VIGÉSIMO") is None
    assert _num("ARTÍCULO VIGÉSIMO PRIMERO.-") == ("VIGÉSIMO PRIMERO", ".-")
    assert _num("ARTÍCULO DÉCIMO") == ("DÉCIMO", "")
    assert _num("ARTÍCULO ÚNICO.-") == ("ÚNICO", ".-")


def test_digit_articles_are_unchanged():
    assert _num("**ARTICULO 27 Bis.-** Texto") == ("27 Bis", ".-")
    assert _num("Artículo 5 A las personas") == ("5", "")


def test_plural_heading_keeps_its_reading():
    # `ARTICULOS I y II` is read as article `I` — the same reading the digit
    # form `ARTICULOS 1 y 2` has always had. Pinned, not designed (#279).
    assert _num("ARTICULOS I y II") == ("I", "")
    assert _num("ARTICULOS 1 y 2") == ("1", "")


def test_treaty_yields_one_article_unit_per_roman_article():
    text = FIXTURE.read_text(encoding="utf-8")
    units = text_units(text, split_articles=False)
    articles = [u for u in units if u.unit_type == "article"]
    assert [u.eId for u in articles] == [
        "art_UNICO", "art_I", "art_II", "art_III", "art_IV", "art_V",
    ]
    assert len({u.eId for u in articles}) == len(articles)


def test_paragraphs_stay_inside_their_roman_article():
    text = FIXTURE.read_text(encoding="utf-8")
    # rule 8 must not fire: the gate now sees articles.
    assert modo_sin_articulos(list(iter_blocks(text))) is None
    article_iii = next(
        u for u in text_units(text, split_articles=False) if u.eId == "art_III"
    )
    for marker in ("1. Las Partes", "2. Las autoridades", "3. Cada Parte"):
        assert marker in article_iii.text


def test_coverage_holds_on_the_treaty():
    text = FIXTURE.read_text(encoding="utf-8")
    tree = parse_markdown(text)
    for split in (True, False):
        cov = coverage(tree, text_units(text, split_articles=split))
        assert cov.uncovered_chars == 0, cov


def test_roman_and_arabic_eids_never_collide():
    text = (
        "**ARTÍCULO I**\n\nUno romano.\n\n**ARTÍCULO 1**\n\nUno arábigo.\n\n"
        "**ARTÍCULO I**\n\nOtra vez.\n"
    )
    eids = [u.eId for u in text_units(text, split_articles=False)]
    assert eids == ["art_I", "art_1", "art_I_2"]


def test_numeral_paragraphs_of_a_roman_treaty_are_not_articles():
    # Before rule 10 this document had no article, so rule 8 read every
    # `1.` / `2.` paragraph as its own "article" and lost the roman heading.
    text = (
        "**ARTÍCULO I**\n\n1. Uno.\n\n2. Dos.\n\n3. Tres.\n\n4. Cuatro.\n\n"
        "5. Cinco.\n\n6. Seis.\n"
    )
    assert modo_sin_articulos(list(iter_blocks(text))) is None
    units = text_units(text, split_articles=False)
    assert [u.unit_type for u in units] == ["article"]

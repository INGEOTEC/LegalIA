"""Rules 19-23 (review fix-2 of #291): an instrument written with the outline
the SCJN's API states -- one marker line before each block -- is built from
that outline, and uses the text heuristics only inside a block.

`fixtures_units/outline_tratado.md` and `outline_decreto.md` are `scjn.api`'s
own output for real API blocks (cut down): the tratado 96728 (an annex that
holds eleven parts, each with an appendix, behind an index) and the
reglamento 103948 (whose last reform decree reprints the whole instrument as
its `ANEXO ÚNICO`). `packages/scjn/tests/test_outline.py` regenerates both,
byte for byte, from the API blocks, so the marker grammar this package reads
cannot drift from the one that is written.
"""

from pathlib import Path

from md2akn import parse_markdown
from md2akn.outline import lee_marcador
from md2akn.units import coverage, text_units

FIXTURES = Path(__file__).parent / "fixtures_units"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _walk(node):
    yield node
    for child in node.children:
        yield from _walk(child)


def _outline(node, depth=0):
    """``[(depth, akn_type, num)]`` of the structural nodes."""
    rows = []
    for child in node.children:
        if child.akn_type in ("content", "paragraph", "point", "subpoint", "intro", "table"):
            continue
        rows.append((depth, child.akn_type, child.num))
        rows.extend(_outline(child, depth + 1))
    return rows


def test_lee_marcador():
    line = '<!-- scjn:block orden=7 numero=1 kind=container type=part num="1" label="PARTE 1" -->'
    assert lee_marcador(line) == {
        "orden": "7", "numero": "1", "kind": "container", "type": "part", "num": "1",
        "label": "PARTE 1",
    }
    assert lee_marcador("<!-- otro -->") is None
    assert lee_marcador("PARTE 1") is None


# -- the annex that holds the parts ---------------------------------------


def test_annex_holds_its_parts_and_each_part_its_appendix():
    tree = parse_markdown(_read("outline_tratado.md"))
    body = next(c for c in tree.children if c.akn_type == "body")
    top = [(c.akn_type, c.num) for c in body.children if c.akn_type not in ("article", "content")]
    # the index is a toc, not containers; the block that holds articles `1.1`
    # and `1.2` in its own text is an implicit section (rule 23); ANEXO 1 and
    # ANEXO 2 are siblings
    assert top == [
        ("toc", "1"), ("section", "1 ÁMBITO"), ("attachment", "1"), ("attachment", "2"),
    ]
    anexo1 = body.find("att_1")
    assert [(c.akn_type, c.num) for c in anexo1.children if c.akn_type == "part"] == [
        ("part", "1"), ("part", "2"),
    ]
    part1 = anexo1.find("att_1__part_1")
    # an `APÉNDICE` heading the *text* of the PARTE 1 block contains belongs to that part
    assert [c.akn_type for c in part1.children if c.akn_type == "attachment"] == ["attachment"]
    assert part1.heading == "ENSAYO DE INCOMBUSTIBILlDAD"


def test_the_same_number_in_two_parts_is_not_a_repeat():
    tree = parse_markdown(_read("outline_tratado.md"))
    eids = [n.eId for n in _walk(tree) if n.akn_type == "article"]
    # the implicit section's own `1.1` and Part 1's `1.1` are two identifiers
    assert "sec_1_ÁMBITO__art_1.1" in eids and "att_1__part_1__art_1.1" in eids
    assert not any(n.repeated for n in _walk(tree))
    assert len(eids) == len(set(eids))


def test_a_number_repeated_in_one_container_is_flagged():
    """The other side of the definition: two provisions that end up with the
    same identifier -- both of them -- are the ones one of which needed a `_2`."""
    text = (
        '<!-- scjn:block orden=1 numero=0 kind=container type=chapter num="I" label="CAPÍTULO I" -->\n\n'
        "**CAPÍTULO I**\n\n"
        '<!-- scjn:block orden=2 numero=1 kind=article num="1" label="ARTÍCULO 1" -->\n\n'
        "**Artículo 1.-** Uno.\n\n"
        '<!-- scjn:block orden=3 numero=1 kind=article num="1" label="ARTÍCULO 1" -->\n\n'
        "**Artículo 1.-** Otra vez.\n\n"
        '<!-- scjn:block orden=4 numero=0 kind=container type=chapter num="II" label="CAPÍTULO II" -->\n\n'
        "**CAPÍTULO II**\n\n"
        '<!-- scjn:block orden=5 numero=1 kind=article num="1" label="ARTÍCULO 1" -->\n\n'
        "**Artículo 1.-** En otro capítulo.\n"
    )
    arts = [n for n in _walk(parse_markdown(text)) if n.akn_type == "article"]
    assert [(n.eId, n.repeated) for n in arts] == [
        ("cap_I__art_1", True), ("cap_I__art_1_2", True), ("cap_II__art_1", False),
    ]


def test_the_index_is_a_toc_even_though_its_text_opens_with_anexo():
    tree = parse_markdown(_read("outline_tratado.md"))
    toc = next(n for n in _walk(tree) if n.akn_type == "toc")
    assert "**ANEXO**" in toc.text and "9 LISTA DE REFERENCIAS" in toc.text
    # nothing inside it was read as a container or an article
    assert toc.children == []


# -- the reform decree that reprints the instrument -----------------------


def test_a_decree_keeps_what_it_reprints():
    tree = parse_markdown(_read("outline_decreto.md"))
    body = next(c for c in tree.children if c.akn_type == "body")
    own = [n for n in _walk(body) if n.akn_type == "article" and n.eId.startswith("tit_")
           or n.akn_type == "article" and n.eId.startswith("art_")]
    # the instrument's own articles are not doubled by the reprint
    assert sorted(n.num for n in body.find("tit_PRIMERO").children[0].children
                  if n.akn_type == "article") == ["1", "3"]
    decree = body.find("sec_10_DE_NOVIEMBRE_DE_2023")
    assert decree.akn_type == "section" and decree.refers_to == "#transitorios"
    annex = decree.find("sec_10_DE_NOVIEMBRE_DE_2023__att_ÚNICO")
    assert annex is not None and annex.akn_type == "attachment"
    # its titles, chapters and articles hang under the annex, inside the decree
    assert {n.akn_type for n in _walk(annex)} >= {"title", "chapter", "article"}
    # the decree's own transitorios nest under it instead of ending it
    assert decree.find("sec_10_DE_NOVIEMBRE_DE_2023__sec_transitorios") is not None
    assert not any(n.repeated for n in _walk(tree))
    assert own  # the instrument's own articles are there


def test_decrees_are_siblings():
    tree = parse_markdown(_read("outline_decreto.md"))
    body = next(c for c in tree.children if c.akn_type == "body")
    assert [c.num for c in body.children if c.refers_to == "#transitorios"] == [
        "10 DE NOVIEMBRE DE 2023", "28 DE NOVIEMBRE DE 2023",
    ]


# -- a marker is metadata, not text ---------------------------------------


def test_markers_are_never_embedded_and_coverage_holds():
    for name in ("outline_tratado.md", "outline_decreto.md"):
        text = _read(name)
        tree = parse_markdown(text)
        units = text_units(tree)
        assert units and not any("scjn:block" in u.text for u in units)
        # a marker's characters are accounted for like a reform annotation's
        assert coverage(tree, units).uncovered_chars == 0


# -- rules 22-24 -----------------------------------------------------------


def test_an_article_label_that_disagrees_with_its_text_does_not_collide():
    """The API's label is not always the article's number: `ARTÍCULO 6` on a
    block whose text says `ARTICULO 7`. The text's number wins when the label
    does not extend it, so the two articles are two identifiers."""
    text = (
        '<!-- scjn:block orden=1 numero=6 kind=article num="6" label="ARTÍCULO 6" -->\n\n'
        "**Artículo 6.-** Seis.\n\n"
        '<!-- scjn:block orden=2 numero=7 kind=article num="6" label="ARTÍCULO 6" -->\n\n'
        "**Artículo 7.-** Siete.\n"
    )
    arts = [n for n in _walk(parse_markdown(text)) if n.akn_type == "article"]
    assert [n.eId for n in arts] == ["art_6", "art_7"]
    assert not any(n.repeated for n in arts)


def test_an_article_block_that_quotes_another_article_stays_one_article():
    """Rule 24: inside an `article` block no line opens structure -- an
    amendment article quoting `ARTÍCULO 5.-` is that article's own text."""
    text = (
        '<!-- scjn:block orden=1 numero=1 kind=article num="1" label="ARTÍCULO 1" -->\n\n'
        "**Artículo 1.-** Se reforma el siguiente artículo:\n\n"
        "**ARTÍCULO 5.-** Texto citado.\n"
    )
    tree = parse_markdown(text)
    arts = [n for n in _walk(tree) if n.akn_type == "article"]
    assert [n.eId for n in arts] == ["art_1"]
    assert "Texto citado." in arts[0].text


def test_a_block_that_holds_articles_in_its_own_text_opens_a_section():
    """Rule 23: an `other` block carrying articles opens an implicit section,
    so a number the next block restarts is not a repeat."""
    text = (
        '<!-- scjn:block orden=1 numero=0 kind=other label="1 ÁMBITO" -->\n\n'
        "**1 ÁMBITO**\n\n"
        "**Artículo 1.1:** Uno.\n\n"
        '<!-- scjn:block orden=2 numero=0 kind=other label="2 OTRO" -->\n\n'
        "**2 OTRO**\n\n"
        "**Artículo 1.1:** Otra vez, en otra sección.\n"
    )
    tree = parse_markdown(text)
    secs = [n for n in _walk(tree) if n.akn_type == "section"]
    assert len(secs) == 2
    assert not any(n.repeated for n in _walk(tree))


def test_a_container_label_written_as_a_table_row_is_a_heading_not_a_table():
    """The SCJN's text puts a tab after some labels, which `scjn.api` reads as
    a two-cell row (`| CAPÍTULO II |  |`); the marker says it is the chapter's
    own heading block, so its cells are the label and the title follows."""
    text = (
        '<!-- scjn:block orden=1 numero=0 kind=container type=chapter num="II" label="CAPÍTULO II" -->\n\n'
        "| CAPÍTULO II |  |\n\n**DE LOS INGRESOS**\n\n"
        '<!-- scjn:block orden=2 numero=1 kind=article num="1" label="ARTÍCULO 1" -->\n\n'
        "**Artículo 1.** Uno.\n"
    )
    tree = parse_markdown(text)
    chapter = next(n for n in _walk(tree) if n.akn_type == "chapter")
    assert chapter.heading == "DE LOS INGRESOS"
    heading = next(u for u in text_units(text, split_articles=False) if u.unit_type == "heading")
    assert "|" not in heading.text and heading.text.startswith("CAPÍTULO II")


def test_a_chapter_the_api_labels_as_an_article_is_a_chapter():
    """The SCJN's own label is wrong for the LIGIE's tariff: `Capítulo 84` is
    served under `ARTÍCULO 84`. The text says container, with the label's own
    number, so it is a chapter; a container with another number printed above an
    article (`Sección A` over `Artículo 3-01`) stays inside the article."""
    text = (
        '<!-- scjn:block orden=1 numero=84 kind=article num="84" label="ARTÍCULO 84" -->\n\n'
        "Capítulo 84\n\nReactores nucleares\n\n"
        '<!-- scjn:block orden=2 numero=85 kind=article num="85" label="ARTÍCULO 85" -->\n\n'
        "Capítulo 85\n\nMáquinas eléctricas\n\n"
        '<!-- scjn:block orden=3 numero=3 kind=article num="3" label="ARTÍCULO  3" -->\n\n'
        "Sección A - Ámbito\n\n**Artículo 3-01**: Texto.\n"
    )
    tree = parse_markdown(text)
    assert [(n.akn_type, n.num) for n in _walk(tree) if n.akn_type in ("chapter", "article")] == [
        ("chapter", "84"), ("chapter", "85"), ("article", "3"),
    ]

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
    # the index is a toc, not containers; ANEXO 1 and ANEXO 2 are siblings
    assert top == [("toc", "1"), ("attachment", "1"), ("attachment", "2")]
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
    # the body's own `1.1` (the Code's) and Part 1's `1.1` are two identifiers
    assert "art_1.1" in eids and "att_1__part_1__art_1.1" in eids
    assert not any(n.repeated for n in _walk(tree))
    assert len(eids) == len(set(eids))


def test_a_number_repeated_in_one_container_is_flagged():
    """The other side of the definition: two provisions that end up with the
    same identifier are exactly the ones whose eId needed a `_2`."""
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
        ("cap_I__art_1", False), ("cap_I__art_1_2", True), ("cap_II__art_1", False),
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

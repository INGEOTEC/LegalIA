"""Invariant I5 and rules 44 to 47 (review fix-2 of #300 and #301, found after the fact).

The SCJN's API serves an instrument as a list of blocks, each with a marker (`kind`,
`type`, `num`, `label`), and that outline has to organise the document first: the text
rules may only refine *inside* a block. The review found them overruling it -- a block's
own label line read as content and compared as text, a `kind=other` block renamed
`section` (with a `SECCIÓN` the SCJN never wrote) because articles follow it, an
annotation pulled into the node of the next block -- and nobody had measured where else
the tree disagreed with the markers. `md2akn.validate.block_conformance` is that
measurement, and every case below is one of the shapes it found, cut down from the real
snapshot it was found in (the Acta de Ginebra 143097, the Código Civil Federal, reglamento
53, lineamiento 72248, the Radio Regulations' and IMDG treaties).
"""

import hashlib
import json
from pathlib import Path

from md2akn import parse_markdown, text_units
from md2akn.validate import _block_escapes, block_conformance

M = "<!-- scjn:block "
FIXTURES = Path(__file__).parent / "fixtures"
UNIT_FIXTURES = Path(__file__).parent / "fixtures_units"


def _walk(node):
    yield node
    for child in node.children:
        yield from _walk(child)


def _real(disagreements):
    return [d for d in disagreements if not d.documented]


def _units(text):
    return text_units(parse_markdown(text), split_articles=False)


ACTA = (
    f'{M}orden=1 numero=0 kind=header label="ENCABEZADO" -->\n\n'
    "**ACTA DE GINEBRA**\n\n"
    f'{M}orden=9 numero=0 kind=other label="DISPOSICIONES PRELIMINARES" -->\n\n'
    "**DISPOSICIONES PRELIMINARES**\n\n"
    f'{M}orden=10 numero=1 kind=article num="1" label="ARTÍCULO 1" -->\n\n'
    "**Artículo 1**\n\nExpresiones abreviadas\n\nA los fines de la presente Acta:\n\n"
    f'{M}orden=11 numero=2 kind=article num="2" label="ARTÍCULO 2" -->\n\n'
    "**Artículo 2**\n\nAplicación de otra protección.\n"
)


def test_a_block_that_is_only_its_label_is_a_heading_and_never_compared():
    """Acta de Ginebra 143097: `DISPOSICIONES PRELIMINARES` is an `other` block holding
    only its own label line; articles 1 and 2 follow as blocks of their own. It used to
    open a `section` (a `SECCIÓN` the SCJN never said) whose only content was that line,
    an `intro` unit that was compared."""
    tree = parse_markdown(ACTA)
    block = next(n for n in _walk(tree) if n.num == "DISPOSICIONES PRELIMINARES")
    assert block.akn_type == "hcontainer"
    assert block.eId == "hc_DISPOSICIONES_PRELIMINARES"
    assert not [n for n in _walk(tree) if n.akn_type in ("section", "intro")]
    assert [a.eId for a in block.children] == [
        "hc_DISPOSICIONES_PRELIMINARES__art_1", "hc_DISPOSICIONES_PRELIMINARES__art_2"]
    units = _units(ACTA)
    heading = next(u for u in units if u.unit_type == "heading")
    assert heading.text == "**DISPOSICIONES PRELIMINARES**"
    assert heading.eId == "hc_DISPOSICIONES_PRELIMINARES"
    assert not any(u.unit_type == "intro" for u in units)
    # the heading is a heading unit (neither source nor candidate); the first compared
    # excerpt is article 1, and its path carries the label with no `SECCIÓN` in front
    compared = [u for u in units if u.unit_type not in ("heading", "preamble")]
    assert compared[0].eId.endswith("__art_1")
    assert compared[0].path == ("DISPOSICIONES PRELIMINARES",)
    assert _real(block_conformance(tree)) == []


def test_the_label_line_is_found_case_accent_and_punctuation_blind():
    for line in ("Disposiciones preliminares", "**DISPOSICIONES  PRELIMINARES:**", "**Disposiciones Preliminares.**"):
        text = ACTA.replace("**DISPOSICIONES PRELIMINARES**", line)
        tree = parse_markdown(text)
        assert not any(n.akn_type == "intro" for n in _walk(tree)), line
        assert [u.unit_type for u in _units(text)].count("heading") == 1, line


def test_a_numeral_before_the_label_is_part_of_the_heading():
    """Lineamiento 72248: the block labelled `CONSIDERANDOS` prints `III. CONSIDERANDOS`."""
    text = (
        f'{M}orden=1 numero=0 kind=other label="CONSIDERANDOS" -->\n\n'
        "**III. CONSIDERANDOS**\n\n"
        "Que el Tribunal considera lo siguiente.\n"
    )
    tree = parse_markdown(text)
    block = next(n for n in _walk(tree) if n.akn_type == "hcontainer")
    assert [c.akn_type for c in block.children] == ["intro"]
    units = _units(text)
    assert [(u.unit_type, u.text) for u in units if u.unit_type == "heading"] == [
        ("heading", "**III. CONSIDERANDOS**")]
    assert [u.text for u in units if u.unit_type == "intro"] == [
        "CONSIDERANDOS Que el Tribunal considera lo siguiente."]
    assert _real(block_conformance(tree)) == []


def test_a_line_that_only_starts_with_the_label_is_content():
    """Not the whole line: `Finalidad. Esta norma ...` is text, and stays text."""
    text = (
        f'{M}orden=1 numero=0 kind=other label="Finalidad." -->\n\n'
        "Finalidad. Esta norma regula el servicio.\n"
    )
    tree = parse_markdown(text)
    block = next(n for n in _walk(tree) if n.akn_type == "hcontainer")
    assert [c.akn_type for c in block.children] == ["intro"]
    assert not [u for u in _units(text) if u.unit_type == "heading"]


def test_what_the_court_prints_above_the_label_line_describes_that_heading():
    """Rule 44: `(REFORMADO, D.O.F. ...)` and the court's `(NOTA: ...)` above the label line
    are the heading's annotations, not the block's first paragraph."""
    text = (
        f'{M}orden=1 numero=0 kind=other label="G.O. 9 DE SEPTIEMBRE DE 2009" -->\n\n'
        "(NOTA: EL PLENO DE LA SUPREMA CORTE DECLARÓ LA INVALIDEZ DE ESTA LEY.)\n\n"
        "**(REFORMADO, D.O.F. 5 DE NOVIEMBRE DE 2014)**\n\n"
        "**G.O. 9 DE SEPTIEMBRE DE 2009.**\n\n"
        "**ARTÍCULO PRIMERO.** Publíquese.\n"
    )
    tree = parse_markdown(text)
    block = next(n for n in _walk(tree) if n.akn_type == "hcontainer")
    assert block.start_char < min(c.start_char for c in block.children)
    assert not [n for n in _walk(tree) if n.akn_type == "intro"]
    assert len(block.notes) == 2
    assert _real(block_conformance(tree)) == []


def test_the_caption_rule_does_not_take_the_label_line():
    """Lineamiento 72769: `ACTA DE CERTIFICACION` stands above a table of key/value rows;
    it is the block's heading, not the table's caption."""
    text = (
        f'{M}orden=1 numero=0 kind=other label="ACTA DE CERTIFICACIÓN" -->\n\n'
        "**ACTA DE CERTIFICACION**\n\n"
        "| SESIONES: | 6ª SESION ORDINARIA DE 2009 |\n\n| FECHA: | 17-VI-2009 |\n"
    )
    tree = parse_markdown(text)
    table = next(n for n in _walk(tree) if n.akn_type == "table")
    assert not table.heading
    assert _real(block_conformance(tree)) == []


def test_a_trailing_annotation_belongs_to_the_block_it_was_read_in():
    """Rule 46, the Código Civil Federal's articles 187 and 188: `(DEROGADO SEGUNDO
    PÁRRAFO, ...)` stands after article 187's last paragraph. Held for "the next node" it
    was pulled into article 188, whose span began inside article 187's block, before its
    own marker."""
    text = (
        f'{M}orden=1 numero=187 kind=article num="187" label="ARTÍCULO 187" -->\n\n'
        "**(REFORMADO PRIMER PÁRRAFO, D.O.F. 3 DE JUNIO DE 2019)**\n\n"
        "**ARTICULO 187.-** La sociedad conyugal puede terminar.\n\n"
        "**(DEROGADO SEGUNDO PÁRRAFO, D.O.F. 3 DE JUNIO DE 2019)**\n\n"
        f'{M}orden=2 numero=188 kind=article num="188" label="ARTÍCULO 188" -->\n\n'
        "**(REFORMADO PRIMER PÁRRAFO, D.O.F. 27 DE DICIEMBRE DE 1983)**\n\n"
        "**ARTICULO 188.-** Puede también terminar la sociedad conyugal.\n\n"
        "I.- Si el socio administrador amenaza arruinar a su consocio;\n"
    )
    tree = parse_markdown(text)
    doc = tree.span.doc.text
    a187, a188 = (n for n in _walk(tree) if n.akn_type == "article")
    assert len(a187.notes) == 2 and len(a188.notes) == 1
    assert a188.start_char >= doc.index("orden=2 ") - 20
    assert a187.end_char <= doc.index(f"{M}orden=2") + 1
    assert _block_escapes(tree) == [] and block_conformance(tree) == []
    # the text of an article is what it was: annotations are never part of a unit's text
    assert [u.text for u in _units(text) if u.unit_type == "article"][0].endswith("puede terminar.")


def test_a_part_holds_the_titles_the_next_marker_opens():
    """Rule 45, the Código Civil Federal's fourth book: `PRIMERA PARTE`, then its titles;
    the next part is the part's sibling, inside the book."""
    blocks = [
        ("container", 'type=book num="CUARTO" label="LIBRO CUARTO"', "**LIBRO CUARTO**"),
        ("container", 'type=part num="PRIMERA" label="PRIMERA PARTE"', "Primera Parte"),
        ("container", 'type=title num="PRIMERO" label="TÍTULO PRIMERO"', "**TÍTULO PRIMERO**"),
        ("article", 'num="1792" label="ARTÍCULO 1792"', "**ARTICULO 1792.-** Uno."),
        ("container", 'type=title num="SEGUNDO" label="TÍTULO SEGUNDO"', "**TÍTULO SEGUNDO**"),
        ("article", 'num="1793" label="ARTÍCULO 1793"', "**ARTICULO 1793.-** Dos."),
        ("container", 'type=part num="SEGUNDA" label="SEGUNDA PARTE"', "Segunda Parte"),
        ("container", 'type=title num="PRIMERO" label="TÍTULO PRIMERO"', "**TÍTULO PRIMERO**"),
        ("article", 'num="2000" label="ARTÍCULO 2000"', "**ARTICULO 2000.-** Tres."),
    ]
    text = "\n\n".join(
        f"{M}orden={i} numero=0 kind={kind} {attrs} -->\n\n{line}"
        for i, (kind, attrs, line) in enumerate(blocks, 1)
    ) + "\n"
    tree = parse_markdown(text)
    book = next(n for n in _walk(tree) if n.akn_type == "book")
    assert [(c.akn_type, c.num) for c in book.children] == [("part", "PRIMERA"), ("part", "SEGUNDA")]
    first, second = book.children
    assert [(c.akn_type, c.num) for c in first.children] == [("title", "PRIMERO"), ("title", "SEGUNDO")]
    assert [(c.akn_type, c.num) for c in second.children] == [("title", "PRIMERO")]
    assert block_conformance(tree) == []


def test_a_part_that_holds_chapters_closes_the_chapter_open_before_it():
    """Rule 45, a treaty whose `SEGUNDA PARTE` holds its chapters VIII and IX: a chapter
    open before the part is not its parent."""
    blocks = [
        ("container", 'type=chapter num="VII" label="CAPÍTULO VII"', "**CAPÍTULO VII**"),
        ("article", 'num="1" label="ARTÍCULO 1"', "**Artículo 1.-** Uno."),
        ("container", 'type=part num="SEGUNDA" label="SEGUNDA PARTE"', "Segunda Parte"),
        ("container", 'type=chapter num="VIII" label="CAPÍTULO VIII"', "**CAPÍTULO VIII**"),
        ("article", 'num="2" label="ARTÍCULO 2"', "**Artículo 2.-** Dos."),
        ("container", 'type=chapter num="IX" label="CAPÍTULO IX"', "**CAPÍTULO IX**"),
        ("article", 'num="3" label="ARTÍCULO 3"', "**Artículo 3.-** Tres."),
    ]
    text = "\n\n".join(
        f"{M}orden={i} numero=0 kind={kind} {attrs} -->\n\n{line}"
        for i, (kind, attrs, line) in enumerate(blocks, 1)
    ) + "\n"
    tree = parse_markdown(text)
    body = next(c for c in tree.children if c.akn_type == "body")
    assert [(c.akn_type, c.num) for c in body.children] == [("chapter", "VII"), ("part", "SEGUNDA")]
    assert [(c.akn_type, c.num) for c in body.children[1].children] == [
        ("chapter", "VIII"), ("chapter", "IX")]
    assert block_conformance(tree) == []


def test_a_part_whose_next_marker_is_an_article_keeps_the_document_reading():
    """SOLAS: `Parte A` and its regulations sit inside the chapter that precedes them."""
    blocks = [
        ("container", 'type=chapter num="II-2" label="CAPÍTULO II-2"', "**CAPÍTULO II-2**"),
        ("container", 'type=part num="A" label="PARTE A"', "**PARTE A**"),
        ("article", 'num="1" label="ARTÍCULO 1"', "**Artículo 1.-** Uno."),
        ("container", 'type=part num="B" label="PARTE B"', "**PARTE B**"),
        ("article", 'num="2" label="ARTÍCULO 2"', "**Artículo 2.-** Dos."),
    ]
    text = "\n\n".join(
        f"{M}orden={i} numero=0 kind={kind} {attrs} -->\n\n{line}"
        for i, (kind, attrs, line) in enumerate(blocks, 1)
    ) + "\n"
    tree = parse_markdown(text)
    chapter = next(n for n in _walk(tree) if n.akn_type == "chapter")
    assert [c.num for c in chapter.children if c.akn_type == "part"] == ["A", "B"]
    assert block_conformance(tree) == []


def test_a_provision_block_is_one_text():
    """Rule 47, the IMDG Code's `1.1.2 Convenios`: one `provision` block that lists the
    conventions with their own `Capítulo` and `Anexo` lines. They opened an `ANEXO I` at
    the body level that swallowed every chapter after it."""
    blocks = [
        ("container", 'type=chapter num="1.1" label="CAPÍTULO 1.1"', "**CAPÍTULO 1.1**"),
        ("provision", 'num="1.1.2" label="1.1.2"',
         "1.1.2 Convenios\n\nAnexo I\n\nReglas para la prueba\n\nCapítulo VII\n\nTransporte"),
        ("container", 'type=chapter num="1.2" label="CAPÍTULO 1.2"', "**CAPÍTULO 1.2**"),
        ("provision", 'num="1.2.1" label="1.2.1"', "1.2.1 Texto."),
    ]
    text = "\n\n".join(
        f"{M}orden={i} numero=0 kind={kind} {attrs} -->\n\n{line}"
        for i, (kind, attrs, line) in enumerate(blocks, 1)
    ) + "\n"
    tree = parse_markdown(text)
    assert not [n for n in _walk(tree) if n.akn_type == "attachment"]
    body = next(c for c in tree.children if c.akn_type == "body")
    assert [(c.akn_type, c.num) for c in body.children] == [("chapter", "1.1"), ("chapter", "1.2")]
    assert block_conformance(tree) == []


def test_a_block_with_a_marker_for_every_kind_is_typed_as_the_marker_says():
    text = (
        f'{M}orden=1 numero=0 kind=header label="ENCABEZADO" -->\n\n**TÍTULO**\n\n'
        f'{M}orden=2 numero=0 kind=toc label="ÍNDICE" -->\n\n**ÍNDICE**\n\n**CAPÍTULO I**\n\n'
        f'{M}orden=3 numero=0 kind=container type=chapter num="I" label="CAPÍTULO I" -->\n\n**CAPÍTULO I**\n\n'
        f'{M}orden=4 numero=1 kind=article num="1" label="ARTÍCULO 1" -->\n\n**Artículo 1.-** Uno.\n\n'
        f'{M}orden=5 numero=0 kind=other label="NOTA EXPLICATIVA" -->\n\n**NOTA EXPLICATIVA**\n\nTexto de la nota.\n\n'
        f'{M}orden=6 numero=0 kind=transitorios label="TRANSITORIOS" -->\n\n**TRANSITORIOS**\n\n'
        "**Artículo Primero.-** Entra en vigor.\n"
    )
    tree = parse_markdown(text)
    assert block_conformance(tree) == []


def test_a_document_without_markers_has_no_blocks_and_is_read_as_before():
    """Every rule of this file applies only to a document with outline markers. The digests
    were computed with the parser as it stood before them, over every marker-free fixture."""
    digests = json.loads((UNIT_FIXTURES / "marker_free_digests.json").read_text(encoding="utf-8"))
    seen = 0
    for path in sorted(list(FIXTURES.glob("*.md")) + list(UNIT_FIXTURES.glob("*.md"))):
        text = path.read_text(encoding="utf-8")
        if "<!-- scjn:block" in text:
            continue
        tree = parse_markdown(text)
        assert block_conformance(tree) == [], path.name
        rows = [f"{n.akn_type} {n.eId} {n.num} {n.refers_to} {n.start_char} {n.end_char} "
                f"{int(n.repeated)} {n.heading}" for n in tree.walk()]
        units = [f"{u.unit_type} {u.eId} {u.piece} {u.text_sha1} {u.start_char} {u.end_char}"
                 for u in text_units(tree)]
        got = {"tree": hashlib.sha1("\n".join(rows).encode()).hexdigest(),
               "units": hashlib.sha1("\n".join(units).encode()).hexdigest(), "nodes": len(rows)}
        assert got == digests[path.name], path.name
        seen += 1
    assert seen == len(digests) > 10


# -- the real snapshots the cases above were cut from (skipped where they are not cached) --

SCJN = Path(__file__).resolve().parents[3] / "scripts" / "scjn"


def _real_snapshot(collection, key, name):
    path = SCJN / collection / key / name
    if not path.exists():
        import pytest
        pytest.skip(f"{path} is not on disk")
    return parse_markdown(path.read_text(encoding="utf-8"))


def test_real_143097_first_compared_excerpt_is_article_1():
    tree = _real_snapshot("tratados", "143097", "05-06-2020.md")
    block = next(n for n in _walk(tree) if n.num == "DISPOSICIONES PRELIMINARES")
    assert block.akn_type == "hcontainer"
    assert [c.akn_type for c in block.children] == ["article", "article"]
    units = text_units(tree, split_articles=False)
    compared = [u for u in units if u.unit_type not in ("heading", "toc") and not u.envelope
                and "TRANSITORIOS" not in " ".join(u.path)]
    assert compared[0].eId.endswith("__art_1")
    assert [u.text for u in units if u.eId == "hc_DISPOSICIONES_PRELIMINARES"] == [
        "**DISPOSICIONES PRELIMINARES**"]


def test_real_151973_an_index_titled_with_the_label_does_not_take_the_label_as_its_own():
    """Reglamento 151973: the block labelled `CONTENIDO` opens with `**CONTENIDO**` and the
    index that follows it. The title line is the heading; the index is the `toc`."""
    tree = _real_snapshot("reglamentos", "151973", "20-10-2021.md")
    block = next(n for n in _walk(tree) if n.eId == "hc_CONTENIDO")
    assert [c.akn_type for c in block.children] == ["toc"]
    doc = tree.span.doc.text
    assert doc[block.start_char:block.children[0].start_char].rstrip().endswith("**CONTENIDO**")
    assert _real(block_conformance(tree)) == []


def test_real_labels_are_headings():
    for collection, key, name, label in (
        ("leyes", "ccf", "14-11-2025.md", "Disposiciones preliminares"),
        ("reglamentos", "53", "20-11-2012.md", "DEL CONDUCTOR"),
        ("lineamientos", "72248", "10-02-2012.md", "III. CONSIDERANDOS"),
    ):
        tree = _real_snapshot(collection, key, name)
        units = text_units(tree, split_articles=False)
        headings = [u for u in units if u.unit_type == "heading" and label.upper() in u.text.upper()]
        assert headings, (key, label)
        assert not [u for u in units if u.unit_type == "intro" and u.text.strip().upper().endswith(label.upper())
                    and len(u.text) < len(label) + 80 + sum(len(p) for p in u.path)], (key, label)
        assert _real(block_conformance(tree)) == [], key

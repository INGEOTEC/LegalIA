"""Rules 34-39 (review fix-6 of #291): an API block is the root of its text.

The owner's rule, stated for the sixth time: the marker `<!-- scjn:block ... -->`
*groups everything that follows it* until the next marker. Rules 29-33 claimed it
and still failed when the `other` block came after a `kind=decree` block (the
Ley Federal del Impuesto sobre Automóviles Nuevos' `MODIFICACIONES AL ANEXO 15`):
`_padre_contenedor` returned the open transitorios section before it looked at the
stack, so every paragraph and table of the block hung from the decree and the
block's own container held only its heading line.

These are the four invariants of the fix as small fixtures, one test each (the
release sweep, `test_units_release_sweep.py`, checks them over the four cached
corpora):

* **I1** `md2akn.validate._block_escapes`: nothing a block's text holds hangs from
  anything but what the block opened;
* **I2** no `loose` unit outside a transitorios section;
* **I3** no repeated article number outside transitorios;
* **I4** a gap in the numbering is the source's, never a parse miss.
"""

import pytest

from md2akn import parse_markdown
from md2akn.units import coverage, text_units
from md2akn.validate import _block_escapes, validate

M = "<!-- scjn:block "

LFISAN = (
    f'{M}orden=1 numero=0 kind=header label="ENCABEZADO" -->\n\n'
    "**LEY FEDERAL DEL IMPUESTO SOBRE AUTOMÓVILES NUEVOS**\n\n"
    f'{M}orden=2 numero=1 kind=container type=chapter num="I" label="CAPÍTULO I" -->\n\n'
    "**CAPÍTULO I**\n\n**DISPOSICIONES GENERALES**\n\n"
    f'{M}orden=3 numero=3 kind=article num="3" label="ARTÍCULO 3" -->\n\n'
    "**Artículo 3.-** El impuesto se calculará aplicando la siguiente:\n\n"
    "**TARIFA**\n\n"
    "| Límite | Límite | Cuota |\n\n| Inferior | Superior | fija |\n\n| 0.01 | 477,347.76 | 0.00 |\n\n"
    f'{M}orden=4 numero=0 kind=transitorios label="TRANSITORIOS" -->\n\n'
    "## Transitorios\n\n**PRIMERO.-** Entra en vigor.\n\n"
    f'{M}orden=5 numero=0 kind=decree num="9 DE ENERO DE 2008" label="D.O.F. 9 DE ENERO DE 2008" -->\n\n'
    "**D.O.F. 9 DE ENERO DE 2008.**\n\n"
    "LA RESOLUCIÓN NO SEÑALA DISPOSICIONES TRANSITORIAS.\n\n"
    f'{M}orden=6 numero=21 kind=other label="MODIFICACIONES AL ANEXO 15" -->\n\n'
    "Modificación al Anexo 15 de la Resolución Miscelánea Fiscal\n\n"
    "Contenido\n\nImpuesto sobre Tenencia\n\n**A.** Tarifa para vehículos nuevos.\n\n"
    "**B.** Tarifa para motocicletas nuevas.\n\n**C.** Tarifa para aeronaves.\n\n"
    "**D.** Tarifa del impuesto.\n\n**E.** Cantidades para aeronaves.\n\n**F.** Cantidades de la fracción II.\n\n"
    "**G.** Código de claves vehiculares.\n\n"
    "**A.** Tarifa para vehículos nuevos\n\n"
    "|  | TARIFA |\n\n| 0.01 | 477,347.76 | 0.00 | 3.0 |\n\n"
    "Si el precio es superior a $532,497.60 se reducirá el impuesto.\n\n"
    "**B.** Tarifa para motocicletas nuevas\n\n"
    "| 0.01 | 100.00 | 0.00 | 2.0 |\n\n"
    "**Artículo 8o. **fracción II primer párrafo: $179,017.26\n\n"
    f'{M}orden=7 numero=0 kind=decree num="2 DE JULIO DE 2008" label="D.O.F. 2 DE JULIO DE 2008" -->\n\n'
    "**D.O.F. 2 DE JULIO DE 2008.**\n\n"
    "NO SEÑALA DISPOSICIONES TRANSITORIAS.\n"
)


def _walk(node):
    yield node
    for child in node.children:
        yield from _walk(child)


def _find(tree, fragment):
    return [n for n in _walk(tree) if fragment in n.eId]


def _loose_outside_transitorios(tree):
    return [
        u for u in text_units(tree, split_articles=False)
        if u.unit_type == "loose" and not any(p.startswith("TRANSITORIOS") for p in u.path)
    ]


# -- the mechanism: a block's container bounds its text --------------------------


def test_the_text_of_a_block_after_a_decree_hangs_from_the_block_not_from_the_decree():
    tree = parse_markdown(LFISAN)
    block = tree.find("sec_9_DE_ENERO_DE_2008__hc_MODIFICACIONES_AL_ANEXO_15") \
        or tree.find("sec_9_DE_ENERO_DE_2008__sec_MODIFICACIONES_AL_ANEXO_15")
    assert block is not None
    decree = block.parent
    assert decree.num == "9 DE ENERO DE 2008"
    # nothing of the block's text is a sibling of the block under the decree
    assert [c.akn_type for c in decree.children if c is not block] == ["content"] or \
        all(c is block or c.akn_type == "intro" for c in decree.children)
    inside = {n.akn_type for n in _walk(block)} - {block.akn_type}
    assert {"toc", "table"} <= inside
    assert len([n for n in _walk(block) if n.akn_type == "table"]) == 2


def test_i1_nothing_a_block_holds_escapes_it():
    tree = parse_markdown(LFISAN)
    assert _block_escapes(tree) == []
    assert validate(tree).ok
    assert coverage(tree, text_units(tree)).uncovered_chars == 0


def test_i1_is_checked_on_the_structure_and_notices_an_escape():
    tree = parse_markdown(LFISAN)
    block = next(n for n in _walk(tree) if n.akn_type == "table"
                 and "MODIFICACIONES" in n.eId).parent
    leaf = next(n for n in _walk(block) if n.akn_type == "table")
    # re-parent a table of the block to the decree that holds the block: the exact
    # defect the owner reported
    block.children.remove(leaf)
    block.parent.children.append(leaf)
    leaf.parent = block.parent
    assert any(v.eId == leaf.eId for v in _block_escapes(tree))


def test_a_document_without_markers_has_no_blocks_and_no_violations():
    tree = parse_markdown("**Artículo 1.-** Uno.\n\n**Artículo 2.-** Dos.\n")
    assert _block_escapes(tree) == []


# -- rule 36: a caption belongs to its table, and a table inside an article stays text --


def test_a_caption_and_the_note_after_the_rows_belong_to_the_table():
    tree = parse_markdown(LFISAN)
    tables = [n for n in _walk(tree) if n.akn_type == "table" and "MODIFICACIONES" in n.eId]
    assert [t.num for t in tables] == ["1", "2"]
    first = tables[0]
    assert first.heading.startswith("**A.** Tarifa para vehículos nuevos") \
        or first.heading.startswith("A. Tarifa para vehículos nuevos")
    text = first.text
    assert text.startswith("**A.** Tarifa para vehículos nuevos")
    assert "Si el precio es superior" in text  # the note after the rows


def test_a_table_inside_an_article_is_not_a_node_and_the_article_is_one_excerpt():
    tree = parse_markdown(LFISAN)
    assert not [n for n in _walk(tree) if n.akn_type == "table" and "art_3" in n.eId]
    article = tree.find("cap_I__art_3")
    assert "| Inferior | Superior | fija |" in article.text
    units = [u for u in text_units(tree, split_articles=False) if u.eId == "cap_I__art_3"]
    assert [u.unit_type for u in units] == ["article"]


# -- I2: loose text is placed -----------------------------------------------------


def test_i2_no_loose_unit_outside_transitorios():
    assert _loose_outside_transitorios(parse_markdown(LFISAN)) == []


def test_a_heading_only_container_is_a_heading_not_loose_text():
    doc = (
        f'{M}orden=1 numero=0 kind=container type=chapter num="I" label="CAPÍTULO I" -->\n\n'
        "**CAPÍTULO I**\n\n**DISPOSICIONES GENERALES**\n\n"
        f'{M}orden=2 numero=0 kind=container type=annex num="1" label="ANEXO 1" -->\n\n'
        "**ANEXO 1**\n"
    )
    tree = parse_markdown(doc)
    types = {u.unit_type for u in text_units(tree, split_articles=False)}
    assert "loose" not in types
    assert "heading" in types
    assert coverage(tree, text_units(tree)).uncovered_chars == 0


def test_lines_before_the_heading_belong_to_the_container_the_block_opens():
    """`[...]` ahead of `Transitorios`, the institution's name above `SEGUNDA PARTE`:
    they used to hang from the container before, the one the block closes."""
    doc = (
        f'{M}orden=1 numero=0 kind=container type=chapter num="XIV" label="CAPÍTULO XIV" -->\n\n'
        "**CAPÍTULO XIV**\n\n**DE LA PRISIÓN**\n\n"
        f'{M}orden=2 numero=1 kind=article num="64" label="ARTÍCULO 64" -->\n\n'
        "**Artículo 64.-** Texto del último artículo.\n\n"
        f'{M}orden=3 numero=0 kind=transitorios label="TRANSITORIOS" -->\n\n'
        "[...]\n\n## Transitorios\n\n**Primero.** Entra en vigor.\n"
    )
    tree = parse_markdown(doc)
    assert not [n for n in _walk(tree) if n.akn_type == "content" and n.text.strip() == "[...]"]
    section = next(n for n in _walk(tree) if n.refers_to == "#transitorios")
    assert "[...]" in section.text
    assert _loose_outside_transitorios(tree) == []
    assert _block_escapes(tree) == []


def test_an_index_ends_with_the_api_block_it_starts_in():
    """An entry listed under two chapters (`Sección Primera`) is not the body's heading,
    and the chapter heading after the marker is not an entry."""
    entries = "\n\n".join([
        "Capítulo Primero", "Disposiciones", "Sección Primera", "Generales",
        "Capítulo Segundo", "Del Gobierno", "Sección Primera", "De las Autoridades",
    ])
    doc = (
        f'{M}orden=1 numero=0 kind=header label="ENCABEZADO" -->\n\n'
        "**REGLAMENTO X**\n\n**ÍNDICE**\n\n" + entries + "\n\n"
        f'{M}orden=2 numero=0 kind=container type=chapter num="I" label="CAPÍTULO I" -->\n\n'
        "**CAPITULO I**\n\n**DISPOSICIONES**\n\n"
        f'{M}orden=3 numero=1 kind=article num="1" label="ARTÍCULO 1" -->\n\n'
        "**Artículo 1.-** Texto.\n"
    )
    tree = parse_markdown(doc)
    chapter = tree.find("cap_I")
    assert chapter is not None and chapter.parent.akn_type == "body"
    toc = next(n for n in _walk(tree) if n.akn_type == "toc")
    assert "De las Autoridades" in toc.text
    assert "CAPITULO I" not in toc.text
    assert _loose_outside_transitorios(tree) == []


def test_text_after_an_index_in_the_same_block_is_the_introduction_of_its_container():
    doc = (
        f'{M}orden=1 numero=0 kind=container type=annex num="2" label="ANEXO 2" -->\n\n'
        "**ANEXO 2**\n\n**MANUAL**\n\n**INDICE**\n\nPresentación\n\nObjetivo\n\nFunciones\n\n"
        "**PRESENTACION**\n\nEs un breve resumen del instrumento, se recomienda un lenguaje claro "
        "e incluyente y breve para el lector que consulta el manual.\n\n"
        "**OBJETIVO**\n\nSe plantea el fin hacia el cual se orientan los recursos del área.\n"
    )
    tree = parse_markdown(doc)
    annex = tree.find("att_2")
    assert [c.akn_type for c in annex.children if c.akn_type in ("intro", "toc")] \
        == ["toc", "intro"] or [c.akn_type for c in annex.children][:2] == ["intro", "toc"]
    assert _loose_outside_transitorios(tree) == []


# -- I3 and I4: numbering ---------------------------------------------------------


def _numbers(tree):
    return [(n.eId, n.num) for n in _walk(tree) if n.akn_type == "article"]


def test_a_reference_line_that_names_an_article_is_not_an_opener():
    doc = (
        f'{M}orden=1 numero=0 kind=other label="MODIFICACIONES AL ANEXO" -->\n\n'
        "**F.** Cantidades correspondientes a la fracción II del artículo 8o.\n\n"
        "**Artículo 8o. **fracción II primer párrafo: $179,017.26\n\n"
        "**Artículo 8o. **fracción II segundo párrafo: $179,017.27 y hasta $226,755.19\n"
    )
    tree = parse_markdown(doc)
    assert _numbers(tree) == []
    assert not [n for n in _walk(tree) if n.repeated]


def test_an_article_block_that_only_heads_numbered_provisions_opens_a_section():
    """The Reglamento General de Infantería: `ARTICULO 1°. Generalidades` and, under it,
    `1.- ...` to `5.- ...`. Read as articles the two numberings interleave and 70 numbers
    look missing."""
    blocks = [f'{M}orden=1 numero=0 kind=container type=chapter num="UNO" label="CAPÍTULO UNO" -->',
              "**CAPITULO UNO**\n\nObjeto"]
    n = 1
    orden = 2
    for art, provisions in ((1, 3), (2, 2), (3, 2), (4, 1)):
        blocks.append(f'{M}orden={orden} numero=0 kind=article num="{art}" label="ARTÍCULO {art}" -->')
        blocks.append(f"**ARTICULO {art}°.**\n\nGeneralidades")
        orden += 1
        for _ in range(provisions):
            blocks.append(f'{M}orden={orden} numero={n} kind=provision num="{n}" label="{n}" -->')
            blocks.append(f"{n}.- Texto de la disposición {n}.")
            n += 1
            orden += 1
    tree = parse_markdown("\n\n".join(blocks) + "\n")
    sections = [s for s in _walk(tree) if s.akn_type == "section"]
    assert len(sections) == 4
    assert [a.num for a in _walk(tree) if a.akn_type == "article"] == [str(i) for i in range(1, n)]
    assert all(s.heading and "Generalidades" in s.heading for s in sections)
    assert _block_escapes(tree) == []
    assert coverage(tree, text_units(tree)).uncovered_chars == 0
    assert not [x for x in _walk(tree) if x.repeated]


def test_a_lone_article_followed_by_a_provision_stays_an_article():
    doc = (
        f'{M}orden=1 numero=0 kind=article num="25" label="ARTÍCULO 25" -->\n\n'
        "**Artículo 25.**\n\nPublicación de acuerdos.\n\n"
        f'{M}orden=2 numero=1 kind=provision num="1" label="1" -->\n\n'
        "1. Las Comisiones deberán publicar los programas.\n"
    )
    tree = parse_markdown(doc)
    assert not [s for s in _walk(tree) if s.akn_type == "section"]
    assert tree.find("art_25") is not None


def test_a_numbered_provision_the_api_calls_other_is_an_article():
    doc = (
        f'{M}orden=1 numero=0 kind=article num="97" label="ARTÍCULO 97" -->\n\n'
        "**Artículo 97.** Texto del noventa y siete.\n\n"
        f'{M}orden=2 numero=98 kind=other label="98 (SIC)" -->\n\n'
        "98 (SIC).- El pelotón es la unidad de instrucción.\n\n"
        f'{M}orden=3 numero=99 kind=provision num="99" label="99" -->\n\n'
        "99.- Durante este período se hará la selección.\n"
    )
    tree = parse_markdown(doc)
    assert [n.num for n in _walk(tree) if n.akn_type == "article"] == ["97", "98 (SIC)", "99"]
    assert not [n for n in _walk(tree) if n.akn_type == "hcontainer"]
    assert coverage(tree, text_units(tree)).uncovered_chars == 0


@pytest.mark.parametrize("label", ["PARTE FINAL DEL CONVENIO", "OTRO TEXTO"])
def test_an_other_block_with_a_non_numeric_label_is_still_a_container(label):
    doc = f'{M}orden=1 numero=0 kind=other label="{label}" -->\n\nTexto del bloque.\n'
    tree = parse_markdown(doc)
    assert [n.akn_type for n in _walk(tree) if n.akn_type == "hcontainer"] == ["hcontainer"]

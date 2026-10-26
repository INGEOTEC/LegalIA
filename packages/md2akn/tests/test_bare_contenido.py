"""Rule 40 (issue #300): a bare `CONTENIDO` block the SCJN serves after a block
holding only its own title is that block's content.

The SCOW API serves some treaties as a title-only block (`ANEXO`, `PROTOCOLO`)
followed by a block labelled just `CONTENIDO` with the text itself -- the same
split rule 30 reads when the second label repeats the first (`CONTENIDO
ARTICULOS`), but with nothing after the word, so rule 30 never fired. The
fragments are cut down from the real answers for the treaties 24031 (the CAMR-92
Final Acts, whose four annexes were this shape) and 22588 (a protocol), and the
reglamento 138214, whose `CONTENIDO` is an index after a `CONSIDERANDO`.
"""

from md2akn import parse_markdown
from md2akn.outline import es_contenido_suelto, es_solo_titulo
from md2akn.units import text_units
from md2akn.validate import validate


def _marker(orden, label, kind="other", extra=""):
    return f'<!-- scjn:block orden={orden} numero=0 kind={kind}{extra} label="{label}" -->'


def _doc(*blocks):
    return "\n\n".join(blocks) + "\n"


def _walk(node):
    yield node
    for child in node.children:
        yield from _walk(child)


def _annex_doc():
    return _doc(
        _marker(1, "ENCABEZADO", "header"),
        "**ACTAS FINALES**",
        _marker(2, "PREÁMBULO", "preamble"),
        "Considerando las Resoluciones pertinentes, la Conferencia aprobó lo que sigue.",
        _marker(3, "ANEXO", "container", " type=annex"),
        "**ANEXO**",
        _marker(4, "CONTENIDO"),
        "ARTICULO 1\n\nDefiniciones.\n\n1.1 Para los fines de este Reglamento se entiende por radio.",
        "ARTICULO 2\n\nDisposiciones generales de la Unión.",
        _marker(5, "ANEXO 4", "container", ' type=annex num="4"'),
        "**ANEXO 4**",
        _marker(6, "CONTENIDO"),
        "ARTICULO 1\n\nOtro texto del cuarto anexo.",
        _marker(7, "PARTE FINAL DEL DECRETO PROMULGATORIO"),
        "La presente es copia fiel y completa de las Actas Finales.",
    )


def test_predicates():
    assert es_solo_titulo("**ANEXO**")
    assert es_solo_titulo("ANEXO A LA RESOLUCION No. 46 (CAMR-92)")
    assert es_solo_titulo("(REFORMADO, D.O.F. 1-1-2000)\n\n**ANEXO**")
    assert not es_solo_titulo("**ANEXO**\n\nTexto del anexo.")
    assert not es_solo_titulo("")
    assert es_contenido_suelto("CONTENIDO", "other", "**PROTOCOLO**")
    assert es_contenido_suelto("Contenido.", "other", "**ANEXO 4**")
    assert not es_contenido_suelto("CONTENIDO", "other", "CONSIDERANDO\n\nQue el decreto.")
    assert not es_contenido_suelto("CONTENIDO", "container", "**ANEXO**")
    assert es_contenido_suelto("CONTENIDO", "other", "**ANEXO**", "container")
    assert not es_contenido_suelto("CONTENIDO", "other", "**CONSIDERANDO**", "recitals")
    assert not es_contenido_suelto("CONTENIDO ARTICULOS", "other", "**ARTÍCULOS**")  # rule 30's


def test_the_text_after_a_bare_contenido_is_inside_the_annex_before_it():
    tree = parse_markdown(_annex_doc())
    annexes = [n for n in _walk(tree) if n.akn_type == "attachment"]
    assert len(annexes) == 2
    first, second = annexes
    assert [n.num for n in _walk(first) if n.akn_type == "article"] == ["1", "2"]
    assert [n.num for n in _walk(second) if n.akn_type == "article"] == ["1"]
    # nothing is a section named CONTENIDO beside the annex
    assert not [n for n in _walk(tree) if "CONTENIDO" in (n.eId or "")]
    assert validate(tree).ok


def test_the_units_of_the_annex_are_articles_not_loose():
    units = text_units(parse_markdown(_annex_doc()), split_articles=False)
    kinds = [u.unit_type for u in units]
    assert kinds.count("article") == 3
    assert "loose" not in kinds


def test_a_protocol_and_its_bare_contenido_are_one_container():
    doc = _doc(
        _marker(1, "ENCABEZADO", "header"),
        "**ESTATUTO**",
        _marker(2, "ARTÍCULO  1", "article", " numero=1"),
        "ARTICULO 1\n\nEl Centro tendrá su sede en Trieste.",
        _marker(3, "PROTOCOLO"),
        "**PROTOCOLO**",
        _marker(4, "CONTENIDO"),
        "De la reanudación de la Reunión Plenipotenciaria, celebrada en Viena.\n\n"
        "La sede del Centro estará ubicada en Trieste, Italia.",
        _marker(5, "PARTE FINAL DEL DECRETO PROMULGATORIO"),
        "La presente es copia fiel y completa del Estatuto.",
    )
    tree = parse_markdown(doc)
    protocol = [n for n in _walk(tree) if (n.num or "") == "PROTOCOLO"]
    assert len(protocol) == 1
    assert "Trieste, Italia" in protocol[0].span.text
    assert not [n for n in _walk(tree) if n.eId and n.eId.endswith("CONTENIDO")]
    assert validate(tree).ok


def test_a_bare_contenido_that_is_an_index_stays_an_index():
    doc = _doc(
        _marker(1, "ENCABEZADO", "header"),
        "**ACUERDO**",
        _marker(2, "CONSIDERANDO", "recitals"),
        "**CONSIDERANDO**\n\nQue de conformidad con lo dispuesto en el decreto, se emite.",
        _marker(3, "CONTENIDO"),
        "- Contenido:\n\nCapítulo Primero: DISPOSICIONES GENERALES\n\nCapítulo Segundo: DE LOS PROGRAMAS",
        _marker(4, "CAPÍTULO PRIMERO", "container", " type=chapter"),
        "**CAPÍTULO PRIMERO**\n\n**DISPOSICIONES GENERALES**",
        _marker(5, "ARTÍCULO 1", "article", " numero=1"),
        "**Artículo 1.-** Este reglamento es de observancia general.",
    )
    tree = parse_markdown(doc)
    assert [n.num for n in _walk(tree) if n.akn_type == "article"] == ["1"]
    kinds = [u.unit_type for u in text_units(tree, split_articles=False)]
    assert "loose" not in kinds
    assert validate(tree).ok


def test_a_document_with_no_markers_parses_as_before():
    plain = (
        "**ANEXO**\n\nCONTENIDO\n\n**Artículo 1.-** Texto del artículo uno.\n\n"
        "**Artículo 2.-** Texto del artículo dos.\n"
    )
    tree = parse_markdown(plain)
    assert [n.num for n in _walk(tree) if n.akn_type == "article"] == ["1", "2"]


# -- two more parse errors the re-crawl's restored text showed (issue #300) -------


def test_a_running_head_art_n_before_the_article_with_the_same_number_is_no_article():
    # The Radio Regulations the CAMR-92 Final Acts reprint print `Art. 8` above
    # each article's own heading (rule 41).
    doc = _doc(
        _marker(1, "ENCABEZADO", "header"),
        "**ACTAS FINALES**",
        _marker(2, "ANEXO", "container", " type=annex"),
        "**ANEXO**",
        _marker(3, "CONTENIDO"),
        "ARTICULO 7\n\nDisposiciones previas.",
        "Art. 8",
        "**ARTICULO 8**\n\nAtribución de bandas de frecuencias.",
        "**ARTICULO 9**\n\nOtras disposiciones.",
    )
    tree = parse_markdown(doc)
    assert [n.num for n in _walk(tree) if n.akn_type == "article"] == ["7", "8", "9"]
    assert not [n for n in _walk(tree) if n.repeated]
    assert validate(tree).ok


def test_a_lone_art_n_line_stays_an_article():
    doc = _doc(
        _marker(1, "ENCABEZADO", "header"),
        "**LEY**",
        _marker(2, "ARTÍCULO 1", "article", " numero=1"),
        "Art. 8",
    )
    tree = parse_markdown(doc)
    assert [n.num for n in _walk(tree) if n.akn_type == "article"] == ["8"]


def test_an_article_number_may_end_with_a_capital_letter_that_closes_the_line():
    # `**Artículo 11 **A` (a treaty's amendment inserting articles 11A and 11B)
    # was read as a second article 11.
    doc = _doc(
        _marker(1, "ENCABEZADO", "header"),
        "**ENMIENDA**",
        _marker(2, "ENMIENDA A LA CONVENCIÓN"),
        "**ENMIENDA A LA CONVENCIÓN**",
        _marker(3, "CONTENIDO"),
        "**Artículo 11 **\n\nTexto del artículo once.",
        "**Artículo 11 **A\n\nTexto del once A.",
        "**Artículo 11 **B\n\nTexto del once B.",
    )
    tree = parse_markdown(doc)
    assert [n.num for n in _walk(tree) if n.akn_type == "article"] == ["11", "11 A", "11 B"]
    assert not [n for n in _walk(tree) if n.repeated]
    # a letter with text after it on the line is still a sentence
    from md2akn.patterns import ARTICULO

    assert ARTICULO.match("Artículo 5 A las personas").group("num") == "5"

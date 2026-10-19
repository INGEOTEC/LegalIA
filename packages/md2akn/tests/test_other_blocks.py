"""Rules 29-32 (review fix-4 of #291): every block the SCJN's API labels
`kind=other` is a container of its own, a numbering that starts over inside one
block opens a division, and the article-number grammar of a radio-regulations
text is read on the marker path.

The fragments are cut down from real API blocks of the tratados 44552 (the Final
Acts of the World Radiocommunication Conference, 2000), 11293 (the SOLAS
amendments, whose appendix prints five model certificates) and 22834 (an
agreement whose articles restart after a plain heading line). Every document
carries outline markers: without them the parse is the one it always was, and
the last test of this file says so.
"""

from md2akn import parse_markdown
from md2akn.divisions import clave_de_numero, inicio_de_la_serie
from md2akn.outline import es_continuacion, rol_de_etiqueta
from md2akn.units import coverage, text_units


def _marker(orden, label, kind="other", numero=0, extra=""):
    return f'<!-- scjn:block orden={orden} numero={numero} kind={kind}{extra} label="{label}" -->'


def _walk(node):
    yield node
    for child in node.children:
        yield from _walk(child)


def _doc(*blocks):
    return "\n\n".join(blocks) + "\n"


def _hcontainers(tree):
    return [(n.eId, n.num, n.refers_to) for n in _walk(tree) if n.akn_type == "hcontainer"]


def _articles(tree):
    return [n.eId for n in _walk(tree) if n.akn_type == "article"]


# -- the role table (rule 29) and the split block (rule 30) -----------------------


def test_role_table():
    assert rol_de_etiqueta("PARTE FINAL DE LAS ACTAS") == "conclusions"
    assert rol_de_etiqueta("NOTA EXPLICATIVA") == "note"
    assert rol_de_etiqueta("NOTAS") == "note"
    for label in ("FIRMA", "APROBACIÓN", "RESERVA", "RESERVAS", "RATIFICACIÓN Y DEPÓSITO",
                  "PROMULGACIÓN", "CERTIFICACIÓN", "DECLARACIONES INTERPRETATIVAS",
                  "ADHESIÓN Y DEPÓSITO", "RATIFICACIÓN, FIRMA Y DEPÓSITO"):
        assert rol_de_etiqueta(label) == "promulgation", label
    assert rol_de_etiqueta("RESOLUCIÓN MSC.404(96)") == "resolution"
    # a label that only starts with one of the words is a text of the instrument
    assert rol_de_etiqueta("RESERVAS AL REGLAMENTO DE RADIOCOMUNICACIONES") is None
    assert rol_de_etiqueta("APÉNDICES") is None


def test_a_block_split_in_two_is_one_container():
    assert es_continuacion("CONTENIDO ARTICULOS", "ARTÍCULOS")
    assert es_continuacion("CONTENIDO DE LAS RESOLUCIONES", "RESOLUCIONES")
    assert not es_continuacion("CONTENIDO APÉNDICES", "ARTÍCULOS")
    assert not es_continuacion("ARTÍCULOS", "CONTENIDO ARTICULOS")


FINAL_ACTS = _doc(
    _marker(1, "ENCABEZADO", "header"),
    "**ACTAS FINALES DE LA CONFERENCIA MUNDIAL DE RADIOCOMUNICACIONES (CMR-2000)**",
    _marker(2, "FIRMA"),
    "El dos de junio de dos mil, el Plenipotenciario de los Estados Unidos Mexicanos firmó las Actas.",
    _marker(3, "APROBACIÓN"),
    "Las Actas Finales fueron aprobadas por la Cámara de Senadores.",
    _marker(4, "RATIFICACIÓN Y DEPÓSITO"),
    "El instrumento de ratificación fue depositado ante el Secretario General.",
    _marker(5, "PROMULGACIÓN"),
    "Por lo tanto, para su debida observancia, promulgo el presente Decreto.",
    _marker(6, "NOTA EXPLICATIVA"),
    "Las disposiciones marcadas con MOD, ADD o SUP modifican el Reglamento.",
    _marker(7, "RESERVAS AL REGLAMENTO DE RADIOCOMUNICACIONES"),
    "Al firmar el presente texto, la delegación se reserva el derecho de adoptar las medidas.",
    _marker(8, "ARTÍCULOS"),
    "**ARTICULOS**",
    _marker(9, "CONTENIDO ARTICULOS"),
    "**ARTÍCULO S1**\n\nTérminos y definiciones",
    "**MOD**",
    "S1.171 zona de coordinación: Cuando se determina la necesidad de coordinación.",
    "**ARTÍCULO S5**\n\nAtribuciones de frecuencia",
    "**MOD**",
    "S5.43 1) Cuando en el presente Reglamento se indica que un servicio puede funcionar.",
    "**ADD**",
    "S5.43A 1bis) Cuando en el presente Reglamento se indica que un servicio puede funcionar.",
    _marker(10, "PARTE FINAL DE LAS ACTAS"),
    "EN TESTIMONIO DE LO CUAL, los delegados firmaron las presentes Actas.",
)


def test_every_other_block_is_a_container_and_the_promulgation_stays_together():
    tree = parse_markdown(FINAL_ACTS)
    body = next(c for c in tree.children if c.akn_type == "body")
    top = [(c.akn_type, c.num, c.refers_to) for c in body.children]
    # four consecutive blocks of the decree are one container; the note and the
    # conclusions are roles; the reservations of the conference's own text are a
    # container like any other; the split `ARTÍCULOS` pair is one container
    assert top[0] == ("hcontainer", "DECRETO PROMULGATORIO", "#promulgation")
    assert ("hcontainer", "NOTA EXPLICATIVA", "#note") in top
    assert ("hcontainer", "RESERVAS AL REGLAMENTO DE RADIOCOMUNICACIONES", None) in top
    # a block that holds articles opens a section (rule 23): the pair is one
    assert [c.num for c in body.children if c.num == "ARTÍCULOS"] == ["ARTÍCULOS"]
    assert top[-1] == ("hcontainer", "PARTE FINAL DE LAS ACTAS", "#conclusions")
    assert not any(n.num == "CONTENIDO ARTICULOS" for n in _walk(tree))


def test_the_radio_regulations_numbering_is_read_whole():
    tree = parse_markdown(FINAL_ACTS)
    articles = [n.num for n in _walk(tree) if n.akn_type == "article"]
    assert articles == ["S1.171", "S5.43", "S5.43A"]
    assert any(n.akn_type == "section" and n.num == "S5" for n in _walk(tree))
    assert not any(n.repeated for n in _walk(tree))


def test_units_of_other_blocks_are_not_loose_and_cover_the_text():
    tree = parse_markdown(FINAL_ACTS)
    units = text_units(tree, split_articles=False)
    assert not [u for u in units if u.unit_type == "loose"]
    assert coverage(tree, units).uncovered_chars == 0


def test_the_number_grammar():
    assert clave_de_numero("5.43A") == clave_de_numero("5.43 A")
    assert clave_de_numero("5.43") < clave_de_numero("5.43A") < clave_de_numero("5.44")
    assert clave_de_numero("S5.43") == clave_de_numero("5.43")


# -- a numbering that starts over inside one block opens a division (rule 31) ------

SOLAS = _doc(
    _marker(1, "RESOLUCIÓN MSC.496(105)", "container", extra=" type=other"),
    "**RESOLUCIÓN MSC.496(105)**",
    "**APÉNDICE CERTIFICADOS**",
    "Los certificados que se reproducen a continuación se expiden en el formato siguiente.",
    "**MODELO DE CERTIFICADO DE SEGURIDAD PARA BUQUE DE PASAJE**",
    "**SE CERTIFICA:**",
    "1 Que el buque ha sido objeto de reconocimiento.",
    "2.1 Que el buque cumple con las prescripciones del Convenio.",
    "**(VÉASE ARCHIVO ANEXO)**",
    "**SE CERTIFICA:**",
    "1 Que el buque ha sido objeto de reconocimiento.",
    "2.1 Que el buque cumple con las prescripciones del Convenio.",
)


def test_the_boxes_of_a_model_certificate_are_not_articles():
    tree = parse_markdown(SOLAS)
    assert _articles(tree) == []
    assert not any(n.repeated for n in _walk(tree))
    forms = [n for n in _walk(tree) if n.refers_to == "#form"]
    # the first certificate has its own heading, the second only the placeholder
    assert len(forms) == 2
    assert forms[0].num.startswith("MODELO DE CERTIFICADO")
    assert forms[1].num == "SE CERTIFICA"


LEAD_IN = _doc(
    _marker(1, "ACUERDO", "other"),
    "**Artículo 1.-** Las partes convienen en cooperar.",
    "**Artículo 2.-** Las partes designan un Comité Ejecutivo.",
    "Artículos que se refieren al Comité Ejecutivo",
    "**Artículo 1.-** El Comité Ejecutivo se reunirá cada año.",
    "**Artículo 2.-** El Comité Ejecutivo adoptará su reglamento.",
)


def test_a_plain_lead_in_line_opens_a_division_when_the_numbers_start_over():
    tree = parse_markdown(LEAD_IN)
    assert not any(n.repeated for n in _walk(tree))
    divisions = [n for n in _walk(tree) if n.refers_to == "#division"]
    assert len(divisions) == 1
    assert divisions[0].akn_type == "hcontainer"
    assert divisions[0].num == "Artículos que se refieren al Comité Ejecutivo"
    assert len(_articles(tree)) == 4


def test_no_heading_no_division():
    # the same restart with nothing that can head it stays what rule 22 makes it:
    # a repeat, flagged on both articles, never merged or renumbered
    text = _doc(
        _marker(1, "ARTÍCULO 1", "article", numero=1, extra=' num="1"'),
        "**Artículo 1.-** Primero.",
        _marker(2, "ARTÍCULO 2", "article", numero=2, extra=' num="2"'),
        "**Artículo 2.-** Segundo.",
        _marker(3, "ARTÍCULO 1", "article", numero=1, extra=' num="1"'),
        "**Artículo 1.-** Otra vez primero.",
    )
    tree = parse_markdown(text)
    assert sum(1 for n in _walk(tree) if n.repeated) == 2


def test_the_start_of_a_series():
    assert inicio_de_la_serie(["1", "2", "3", "1", "2"], 4) == 3
    assert inicio_de_la_serie(["1", "2", "3"], 2) == 0


# -- no marker, no change (the golden property) -------------------------------------


def test_a_document_without_markers_is_read_as_before():
    plain = "**Artículo 1.-** Texto.\n\n**Artículo 2.-** Texto.\n"
    tree = parse_markdown(plain)
    assert not [n for n in _walk(tree) if n.akn_type == "hcontainer"]
    assert _articles(tree) == ["art_1", "art_2"]

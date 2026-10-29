"""Rules 25-28 (review fix-3 of #291): annexes belong to the chapter the SCJN's
outline says they annex, appendices to the annex or instrument they follow, and
a text of its own printed after them (an amending resolution) to none of them.

The fixtures are built from the real marker lines of two treaties -- the T-MEC
(`137355`, annexes `2-A` ... `3-D` after chapters 2 and 3, a running
`**APÉNDICE**` header repeated through the tariff appendix of annex `2-C`) and
the IMO's gas carrier code (`96707`, chapter 19 followed by appendices 1-5 and
then amending resolutions) -- with the text cut down to a line or two a block.
"""

from md2akn import parse_markdown
from md2akn.model import REFERS_TO_ANEXO, REFERS_TO_APENDICE
from md2akn.units import coverage, text_units

M = "<!-- scjn:block "


def _walk(node):
    yield node
    for child in node.children:
        yield from _walk(child)


def _shape(node, skip=("content", "paragraph", "point", "subpoint", "intro", "table", "article")):
    """``{eId: [child eIds]}`` of the structural nodes."""
    return {
        n.eId: [c.eId for c in n.children if c.akn_type not in skip]
        for n in _walk(node) if n.akn_type not in skip
    }


# -- the T-MEC --------------------------------------------------------------

TMEC = (
    f'{M}orden=17 numero=0 kind=container type=chapter num="2" label="CAPÍTULO 2" -->\n\n'
    "**CAPÍTULO 2**\n\n**TRATO NACIONAL**\n\n"
    f'{M}orden=18 numero=2 kind=article num="2.1" label="ARTÍCULO 2.1" -->\n\n'
    "**Artículo 2.1**: Definiciones\n\nUno.\n\n"
    f'{M}orden=34 numero=0 kind=container type=section num="A" label="SECCIÓN A" -->\n\n'
    "**SECCIÓN A**\n\n**Trato**\n\n"
    f'{M}orden=35 numero=2 kind=container type=annex num="2-A" label="ANEXO 2-A" -->\n\n'
    "**ANEXO 2-A:**\n\n**EXCEPCIONES AL ARTÍCULO 2.3**\n\n"
    f'{M}orden=36 numero=2 kind=article num="2.A.1" label="ARTÍCULO 2.A.1" -->\n\n'
    "**Artículo 2.A.1**: Aplicación\n\nDos.\n\n"
    f'{M}orden=37 numero=2 kind=container type=annex num="2-C" label="ANEXO 2-C" -->\n\n'
    "**ANEXO 2-C**\n\n**DISPOSICIONES SOBRE MERCANCÍAS AUTOMOTRICES**\n\n"
    "1. Este Anexo no aplica.\n\n"
    "**APÉNDICE**\n\n**AUTOPARTES**\n\n| 381900 | Líquidos para frenos |\n\n"
    "**APÉNDICE**\n\n**AUTOPARTES**\n\nContinúa\n\n| 700711 | Vidrio templado |\n\n"
    "**APÉNDICE**\n\n**AUTOPARTES**\n\nContinúa\n\n| 903289 | Los demás instrumentos |\n\n"
    f'{M}orden=38 numero=0 kind=container type=chapter num="3" label="CAPÍTULO 3" -->\n\n'
    "**CAPÍTULO 3**\n\n**AGRICULTURA**\n\n"
    f'{M}orden=57 numero=3 kind=container type=annex num="3-A" label="ANEXO 3-A" -->\n\n'
    "**ANEXO 3-A**\n\n**COMERCIO AGRÍCOLA**\n\nTres.\n\n"
    f'{M}orden=60 numero=3 kind=container type=annex num="3-D" label="ANEXO 3-D" -->\n\n'
    "**ANEXO 3-D**\n\n**FÓRMULAS PATENTADAS**\n\nCuatro.\n\n"
    f'{M}orden=61 numero=0 kind=container type=chapter num="4" label="CAPÍTULO 4" -->\n\n'
    "**CAPÍTULO 4**\n\n**REGLAS DE ORIGEN**\n\n"
    f'{M}orden=81 numero=4 kind=container type=annex num="4-A" label="ANEXO 4-A" -->\n\n'
    "**ANEXO 4-A**\n\n**EXCEPCIONES**\n\nCinco.\n\n"
    f'{M}orden=684 numero=0 kind=container type=annex num="I" label="ANEXO I" -->\n\n'
    "**ANEXO I**\n\n**MEDIDAS DISCONFORMES**\n\nSeis.\n"
)


def test_an_annex_is_a_child_of_the_chapter_it_annexes():
    tree = parse_markdown(TMEC)
    shape = _shape(tree)
    assert shape["cap_2"] == [
        "cap_2__sec_A", "cap_2__att_2-A", "cap_2__att_2-C",
    ] or shape["cap_2"] == ["cap_2__att_2-A", "cap_2__att_2-C", "cap_2__sec_A"]
    assert shape["cap_3"] == ["cap_3__att_3-A", "cap_3__att_3-D"]
    assert shape["cap_4"] == ["cap_4__att_4-A"]
    # an annex with no owner (`numero` 0, no `N-` prefix) stays at the body
    body = next(c for c in tree.children if c.akn_type == "body")
    assert [c.eId for c in body.children if c.akn_type != "section"] == [
        "cap_2", "cap_3", "cap_4", "att_I",
    ]


def test_a_chapter_is_never_inside_an_annex():
    tree = parse_markdown(TMEC)
    for node in _walk(tree):
        if node.akn_type == "chapter":
            assert node.parent.akn_type == "body", node.eId
    assert {n.refers_to for n in _walk(tree) if n.akn_type == "attachment"} == {
        REFERS_TO_ANEXO, REFERS_TO_APENDICE,
    }


def test_the_annex_takes_its_title_from_its_label_not_from_the_number_prefix():
    """`ANEXO 2-A:` read by the text alone is annex `2` titled `A`."""
    tree = parse_markdown(TMEC)
    annex = tree.find("cap_2__att_2-A")
    assert annex.num == "2-A"
    assert annex.heading == "EXCEPCIONES AL ARTÍCULO 2.3"
    assert [a.eId for a in annex.children if a.akn_type == "article"] == [
        "cap_2__att_2-A__art_2.A.1",
    ]


def test_a_repeated_running_header_is_text_of_the_appendix_it_repeats():
    tree = parse_markdown(TMEC)
    annex = tree.find("cap_2__att_2-C")
    appendices = [n for n in annex.children if n.akn_type == "attachment"]
    assert [(n.refers_to, n.num, n.heading) for n in appendices] == [
        (REFERS_TO_APENDICE, None, "AUTOPARTES"),
    ]
    # the three tariff rows are in the one appendix's text, none lost
    assert coverage(tree, text_units(tree)).uncovered_chars == 0


def test_no_container_is_repeated_in_the_t_mec_fixture():
    tree = parse_markdown(TMEC)
    assert not [n.eId for n in _walk(tree) if n.repeated]


# -- the gas carrier code ---------------------------------------------------

CODIGO = (
    f'{M}orden=1 numero=0 kind=header label="ENCABEZADO" -->\n\n'
    "**ACUERDO por el que se da a conocer la Resolución.**\n\n"
    f'{M}orden=2 numero=0 kind=recitals label="CONSIDERANDO" -->\n\n'
    "**CONSIDERANDO**\n\nQue el Código.\n\n"
    f'{M}orden=3 numero=1 kind=provision num="PRIMERO" label="PRIMERO" -->\n\n'
    "**PRIMERO.-** Se da a conocer.\n\n"
    f'{M}orden=5 numero=0 kind=transitorios label="TRANSITORIOS" -->\n\n'
    "**TRANSITORIOS**\n\n**ÚNICO.-** Entra en vigor.\n\n"
    f'{M}orden=6 numero=0 kind=other label="RESOLUCIÓN MSC.370(93)" -->\n\n'
    "**RESOLUCIÓN MSC.370(93)**\n\n(adoptada el 22 de mayo de 2014)\n\n"
    "**EL COMITÉ DE SEGURIDAD MARÍTIMA,**\n\n"
    "**ANEXO**\n\n**ENMIENDAS AL CÓDIGO CIG**\n\nSe sustituye el texto.\n\n"
    f'{M}orden=26 numero=18 kind=container type=chapter num="18" label="CAPÍTULO 18" -->\n\n'
    "**CAPÍTULO 18**\n\n**USO DE LA CARGA**\n\n"
    f'{M}orden=27 numero=19 kind=container type=chapter num="19" label="CAPÍTULO 19" -->\n\n'
    "**CAPÍTULO 19**\n\n**RESUMEN DE PRESCRIPCIONES**\n\nTabla.\n\n"
    f'{M}orden=28 numero=0 kind=container type=appendix num="1" label="APÉNDICE 1" -->\n\n'
    "**APÉNDICE 1**\n\n**FORMULARIO DE NOTIFICACIÓN**\n\nUno.\n\n"
    f'{M}orden=29 numero=0 kind=container type=appendix num="2" label="APÉNDICE 2" -->\n\n'
    "**APÉNDICE 2**\n\n**MODELO DE CERTIFICADO**\n\nDos.\n\n"
    f'{M}orden=32 numero=0 kind=container type=appendix num="5" label="APÉNDICE 5" -->\n\n'
    "**APÉNDICE 5**\n\n**NORMA PARA LA UTILIZACIÓN**\n\nCinco.\n\n"
    f'{M}orden=33 numero=0 kind=other label="RESOLUCIÓN MSC.411(97)" -->\n\n'
    "**RESOLUCIÓN MSC.411(97)**\n\n(adoptada el 25 de noviembre de 2016)\n\n"
    "**ANEXO**\n\n**ENMIENDAS AL CÓDIGO CIG**\n\n"
    "**Artículo 1.-** Se sustituye el párrafo.\n\n"
    f'{M}orden=34 numero=0 kind=other label="RESOLUCIÓN MSC.441(99)" -->\n\n'
    "**RESOLUCIÓN MSC.441(99)**\n\n(adoptada el 24 de mayo de 2018)\n\n"
    "**Artículo 1.-** Se sustituye el capítulo.\n"
)


def test_appendices_are_appendices_inside_the_code_they_close():
    tree = parse_markdown(CODIGO)
    code = tree.find("att_attachment")
    assert code.refers_to == REFERS_TO_ANEXO
    kids = [(c.eId, c.akn_type, c.refers_to, c.num) for c in code.children
            if c.akn_type in ("chapter", "attachment")]
    assert kids == [
        ("att_attachment__cap_18", "chapter", None, "18"),
        ("att_attachment__cap_19", "chapter", None, "19"),
        ("att_attachment__app_1", "attachment", REFERS_TO_APENDICE, "1"),
        ("att_attachment__app_2", "attachment", REFERS_TO_APENDICE, "2"),
        ("att_attachment__app_5", "attachment", REFERS_TO_APENDICE, "5"),
    ]
    # none of them was swallowed by the chapter before it
    assert not [c for c in tree.find("att_attachment__cap_19").children
                if c.akn_type == "attachment"]


def test_an_amending_resolution_is_never_inside_an_appendix():
    tree = parse_markdown(CODIGO)
    for node in _walk(tree):
        if node.num and node.num.startswith("RESOLUCIÓN MSC.4"):
            assert node.parent.akn_type == "body", node.eId
    for app in (n for n in _walk(tree) if n.refers_to == REFERS_TO_APENDICE):
        assert not [d for d in _walk(app) if d.num and d.num.startswith("RESOLUCIÓN")]


def test_the_appendix_fixture_keeps_every_character_and_repeats_nothing():
    tree = parse_markdown(CODIGO)
    assert coverage(tree, text_units(tree)).uncovered_chars == 0
    # (the text of the first resolution, which follows the transitorios, is
    # read as theirs: its paragraphs repeat `p_content`, which is not a number)
    assert not [n.eId for n in _walk(tree) if n.repeated and n.akn_type != "content"]


def test_an_appendix_with_no_annex_open_hangs_from_the_instrument():
    text = (
        f'{M}orden=1 numero=0 kind=container type=chapter num="1" label="CAPÍTULO 1" -->\n\n'
        "**CAPÍTULO 1**\n\n**UNO**\n\n"
        f'{M}orden=2 numero=1 kind=article num="1" label="ARTÍCULO 1" -->\n\n'
        "**Artículo 1.-** Uno.\n\n"
        f'{M}orden=3 numero=0 kind=container type=appendix num="1" label="APÉNDICE 1" -->\n\n'
        "**APÉNDICE 1**\n\n**FORMULARIO**\n\nDos.\n\n"
    )
    tree = parse_markdown(text)
    app = tree.find("app_1")
    assert app.parent.akn_type == "body" and app.refers_to == REFERS_TO_APENDICE


# -- documents without markers are untouched --------------------------------


def test_a_document_without_markers_keeps_its_attachment_eids():
    text = (
        "**CAPÍTULO 2**\n\n**TRATO**\n\n**Artículo 1.-** Uno.\n\n"
        "**ANEXO 2-A**\n\n**EXCEPCIONES**\n\nDos.\n\n"
        "**APÉNDICE 1**\n\n**LISTA**\n\nTres.\n"
    )
    tree = parse_markdown(text)
    assert [(n.eId, n.parent.akn_type) for n in _walk(tree) if n.akn_type == "attachment"] == [
        ("att_2-A", "body"), ("att_1", "body"),
    ]


def test_a_section_of_its_own_stays_in_its_chapter_when_no_attachment_is_open():
    """Rule 28 only separates a text of its own from an annex or appendix: the
    `DISPOSICIONES GENERALES` the Código Civil Federal's chapter IV opens with is
    a section of that chapter (an unguarded rule hung it from the body)."""
    text = (
        f'{M}orden=1 numero=0 kind=container type=chapter num="IV" label="CAPÍTULO IV" -->\n\n'
        "**CAPÍTULO IV**\n\n**Del contrato**\n\n"
        f'{M}orden=2 numero=0 kind=other label="DISPOSICIONES GENERALES" -->\n\n'
        "**DISPOSICIONES GENERALES**\n\n"
        f'{M}orden=3 numero=178 kind=article num="178" label="ARTÍCULO 178" -->\n\n'
        "**Artículo 178.-** Uno.\n"
    )
    tree = parse_markdown(text)
    section = next(n for n in _walk(tree) if n.num == "DISPOSICIONES GENERALES")
    # Rule 44: a block the API labels `other` is an `hcontainer`, not a `section`.
    assert section.akn_type == "hcontainer"
    assert section.parent.eId == "cap_IV"

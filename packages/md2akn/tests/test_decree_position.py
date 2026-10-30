"""Rules 42 and 43 (review fix-1 of #301, a correction found after the fact).

On a snapshot with outline markers, `preamble` is exactly the title block, `conclusions`
never crosses an API block, and whether a promulgation-decree block is the *envelope*
(`TextUnit.envelope`, "not compared") is read from its **position**: a run of decree
blocks at the start of the document (after the title block) or at its end. A decree-step
label inside the instrument's own text -- a treaty's testimonium `FIRMA` -- is not.
Real instruments are checked by `test_units_release_sweep.py` (I1) and, when the
snapshots are on disk, by `TestInstrumentosReales` below.
"""

import json
from pathlib import Path

import pytest

from md2akn import parse_markdown, text_units
from md2akn.outline import bloques_de_decreto, es_etiqueta_de_decreto
from md2akn.validate import _block_escapes


def _marker(orden, label, kind="other", extra=""):
    return f'<!-- scjn:block orden={orden} numero=0 kind={kind}{extra} label="{label}" -->'


def _doc(*blocks):
    return "\n\n".join(blocks) + "\n"


def _article(orden, n, text="Las partes cooperarán."):
    return [
        f'<!-- scjn:block orden={orden} numero={n} kind=article num="{n}" label="ARTÍCULO {n}" -->',
        f"**Artículo {n}.-** {text}",
    ]


def _units(markdown):
    return text_units(parse_markdown(markdown), split_articles=False)


def _by_text(units, fragment):
    found = [u for u in units if fragment in u.text]
    assert found, fragment
    return found


# A treaty the way the SCJN serves 16475: the decree first, then the treaty, then the
# treaty's own closing (a dated line and `Rúbricas.`) and the decree's certification.
def _treaty_with_a_dated_closing():
    return _doc(
        _marker(1, "ENCABEZADO", "header"),
        "**ACTA CONSTITUTIVA**\n\nCARLOS SALINAS, Presidente, a sus habitantes, sabed:",
        _marker(2, "FIRMA"),
        "El doce de octubre el Plenipotenciario firmó ad referéndum el Acta.",
        _marker(3, "PROMULGACIÓN"),
        "Por lo tanto, para su debida observancia, promulgo el presente Decreto.",
        _marker(4, "PREÁMBULO", "preamble"),
        "Los Estados Miembros acuerdan constituir la Asociación.",
        *_article(5, 1),
        _marker(6, "PARTE FINAL DEL ACTA"),
        "La presente Acta consta de dos ejemplares.\n\nLima, a los doce días del mes de octubre. Rúbricas.",
        _marker(7, "PARTE FINAL DEL DECRETO PROMULGATORIO"),
        "La presente es copia fiel y completa del Acta.\n\nConté.- Rúbrica.",
    )


def test_conclusions_never_cross_an_api_block():
    markdown = _treaty_with_a_dated_closing()
    tree = parse_markdown(markdown)
    assert _block_escapes(tree) == []
    assert not [c for c in tree.children if c.akn_type == "conclusions"]
    units = _units(markdown)
    acta = _by_text(units, "consta de dos ejemplares")
    assert [u.envelope for u in acta] == [False]
    assert "Lima, a los doce días" in acta[0].text  # the treaty's own closing stays with its block
    certification = _by_text(units, "copia fiel")
    assert [u.envelope for u in certification] == [True]
    assert certification[0].path[-1] == "PARTE FINAL DEL DECRETO PROMULGATORIO"


def test_the_decree_certification_is_its_own_node():
    tree = parse_markdown(_treaty_with_a_dated_closing())
    eids = [n.eId for n in tree.walk()]
    assert "hc_PARTE_FINAL_DEL_DECRETO_PROMULGATORIO" in eids
    assert "hc_PARTE_FINAL_DEL_ACTA" in eids


def test_the_preamble_is_the_title_block_and_nothing_else():
    markdown = _doc(
        _marker(1, "ENCABEZADO", "header"),
        "**REGLAMENTO INTERIOR**\n\nEl Consejo General, con fundamento en la ley,",
        _marker(2, "CONSIDERANDO", "recitals"),
        "**CONSIDERANDO**\n\nQue el reglamento debe actualizarse;\n\nQue es necesario aprobar lo siguiente.",
        *_article(3, 1, "Este reglamento regula al Consejo."),
    )
    tree = parse_markdown(markdown)
    assert _block_escapes(tree) == []
    preamble = next(c for c in tree.children if c.akn_type == "preamble")
    assert "Que el reglamento" not in preamble.text if hasattr(preamble, "text") else True
    units = _units(markdown)
    assert [u.unit_type for u in _by_text(units, "REGLAMENTO INTERIOR")] == ["preamble"]
    considerando = _by_text(units, "Que el reglamento debe actualizarse")
    assert [(u.unit_type, u.envelope) for u in considerando] == [("intro", False)]
    assert considerando[0].eId == "hc_CONSIDERANDO__intro"


def test_a_treatys_own_preamble_after_the_title_block_is_compared():
    markdown = _doc(
        _marker(1, "ENCABEZADO", "header"),
        "**CONVENIO**",
        _marker(2, "PREÁMBULO", "preamble"),
        "Los Gobiernos signatarios, deseosos de cooperar, convienen lo siguiente.",
        *_article(3, 1),
    )
    units = _units(markdown)
    own = _by_text(units, "deseosos de cooperar")
    assert [u.envelope for u in own] == [False]
    assert [u.envelope for u in _by_text(units, "CONVENIO")] == [True]


# 11374: the decree opens with a block the label table does not know, and the treaty's
# own testimonium is labelled `FIRMA`.
def _treaty_with_a_testimonium():
    return _doc(
        _marker(1, "ENCABEZADO", "header"),
        "**CONSTITUCIÓN**",
        _marker(2, "APROBACIÓN DE INSTRUMENTOS"),
        "Que en la Conferencia se aprobaron instrumentos cuyo texto traducido al español es el siguiente:",
        _marker(3, "PREÁMBULO", "preamble"),
        "La Conferencia General de la Organización, reunida en su sede.",
        *_article(4, 1),
        *_article(5, 2),
        _marker(6, "FIRMA"),
        "En fe de lo cual, los suscritos han firmado esta Constitución.",
        _marker(7, "PARTE FINAL DEL ACTA"),
        "Hecho en la sede de la Organización.",
        _marker(8, "PARTE FINAL DEL DECRETO PROMULGATORIO"),
        "La presente es copia fiel y completa de la Constitución.",
    )


def test_a_leading_block_with_no_role_label_is_envelope_by_position():
    units = _units(_treaty_with_a_testimonium())
    assert [u.envelope for u in _by_text(units, "se aprobaron instrumentos")] == [True]
    assert [u.envelope for u in _by_text(units, "reunida en su sede")] == [False]


def test_a_decree_step_label_inside_the_own_text_is_not_envelope_nor_the_decree_group():
    markdown = _treaty_with_a_testimonium()
    tree = parse_markdown(markdown)
    assert _block_escapes(tree) == []
    units = _units(markdown)
    firma = _by_text(units, "En fe de lo cual")
    assert [u.envelope for u in firma] == [False]
    assert not any("DECRETO PROMULGATORIO" in part for part in firma[0].path)
    assert [u.envelope for u in _by_text(units, "Hecho en la sede")] == [False]
    assert [u.envelope for u in _by_text(units, "copia fiel")] == [True]
    # the leading decree is one group; the testimonium is not in it
    groups = [n for n in tree.walk() if n.refers_to == "#promulgation"]
    assert len(groups) == 1 and groups[0].start_char < firma[0].start_char


def test_a_generic_block_between_decree_steps_belongs_to_the_decree():
    markdown = _doc(
        _marker(1, "ENCABEZADO", "header"),
        "**CANJE DE NOTAS**",
        _marker(2, "FIRMA"),
        "Se firmó el canje.",
        _marker(3, "CANJE DE NOTAS"),
        "Las notas fueron intercambiadas.",
        _marker(4, "PROMULGACIÓN"),
        "Promulgo el presente Decreto.",
        _marker(5, "PREÁMBULO", "preamble"),
        "Texto propio del instrumento.",
        *_article(6, 1),
    )
    units = _units(markdown)
    assert [u.envelope for u in _by_text(units, "notas fueron intercambiadas")] == [True]
    assert [u.envelope for u in _by_text(units, "Texto propio")] == [False]
    tree = parse_markdown(markdown)
    assert len([n for n in tree.walk() if n.refers_to == "#promulgation"]) == 1


def test_the_decrees_own_transitorio_is_envelope_and_an_instruments_own_is_not():
    treaty = {"kind": "other", "label": "PROMULGACIÓN"}, {"kind": "transitorios", "label": "TRANSITORIO"}
    blocks = [{"kind": "header"}, {"kind": "other", "label": "FIRMA"}, *treaty,
              {"kind": "other", "label": "CERTIFICACIÓN"}, {"kind": "preamble"}]
    assert bloques_de_decreto(blocks) == {1: "leading", 2: "leading", 3: "leading", 4: "leading"}
    # a guideline's certification followed by its own transitorios: the instrument's own
    # block ends the trailing run, so the certification is not a decree's
    blocks = [{"kind": "header"}, {"kind": "other", "label": "INTRODUCCIÓN"},
              {"kind": "other", "label": "CERTIFICACIÓN"}, {"kind": "transitorios", "label": "TRANSITORIOS"}]
    assert bloques_de_decreto(blocks) == {}


def test_a_bare_contenido_is_never_read_as_the_decrees():
    # treaty 24128's `CONTENIDO` after the `CERTIFICACIÓN` is the exchange of notes itself
    blocks = [{"kind": "header"}, {"kind": "other", "label": "FIRMA"},
              {"kind": "other", "label": "CERTIFICACIÓN"}, {"kind": "other", "label": "CONTENIDO"},
              {"kind": "other", "label": "PARTE FINAL DEL DECRETO PROMULGATORIO"}]
    assert bloques_de_decreto(blocks) == {1: "leading", 2: "leading", 4: "trailing"}
    blocks = [{"kind": "header"}, {"kind": "article"}, {"kind": "other", "label": "APROBACIÓN"},
              {"kind": "other", "label": "PROMULGACIÓN"}, {"kind": "other", "label": "CONTENIDO"}]
    assert bloques_de_decreto(blocks) == {}
    # the reservations a treaty lists, then its own bare CONTENIDO, between decree steps
    blocks = [{"kind": "header"}, {"kind": "article"}, {"kind": "other", "label": "RESERVAS"},
              {"kind": "other", "label": "CONTENIDO"}, {"kind": "other", "label": "APROBACIÓN"},
              {"kind": "other", "label": "PROMULGACIÓN"}]
    assert bloques_de_decreto(blocks) == {4: "trailing", 5: "trailing"}


def test_the_decrees_leading_run_ends_at_its_certification():
    blocks = [{"kind": "header"}, {"kind": "other", "label": "FIRMA"},
              {"kind": "other", "label": "CERTIFICACIÓN"},
              {"kind": "other", "label": "INTERCAMBIO DE CARTAS"},
              {"kind": "other", "label": "PARTE FINAL DEL DECRETO PROMULGATORIO"}]
    # the exchange of letters between the certification and the decree's closing is the instrument's
    assert bloques_de_decreto(blocks) == {1: "leading", 2: "leading", 4: "trailing"}


def test_an_instrument_that_is_nothing_but_its_envelope_marks_none_of_it():
    only_the_decree = _doc(
        _marker(1, "ENCABEZADO", "header"),
        "**INSTRUMENTOS ADOPTADOS POR EL CONGRESO**",
        _marker(2, "FIRMA"),
        "El Plenipotenciario firmó los instrumentos.",
        _marker(3, "PROMULGACIÓN"),
        "Promulgo el presente Decreto.",
    )
    units = _units(only_the_decree)
    assert units and not any(u.envelope for u in units)
    # one own article is enough to bring the envelope back
    with_an_article = only_the_decree + "\n" + "\n\n".join(_article(4, 1)) + "\n"
    units = _units(with_an_article)
    assert [u.envelope for u in _by_text(units, "Promulgo")] == [True]
    # headings and transitorios are not text of its own: still nothing but the envelope
    with_transitorios = _doc(
        _marker(1, "ENCABEZADO", "header"),
        "**DECRETO**",
        _marker(2, "FIRMA"),
        "El Plenipotenciario firmó.",
        _marker(3, "TRANSITORIOS", "transitorios"),
        "**TRANSITORIOS**\n\n**ÚNICO.-** Entra en vigor.",
    )
    assert not any(u.envelope for u in _units(with_transitorios))


def test_a_decree_closing_is_envelope_even_when_annexes_follow_it():
    blocks = [{"kind": "header"}, {"kind": "article"},
              {"kind": "other", "label": "PARTE FINAL DEL DECRETO PROMULGATORIO"},
              {"kind": "other", "label": "CUADRO DE RUTAS"}]
    assert bloques_de_decreto(blocks) == {2: "closing"}


def test_own_blocks_after_a_generic_leading_block_are_not_swallowed():
    # an instrument that opens with a generic `other` block and has no decree step
    markdown = _doc(
        _marker(1, "ENCABEZADO", "header"),
        "**ACUERDO**",
        _marker(2, "ANTECEDENTES"),
        "Antecedentes del acuerdo, texto propio.",
        *_article(3, 1),
    )
    assert not any(u.envelope for u in _by_text(_units(markdown), "Antecedentes del acuerdo"))


def test_widened_labels_and_the_ones_that_must_not_match():
    for label in ("APROBACIÓN Y DECLARACIÓN", "APROBACIÓN Y DECLARACIONES", "APROBACIÓN DEL PROYECTO",
                  "APROBACIÓN, RATIFICACIÓN Y CANJE DE RATIFICACIONES", "APROBACIÓN DE INSTRUMENTOS",
                  "APROBACIÓN DE LA ASAMBLEA GENERAL DE LAS NACIONES UNIDAS",
                  "PARTE FINAL DEL DECRETO PROMULGATORIO", "PARTE FINAL DE LA CERTIFICACIÓN"):
        assert es_etiqueta_de_decreto(label), label
    for label in ("SECCIÓN 5 APROBACIÓN", "ANEXO DE APROBACIÓN DE TARIFAS", "APROBACIÓN DE LA LEY",
                  "PARTE FINAL DEL ACTA", "PARTE FINAL DEL DECRETO", "ANTECEDENTES"):
        assert not es_etiqueta_de_decreto(label), label


def test_bloques_de_decreto_reads_position_only():
    blocks = [{"kind": "header"}, {"kind": "other", "label": "ARGENTINA"},
              {"kind": "other", "label": "MÉXICO"}, {"kind": "other", "label": "APROBACIÓN"},
              {"kind": "other", "label": "PARTE FINAL DEL DECRETO PROMULGATORIO"}]
    # the notes the two countries exchanged are the instrument's own; the decree is at the end
    assert bloques_de_decreto(blocks) == {3: "trailing", 4: "trailing"}
    # a decree step followed by an own block is not trailing
    blocks = [{"kind": "header"}, {"kind": "article"}, {"kind": "other", "label": "APROBACIÓN"},
              {"kind": "container", "label": "ANEXO 1"}]
    assert bloques_de_decreto(blocks) == {}


def test_a_document_with_no_markers_is_read_as_before():
    plain = (
        "**CAPITULO I**\n\n**Del objeto**\n\n**Artículo 1.-** Esta ley regula el objeto.\n\n"
        "Dado en el Palacio Nacional.- Rúbricas.\n"
    )
    tree = parse_markdown(plain)
    assert [c.akn_type for c in tree.children] == ["body", "conclusions"]
    assert _block_escapes(tree) == []
    assert [u.unit_type for u in text_units(plain)][-1] == "conclusions"


def test_closing_signatures_inside_the_last_block_stay_conclusions():
    # no later marker: the dated line is the tail of the last API block, as before
    markdown = _doc(
        _marker(1, "ENCABEZADO", "header"),
        "**LEY**",
        *_article(2, 1, "Texto del artículo."),
        "Dado en el Palacio Nacional.- Rúbricas.",
    )
    tree = parse_markdown(markdown)
    assert _block_escapes(tree) == []
    assert [c.akn_type for c in tree.children] == ["preamble", "body", "conclusions"]


# -- real instruments (skipped when the snapshots are not on disk) ----------------

_SCJN = Path(__file__).resolve().parents[3] / "scripts" / "scjn"


def _newest(collection, key):
    from datetime import datetime

    folder = _SCJN / collection / key
    if not folder.is_dir():
        pytest.skip(f"{folder} is not on disk")
    return max(
        folder.glob("*.md"),
        key=lambda p: (datetime.strptime(p.stem[:10], "%d-%m-%Y"), p.name),
    ).read_text(encoding="utf-8")


class TestInstrumentosReales:
    def test_16475_certification_is_envelope_and_the_acta_is_compared(self):
        markdown = _newest("tratados", "16475")
        tree = parse_markdown(markdown)
        assert _block_escapes(tree) == []
        units = _units(markdown)
        assert not [u for u in units if u.unit_type == "conclusions"]
        copia = _by_text(units, "La presente es copia fiel y completa")
        assert [u.envelope for u in copia] == [True]
        acta = _by_text(units, "Lima, a los doce días")
        assert [u.envelope for u in acta] == [False]

    def test_11374_decree_opening_is_envelope_and_the_testimonium_is_not(self):
        markdown = _newest("tratados", "11374")
        units = _units(markdown)
        aprobacion = _by_text(units, "se aprobaron instrumentos")
        assert all(u.envelope for u in aprobacion)
        firma = _by_text(units, "En fe de lo cual")
        assert not any(u.envelope for u in firma)
        tree = parse_markdown(markdown)
        grupo = [n for n in tree.walk() if n.refers_to == "#promulgation"]
        assert not [g for g in grupo if g.start_char <= firma[0].start_char < g.end_char]

    def test_24031_keeps_its_envelope(self):
        markdown = _newest("tratados", "24031")
        units = _units(markdown)
        compared = sum(1 for u in units if not u.envelope)
        envelope = sum(1 for u in units if u.envelope)
        assert envelope >= 1 and compared > envelope
        assert _block_escapes(parse_markdown(markdown)) == []

    @pytest.mark.parametrize("key", ["109", "136"])
    def test_a_reglamento_considerando_is_compared_and_not_the_preamble(self, key):
        markdown = _newest("reglamentos", key)
        units = _units(markdown)
        recitals = [u for u in units if u.eId.startswith("hc_CONSIDERAND")]
        if not recitals:
            pytest.skip("this snapshot has no recitals block")
        assert not any(u.envelope for u in recitals)
        assert not any(u.unit_type == "preamble" and "CONSIDERAND" in u.text.upper() for u in units)

    def test_11084_own_preamble_is_compared(self):
        units = _units(_newest("tratados", "11084"))
        own = [u for u in units if u.eId.startswith("hc_PRE")]
        assert own and not any(u.envelope for u in own)

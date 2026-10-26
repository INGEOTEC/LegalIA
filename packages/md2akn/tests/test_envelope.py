"""`TextUnit.envelope` (issue #301): the formulaic text the Mexican publication wraps
around an instrument -- the API's title block, the steps of the promulgation decree and
the decree's closing certification -- is marked on its units, from the outline markers
alone. A treaty's own preamble and its own closing clause are not envelope, and a document
with no markers has none."""

from md2akn import parse_markdown, text_units
from md2akn.outline import es_bloque_de_envoltura, rangos_de_envoltura


def _marker(orden, label, kind="other", extra=""):
    return f'<!-- scjn:block orden={orden} numero=0 kind={kind}{extra} label="{label}" -->'


def _doc(*blocks):
    return "\n\n".join(blocks) + "\n"


def _treaty():
    return _doc(
        _marker(1, "ENCABEZADO", "header"),
        "**ACTAS FINALES DE LA CONFERENCIA**\n\nTEXTO ORIGINAL.\n\nCARLOS SALINAS, Presidente, a sus habitantes, sabed:",
        _marker(2, "FIRMA"),
        "El día tres de marzo el Plenipotenciario firmó ad referéndum las Actas Finales.",
        _marker(3, "PROMULGACIÓN"),
        "Por lo tanto, para su debida observancia, promulgo el presente Decreto.",
        _marker(4, "PREÁMBULO", "preamble"),
        "Considerando las Resoluciones pertinentes, la Conferencia aprobó lo que sigue.",
        _marker(5, "ARTÍCULO 1", "article", " numero=1"),
        "**Artículo 1.-** Las partes se comprometen a cooperar.",
        _marker(6, "PARTE FINAL DEL ESTATUTO"),
        "EN FE DE LO CUAL, los plenipotenciarios firman el presente Estatuto.",
        _marker(7, "NOTIFICACIÓN"),
        "El Gobierno depositario notificó la ratificación a las demás partes.",
        _marker(8, "PARTE FINAL DEL DECRETO PROMULGATORIO"),
        "La presente es copia fiel y completa de las Actas Finales.",
    )


def test_predicate_reads_the_marker_and_the_rule_29_role():
    assert es_bloque_de_envoltura({"kind": "header", "label": "ENCABEZADO"})
    assert es_bloque_de_envoltura({"kind": "other", "label": "APROBACIÓN Y RESERVA"})
    assert es_bloque_de_envoltura({"kind": "other", "label": "Parte final del decreto promulgatorio"})
    assert not es_bloque_de_envoltura({"kind": "other", "label": "PARTE FINAL DE LAS ACTAS"})
    assert not es_bloque_de_envoltura({"kind": "preamble", "label": "PREÁMBULO"})
    assert not es_bloque_de_envoltura({"kind": "article", "label": "ARTÍCULO 1"})


def test_envelope_is_the_header_the_decree_steps_and_the_decree_closing_only():
    units = text_units(parse_markdown(_treaty()), split_articles=False)
    marked = {u.text[:30]: u.envelope for u in units}
    envelope = [u.text for u in units if u.envelope]
    assert any("ACTAS FINALES" in t for t in envelope)  # the header
    assert any("Plenipotenciario" in t for t in envelope)  # FIRMA
    assert any("promulgo" in t for t in envelope)  # PROMULGACIÓN
    assert any("copia fiel" in t for t in envelope)  # the decree's PARTE FINAL
    # the instrument's own text is compared
    for fragment in ("Considerando las Resoluciones", "Las partes se comprometen", "EN FE DE LO CUAL"):
        assert not [u for u in units if fragment in u.text and u.envelope], fragment
    assert marked  # units exist


def test_a_stray_promulgation_step_hanging_outside_the_decree_is_envelope():
    # `NOTIFICACIÓN` is a promulgation step in the T-MEC (137355) and hangs elsewhere;
    # its role comes from rule 29's table, not from where it hangs. In this document
    # the label is not in the table, so it stays compared; the table is the definition.
    units = text_units(parse_markdown(_treaty()), split_articles=False)
    assert not [u for u in units if "depositario" in u.text and u.envelope]
    stray = _doc(
        _marker(1, "ENCABEZADO", "header"),
        "**TRATADO**",
        _marker(2, "ARTÍCULO 1", "article", " numero=1"),
        "**Artículo 1.-** Texto del artículo.",
        _marker(3, "RATIFICACIÓN Y DEPÓSITO"),
        "El instrumento de ratificación fue depositado ante el Secretario General.",
    )
    units = text_units(parse_markdown(stray), split_articles=False)
    assert [u.envelope for u in units if "ratificación" in u.text] == [True]
    assert [u.envelope for u in units if "Texto del artículo" in u.text] == [False]


def test_the_field_changes_nothing_else():
    tree = parse_markdown(_treaty())
    with_markers = text_units(tree, split_articles=False)
    assert all(isinstance(u.envelope, bool) for u in with_markers)
    assert [u.text_sha1 for u in with_markers] == [u.text_sha1 for u in text_units(tree, split_articles=False)]


def test_a_document_with_no_markers_has_no_envelope():
    plain = (
        "**CAPITULO I**\n\n**Del objeto**\n\n**Artículo 1.-** Esta ley regula el objeto.\n\n"
        "FIRMA\n\nEl Presidente firma.\n"
    )
    assert rangos_de_envoltura(plain) == []
    assert not any(u.envelope for u in text_units(plain))

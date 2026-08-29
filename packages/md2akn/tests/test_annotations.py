"""Reform annotations (issue #161).

The fixtures here are shapes taken from the SCJN corpus, not invented ones:
every string in this file was copied from a law under `scripts/scjn/leyes/`,
which is why the accents are inconsistent and one date is misspelled.
"""

import datetime as dt

import pytest

from md2akn import parse_markdown
from md2akn.annotations import (
    es_anotacion,
    normaliza_accion,
    parse_annotation,
    parse_fecha,
)


class TestParseFecha:
    """Dates are written out in Spanish, in every spelling the corpus uses."""

    @pytest.mark.parametrize(
        "mes,numero",
        [
            ("ENERO", 1), ("FEBRERO", 2), ("MARZO", 3), ("ABRIL", 4),
            ("MAYO", 5), ("JUNIO", 6), ("JULIO", 7), ("AGOSTO", 8),
            ("SEPTIEMBRE", 9), ("OCTUBRE", 10), ("NOVIEMBRE", 11),
            ("DICIEMBRE", 12),
        ],
    )
    def test_los_doce_meses(self, mes, numero):
        assert parse_fecha(f"10 DE {mes} DE 2011") == dt.date(2011, numero, 10)

    def test_setiembre_sin_p(self):
        # The older spelling, still in the corpus.
        assert parse_fecha("3 DE SETIEMBRE DE 1970") == dt.date(1970, 9, 3)

    @pytest.mark.parametrize("dia", ["1o.", "1°", "1º", "1"])
    def test_primero_ordinal(self, dia):
        assert parse_fecha(f"{dia} DE ENERO DE 1995") == dt.date(1995, 1, 1)

    @pytest.mark.parametrize(
        "texto",
        ["30 DICIEMBRE DE 2002", "30 DE DICIEMBRE 2002", "30 DE DICIEMBRE DE 2002"],
    )
    def test_los_de_son_opcionales(self, texto):
        assert parse_fecha(texto) == dt.date(2002, 12, 30)

    def test_anio_de_cuatro_digitos(self):
        assert parse_fecha("5 DE FEBRERO DE 917") is None

    def test_dia_que_el_mes_no_tiene(self):
        # The text said what it said; no silent shift to the 1st of March.
        assert parse_fecha("31 DE FEBRERO DE 2011") is None

    def test_un_mes_inventado(self):
        assert parse_fecha("13 DE AGOSTO CE 2009") is None


class TestNormalizaAccion:
    @pytest.mark.parametrize(
        "escrito", ["REFORMADO", "REFORMADA", "REFORMADOS", "REFORMADAS"]
    )
    def test_genero_y_numero_se_pliegan(self, escrito):
        # Gender agrees with what was reformed, which says nothing about the
        # reform itself.
        assert normaliza_accion(escrito) == "REFORMADO"

    def test_accion_compuesta(self):
        assert normaliza_accion("REFORMADA Y REUBICADA") == "REFORMADO Y REUBICADO"

    def test_fe_de_erratas_no_tiene_genero(self):
        assert normaliza_accion("F. DE E.") == "F. DE E."


class TestEsAnotacion:
    @pytest.mark.parametrize(
        "cuerpo",
        [
            "REFORMADO PRIMER PÁRRAFO, D.O.F. 10 DE JUNIO DE 2011",
            "ADICIONADA, D.O.F. 30 DE DICIEMBRE DE 2002",
            "F. DE E., D.O.F. 3 DE MARZO DE 2011",
        ],
    )
    def test_una_accion_conocida(self, cuerpo):
        assert es_anotacion(cuerpo)

    @pytest.mark.parametrize(
        "cuerpo",
        [
            # Same shape, and none of them is a reform: admitting these would
            # make #162's "annotations not understood" count meaningless.
            "NOTA: EL 22 DE JUNIO DE 2023, EL PLENO DE LA SUPREMA CORTE",
            "ARANCEL",
            "VÉASE TABLA ANEXA",
        ],
    )
    def test_un_parentesis_cualquiera_no_lo_es(self, cuerpo):
        assert not es_anotacion(cuerpo)


class TestParseAnnotation:
    def test_accion_alcance_y_fecha(self):
        a = parse_annotation("**(REFORMADO PRIMER PÁRRAFO, D.O.F. 10 DE JUNIO DE 2011)**")
        assert a.action == "REFORMADO"
        assert a.scope == "PRIMER PARRAFO"
        assert a.date == dt.date(2011, 6, 10)
        assert a.source is None

    def test_sin_alcance(self):
        a = parse_annotation("**(ADICIONADO, D.O.F. 4 DE JUNIO DE 2012)**")
        assert (a.action, a.scope, a.date) == (
            "ADICIONADO", None, dt.date(2012, 6, 4),
        )

    def test_genero_femenino(self):
        a = parse_annotation("**(REFORMADA, D.O.F. 9 DE ABRIL DE 2012)**")
        assert a.action == "REFORMADO"

    def test_accion_compuesta(self):
        a = parse_annotation("**(REFORMADO Y REUBICADO, D.O.F. 1o. DE JUNIO DE 2009)**")
        assert a.action == "REFORMADO Y REUBICADO"
        assert a.date == dt.date(2009, 6, 1)

    def test_alcance_largo(self):
        a = parse_annotation(
            "**(ADICIONADO CON LOS ARTICULOS QUE LO INTEGRAN, "
            "D.O.F. 30 DE NOVIEMBRE DE 2010)**"
        )
        assert a.scope == "CON LOS ARTICULOS QUE LO INTEGRAN"

    def test_una_norma_en_lugar_de_una_fecha(self):
        a = parse_annotation(
            "**(DEROGADO POR ARTICULO SEGUNDO TRANSITORIO DE LA LEY DEL "
            "SERVICIO POSTAL MEXICANO, D.O.F. 24 DE DICIEMBRE DE 1986)**"
        )
        assert a.action == "DEROGADO"
        # The instrument that did the repealing is `source`, not `scope`: it
        # says who acted, not what was acted on.
        assert a.source == (
            "ARTICULO SEGUNDO TRANSITORIO DE LA LEY DEL SERVICIO POSTAL MEXICANO"
        )
        assert a.scope is None
        assert a.date == dt.date(1986, 12, 24)

    def test_fe_de_erratas(self):
        a = parse_annotation("**(F. DE E., D.O.F. 3 DE MARZO DE 2011)**")
        assert a.action == "F. DE E."
        assert a.date == dt.date(2011, 3, 3)

    @pytest.mark.parametrize(
        "raw",
        [
            "**(REFORMADO, D.O.F 8 DE NOVIEMBRE DE 2019)**",       # no final dot
            "**(REFORMADO, D.O.F., 8 DE NOVIEMBRE DE 2019)**",     # a comma too many
            "**(REFORMADO, D.O.F. DE 8 DE NOVIEMBRE DE 2019)**",   # a `DE` too many
            "**(REFORMADO, 8 DE NOVIEMBRE DE 2019)**",             # no gazette at all
        ],
    )
    def test_variantes_tipograficas_de_la_cita(self, raw):
        # 46 of the corpus' 36,836 annotations are written one of these ways.
        assert parse_annotation(raw).date == dt.date(2019, 11, 8)

    def test_una_fecha_ilegible_conserva_el_crudo(self):
        # A genuine typo in the source: `CE` for `DE`. The annotation is kept
        # with `date=None` rather than dropped, so #162's sweep can count it.
        raw = "**(REFORMADO, D.O.F. 13 DE AGOSTO CE 2009)**"
        a = parse_annotation(raw)
        assert a.action == "REFORMADO"
        assert a.date is None
        assert a.raw == raw

    def test_lo_que_no_es_anotacion_conserva_el_crudo(self):
        a = parse_annotation("(VÉASE TABLA ANEXA)")
        assert a.action is None and a.raw == "(VÉASE TABLA ANEXA)"


class TestAnotacionesEnElArbol:
    """Where the annotations land once the document is parsed."""

    def test_antes_de_un_articulo(self):
        act = parse_markdown(
            "**(REFORMADO, D.O.F. 10 DE JUNIO DE 2011)**\n\n"
            "**ARTICULO 1.-** El texto.\n"
        )
        art = act.find("art_1")
        assert [a.action for a in art.notes] == ["REFORMADO"]
        # Annotations are metadata, not nodes: nothing extra in the tree.
        assert not any(n.akn_type == "anotacion" for n in act.walk())

    def test_el_nodo_cubre_el_texto_de_la_anotacion(self):
        md = (
            "**(REFORMADO, D.O.F. 10 DE JUNIO DE 2011)**\n\n"
            "**ARTICULO 1.-** El texto.\n"
        )
        art = parse_markdown(md).find("art_1")
        # The annotation produces no node of its own, so the node it attaches
        # to has to cover it or the document stops being fully covered.
        assert "REFORMADO" in art.text

    def test_antes_de_un_contenedor(self):
        act = parse_markdown(
            "**(REFORMADA SU DENOMINACION, D.O.F. 4 DE JUNIO DE 2012)**\n\n"
            "**TITULO PRIMERO**\n\n"
            "**ARTICULO 1.-** El texto.\n"
        )
        titulo = next(n for n in act.walk() if n.akn_type == "title")
        assert [(a.action, a.scope) for a in titulo.notes] == [
            ("REFORMADO", "SU DENOMINACION")
        ]

    def test_dentro_del_encabezado_del_articulo(self):
        # A repealed article has no text left to put a block annotation above,
        # so the note is written into the heading itself.
        act = parse_markdown(
            "**ARTICULO 5.- (DEROGADO, D.O.F. 3 DE MAYO DE 1999)**\n"
        )
        art = act.find("art_5")
        assert [a.action for a in art.notes] == ["DEROGADO"]

    def test_entre_fracciones(self):
        act = parse_markdown(
            "**ARTICULO 1.-** Son obligaciones:\n\n"
            "I.- La primera.\n\n"
            "**(REFORMADA, D.O.F. 10 DE JUNIO DE 2011)**\n\n"
            "II.- La segunda.\n"
        )
        segunda = act.find("art_1__para_II")
        assert [a.action for a in segunda.notes] == ["REFORMADO"]
        assert act.find("art_1__para_I").notes == []

    def test_varias_seguidas_van_al_mismo_nodo(self):
        act = parse_markdown(
            "**(ADICIONADO, D.O.F. 4 DE JUNIO DE 2012)**\n\n"
            "**(REFORMADO, D.O.F. 10 DE JUNIO DE 2011)**\n\n"
            "**ARTICULO 1.-** El texto.\n"
        )
        assert [a.action for a in act.find("art_1").notes] == [
            "ADICIONADO", "REFORMADO",
        ]

    def test_al_final_del_documento_cuelga_del_act(self):
        # Nothing follows it, so it has no node to describe. Kept on the act
        # rather than lost.
        act = parse_markdown(
            "**ARTICULO 1.-** El texto.\n\n"
            "**(DEROGADO, D.O.F. 3 DE MAYO DE 1999)**\n"
        )
        assert [a.action for a in act.notes] == ["DEROGADO"]

    def test_un_parentesis_que_no_es_anotacion_sigue_siendo_texto(self):
        act = parse_markdown(
            "**ARTICULO 1.-** El texto.\n\n"
            "(VÉASE TABLA ANEXA)\n"
        )
        assert act.find("art_1").notes == []
        assert "VÉASE TABLA ANEXA" in act.find("art_1").text

import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import docx
import requests

from nota2md import scjn


def _hacer_tgz(archivos: dict) -> bytes:
    """Build an in-memory tarball from {member_name: raw_bytes_or_str},
    same helper shape as packages/nota2md/tests/test_utils.py's own."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for nombre, contenido in archivos.items():
            data = contenido if isinstance(contenido, bytes) else contenido.encode("utf-8")
            info = tarfile.TarInfo(name=nombre)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()

PAGINA_BUSQUEDA = (
    '<html><body><form id="aspnetForm" action="Paginas/Buscar.aspx">'
    '<input type="hidden" name="__VIEWSTATE" value="abc"/>'
    "</form></body></html>"
)


def _pagina_resultados(candidatos_html: str) -> str:
    return f"<html><body>{candidatos_html}</body></html>"


def _candidato_html(titulo: str, ambito: str, vigencia: str, href: str) -> str:
    return (
        f'<a href="{href}">{titulo}<br/>Última actualización: 01/01/2020<br/>'
        f"Vigencia:<span>{vigencia}</span><br/>Ambito: {ambito}<br/>"
        "<span>Ver cronología del ordenamiento</span></a>"
    )


class TestBuscarYCandidato(unittest.TestCase):
    def test_parsea_titulo_ambito_y_vigencia_de_cada_candidato(self):
        sesion = Mock()
        sesion.get.return_value = Mock(text=PAGINA_BUSQUEDA, url=scjn.BASE_URL)
        sesion.post.return_value = Mock(
            text=_pagina_resultados(
                _candidato_html(
                    "LEY DE AMNISTIA", "FEDERAL", "VIGENTE",
                    "wfOrdenamientoDetalle.aspx?q=xyz",
                )
            ),
            url=scjn.BASE_URL + "resultados",
        )

        candidatos, referer = scjn.buscar(sesion, "Ley de Amnistia")

        self.assertEqual(len(candidatos), 1)
        candidato = candidatos[0]
        self.assertEqual(candidato.titulo, "LEY DE AMNISTIA")
        self.assertEqual(candidato.ambito, "FEDERAL")
        self.assertEqual(candidato.vigencia, "VIGENTE")
        self.assertTrue(candidato.url.endswith("wfOrdenamientoDetalle.aspx?q=xyz"))
        self.assertEqual(referer, scjn.BASE_URL + "resultados")

    def test_manda_el_nombre_buscado_y_el_evento_de_boton_buscar_en_el_post(self):
        sesion = Mock()
        sesion.get.return_value = Mock(text=PAGINA_BUSQUEDA, url=scjn.BASE_URL)
        sesion.post.return_value = Mock(text=_pagina_resultados(""), url=scjn.BASE_URL)

        scjn.buscar(sesion, "Ley de Amnistia")

        _, kwargs = sesion.post.call_args
        self.assertEqual(
            kwargs["data"]["ctl00$MainContentPlaceHolder$ucBusqueda1$txtPalabra"],
            "Ley de Amnistia",
        )
        self.assertEqual(
            kwargs["data"]["__EVENTTARGET"],
            "ctl00$MainContentPlaceHolder$ucBusqueda1$btnBuscar",
        )
        # __VIEWSTATE and friends are resubmitted unchanged, like any WebForms POST.
        self.assertEqual(kwargs["data"]["__VIEWSTATE"], "abc")


class TestEligeCandidato(unittest.TestCase):
    def _candidato(self, titulo, ambito, vigencia, url="u"):
        return scjn.Candidato(titulo=titulo, url=url, ambito=ambito, vigencia=vigencia)

    def test_regresa_none_sin_candidatos(self):
        self.assertIsNone(scjn.elige_candidato([], "Ley X"))

    def test_prefiere_federal_y_vigente_sobre_estatal_o_abrogado(self):
        candidatos = [
            self._candidato("REGLAMENTO DE LA LEY ADUANERA", "FEDERAL", "ABROGADO (A)", url="viejo"),
            self._candidato("REGLAMENTO DE LA LEY ADUANERA", "FEDERAL", "VIGENTE", url="nuevo"),
            self._candidato("CODIGO CIVIL DEL ESTADO DE MEXICO", "ESTATAL", "VIGENTE", url="estatal"),
        ]

        elegido = scjn.elige_candidato(candidatos, "Reglamento de la Ley Aduanera")

        self.assertEqual(elegido.url, "nuevo")

    def test_no_descarta_el_unico_candidato_por_no_ser_federal_o_vigente(self):
        candidatos = [self._candidato("LEY DE EDUCACION DE NUEVO LEON", "ESTATAL", "VIGENTE")]

        elegido = scjn.elige_candidato(candidatos, "Ley de Educacion de Nuevo Leon")

        self.assertIsNotNone(elegido)

    def test_desempata_por_similitud_de_titulo_entre_varios_federales_vigentes(self):
        candidatos = [
            self._candidato("LEY DE MINERIA", "FEDERAL", "VIGENTE", url="correcto"),
            self._candidato("LEY FEDERAL DEL TRABAJO", "FEDERAL", "VIGENTE", url="otro"),
        ]

        elegido = scjn.elige_candidato(candidatos, "Ley de Mineria")

        self.assertEqual(elegido.url, "correcto")

    # --- issue #115: 5 documento-equivocado cases confirmed by manual audit

    def test_marca_no_sospechoso_un_candidato_de_alta_similitud(self):
        candidatos = [self._candidato("LEY FEDERAL DEL TRABAJO", "FEDERAL", "VIGENTE")]

        elegido = scjn.elige_candidato(candidatos, "Ley Federal del Trabajo")

        self.assertIsNotNone(elegido)
        self.assertFalse(elegido.sospechoso)
        self.assertGreater(elegido.ratio, scjn.UMBRAL_CONFIANZA_SIMILITUD)

    def test_lsint_rechaza_un_acuerdo_administrativo_del_pleno_como_unico_candidato(self):
        # https://github.com/INGEOTEC/LegalIA/issues/115 hallazgo C: the
        # SCJN's search for "LEY de Seguridad Interior" returned no law at
        # all, only its own Pleno acuerdo mentioning the name.
        candidatos = [
            self._candidato(
                "ACUERDO GENERAL NÚMERO 3/2018 DEL PLENO DE LA SUPREMA CORTE DE JUSTICIA "
                "DE LA NACIÓN",
                "FEDERAL",
                "VIGENTE",
            )
        ]

        self.assertIsNone(scjn.elige_candidato(candidatos, "LEY de Seguridad Interior"))

    def test_lisr_rechaza_un_acuerdo_administrativo_del_pleno_como_unico_candidato(self):
        candidatos = [
            self._candidato(
                "ACUERDO GENERAL NÚMERO 11/2015 DEL PLENO DE LA SUPREMA CORTE DE JUSTICIA "
                "DE LA NACIÓN",
                "FEDERAL",
                "VIGENTE",
            )
        ]

        self.assertIsNone(
            scjn.elige_candidato(candidatos, "LEY del Impuesto sobre la Renta")
        )

    def test_ccf_rechaza_un_candidato_por_debajo_del_piso_de_similitud(self):
        candidatos = [
            self._candidato(
                "CÓDIGO DE CONDUCTA DE LA AGENCIA FEDERAL DE AVIACIÓN CIVIL",
                "FEDERAL",
                "VIGENTE",
            )
        ]

        self.assertIsNone(scjn.elige_candidato(candidatos, "CÓDIGO Civil Federal"))

    def test_ccf_ignora_el_nombre_anterior_al_elegir_el_candidato_renombrado(self):
        candidatos = [
            self._candidato(
                "CÓDIGO DE CONDUCTA DE LA AGENCIA FEDERAL DE AVIACIÓN CIVIL",
                "FEDERAL",
                "VIGENTE",
            ),
            self._candidato(
                "CODIGO CIVIL FEDERAL -ANTES CODIGO CIVIL PARA EL DISTRITO FEDERAL EN "
                "MATERIA COMUN Y PARA TODA LA REPUBLICA EN MATERIA FEDERAL -",
                "FEDERAL",
                "VIGENTE",
            ),
        ]

        elegido = scjn.elige_candidato(candidatos, "CÓDIGO Civil Federal")

        self.assertIsNotNone(elegido)
        self.assertTrue(elegido.titulo.startswith("CODIGO CIVIL FEDERAL -ANTES"))
        self.assertFalse(elegido.sospechoso)

    def test_lopgjdf_rechaza_el_reglamento_de_la_ley_buscada(self):
        candidatos = [
            self._candidato(
                "REGLAMENTO DE LA LEY ORGÁNICA DE LA PROCURADURÍA GENERAL DE JUSTICIA "
                "DEL DISTRITO FEDERAL",
                "FEDERAL",
                "VIGENTE",
            )
        ]

        self.assertIsNone(
            scjn.elige_candidato(
                candidatos,
                "LEY Orgánica de la Procuraduría General de Justicia del Distrito Federal",
            )
        )

    def test_lfd_marca_sospechoso_en_vez_de_rechazar_una_ley_distinta_de_nombre_parecido(self):
        # Zone grise (issue #115, plan de acción punto 3): "LEY Federal de
        # Derechos" and "LEY FEDERAL DE LOS DERECHOS DEL CONTRIBUYENTE" are
        # two real, different laws — not resolvable by text alone, so this
        # is flagged for manual review rather than silently accepted or
        # rejected.
        candidatos = [
            self._candidato(
                "LEY FEDERAL DE LOS DERECHOS DEL CONTRIBUYENTE", "FEDERAL", "VIGENTE"
            )
        ]

        elegido = scjn.elige_candidato(candidatos, "LEY Federal de Derechos")

        self.assertIsNotNone(elegido)
        self.assertTrue(elegido.sospechoso)
        self.assertGreaterEqual(elegido.ratio, scjn.UMBRAL_MINIMO_SIMILITUD)
        self.assertLess(elegido.ratio, scjn.UMBRAL_CONFIANZA_SIMILITUD)


class TestRatioSimilitudYGuardas(unittest.TestCase):
    def test_es_acuerdo_interno_detecta_un_acuerdo_general_del_pleno(self):
        self.assertTrue(
            scjn.es_acuerdo_interno(
                "ACUERDO GENERAL NÚMERO 3/2018 DEL PLENO DE LA SUPREMA CORTE DE JUSTICIA "
                "DE LA NACIÓN"
            )
        )

    def test_es_acuerdo_interno_no_marca_una_ley_cualquiera(self):
        self.assertFalse(scjn.es_acuerdo_interno("LEY FEDERAL DEL TRABAJO"))

    def test_ratio_similitud_ignora_el_sufijo_de_nombre_anterior(self):
        titulo = (
            "CODIGO CIVIL FEDERAL -ANTES CODIGO CIVIL PARA EL DISTRITO FEDERAL EN "
            "MATERIA COMUN Y PARA TODA LA REPUBLICA EN MATERIA FEDERAL -"
        )
        self.assertEqual(scjn.ratio_similitud(titulo, "Código Civil Federal"), 1.0)

    def test_grupo_instrumento_reconoce_ley_codigo_y_reglamento(self):
        self.assertEqual(scjn.grupo_instrumento("LEY FEDERAL DEL TRABAJO"), "ley")
        self.assertEqual(scjn.grupo_instrumento("CÓDIGO Civil Federal"), "ley")
        self.assertEqual(
            scjn.grupo_instrumento("REGLAMENTO DE LA LEY ADUANERA"), "reglamento"
        )
        self.assertIsNone(scjn.grupo_instrumento("Convenio 107 OIT"))


TABLA_REFORMAS = """
<table>
<tr><td>
Fecha de publicación: 14/06/2024 Fecha de expedición: 10/06/2024
Categoría: DECRETO No. y sección de Publicación: 5
<a href="descarga1.docx">Ver texto completo de la última publicación</a>
</td></tr>
<tr><td>
Fecha de publicación: 22/01/1994 Fecha de expedición: 21/01/1994
Categoría: LEY No. y sección de Publicación: 16
<a href="descarga2.docx">Ver texto completo de la última publicación</a>
</td></tr>
<tr><td>
Sin fecha de publicación reconocible, esta fila se ignora.
<a href="descarga3.docx">Ver texto completo de la última publicación</a>
</td></tr>
</table>
"""


TABLA_REFORMAS_PAGINA1 = """
<form id="aspnetForm" action="detalle.aspx">
<input type="hidden" name="__VIEWSTATE" value="estado1"/>
</form>
<table><tr><td>Página 1 de 2 [2 Registros en total]</td></tr></table>
<table>
<tr><td>
Fecha de publicación: 14/06/2024 Fecha de expedición: 10/06/2024
Categoría: DECRETO No. y sección de Publicación: 5
<a href="descarga1.docx">Ver texto completo de la última publicación</a>
</td></tr>
</table>
"""

TABLA_REFORMAS_PAGINA2 = """
<table>
<tr><td>
Fecha de publicación: 22/01/1994 Fecha de expedición: 21/01/1994
Categoría: LEY No. y sección de Publicación: 16
<a href="descarga2.docx">Ver texto completo de la última publicación</a>
</td></tr>
</table>
"""


class TestFilasDeReforma(unittest.TestCase):
    def test_extrae_fecha_categoria_y_url_de_cada_fila_con_docx(self):
        sesion = Mock()
        sesion.get.return_value = Mock(
            text=f"<html><body>{TABLA_REFORMAS}</body></html>",
            url="https://legislacion.scjn.gob.mx/Buscador/Paginas/wfOrdenamientoDetalle.aspx?q=1",
        )

        filas, referer = scjn.filas_de_reforma(sesion, "detalle-url", "busqueda-url")

        self.assertEqual(len(filas), 2)
        self.assertEqual(filas[0].fecha_publicacion, "14-06-2024")
        self.assertEqual(filas[0].fecha_expedicion, "10-06-2024")
        self.assertEqual(filas[0].categoria, "DECRETO")
        self.assertTrue(filas[0].url_docx.endswith("descarga1.docx"))
        self.assertEqual(filas[1].fecha_publicacion, "22-01-1994")
        self.assertEqual(filas[1].categoria, "LEY")

    def test_recorre_todas_las_paginas_del_grid_paginado(self):
        detail_url = "https://legislacion.scjn.gob.mx/Buscador/Paginas/wfOrdenamientoDetalle.aspx?q=1"
        sesion = Mock()
        sesion.get.return_value = Mock(
            text=f"<html><body>{TABLA_REFORMAS_PAGINA1}</body></html>", url=detail_url
        )
        sesion.post.return_value = Mock(
            text=f"<html><body>{TABLA_REFORMAS_PAGINA2}</body></html>", url=detail_url
        )

        filas, referer = scjn.filas_de_reforma(sesion, detail_url, "busqueda-url", espera=0)

        # 10-per-page grid only shows page 1 unless its own pager is walked.
        self.assertEqual(len(filas), 2)
        self.assertEqual(filas[0].fecha_publicacion, "14-06-2024")
        self.assertEqual(filas[1].fecha_publicacion, "22-01-1994")

        _, kwargs = sesion.post.call_args
        self.assertEqual(kwargs["data"]["__EVENTTARGET"], scjn._PAGER_TARGET)
        self.assertEqual(kwargs["data"]["__EVENTARGUMENT"], "PN1")
        # The detail page's own __VIEWSTATE is resubmitted, not the search page's.
        self.assertEqual(kwargs["data"]["__VIEWSTATE"], "estado1")


TABLA_REFORMAS_FECHAS_DUPLICADAS = """
<table>
<tr><td>
Fecha de publicación: 14/06/2024 Fecha de expedición: 10/06/2024
Categoría: DECRETO No. y sección de Publicación: 5
<a href="descarga1.docx">Ver texto completo de la última publicación</a>
</td></tr>
<tr><td>
Fecha de publicación: 14/06/2024 Fecha de expedición: 09/06/2024
Categoría: DECRETO No. y sección de Publicación: 4
<a href="descarga2.docx">Ver texto completo de la última publicación</a>
</td></tr>
</table>
"""


class TestDescargaDocx(unittest.TestCase):
    def test_reintenta_ante_connection_error_y_luego_tiene_exito(self):
        sesion = Mock()
        sesion.get.side_effect = [
            requests.exceptions.ConnectionError(),
            Mock(content=b"PK\x03\x04resto", headers={"content-type": "x"}),
        ]

        contenido = scjn.descarga_docx(sesion, "url", "referer", espera=0)

        self.assertEqual(contenido, b"PK\x03\x04resto")
        self.assertEqual(sesion.get.call_count, 2)

    def test_agota_intentos_y_relanza_el_connection_error(self):
        sesion = Mock()
        sesion.get.side_effect = requests.exceptions.ConnectionError()

        with self.assertRaises(requests.exceptions.ConnectionError):
            scjn.descarga_docx(sesion, "url", "referer", intentos=2, espera=0)

        self.assertEqual(sesion.get.call_count, 2)

    def test_rechaza_una_respuesta_que_no_es_un_docx(self):
        sesion = Mock()
        sesion.get.return_value = Mock(
            content=b"<html>error</html>", headers={"content-type": "text/html"}
        )

        with self.assertRaises(ValueError):
            scjn.descarga_docx(sesion, "url", "referer")


def _docx_bytes(parrafos: list[str]) -> bytes:
    import io

    documento = docx.Document()
    for texto in parrafos:
        documento.add_paragraph(texto)
    buf = io.BytesIO()
    documento.save(buf)
    return buf.getvalue()


class TestDocxAMarkdown(unittest.TestCase):
    def test_convierte_titular_articulo_y_transitorios_a_markdown(self):
        contenido = _docx_bytes(
            [
                "TEXTO ORIGINAL.",
                "",
                "Al margen un sello con el Escudo Nacional.",
                "",
                "Artículo 1o.- Se decreta la disposición de prueba.",
                "",
                "TRANSITORIOS",
                "",
                "PRIMERO.- Entra en vigor de inmediato.",
            ]
        )

        markdown = scjn.docx_a_markdown(contenido)

        self.assertIn("**TEXTO ORIGINAL.**", markdown)
        self.assertIn("## Al margen un sello con el Escudo Nacional.", markdown)
        self.assertIn(
            "**Artículo 1o.-** Se decreta la disposición de prueba.", markdown
        )
        self.assertIn("## Transitorios", markdown)
        self.assertIn("**PRIMERO.-** Entra en vigor de inmediato.", markdown)

    def test_omite_los_parrafos_vacios_usados_como_separadores(self):
        contenido = _docx_bytes(["Primer párrafo.", "", "", "Segundo párrafo."])

        markdown = scjn.docx_a_markdown(contenido)

        self.assertEqual(markdown, "Primer párrafo.\n\nSegundo párrafo.\n")

    def test_quita_la_nota_editorial_embebida_en_una_anotacion_de_reforma(self):
        contenido = _docx_bytes(
            ["(REFORMADO [N. DE E. ESTE PÁRRAFO], D.O.F. 19 DE DICIEMBRE DE 2017)"]
        )

        markdown = scjn.docx_a_markdown(contenido)

        self.assertEqual(
            markdown, "**(REFORMADO, D.O.F. 19 DE DICIEMBRE DE 2017)**\n"
        )

    def test_omite_por_completo_un_parrafo_que_es_solo_nota_editorial(self):
        contenido = _docx_bytes(
            [
                "Artículo 1o.- Se decreta la disposición de prueba.",
                "",
                '[N. DE E. TRANSITORIO DEL "DECRETO POR EL QUE SE REFORMA".]',
                "",
                "Artículo 2o.- Otra disposición.",
            ]
        )

        markdown = scjn.docx_a_markdown(contenido)

        self.assertNotIn("N. DE E.", markdown)
        self.assertIn("**Artículo 1o.-** Se decreta la disposición de prueba.", markdown)
        self.assertIn("**Artículo 2o.-** Otra disposición.", markdown)


class TestQuitaNotasEditoriales(unittest.TestCase):
    def test_quita_la_nota_embebida_dentro_de_una_anotacion_de_reforma(self):
        self.assertEqual(
            scjn.quita_notas_editoriales(
                "(REFORMADO [N. DE E. ESTE PÁRRAFO], D.O.F. 19 DE DICIEMBRE DE 2017)"
            ),
            "(REFORMADO, D.O.F. 19 DE DICIEMBRE DE 2017)",
        )

    def test_quita_la_nota_embebida_con_variante_n_de_punto_e(self):
        self.assertEqual(
            scjn.quita_notas_editoriales(
                "(ADICIONADA [N. DE . E. REUBICADA], D.O.F. 15 DE JUNIO DE 2007)"
            ),
            "(ADICIONADA, D.O.F. 15 DE JUNIO DE 2007)",
        )

    def test_deja_vacio_un_parrafo_que_es_enteramente_nota_editorial(self):
        self.assertEqual(
            scjn.quita_notas_editoriales(
                '[N. DE E. TRANSITORIO DEL "DECRETO POR EL QUE SE REFORMA".]'
            ),
            "",
        )

    def test_deja_vacio_un_parrafo_con_el_marcador_nota_n(self):
        self.assertEqual(
            scjn.quita_notas_editoriales(
                "[NOTA 1. DE CONFORMIDAD CON EL ACUERDO EMITIDO POR EL CONSEJO.]"
            ),
            "",
        )

    def test_quita_un_corchete_sin_marcador_pero_todo_en_mayusculas(self):
        self.assertEqual(
            scjn.quita_notas_editoriales(
                "(REUBICADO [ANTES ARTICULO 57], D.O.F. 23 DE ENERO DE 2004)"
            ),
            "(REUBICADO, D.O.F. 23 DE ENERO DE 2004)",
        )

    def test_no_toca_una_formula_arancelaria_entre_corchetes(self):
        parrafo = "El resultado de la fórmula [(S/365)+V * (I + D)] se aplica."
        self.assertEqual(scjn.quita_notas_editoriales(parrafo), parrafo)

    def test_no_toca_un_nombre_quimico_entre_corchetes(self):
        parrafo = "Se entiende por [4-nitro-3-(trifluorometil)fenilo] la substancia."
        self.assertEqual(scjn.quita_notas_editoriales(parrafo), parrafo)

    def test_no_confunde_la_nota_n_con_una_cita_de_nota_arancelaria(self):
        parrafo = "Mezclas previstas en la Nota 1 b) de este Capítulo."
        self.assertEqual(scjn.quita_notas_editoriales(parrafo), parrafo)

    def test_quita_el_marcador_suelto_que_sigue_a_una_anotacion_ya_cerrada(self):
        self.assertEqual(
            scjn.quita_notas_editoriales(
                "(ADICIONADA, D.O.F. 14 DE NOVIEMBRE DE 2013) N. DE E. SÓLO EN "
                "CUANTO AL CONTENIDO, PORQUE DEL ANÁLISIS DEL TEXTO ORIGINAL "
                "PUBLICADO EL 2 DE AGOSTO DE 2006, SE APRECIA LA EXISTENCIA DE "
                "ESTA FRACCIÓN."
            ),
            "(ADICIONADA, D.O.F. 14 DE NOVIEMBRE DE 2013)",
        )

    def test_quita_el_marcador_suelto_embebido_antes_de_que_la_anotacion_reanude(self):
        self.assertEqual(
            scjn.quita_notas_editoriales(
                "(REFORMADO N. DE E. ESTE PÁRRAFO, D.O.F. 21 DE FEBRERO DE 2018)"
            ),
            "(REFORMADO, D.O.F. 21 DE FEBRERO DE 2018)",
        )

    def test_deja_vacio_un_parrafo_envuelto_en_parentesis_sin_marcador_de_corchete(self):
        self.assertEqual(
            scjn.quita_notas_editoriales(
                "(NOTA 1: EL PLENO DE LA SUPREMA CORTE DECLARÓ LA INVALIDEZ.)"
            ),
            "",
        )

    def test_quita_la_nota_entre_parentesis_que_sigue_a_texto_real_en_el_mismo_parrafo(self):
        self.assertEqual(
            scjn.quita_notas_editoriales(
                "Artículo 58.- La música oficial del Himno Nacional es la "
                "siguiente: (N. DE E., VÉASE D.O.F. 8 DE FEBRERO DE 1984)"
            ),
            "Artículo 58.- La música oficial del Himno Nacional es la siguiente:",
        )

    def test_conserva_la_negrita_de_un_parrafo_ya_formateado_al_limpiarlo(self):
        self.assertEqual(
            scjn.quita_notas_editoriales(
                "**(REFORMADA [N. DE E. ADICIONADA], D.O.F. 15 DE ENERO DE 2026)**"
            ),
            "**(REFORMADA, D.O.F. 15 DE ENERO DE 2026)**",
        )

    def test_es_un_no_op_sobre_un_parrafo_ya_limpio(self):
        parrafo = "**(REFORMADA, D.O.F. 15 DE ENERO DE 2026)**"
        self.assertEqual(scjn.quita_notas_editoriales(parrafo), parrafo)


class TestSlugInstrumento(unittest.TestCase):
    def test_usa_abrev_cuando_esta_disponible(self):
        self.assertEqual(scjn.slug_instrumento({"abrev": "cpeum", "nombre": "CONSTITUCIÓN"}), "cpeum")

    def test_forma_un_slug_del_nombre_cuando_no_hay_abrev(self):
        self.assertEqual(
            scjn.slug_instrumento({"nombre": "Convenio 107 OIT"}), "convenio-107-oit"
        )


class TestDescargaOrdenamiento(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.outdir = Path(self.tmpdir.name) / "ley-de-prueba"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_regresa_lista_vacia_sin_candidatos(self):
        sesion = Mock()
        sesion.get.return_value = Mock(text=PAGINA_BUSQUEDA, url=scjn.BASE_URL)
        sesion.post.return_value = Mock(text=_pagina_resultados(""), url=scjn.BASE_URL)

        escritos = scjn.descarga_ordenamiento(sesion, "Ley Inexistente", self.outdir, espera=0)

        self.assertEqual(escritos, [])
        self.assertFalse(self.outdir.exists())

    def test_escribe_una_version_por_fila_con_cabecera_de_procedencia_oldest_first(self):
        sesion = Mock()
        sesion.get.return_value = Mock(text=PAGINA_BUSQUEDA, url=scjn.BASE_URL)
        sesion.post.return_value = Mock(
            text=_pagina_resultados(
                _candidato_html(
                    "LEY DE AMNISTIA", "FEDERAL", "VIGENTE",
                    "wfOrdenamientoDetalle.aspx?q=xyz",
                )
            ),
            url=scjn.BASE_URL + "resultados",
        )

        docx_reciente = _docx_bytes(["Texto reciente."])
        docx_original = _docx_bytes(["TEXTO ORIGINAL."])

        def get_side_effect(url, headers=None, timeout=None):
            if "wfOrdenamientoDetalle" in url:
                return Mock(text=f"<html><body>{TABLA_REFORMAS}</body></html>", url=url)
            if url.endswith("descarga1.docx"):
                return Mock(content=docx_reciente, headers={"content-type": "x"})
            if url.endswith("descarga2.docx"):
                return Mock(content=docx_original, headers={"content-type": "x"})
            raise AssertionError(f"unexpected GET {url}")

        sesion.get.side_effect = lambda url, headers=None, timeout=None: (
            Mock(text=PAGINA_BUSQUEDA, url=scjn.BASE_URL)
            if url == scjn.BASE_URL
            else get_side_effect(url, headers, timeout)
        )

        escritos = scjn.descarga_ordenamiento(sesion, "Ley de Amnistia", self.outdir, espera=0)

        self.assertEqual(len(escritos), 2)
        # oldest first
        self.assertEqual(escritos[0].name, "22-01-1994.md")
        self.assertEqual(escritos[1].name, "14-06-2024.md")

        original = escritos[0].read_text(encoding="utf-8")
        self.assertIn("fuente: scjn", original)
        self.assertIn("nombre_buscado: Ley de Amnistia", original)
        self.assertIn("ordenamiento: LEY DE AMNISTIA", original)
        self.assertIn("fecha_publicacion: 22-01-1994", original)
        self.assertIn("categoria: LEY", original)
        self.assertIn("**TEXTO ORIGINAL.**", original)

    def test_no_vuelve_a_descargar_una_fila_ya_escrita_en_disco(self):
        self.outdir.mkdir(parents=True)
        (self.outdir / "22-01-1994.md").write_text("ya existe", encoding="utf-8")
        (self.outdir / "14-06-2024.md").write_text("ya existe", encoding="utf-8")

        sesion = Mock()
        sesion.get.side_effect = lambda url, headers=None, timeout=None: (
            Mock(text=PAGINA_BUSQUEDA, url=scjn.BASE_URL)
            if url == scjn.BASE_URL
            else Mock(text=f"<html><body>{TABLA_REFORMAS}</body></html>", url=url)
        )
        sesion.post.return_value = Mock(
            text=_pagina_resultados(
                _candidato_html(
                    "LEY DE AMNISTIA", "FEDERAL", "VIGENTE",
                    "wfOrdenamientoDetalle.aspx?q=xyz",
                )
            ),
            url=scjn.BASE_URL + "resultados",
        )

        escritos = scjn.descarga_ordenamiento(sesion, "Ley de Amnistia", self.outdir, espera=0)

        self.assertEqual(len(escritos), 2)
        # No .docx download was attempted for either row.
        descargas = [c for c in sesion.get.call_args_list if "descarga" in c.args[0]]
        self.assertEqual(descargas, [])
        self.assertEqual((self.outdir / "22-01-1994.md").read_text(encoding="utf-8"), "ya existe")

    def test_agrega_sufijo_cuando_dos_filas_comparten_fecha_de_publicacion(self):
        sesion = Mock()
        sesion.get.return_value = Mock(text=PAGINA_BUSQUEDA, url=scjn.BASE_URL)
        sesion.post.return_value = Mock(
            text=_pagina_resultados(
                _candidato_html(
                    "LEY DE AMNISTIA", "FEDERAL", "VIGENTE",
                    "wfOrdenamientoDetalle.aspx?q=xyz",
                )
            ),
            url=scjn.BASE_URL + "resultados",
        )

        docx_a = _docx_bytes(["Version A."])
        docx_b = _docx_bytes(["Version B."])

        def get_side_effect(url, headers=None, timeout=None):
            if "wfOrdenamientoDetalle" in url:
                return Mock(
                    text=f"<html><body>{TABLA_REFORMAS_FECHAS_DUPLICADAS}</body></html>", url=url
                )
            if url.endswith("descarga1.docx"):
                return Mock(content=docx_a, headers={"content-type": "x"})
            if url.endswith("descarga2.docx"):
                return Mock(content=docx_b, headers={"content-type": "x"})
            raise AssertionError(f"unexpected GET {url}")

        sesion.get.side_effect = lambda url, headers=None, timeout=None: (
            Mock(text=PAGINA_BUSQUEDA, url=scjn.BASE_URL)
            if url == scjn.BASE_URL
            else get_side_effect(url, headers, timeout)
        )

        escritos = scjn.descarga_ordenamiento(sesion, "Ley de Amnistia", self.outdir, espera=0)

        # Two real, distinct reforms on the same date must not collide into one file.
        self.assertEqual(sorted(p.name for p in escritos), ["14-06-2024-2.md", "14-06-2024.md"])
        primera = (self.outdir / "14-06-2024.md").read_text(encoding="utf-8")
        segunda = (self.outdir / "14-06-2024-2.md").read_text(encoding="utf-8")
        self.assertIn("Version A.", primera)
        self.assertIn("Version B.", segunda)


class TestCabecera(unittest.TestCase):
    def test_incluye_el_nombre_buscado_junto_con_el_ordenamiento_elegido(self):
        candidato = scjn.Candidato(
            titulo="LEY DE AMNISTIA", url="u", ambito="FEDERAL", vigencia="VIGENTE"
        )
        fila = scjn.FilaReforma(
            fecha_publicacion="22-01-1994", fecha_expedicion=None, categoria=None,
            url_docx="d",
        )

        cabecera = scjn._cabecera(candidato, fila, "Ley de Amnistia")

        self.assertIn("nombre_buscado: Ley de Amnistia", cabecera)
        self.assertIn("ordenamiento: LEY DE AMNISTIA", cabecera)


class TestLeeCabecera(unittest.TestCase):
    def test_lee_los_campos_de_la_cabecera_de_procedencia(self):
        with tempfile.TemporaryDirectory() as tmp:
            archivo = Path(tmp) / "22-01-1994.md"
            archivo.write_text(
                "---\n"
                "fuente: scjn\n"
                "ordenamiento: LEY DE AMNISTIA\n"
                "fecha_publicacion: 22-01-1994\n"
                "fecha_expedicion: 21-01-1994\n"
                "categoria: LEY\n"
                "---\n\n"
                "**TEXTO ORIGINAL.**\n",
                encoding="utf-8",
            )

            campos = scjn.lee_cabecera(archivo)

            self.assertEqual(campos["fuente"], "scjn")
            self.assertEqual(campos["ordenamiento"], "LEY DE AMNISTIA")
            self.assertEqual(campos["fecha_publicacion"], "22-01-1994")
            self.assertEqual(campos["fecha_expedicion"], "21-01-1994")
            self.assertEqual(campos["categoria"], "LEY")

    def test_no_lee_mas_alla_del_cierre_de_la_cabecera(self):
        with tempfile.TemporaryDirectory() as tmp:
            archivo = Path(tmp) / "x.md"
            archivo.write_text(
                "---\nfecha_publicacion: 22-01-1994\n---\n\nordenamiento: no es esto\n",
                encoding="utf-8",
            )

            campos = scjn.lee_cabecera(archivo)

            self.assertEqual(campos, {"fecha_publicacion": "22-01-1994"})


class TestVersionesDeDirectorio(unittest.TestCase):
    def test_regresa_las_versiones_ordenadas_por_fecha_oldest_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            outdir = Path(tmp)
            for nombre, fecha in [
                ("14-06-2024.md", "14-06-2024"),
                ("22-01-1994.md", "22-01-1994"),
            ]:
                (outdir / nombre).write_text(
                    f"---\nfecha_publicacion: {fecha}\n---\n\ntexto\n", encoding="utf-8"
                )

            versiones = scjn.versiones_de_directorio(outdir)

            self.assertEqual(
                [v.fecha_publicacion for v in versiones], ["22-01-1994", "14-06-2024"]
            )

    def test_desempata_fechas_repetidas_por_nombre_de_archivo(self):
        with tempfile.TemporaryDirectory() as tmp:
            outdir = Path(tmp)
            for nombre in ["14-06-2024-2.md", "14-06-2024.md"]:
                (outdir / nombre).write_text(
                    "---\nfecha_publicacion: 14-06-2024\n---\n\ntexto\n", encoding="utf-8"
                )

            versiones = scjn.versiones_de_directorio(outdir)

            self.assertEqual(
                [v.archivo.name for v in versiones], ["14-06-2024.md", "14-06-2024-2.md"]
            )


class TestTitleCandidatesPorFecha(unittest.TestCase):
    def test_agrupa_por_fecha_solo_los_codnota_cuyo_titulo_menciona_el_nombre(self):
        porf = {
            "22-01-1994": [
                {"codNota": 100, "titulo": "DECRETO que reforma la Ley Federal del Trabajo"},
                {"codNota": 999, "titulo": "DECRETO sobre otro asunto"},
            ],
        }

        agrupado = scjn.title_candidates_por_fecha(
            ["22-01-1994"], "Ley Federal del Trabajo", porf
        )

        self.assertEqual(agrupado, {"22-01-1994": [100]})

    def test_una_fecha_sin_registros_en_porf_regresa_lista_vacia(self):
        agrupado = scjn.title_candidates_por_fecha(["22-01-1994"], "Ley Federal del Trabajo", {})

        self.assertEqual(agrupado, {"22-01-1994": []})

    def test_no_repite_fechas_duplicadas_en_el_resultado(self):
        porf = {"14-06-2024": [{"codNota": 100, "titulo": "Ley de Amnistia"}]}

        agrupado = scjn.title_candidates_por_fecha(
            ["14-06-2024", "14-06-2024"], "Ley de Amnistia", porf
        )

        self.assertEqual(list(agrupado.keys()), ["14-06-2024"])


class TestEnlazaPorTitulo(unittest.TestCase):
    def _version(self, fecha: str, nombre: str = None) -> scjn.VersionInstrumento:
        return scjn.VersionInstrumento(fecha, Path(nombre or f"{fecha}.md"))

    def test_enlaza_cuando_hay_exactamente_un_candidato_esa_fecha(self):
        versiones = [self._version("22-01-1994"), self._version("14-06-2024")]
        candidatos_por_fecha = {"22-01-1994": [100], "14-06-2024": [200]}

        enlazadas = scjn.enlaza_por_titulo(versiones, candidatos_por_fecha)

        self.assertEqual([v.codNota for v in enlazadas], [100, 200])

    def test_deja_sin_enlazar_una_fecha_sin_candidato(self):
        versiones = [self._version("22-01-1994")]

        enlazadas = scjn.enlaza_por_titulo(versiones, {})

        self.assertIsNone(enlazadas[0].codNota)

    def test_deja_sin_enlazar_una_fecha_con_varios_candidatos_ambiguos(self):
        # Sin historial que desempate, el titulo solo no puede elegir entre
        # varios candidatos del mismo dia — issue #127's content diff es lo
        # unico que puede resolver este caso.
        versiones = [self._version("22-01-1994")]
        candidatos_por_fecha = {"22-01-1994": [100, 200]}

        enlazadas = scjn.enlaza_por_titulo(versiones, candidatos_por_fecha)

        self.assertIsNone(enlazadas[0].codNota)

    def test_el_primer_snapshot_del_dia_reclama_el_unico_candidato_y_el_segundo_queda_sin_enlazar(
        self,
    ):
        # Dos snapshots de un mismo dia, pero el titulo solo revela un
        # candidato para esa fecha: solo el primero (oldest-first) lo
        # reclama, el segundo se queda sin enlazar en vez de reclamarlo
        # tambien.
        versiones = [
            self._version("14-06-2024", "14-06-2024.md"),
            self._version("14-06-2024", "14-06-2024-2.md"),
        ]
        candidatos_por_fecha = {"14-06-2024": [100]}

        enlazadas = scjn.enlaza_por_titulo(versiones, candidatos_por_fecha)

        self.assertEqual([v.codNota for v in enlazadas], [100, None])


class TestTitleLinkStatus(unittest.TestCase):
    def test_enlazado_cuando_hay_codnota(self):
        self.assertEqual(scjn.title_link_status(100, [100]), "linked")

    def test_ninguno_sin_candidatos(self):
        self.assertEqual(scjn.title_link_status(None, []), "none")

    def test_reclamado_cuando_el_unico_candidato_ya_fue_tomado(self):
        self.assertEqual(scjn.title_link_status(None, [100]), "claimed")

    def test_ambiguo_con_varios_candidatos(self):
        self.assertEqual(scjn.title_link_status(None, [100, 200]), "ambiguous")


class TestTitleMentionsName(unittest.TestCase):
    def test_reconoce_mencion_explicita_case_e_acento_insensible(self):
        self.assertTrue(
            scjn._title_mentions_name(
                "Ley Federal del Trabajo",
                "DECRETO por el que se reforma la ley federal del trabajo",
            )
        )

    def test_no_reconoce_una_ley_distinta(self):
        self.assertFalse(
            scjn._title_mentions_name(
                "Ley Federal del Trabajo",
                "DECRETO por el que se reforma el Codigo Fiscal de la Federacion",
            )
        )

    def test_no_se_deja_engañar_por_palabras_cortas_compartidas(self):
        # "Ley"/"del"/"de" son demasiado cortas para contar por si solas.
        self.assertFalse(
            scjn._title_mentions_name(
                "Ley Federal del Trabajo",
                "DECRETO por el que se reforma la Ley de Amparo",
            )
        )

    def test_exige_todas_las_palabras_significativas_del_nombre(self):
        self.assertFalse(
            scjn._title_mentions_name(
                "Ley Federal de los Derechos del Contribuyente",
                "DECRETO por el que se reforma la Ley Federal del Trabajo",
            )
        )

    def test_no_confirma_con_una_sola_palabra_significativa_aunque_aparezca(self):
        # "LEY de Amparo" solo deja "Amparo" tras el filtro de palabras
        # cortas — una sola palabra, por comun que sea en textos legales,
        # no basta como mencion explicita: cualquier decreto que la use de
        # paso convertiria esto en una busqueda de palabra clave, no en una
        # mencion del ordenamiento.
        self.assertFalse(
            scjn._title_mentions_name(
                "Ley de Amparo",
                "DECRETO por el que se reforma el Reglamento de la Ley de Amparo",
            )
        )


class TestAddedBlocksYOverlapScore(unittest.TestCase):
    def test_detecta_un_parrafo_nuevo_entre_dos_versiones(self):
        anterior = "Articulo 1.- Texto original.\n\nArticulo 2.- Otro texto."
        nuevo = "Articulo 1.- Texto original.\n\nArticulo 2.- Texto modificado por la reforma."

        agregados = scjn._added_blocks(anterior, nuevo)

        self.assertEqual(len(agregados), 1)
        self.assertIn("modificado", agregados[0])

    def test_no_marca_nada_agregado_cuando_las_versiones_son_iguales(self):
        texto = "Articulo 1.- Texto original.\n\nArticulo 2.- Otro texto."

        self.assertEqual(scjn._added_blocks(texto, texto), [])

    def test_score_es_uno_cuando_el_candidato_cubre_todo_lo_agregado(self):
        agregados = ["articulo 2 texto modificado por la reforma"]
        candidato = "decreto que reforma el articulo 2 texto modificado por la reforma"

        self.assertEqual(scjn._overlap_score(agregados, candidato), 1.0)

    def test_score_es_cero_sin_relacion_alguna(self):
        agregados = ["articulo 2 texto modificado por la reforma"]
        candidato = "decreto sobre un asunto completamente distinto"

        self.assertEqual(scjn._overlap_score(agregados, candidato), 0.0)

    def test_score_es_cero_sin_nada_agregado(self):
        self.assertEqual(scjn._overlap_score([], "cualquier texto"), 0.0)

    def test_distingue_candidatos_que_solo_difieren_en_un_numero_corto(self):
        # Una reforma que solo cambia una tasa/monto/plazo corto (menos de 4
        # digitos) no debe volverse invisible para el score.
        agregados = ["se establece una tasa de 20 por ciento"]
        candidato_correcto = "decreto que fija la tasa en 20 por ciento"
        candidato_equivocado = "decreto que fija la tasa en 15 por ciento"

        score_correcto = scjn._overlap_score(agregados, candidato_correcto)
        score_equivocado = scjn._overlap_score(agregados, candidato_equivocado)

        self.assertGreater(score_correcto, score_equivocado)


class TestConfirmByContentDiff(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.outdir = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _snapshot(self, fecha: str, cuerpo: str, sufijo: str = "") -> scjn.VersionInstrumento:
        archivo = self.outdir / f"{fecha}{sufijo}.md"
        archivo.write_text(f"---\nfecha_publicacion: {fecha}\n---\n\n{cuerpo}", encoding="utf-8")
        return scjn.VersionInstrumento(fecha, archivo)

    def test_la_primera_version_no_tiene_confirmacion_por_no_tener_version_previa(self):
        versiones = [self._snapshot("22-01-1994", "Articulo 1.- Texto original.")]

        resultados = scjn.confirm_by_content_diff(versiones, {}, {})

        self.assertEqual(len(resultados), 1)
        self.assertIsNone(resultados[0].confirmed_codNota)
        self.assertIsNone(resultados[0].score)

    def test_confirma_el_candidato_cuyo_texto_cubre_el_cambio_observado(self):
        versiones = [
            self._snapshot("22-01-1994", "Articulo 1.- Texto original.\n\nArticulo 2.- Antes."),
            self._snapshot(
                "14-06-2024",
                "Articulo 1.- Texto original.\n\nArticulo 2.- Reformado por decreto especial.",
            ),
        ]
        candidatos_por_fecha = {"14-06-2024": [100, 200]}
        markdown_por_codNota = {
            100: "DECRETO sin relacion con nada de esto.",
            200: "DECRETO por el que se reforma el articulo 2 para quedar reformado por decreto especial.",
        }

        resultados = scjn.confirm_by_content_diff(
            versiones, candidatos_por_fecha, markdown_por_codNota
        )

        self.assertEqual(resultados[1].confirmed_codNota, 200)
        self.assertGreaterEqual(resultados[1].score, scjn.UMBRAL_CONFIRMACION_DIFF)

    def test_no_confirma_por_debajo_del_umbral_pero_reporta_el_mejor_score(self):
        versiones = [
            self._snapshot("22-01-1994", "Articulo 1.- Texto original."),
            self._snapshot("14-06-2024", "Articulo 1.- Texto con cambios sustanciales agregados."),
        ]
        candidatos_por_fecha = {"14-06-2024": [100]}
        markdown_por_codNota = {100: "DECRETO que apenas menciona cambios de pasada."}

        resultados = scjn.confirm_by_content_diff(
            versiones, candidatos_por_fecha, markdown_por_codNota
        )

        self.assertIsNone(resultados[1].confirmed_codNota)
        self.assertIsNotNone(resultados[1].score)
        self.assertLess(resultados[1].score, scjn.UMBRAL_CONFIRMACION_DIFF)

    def test_score_none_cuando_ningun_candidato_tiene_texto_disponible(self):
        # Issue #127: sin texto disponible, el enlace se queda tal como lo
        # dejaron #124/#126 — ni bloqueado ni degradado.
        versiones = [
            self._snapshot("22-01-1994", "Articulo 1.- Texto original."),
            self._snapshot("14-06-2024", "Articulo 1.- Texto modificado."),
        ]
        candidatos_por_fecha = {"14-06-2024": [100]}

        resultados = scjn.confirm_by_content_diff(versiones, candidatos_por_fecha, {})

        self.assertIsNone(resultados[1].confirmed_codNota)
        self.assertIsNone(resultados[1].score)

    def test_lista_vacia_de_versiones_no_falla(self):
        self.assertEqual(scjn.confirm_by_content_diff([], {}, {}), [])

    def test_no_confirma_el_mismo_codnota_para_dos_reformas_del_mismo_dia(self):
        # Confirmado en vivo sobre ccf/27-12-1983: dos decretos reales y
        # distintos ese dia, con texto que se superpone lo bastante como
        # para que el candidato del primero tambien anote el mejor score
        # del segundo — sin exclusividad, ambos "roban" el mismo codNota y
        # el segundo pierde su propio enlace, ya correcto, de #124/#126.
        v1 = "Decreto especial primero segundo aplicado aqui mismo."
        v2 = v1 + "\n\nOtro parrafo decreto especial primero segundo mencionado de nuevo hoy."
        versiones = [
            self._snapshot("01-01-1980", "Texto original sin relacion alguna."),
            self._snapshot("27-12-1983", v1, sufijo=""),
            self._snapshot("27-12-1983", v2, sufijo="-2"),
        ]
        candidatos_por_fecha = {"27-12-1983": [1001, 1002]}
        markdown_por_codNota = {
            1001: "DECRETO que aplica un cambio especial primero segundo parrafo mencionado.",
            1002: "DECRETO que aplica un cambio especial primero segundo aqui mismo "
            "parrafo mencionado nuevo hoy ademas otras cosas.",
        }

        resultados = scjn.confirm_by_content_diff(
            versiones, candidatos_por_fecha, markdown_por_codNota
        )

        primera, segunda = resultados[1], resultados[2]
        self.assertEqual(primera.confirmed_codNota, 1002)
        self.assertEqual(segunda.confirmed_codNota, 1001)


class TestDownloadScjnLeyesCorpus(unittest.TestCase):
    @patch("nota2md.scjn.requests.get")
    def test_lanza_key_error_cuando_el_release_no_tiene_leyes_tgz(self, mock_get):
        mock_get.return_value = Mock(json=lambda: {"assets": []}, raise_for_status=Mock())

        with self.assertRaises(KeyError):
            scjn.download_scjn_leyes_corpus()

    @patch("nota2md.scjn.requests.get")
    def test_une_indice_con_el_markdown_de_cada_snapshot(self, mock_get):
        indice = [
            {"archivo": "22-01-1994.md", "codNota": 100, "ratio_similitud": 0.9,
             "sospechoso": False, "title_candidates": [100], "title_link_status": "linked",
             "content_diff_confirmed_codNota": None, "content_diff_score": None},
        ]
        contenido = _hacer_tgz({
            "cpeum/indice.json": json.dumps(indice),
            "cpeum/22-01-1994.md": "**TEXTO ORIGINAL.**",
        })
        respuestas = [
            Mock(json=lambda: {"assets": [
                {"name": "leyes.tgz", "browser_download_url": "https://x/leyes.tgz"}
            ]}, raise_for_status=Mock()),
            Mock(content=contenido, raise_for_status=Mock()),
        ]
        mock_get.side_effect = respuestas

        resultado = scjn.download_scjn_leyes_corpus()

        self.assertEqual(len(resultado), 1)
        self.assertEqual(resultado[0]["slug"], "cpeum")
        self.assertEqual(len(resultado[0]["snapshots"]), 1)
        snap = resultado[0]["snapshots"][0]
        self.assertEqual(snap["codNota"], 100)
        self.assertEqual(snap["title_link_status"], "linked")
        self.assertEqual(snap["markdown"], "**TEXTO ORIGINAL.**")

    @patch("nota2md.scjn.requests.get")
    def test_instrumento_sin_indice_json_regresa_snapshots_sin_enlace_en_vez_de_omitirse(
        self, mock_get
    ):
        # Fase 2 (issue #105) pendiente para este instrumento: hay
        # snapshots pero enlaza_scjn_legislacion.py no ha corrido para el.
        contenido = _hacer_tgz({"lfea/01-01-2012.md": "**TEXTO ORIGINAL.**"})
        respuestas = [
            Mock(json=lambda: {"assets": [
                {"name": "leyes.tgz", "browser_download_url": "https://x/leyes.tgz"}
            ]}, raise_for_status=Mock()),
            Mock(content=contenido, raise_for_status=Mock()),
        ]
        mock_get.side_effect = respuestas

        resultado = scjn.download_scjn_leyes_corpus()

        self.assertEqual(len(resultado), 1)
        self.assertEqual(resultado[0]["slug"], "lfea")
        snap = resultado[0]["snapshots"][0]
        self.assertEqual(snap["archivo"], "01-01-2012.md")
        self.assertIsNone(snap["codNota"])
        self.assertEqual(snap["markdown"], "**TEXTO ORIGINAL.**")


if __name__ == "__main__":
    unittest.main()

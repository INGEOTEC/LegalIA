import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import docx
import requests

from nota2md import scjn

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


if __name__ == "__main__":
    unittest.main()

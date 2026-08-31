"""Unit tests for `nota2md.scjn_api` against recorded JSON shapes — no
network. What they pin down is the handful of behaviours issue #173
measured live and issue #174 asks for: the `<em>` highlighting the search
puts in every title, paging until `tamanio` is exhausted, an in-body
`codigo` that is not 200 being an error rather than an empty result, and a
WAF challenge page raising something other than a JSONDecodeError.
"""

import json
import unittest
from pathlib import Path

from nota2md.scjn_api import ScjnApi, ScjnApiError, ScjnApiWafError

FIXTURES = Path(__file__).parent / "fixtures" / "scjn_api"


def fixture(nombre: str) -> dict:
    return json.loads((FIXTURES / nombre).read_text(encoding="utf-8"))


class RespuestaFalsa:
    def __init__(self, cuerpo, status_code=200):
        self._cuerpo = cuerpo
        self.status_code = status_code
        self.url = "http://fake/"
        self.content = cuerpo if isinstance(cuerpo, bytes) else b"{}"

    def json(self):
        if isinstance(self._cuerpo, bytes):
            raise ValueError("no JSON")
        return self._cuerpo


class SesionFalsa:
    """Answers each request with the next canned response, recording the
    params it was called with so paging can be asserted."""

    def __init__(self, respuestas):
        self.respuestas = list(respuestas)
        self.llamadas = []
        self.headers = {}

    def request(self, metodo, url, **kwargs):
        self.llamadas.append((metodo, url, kwargs))
        return self.respuestas.pop(0)


def api(respuestas) -> tuple[ScjnApi, SesionFalsa]:
    sesion = SesionFalsa(respuestas)
    return ScjnApi(espera=0, reintentos=0, session=sesion), sesion


class TestBusqueda(unittest.TestCase):
    def test_limpia_el_resaltado_em(self):
        cliente, _ = api([RespuestaFalsa(fixture("busqueda_lfca.json"))])
        (hit,) = cliente.search_ordenamiento("LEY FEDERAL DE CINE Y EL AUDIOVISUAL")
        self.assertEqual(hit.ordenamiento, "LEY FEDERAL DE CINE Y EL AUDIOVISUAL")
        self.assertEqual(hit.idOrdenamiento, "188805")
        self.assertEqual(hit.ambito, "FEDERAL")
        self.assertEqual(hit.fechaPublicado, "22-05-2026")

    def test_manda_los_filtros_vacios(self):
        # Issue #173: omitirlos hace que la API conteste HTTP 500.
        cliente, sesion = api([RespuestaFalsa(fixture("busqueda_lfca.json"))])
        cliente.search_ordenamiento("X")
        cuerpo = sesion.llamadas[0][2]["json"]
        for llave in ("ambitoF", "vigenciaF", "materiaF", "fechaPublicacionInicio"):
            self.assertEqual(cuerpo[llave], "")
        self.assertEqual(cuerpo["consultaArticulos"], 0)

    def test_sin_resultados_no_es_error(self):
        cliente, _ = api([RespuestaFalsa({"tamanio": 0, "codigo": 200, "resultados": []})])
        self.assertEqual(cliente.search_ordenamiento("nada"), [])


class TestReformas(unittest.TestCase):
    def test_pagina_hasta_agotar_tamanio(self):
        pagina1 = {"tamanio": 3, "codigo": 200, "resultados": fixture("reformas.json")["resultados"][:2]}
        pagina2 = {"tamanio": 3, "codigo": 200, "resultados": fixture("reformas.json")["resultados"][2:]}
        cliente, sesion = api([RespuestaFalsa(pagina1), RespuestaFalsa(pagina2)])
        filas = cliente.reformas_of_ordenamiento(188805)
        self.assertEqual(len(filas), 3)
        self.assertEqual([l[2]["params"]["numeroPagina"] for l in sesion.llamadas], [1, 2])

    def test_normaliza_fechas_y_seccion(self):
        cliente, _ = api([RespuestaFalsa(fixture("reformas.json"))])
        primera = cliente.reformas_of_ordenamiento(188805)[0]
        self.assertEqual(primera.fecha_publicacion, "22-05-2026")
        self.assertEqual(primera.fecha_expedicion, "02-05-2026")
        self.assertEqual(primera.categoria, "LEY")
        # el trailing space de la API no llega al front-matter
        self.assertEqual(primera.seccionPublicacion, "133/2026 EDICION VESPERTINA")


class TestArticulos(unittest.TestCase):
    def test_devuelve_referencia_orden_y_contenido(self):
        cliente, _ = api([RespuestaFalsa(fixture("articulos.json"))])
        arts = cliente.articulos_of_reforma(188805, 1)
        self.assertEqual([a.referencia for a in arts], ["ENCABEZADO", "TÍTULO PRIMERO", "ARTÍCULO 1"])
        self.assertEqual([a.orden for a in arts], [1, 2, 3])
        self.assertTrue(arts[0].contenido.startswith("LEY FEDERAL DE CINE"))

    def test_un_500_por_reforma_es_error(self):
        # `lfd` reforma 8 contesta 500 siempre (issue #173): el llamador tiene
        # que poder saltarse esa reforma, no recibir una lista vacía.
        cliente, _ = api([RespuestaFalsa({}, status_code=500)])
        with self.assertRaises(ScjnApiError):
            cliente.articulos_of_reforma(693, 8)


class TestErrores(unittest.TestCase):
    def test_codigo_distinto_de_200_es_error(self):
        cliente, _ = api([RespuestaFalsa({"codigo": 404, "mensaje": "Sin resultados"})])
        with self.assertRaises(ScjnApiError):
            cliente.search_ordenamiento("X")

    def test_reto_del_waf_no_es_jsondecodeerror(self):
        cliente, _ = api([RespuestaFalsa(b"<html>Incapsula incident</html>")])
        with self.assertRaises(ScjnApiWafError):
            cliente.search_ordenamiento("X")

    def test_reintenta_y_luego_falla(self):
        sesion = SesionFalsa([RespuestaFalsa({}, 503), RespuestaFalsa({}, 503)])
        cliente = ScjnApi(espera=0, reintentos=1, session=sesion)
        with self.assertRaises(ScjnApiError):
            cliente.reformas_of_ordenamiento(1)
        self.assertEqual(len(sesion.llamadas), 2)


if __name__ == "__main__":
    unittest.main()


class TestEscritor(unittest.TestCase):
    """El formato en disco no cambia (issue #175): la verificación real es el
    diff contra el corpus que el crawler viejo dejó, y estas pruebas fijan las
    piezas de ese formato que no dependen de la red."""

    def setUp(self):
        from nota2md.scjn_api import Articulo, Ordenamiento, Reforma

        self.ordenamiento = Ordenamiento(
            idOrdenamiento="410",
            ordenamiento="LEY FEDERAL DEL TRABAJO",
            materia="SEGURIDAD SOCIAL, LABORAL",
            ratio=1.0,
            sospechoso=False,
        )
        self.reforma = Reforma(
            reformaId=61,
            fecha_publicacion="14-05-2026",
            fecha_expedicion="13-05-2026",
            categoria="DECRETO",
            seccionPublicacion="124/2026",
        )
        self.Articulo = Articulo

    def test_cabecera_conserva_orden_y_agrega_al_final(self):
        from nota2md.scjn_api import cabecera

        lineas = cabecera(self.ordenamiento, self.reforma, "LEY FEDERAL DEL TRABAJO").split("\n")
        self.assertEqual(
            lineas,
            [
                "---",
                "fuente: scjn",
                "ordenamiento: LEY FEDERAL DEL TRABAJO",
                "fecha_publicacion: 14-05-2026",
                "fecha_expedicion: 13-05-2026",
                "categoria: DECRETO",
                "ratio_similitud: 1.000",
                "sospechoso: false",
                "seccion_publicacion: 124/2026",
                "materia: SEGURIDAD SOCIAL, LABORAL",
                "id_ordenamiento: 410",
                "reforma_id: 61",
                "---",
            ],
        )

    def test_lee_cabecera_lee_las_llaves_nuevas(self):
        # `scjn.lee_cabecera` solo reconoce `^[a-z_]+:`, de ahí el snake_case.
        from tempfile import TemporaryDirectory

        from nota2md.scjn import lee_cabecera
        from nota2md.scjn_api import snapshot

        with TemporaryDirectory() as tmp:
            archivo = Path(tmp) / "14-05-2026.md"
            archivo.write_text(
                # un nombre que de verdad difiere: `ratio_similitud` normaliza
                # acentos y mayúsculas, así que "LEY Federal del Trabajo" da 1.0
                snapshot(self.ordenamiento, self.reforma, [], "LEY Federal del Trabajo de 1970"),
                encoding="utf-8",
            )
            campos = lee_cabecera(archivo)
        self.assertEqual(campos["fuente"], "scjn")
        self.assertEqual(campos["nombre_buscado"], "LEY Federal del Trabajo de 1970")
        self.assertEqual(campos["id_ordenamiento"], "410")
        self.assertEqual(campos["reforma_id"], "61")
        self.assertEqual(campos["seccion_publicacion"], "124/2026")

    def test_nombre_buscado_solo_cuando_el_titulo_difiere(self):
        from nota2md.scjn_api import cabecera

        igual = cabecera(self.ordenamiento, self.reforma, "LEY FEDERAL DEL TRABAJO")
        self.assertNotIn("nombre_buscado", igual)

    def test_mismo_markdown_que_el_camino_docx(self):
        from nota2md.scjn_api import articulos_a_markdown

        arts = [
            self.Articulo(1, 1, "ENCABEZADO", "LEY FEDERAL DEL TRABAJO\r\n\r\nTEXTO ORIGINAL."),
            self.Articulo(
                2, 2, "ARTÍCULO 1", "Artículo 1o. La presente Ley es de observancia general."
            ),
        ]
        self.assertEqual(
            articulos_a_markdown(arts),
            "**LEY FEDERAL DEL TRABAJO**\n\n**TEXTO ORIGINAL.**\n\n"
            # el espacio queda dentro de las negritas porque el patrón de
            # `_formatea_parrafo` lo captura — se conserva tal cual
            "**Artículo 1o. **La presente Ley es de observancia general.\n",
        )

    def test_quita_el_html_inline_del_encabezado(self):
        # El ENCABEZADO de algunas leyes abre con el markup propio de la SCJN;
        # sin quitarlo, la primera línea de cada snapshot difiere del .docx.
        from nota2md.scjn_api import articulos_a_markdown

        arts = [
            self.Articulo(
                1,
                1,
                "ENCABEZADO",
                "<p style='color:#7D2007;'></p> <br>LEY FEDERAL DEL TRABAJO",
            )
        ]
        self.assertEqual(articulos_a_markdown(arts), "**LEY FEDERAL DEL TRABAJO**\n")

    def test_quita_las_notas_editoriales_igual_que_el_docx(self):
        # Issue #173, pregunta 3: la API trae los marcadores `N. DE E.` con la
        # misma grafía que el .docx, así que `quita_notas_editoriales` se reusa.
        from nota2md.scjn_api import articulos_a_markdown

        arts = [
            self.Articulo(1, 1, "ARTÍCULO 1", "[N. DE E. EN RELACION CON LA ENTRADA EN VIGOR.]")
        ]
        self.assertEqual(articulos_a_markdown(arts), "\n")

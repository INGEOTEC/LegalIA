"""Unit tests for `scjn.api` against recorded JSON shapes — no
network. What they pin down is the handful of behaviours issue #173
measured live and issue #174 asks for: the `<em>` highlighting the search
puts in every title, paging until `tamanio` is exhausted, an in-body
`codigo` that is not 200 being an error rather than an empty result, and a
WAF challenge page raising something other than a JSONDecodeError.
"""

import json
import unittest
from pathlib import Path

from scjn.api import ScjnApi, ScjnApiError, ScjnApiWafError

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
    def test_pagina_mientras_la_pagina_venga_completa(self):
        # Una pagina completa significa que puede haber otra; una corta es la
        # ultima (ver TAMANIO_PAGINA_REFORMAS y el bug de paginacion de #178).
        from scjn.api import TAMANIO_PAGINA_REFORMAS

        fila = fixture("reformas.json")["resultados"][0]
        total = TAMANIO_PAGINA_REFORMAS + 3
        completa = {
            "codigo": 200, "tamanio": total, "resultados": [fila] * TAMANIO_PAGINA_REFORMAS,
        }
        corta = {"codigo": 200, "tamanio": total, "resultados": [fila] * 3}
        cliente, sesion = api([RespuestaFalsa(completa), RespuestaFalsa(corta)])
        filas = cliente.reformas_of_ordenamiento(188805)
        self.assertEqual(len(filas), TAMANIO_PAGINA_REFORMAS + 3)
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
        from scjn.api import Articulo, Ordenamiento, Reforma

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
        from scjn.api import cabecera

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

        from scjn.header import lee_cabecera
        from scjn.api import snapshot

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
        from scjn.api import cabecera

        igual = cabecera(self.ordenamiento, self.reforma, "LEY FEDERAL DEL TRABAJO")
        self.assertNotIn("nombre_buscado", igual)

    def test_mismo_markdown_que_el_camino_docx(self):
        from scjn.api import articulos_a_markdown

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
        from scjn.api import articulos_a_markdown

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
        from scjn.api import articulos_a_markdown

        arts = [
            self.Articulo(1, 1, "ARTÍCULO 1", "[N. DE E. EN RELACION CON LA ENTRADA EN VIGOR.]")
        ]
        self.assertEqual(articulos_a_markdown(arts), "\n")


class TestFormateaParrafo(unittest.TestCase):
    """La clasificación por párrafo que vivía en `scjn.docx_a_markdown` hasta
    el issue #179 — las mismas afirmaciones que fijaban el camino .docx, ahora
    contra su único llamador."""

    def test_clasifica_titular_margen_articulo_y_transitorios(self):
        from scjn.api import Articulo, articulos_a_markdown

        arts = [
            Articulo(1, 1, "ENCABEZADO", "TEXTO ORIGINAL."),
            Articulo(2, 2, "ENCABEZADO", "Al margen un sello con el Escudo Nacional."),
            Articulo(3, 3, "ARTÍCULO 1", "Artículo 1o.- Se decreta la disposición de prueba."),
            Articulo(4, 4, "TRANSITORIOS", "TRANSITORIOS"),
            Articulo(5, 5, "TRANSITORIOS", "PRIMERO.- Entra en vigor de inmediato."),
        ]

        markdown = articulos_a_markdown(arts)

        self.assertIn("**TEXTO ORIGINAL.**", markdown)
        self.assertIn("## Al margen un sello con el Escudo Nacional.", markdown)
        self.assertIn("**Artículo 1o.-** Se decreta la disposición de prueba.", markdown)
        self.assertIn("## Transitorios", markdown)
        self.assertIn("**PRIMERO.-** Entra en vigor de inmediato.", markdown)

    def test_omite_los_parrafos_vacios_usados_como_separadores(self):
        from scjn.api import Articulo, articulos_a_markdown

        arts = [Articulo(1, 1, "ARTÍCULO 1", "Primer párrafo.\n\n\nSegundo párrafo.")]

        self.assertEqual(
            articulos_a_markdown(arts), "Primer párrafo.\n\nSegundo párrafo.\n"
        )

    def test_omite_por_completo_un_parrafo_que_es_solo_nota_editorial(self):
        from scjn.api import Articulo, articulos_a_markdown

        arts = [
            Articulo(1, 1, "ARTÍCULO 1", "Artículo 1o.- Se decreta la disposición de prueba."),
            Articulo(2, 2, "ARTÍCULO 1", '[N. DE E. TRANSITORIO DEL "DECRETO POR EL QUE SE '
                                         'REFORMA".]'),
            Articulo(3, 3, "ARTÍCULO 2", "Artículo 2o.- Otra disposición."),
        ]

        markdown = articulos_a_markdown(arts)

        self.assertNotIn("N. DE E.", markdown)
        self.assertIn("**Artículo 1o.-** Se decreta la disposición de prueba.", markdown)
        self.assertIn("**Artículo 2o.-** Otra disposición.", markdown)


class TestSeleccion(unittest.TestCase):
    """Los 5 instrumentos del issue #115 en los que el buscador viejo trajo
    otro documento, reducidos a los candidatos que la API devuelve hoy para
    cada uno (las corridas en vivo están documentadas en #176)."""

    def candidato(self, titulo, **kw):
        from scjn.api import Ordenamiento

        campos = {
            "idOrdenamiento": kw.pop("id", "1"),
            "ordenamiento": titulo,
            "ambito": kw.pop("ambito", "FEDERAL"),
            "vigencia": kw.pop("vigencia", "VIGENTE"),
            "categoriaOrdenamiento": kw.pop("categoria", "LEY"),
            "iweight": kw.pop("iweight", 10),
        }
        return Ordenamiento(**campos, **kw)

    def elige(self, candidatos, nombre):
        from scjn.api import elige_ordenamiento

        return elige_ordenamiento(candidatos, nombre)

    def test_lisr_descarta_el_acuerdo_del_pleno(self):
        elegido = self.elige(
            [
                self.candidato(
                    "ACUERDO GENERAL NUMERO 11/2015 DEL PLENO DE LA SUPREMA CORTE DE JUSTICIA",
                    categoria="ACUERDO (S)",
                    iweight=200,
                ),
                self.candidato("LEY DEL IMPUESTO SOBRE LA RENTA", id="96834"),
            ],
            "LEY del Impuesto sobre la Renta",
        )
        self.assertEqual(elegido.idOrdenamiento, "96834")

    def test_lopgjdf_descarta_el_reglamento_de_la_misma_ley(self):
        # 0.894 de similitud y el mismo iweight que la ley: sin la exclusión
        # por grupo es un empate que se puede perder.
        elegido = self.elige(
            [
                self.candidato(
                    "REGLAMENTO DE LA LEY ORGANICA DE LA PROCURADURIA GENERAL DE JUSTICIA DEL DISTRITO FEDERAL",
                    categoria="REGLAMENTO",
                    ambito="ESTATAL",
                    vigencia="NO VIGENTE",
                    iweight=139,
                ),
                self.candidato(
                    "LEY ORGANICA DE LA PROCURADURIA GENERAL DE JUSTICIA DEL DISTRITO FEDERAL",
                    id="2588",
                    ambito="ESTATAL",
                    vigencia="ABROGADO (A)",
                    iweight=139,
                ),
            ],
            "LEY Orgánica de la Procuraduría General de Justicia del Distrito Federal",
        )
        self.assertEqual(elegido.idOrdenamiento, "2588")

    def test_la_categoria_atrapa_un_reglamento_que_el_titulo_no_delata(self):
        # Señal nueva: `categoriaOrdenamiento` es la clasificación de la
        # propia SCJN, y no depende de cómo empiece el título.
        elegido = self.elige(
            [
                self.candidato(
                    "DISPOSICIONES REGLAMENTARIAS DE LA LEY DE AGUAS NACIONALES",
                    categoria="REGLAMENTO",
                    iweight=500,
                ),
                self.candidato("LEY DE AGUAS NACIONALES", id="7"),
            ],
            "LEY de Aguas Nacionales",
        )
        self.assertEqual(elegido.idOrdenamiento, "7")

    def test_ccf_no_pierde_contra_un_iweight_mucho_mayor(self):
        # `iweight` solo desempata; nunca gana sobre la similitud de título.
        elegido = self.elige(
            [
                self.candidato(
                    "CODIGO CIVIL DEL DISTRITO FEDERAL Y TERRITORIO DE LA BAJA CALIFORNIA",
                    categoria="CODIGO",
                    vigencia="DEROGADO (A)",
                    iweight=137,
                ),
                self.candidato(
                    "CODIGO CIVIL FEDERAL -ANTES CODIGO CIVIL PARA EL DISTRITO FEDERAL-",
                    id="641",
                    categoria="CODIGO",
                    iweight=37,
                ),
            ],
            "CÓDIGO Civil Federal",
        )
        self.assertEqual(elegido.idOrdenamiento, "641")
        self.assertFalse(elegido.sospechoso)

    def test_iweight_desempata_una_similitud_identica(self):
        elegido = self.elige(
            [
                self.candidato("LEY DE AGUAS NACIONALES", id="bajo", iweight=1),
                self.candidato("LEY DE AGUAS NACIONALES", id="alto", iweight=99),
            ],
            "LEY de Aguas Nacionales",
        )
        self.assertEqual(elegido.idOrdenamiento, "alto")

    def test_lopgjdf_estatal_abrogado_no_se_descarta(self):
        # La preferencia por FEDERAL/VIGENTE nunca vacía la lista.
        elegido = self.elige(
            [
                self.candidato(
                    "LEY ORGANICA DE LA PROCURADURIA GENERAL DE JUSTICIA DEL DISTRITO FEDERAL",
                    ambito="ESTATAL",
                    vigencia="ABROGADO (A)",
                )
            ],
            "LEY Orgánica de la Procuraduría General de Justicia del Distrito Federal",
        )
        self.assertIsNotNone(elegido)

    def test_bajo_el_umbral_minimo_no_elige_nada(self):
        self.assertIsNone(
            self.elige(
                [self.candidato("CODIGO DE CONDUCTA DE LA AGENCIA FEDERAL DE AVIACION CIVIL")],
                "CÓDIGO Civil Federal",
            )
        )

    def test_entre_los_dos_umbrales_queda_sospechoso(self):
        elegido = self.elige(
            [self.candidato("LEY FEDERAL DE LOS DERECHOS DEL CONTRIBUYENTE")],
            "LEY Federal de Derechos",
        )
        self.assertTrue(elegido.sospechoso)
        self.assertLess(elegido.ratio, 0.75)

    def test_sin_candidatos(self):
        self.assertIsNone(self.elige([], "LEY de lo que sea"))


class TestCrawl(unittest.TestCase):
    """El bucle de crawl por instrumento: `fetch_scjn_legislacion.py --api`
    (issue #177) depende de que conserve, una por una, las propiedades de
    `scjn.descarga_ordenamiento` de las que cuelgan sus mecanismos de
    reanudación."""

    def cliente(self, *, reformas, articulos=None, falla=()):
        from scjn.api import Articulo, Ordenamiento, Reforma, ScjnApiError

        prueba = self

        class ClienteFalso:
            def __init__(self):
                self.busquedas = 0

            def search_ordenamiento(self, nombre, **kw):
                self.busquedas += 1
                return [
                    Ordenamiento(
                        idOrdenamiento="410", ordenamiento="LEY FEDERAL DEL TRABAJO"
                    )
                ]

            def reformas_of_ordenamiento(self, id_ordenamiento):
                return [Reforma(reformaId=r[0], fecha_publicacion=r[1]) for r in reformas]

            def articulos_of_reforma(self, id_ordenamiento, id_reforma):
                if id_reforma in falla:
                    raise ScjnApiError("HTTP 500")
                return articulos or [Articulo(1, 1, "ARTÍCULO 1", f"Texto de {id_reforma}.")]

        prueba.Articulo = Articulo
        return ClienteFalso()

    def test_dos_reformas_del_mismo_dia_no_se_pisan(self):
        from tempfile import TemporaryDirectory

        from scjn.api import descarga_ordenamiento

        cliente = self.cliente(reformas=[(3, "01-05-2026"), (2, "01-05-2026"), (1, "01-04-1970")])
        with TemporaryDirectory() as tmp:
            resultado = descarga_ordenamiento(cliente, "LEY FEDERAL DEL TRABAJO", Path(tmp))
            nombres = sorted(p.name for p in Path(tmp).glob("*.md"))
        self.assertEqual(nombres, ["01-04-1970.md", "01-05-2026-2.md", "01-05-2026.md"])
        # devueltos de la más antigua a la más reciente
        self.assertEqual(resultado.escritos[0].name, "01-04-1970.md")

    def test_un_archivo_ya_en_disco_no_se_vuelve_a_bajar(self):
        from tempfile import TemporaryDirectory

        from scjn.api import descarga_ordenamiento

        cliente = self.cliente(reformas=[(2, "02-01-2025"), (1, "01-04-1970")])
        with TemporaryDirectory() as tmp:
            ya = Path(tmp) / "01-04-1970.md"
            ya.write_text("intacto", encoding="utf-8")
            descarga_ordenamiento(cliente, "LEY FEDERAL DEL TRABAJO", Path(tmp))
            self.assertEqual(ya.read_text(encoding="utf-8"), "intacto")

    def test_una_reforma_que_falla_no_aborta_el_instrumento(self):
        from tempfile import TemporaryDirectory

        from scjn.api import descarga_ordenamiento

        cliente = self.cliente(
            reformas=[(9, "02-01-2025"), (8, "21-05-1982"), (7, "31-12-1981")], falla={8}
        )
        with TemporaryDirectory() as tmp:
            resultado = descarga_ordenamiento(cliente, "LEY FEDERAL DE DERECHOS", Path(tmp))
            nombres = sorted(p.name for p in Path(tmp).glob("*.md"))
        self.assertEqual(nombres, ["02-01-2025.md", "31-12-1981.md"])
        self.assertEqual(len(resultado.reformas_fallidas), 1)
        self.assertIn("21-05-1982", resultado.reformas_fallidas[0])

    def test_un_id_ordenamiento_conocido_se_salta_la_busqueda(self):
        from tempfile import TemporaryDirectory

        from scjn.api import descarga_ordenamiento

        cliente = self.cliente(reformas=[(1, "01-04-1970")])
        with TemporaryDirectory() as tmp:
            resultado = descarga_ordenamiento(
                cliente, "LEY FEDERAL DEL TRABAJO", Path(tmp), id_ordenamiento="410"
            )
        self.assertEqual(cliente.busquedas, 0)
        self.assertEqual(resultado.ordenamiento.idOrdenamiento, "410")

    def test_sin_candidato_devuelve_vacio_sin_levantar(self):
        from tempfile import TemporaryDirectory

        from scjn.api import descarga_ordenamiento

        cliente = self.cliente(reformas=[])
        with TemporaryDirectory() as tmp:
            resultado = descarga_ordenamiento(cliente, "CÓDIGO Civil Federal", Path(tmp))
        self.assertEqual(resultado.escritos, [])
        self.assertIsNone(resultado.ordenamiento)


class TestPaginacion(unittest.TestCase):
    """Regresión del bug que el issue #178 destapó auditando `lfd`: 92
    snapshots contra las 98 reformas que su propia ficha de la SCJN lista.

    Dos hechos de esta API se combinaban mal: `tamanio` puede sobrecontar lo
    que el endpoint llega a devolver (`lfd` reforma 99 declara 995 y sirve
    991, completos, cubriendo `orden` 1..995), y pedir una página pasada del
    final contesta HTTP 500 en vez de una página vacía. Con `len(filas) >=
    tamanio` como condición de paro, esas reformas pedían siempre una
    segunda página, esa página fallaba, y la reforma entera se descartaba."""

    def respuestas_articulos(self, paginas, tamanio):
        from scjn.api import ScjnApi

        respuestas = [
            RespuestaFalsa(
                {
                    "codigo": 200,
                    "tamanio": tamanio,
                    "articulos": [
                        {"numero": i, "orden": i, "referencia": f"ARTÍCULO {i}", "contenido": "x"}
                        for i in p
                    ],
                }
            )
            for p in paginas
        ]
        # Una página más allá del final: 500, como hace la API de verdad.
        respuestas.append(RespuestaFalsa({}, status_code=500))
        sesion = SesionFalsa(respuestas)
        return ScjnApi(espera=0, reintentos=0, session=sesion), sesion

    def test_una_pagina_corta_es_la_ultima_aunque_tamanio_sobrecuente(self):
        from scjn.api import TAMANIO_PAGINA_ARTICULOS

        n = TAMANIO_PAGINA_ARTICULOS
        # Página 1 completa, página 2 corta; `tamanio` declara 4 de más.
        cliente, sesion = self.respuestas_articulos(
            [range(1, n + 1), range(n + 1, n + 492)], tamanio=n + 495
        )
        articulos = cliente.articulos_of_reforma(693, 99)
        self.assertEqual(len(articulos), n + 491)
        # Y no pidió la tercera página, que es la que contesta 500.
        self.assertEqual(len(sesion.llamadas), 2)

    def test_una_pagina_completa_que_agota_tamanio_es_la_ultima(self):
        # `lss` reforma 42: declara exactamente 500 y sirve exactamente 500,
        # y su pagina 2 tambien contesta HTTP 500. Con solo la regla de la
        # pagina corta, este caso volvia a pedir una pagina inexistente.
        from scjn.api import TAMANIO_PAGINA_ARTICULOS

        n = TAMANIO_PAGINA_ARTICULOS
        cliente, sesion = self.respuestas_articulos([range(1, n + 1)], tamanio=n)
        self.assertEqual(len(cliente.articulos_of_reforma(853, 42)), n)
        self.assertEqual(len(sesion.llamadas), 1)

    def test_una_sola_pagina_corta_no_pide_una_segunda(self):
        cliente, sesion = self.respuestas_articulos([range(1, 88)], tamanio=87)
        self.assertEqual(len(cliente.articulos_of_reforma(188805, 1)), 87)
        self.assertEqual(len(sesion.llamadas), 1)

    def test_una_reforma_sin_articulos_no_se_pide(self):
        # `tieneArticulos=False` es la API diciendo por adelantado que no tiene
        # texto consolidado; preguntarle igual contesta 500.
        from tempfile import TemporaryDirectory

        from scjn.api import Ordenamiento, Reforma, ScjnApiError, descarga_ordenamiento

        class ClienteFalso:
            def __init__(self):
                self.pedidos = []

            def search_ordenamiento(self, nombre, **kw):
                return [Ordenamiento(idOrdenamiento="693", ordenamiento="LEY FEDERAL DE DERECHOS")]

            def reformas_of_ordenamiento(self, id_ordenamiento):
                return [
                    Reforma(reformaId=9, fecha_publicacion="28-12-2025"),
                    Reforma(
                        reformaId=8,
                        fecha_publicacion="21-05-1982",
                        categoria="FE DE ERRATAS",
                        tieneArticulos=False,
                    ),
                ]

            def articulos_of_reforma(self, id_ordenamiento, id_reforma):
                self.pedidos.append(id_reforma)
                if id_reforma == 8:
                    raise ScjnApiError("HTTP 500")
                return []

        cliente = ClienteFalso()
        with TemporaryDirectory() as tmp:
            resultado = descarga_ordenamiento(cliente, "LEY Federal de Derechos", Path(tmp))
        self.assertEqual(cliente.pedidos, [9])
        self.assertEqual(resultado.reformas_fallidas, [])
        self.assertEqual(len(resultado.reformas_sin_articulos), 1)
        self.assertIn("FE DE ERRATAS", resultado.reformas_sin_articulos[0])
        # snapshots + sin-texto tiene que cuadrar con lo que la SCJN reporta
        self.assertEqual(resultado.total_reformas, 2)
        self.assertEqual(
            len(resultado.escritos) + len(resultado.reformas_sin_articulos),
            resultado.total_reformas,
        )

import unittest
from unittest.mock import patch

from nota2md.leyes import construye_ley, limpia_texto_ley, normaliza_para_comparar

PUBLICACION_ORIGINAL = {
    "cadenaContenido": (
        "<body>"
        "<h1 class='Titulo_1'><span>DECRETO por el que se expide la Ley de Prueba.</span></h1>"
        "<h2 class='Titulo_2'><span>Al margen un sello.</span></h2>"
        "<div class='Texto'><span>Que el Honorable Congreso decreta:</span></div>"
        "<div class='Texto'><span style='font-weight:bold;'>Artículo 1.</span>"
        "<span> Texto original del artículo primero.</span></div>"
        "<div class='Texto'><span style='font-weight:bold;'>Artículo 2.</span>"
        "<span> Texto original del artículo segundo.</span></div>"
        "<div class='Texto'><span style='font-weight:bold;'>Artículo 3.</span>"
        "<span> Texto original del artículo tercero.</span></div>"
        "<h2 class='ANOTACION'><span>Transitorios</span></h2>"
        "<div class='Texto'><span style='font-weight:bold;'>Único.</span>"
        "<span> Entrará en vigor al día siguiente.</span></div>"
        "</body>"
    )
}


def _reforma(articulo_unico: str, articulos_nuevos: str) -> dict:
    return {
        "cadenaContenido": (
            "<body>"
            "<h1 class='Titulo_1'><span>DECRETO de reforma.</span></h1>"
            "<h2 class='Titulo_2'><span>Al margen un sello.</span></h2>"
            f"<div class='Texto'><span style='font-weight:bold;'>{articulo_unico}</span></div>"
            f"{articulos_nuevos}"
            "<h2 class='ANOTACION'><span>Transitorios</span></h2>"
            "<div class='Texto'><span>Este decreto entra en vigor de inmediato.</span></div>"
            "</body>"
        )
    }


class TestConstruyeLey(unittest.TestCase):
    @patch("nota2md.leyes.fetch_nota")
    def test_reforma_reemplaza_el_articulo_sin_mover_su_posicion(self, mock_fetch):
        reforma = _reforma(
            "Artículo Único.- Se reforma el artículo 2 de la Ley de Prueba, "
            "para quedar como sigue:",
            "<div class='Texto'><span style='font-weight:bold;'>Artículo 2.</span>"
            "<span> Texto reformado del artículo segundo.</span></div>",
        )
        mock_fetch.side_effect = [PUBLICACION_ORIGINAL, reforma]

        ley = construye_ley([1, 2])

        self.assertIn("Texto reformado del artículo segundo", ley)
        self.assertNotIn("Texto original del artículo segundo", ley)
        # el orden de los artículos no cambia por una reforma
        self.assertLess(ley.index("Artículo 1."), ley.index("Artículo 2."))
        self.assertLess(ley.index("Artículo 2."), ley.index("Artículo 3."))
        # el resto de la ley, sin tocar
        self.assertIn("Texto original del artículo primero", ley)
        self.assertIn("Texto original del artículo tercero", ley)
        self.assertIn("Entrará en vigor al día siguiente", ley)

    @patch("nota2md.leyes.fetch_nota")
    def test_adiciona_inserta_el_articulo_bis_junto_a_su_base(self, mock_fetch):
        reforma = _reforma(
            "Artículo Único.- Se adiciona un artículo 1 Bis a la Ley de Prueba, "
            "para quedar como sigue:",
            "<div class='Texto'><span style='font-weight:bold;'>Artículo 1 Bis.</span>"
            "<span> Texto del nuevo artículo.</span></div>",
        )
        mock_fetch.side_effect = [PUBLICACION_ORIGINAL, reforma]

        ley = construye_ley([1, 2])

        self.assertIn("Texto del nuevo artículo", ley)
        self.assertLess(ley.index("Artículo 1."), ley.index("Artículo 1 Bis."))
        self.assertLess(ley.index("Artículo 1 Bis."), ley.index("Artículo 2."))

    @patch("nota2md.leyes.fetch_nota")
    def test_deroga_marca_el_articulo_sin_texto_de_reemplazo(self, mock_fetch):
        reforma = _reforma(
            "Artículo Único.- Se deroga el artículo 2 de la Ley de Prueba.", ""
        )
        mock_fetch.side_effect = [PUBLICACION_ORIGINAL, reforma]

        ley = construye_ley([1, 2])

        self.assertIn("Artículo 2.", ley)
        self.assertIn("Derogado", ley)
        self.assertNotIn("Texto original del artículo segundo", ley)
        self.assertLess(ley.index("Artículo 1."), ley.index("Artículo 2."))
        self.assertLess(ley.index("Artículo 2."), ley.index("Artículo 3."))

    @patch("nota2md.leyes.fetch_nota")
    def test_reforma_y_deroga_en_el_mismo_decreto(self, mock_fetch):
        reforma = _reforma(
            "Artículo Único.- Se reforma el artículo 1 y se deroga el artículo 3 "
            "de la Ley de Prueba, para quedar como sigue:",
            "<div class='Texto'><span style='font-weight:bold;'>Artículo 1.</span>"
            "<span> Texto reformado del artículo primero.</span></div>",
        )
        mock_fetch.side_effect = [PUBLICACION_ORIGINAL, reforma]

        ley = construye_ley([1, 2])

        self.assertIn("Texto reformado del artículo primero", ley)
        self.assertIn("Derogado", ley)
        self.assertIn("Texto original del artículo segundo", ley)

    @patch("nota2md.leyes.fetch_nota")
    def test_la_publicacion_original_basta_sin_reformas(self, mock_fetch):
        mock_fetch.return_value = PUBLICACION_ORIGINAL

        ley = construye_ley([1])

        self.assertIn("Texto original del artículo primero", ley)
        self.assertIn("Texto original del artículo segundo", ley)
        self.assertIn("Texto original del artículo tercero", ley)

    def test_rechaza_una_lista_vacia(self):
        with self.assertRaises(ValueError):
            construye_ley([])

    @patch("nota2md.leyes.fetch_nota")
    def test_nota_sin_html_es_un_error_claro(self, mock_fetch):
        mock_fetch.return_value = {"cadenaContenido": ""}
        with self.assertRaises(ValueError):
            construye_ley([1])


class TestLimpiaTextoLey(unittest.TestCase):
    def test_quita_encabezado_pie_de_pagina_y_portada_de_diputados(self):
        paginas = [
            "\nLEY DE PRUEBA\n\nCÁMARA DE DIPUTADOS DEL H. CONGRESO DE LA UNIÓN\n"
            "Secretaría General\nSecretaría de Servicios Parlamentarios\n"
            "Última Reforma DOF 01-01-2020\n\n1 de 2\nLEY DE PRUEBA\n\n"
            "Nueva Ley publicada en el Diario Oficial de la Federación el 1 de enero de 2019\n\n"
            "TEXTO VIGENTE\nÚltima reforma publicada DOF 01-01-2020\n\n"
            "Al margen un sello.\n\nArtículo 1. Texto del artículo.\n",
            "\nLEY DE PRUEBA\n\nCÁMARA DE DIPUTADOS DEL H. CONGRESO DE LA UNIÓN\n"
            "Secretaría General\nSecretaría de Servicios Parlamentarios\n"
            "Última Reforma DOF 01-01-2020\n\n2 de 2\nARTÍCULOS TRANSITORIOS DE DECRETOS DE REFORMA\n\n"
            "DECRETO por el que se reforma...\n",
        ]

        texto = limpia_texto_ley(paginas)

        self.assertIn("Al margen un sello.", texto)
        self.assertIn("Artículo 1. Texto del artículo.", texto)
        self.assertNotIn("CÁMARA DE DIPUTADOS", texto)
        self.assertNotIn("TEXTO VIGENTE", texto)
        self.assertNotIn("ARTÍCULOS TRANSITORIOS DE DECRETOS DE REFORMA", texto)
        self.assertNotIn("DECRETO por el que se reforma", texto)


class TestNormalizaParaComparar(unittest.TestCase):
    def test_ignora_formato_markdown_acentos_y_mayusculas(self):
        self.assertEqual(
            normaliza_para_comparar("**Artículo 1.** Texto  con   Acentos."),
            normaliza_para_comparar("articulo 1. texto con acentos."),
        )

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nota2md.leyes import (
    LeyNoReconstruible,
    reconstruct_legal_provisions,
    normaliza_para_comparar,
)

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


def _bloque(negrita: str, resto: str = "") -> str:
    return (
        f"<div class='Texto'><span style='font-weight:bold;'>{negrita}</span>"
        f"<span> {resto}</span></div>"
    )


PUBLICACION_CON_FRACCIONES = {
    "cadenaContenido": (
        "<body>"
        "<h1 class='Titulo_1'><span>DECRETO por el que se expide la Ley de Prueba.</span></h1>"
        "<h2 class='Titulo_2'><span>Al margen un sello.</span></h2>"
        "<div class='Texto'><span>Que el Honorable Congreso decreta:</span></div>"
        + _bloque("Artículo 2.", "Encabezado del artículo.")
        + _bloque("I.", "Contenido original de la fracción I.")
        + _bloque("II.", "Contenido original de la fracción II.")
        + _bloque("III.", "Contenido original de la fracción III.")
        + _bloque("IV.", "Contenido original de la fracción IV.")
        + "<h2 class='ANOTACION'><span>Transitorios</span></h2>"
        + _bloque("Único.", "Entrará en vigor al día siguiente.")
        + "</body>"
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
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.outdir = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    @patch("nota2md.leyes.fetch_nota")
    def test_reforma_reemplaza_el_articulo_sin_mover_su_posicion(self, mock_fetch):
        reforma = _reforma(
            "Artículo Único.- Se reforma el artículo 2 de la Ley de Prueba, "
            "para quedar como sigue:",
            "<div class='Texto'><span style='font-weight:bold;'>Artículo 2.</span>"
            "<span> Texto reformado del artículo segundo.</span></div>",
        )
        mock_fetch.side_effect = [PUBLICACION_ORIGINAL, reforma]

        dest = reconstruct_legal_provisions([1, 2], self.outdir)
        ley = dest.read_text(encoding="utf-8")

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

        dest = reconstruct_legal_provisions([1, 2], self.outdir)
        ley = dest.read_text(encoding="utf-8")

        self.assertIn("Texto del nuevo artículo", ley)
        self.assertLess(ley.index("Artículo 1."), ley.index("Artículo 1 Bis."))
        self.assertLess(ley.index("Artículo 1 Bis."), ley.index("Artículo 2."))

    @patch("nota2md.leyes.fetch_nota")
    def test_deroga_marca_el_articulo_sin_texto_de_reemplazo(self, mock_fetch):
        reforma = _reforma(
            "Artículo Único.- Se deroga el artículo 2 de la Ley de Prueba.", ""
        )
        mock_fetch.side_effect = [PUBLICACION_ORIGINAL, reforma]

        dest = reconstruct_legal_provisions([1, 2], self.outdir)
        ley = dest.read_text(encoding="utf-8")

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

        dest = reconstruct_legal_provisions([1, 2], self.outdir)
        ley = dest.read_text(encoding="utf-8")

        self.assertIn("Texto reformado del artículo primero", ley)
        self.assertIn("Derogado", ley)
        self.assertIn("Texto original del artículo segundo", ley)

    @patch("nota2md.leyes.fetch_nota")
    def test_una_fraccion_marcada_con_elipsis_se_conserva(self, mock_fetch):
        reforma = _reforma(
            "Artículo Único.- Se reforma la fracción II del artículo 2 de la "
            "Ley de Prueba, para quedar como sigue:",
            _bloque("Artículo 2.", "...")
            + _bloque("I.", "...")
            + _bloque("II.", "Contenido reformado de la fracción II.")
            + _bloque("III.", "...")
            + _bloque("IV.", "..."),
        )
        mock_fetch.side_effect = [PUBLICACION_CON_FRACCIONES, reforma]

        dest = reconstruct_legal_provisions([1, 2], self.outdir)
        ley = dest.read_text(encoding="utf-8")

        self.assertIn("Encabezado del artículo", ley)
        self.assertIn("Contenido original de la fracción I.", ley)
        self.assertIn("Contenido reformado de la fracción II", ley)
        self.assertNotIn("**II.** Contenido original", ley)
        self.assertIn("Contenido original de la fracción III.", ley)
        self.assertIn("Contenido original de la fracción IV.", ley)

    @patch("nota2md.leyes.fetch_nota")
    def test_un_rango_de_fracciones_marcado_con_elipsis_se_conserva(self, mock_fetch):
        reforma = _reforma(
            "Artículo Único.- Se reforma la fracción IV del artículo 2 de la "
            "Ley de Prueba, para quedar como sigue:",
            _bloque("Artículo 2.", "...")
            + "<div class='Texto'><span style='font-weight:bold;'>I.</span>"
            "<span> a </span><span style='font-weight:bold;'>III.</span>"
            "<span> ...</span></div>"
            + _bloque("IV.", "Contenido reformado de la fracción IV."),
        )
        mock_fetch.side_effect = [PUBLICACION_CON_FRACCIONES, reforma]

        dest = reconstruct_legal_provisions([1, 2], self.outdir)
        ley = dest.read_text(encoding="utf-8")

        self.assertIn("Contenido original de la fracción I.", ley)
        self.assertIn("Contenido original de la fracción II.", ley)
        self.assertIn("Contenido original de la fracción III.", ley)
        self.assertIn("Contenido reformado de la fracción IV", ley)
        self.assertNotIn("Contenido original de la fracción IV", ley)

    @patch("nota2md.leyes.fetch_nota")
    def test_incisos_bajo_distintas_fracciones_no_se_confunden(self, mock_fetch):
        publicacion = {
            "cadenaContenido": (
                "<body>"
                "<h1 class='Titulo_1'><span>DECRETO por el que se expide la Ley de Prueba.</span></h1>"
                "<h2 class='Titulo_2'><span>Al margen un sello.</span></h2>"
                + _bloque("Artículo 2.", "Encabezado.")
                + _bloque("I.", "Fracción uno.")
                + _bloque("a)", "inciso a de la fracción I.")
                + _bloque("b)", "inciso b de la fracción I.")
                + _bloque("II.", "Fracción dos.")
                + _bloque("a)", "inciso a de la fracción II.")
                + _bloque("b)", "inciso b de la fracción II.")
                + "<h2 class='ANOTACION'><span>Transitorios</span></h2>"
                + _bloque("Único.", "Entrará en vigor al día siguiente.")
                + "</body>"
            )
        }
        reforma = _reforma(
            "Artículo Único.- Se reforma el inciso b) de la fracción II del "
            "artículo 2 de la Ley de Prueba, para quedar como sigue:",
            _bloque("Artículo 2.", "...")
            + _bloque("I.", "...")
            + _bloque("a)", "...")
            + _bloque("b)", "...")
            + _bloque("II.", "...")
            + _bloque("a)", "...")
            + _bloque("b)", "Inciso b) reformado de la fracción II."),
        )
        mock_fetch.side_effect = [publicacion, reforma]

        dest = reconstruct_legal_provisions([1, 2], self.outdir)
        ley = dest.read_text(encoding="utf-8")

        self.assertIn("inciso a de la fracción I.", ley)
        self.assertIn("inciso b de la fracción I.", ley)
        self.assertIn("inciso a de la fracción II.", ley)
        self.assertIn("Inciso b) reformado de la fracción II", ley)
        self.assertNotIn("inciso b de la fracción II", ley)

    @patch("nota2md.leyes.fetch_nota")
    def test_parrafo_insertado_recorriendo_los_subsecuentes(self, mock_fetch):
        publicacion = {
            "cadenaContenido": (
                "<body>"
                "<h1 class='Titulo_1'><span>DECRETO por el que se expide la Ley de Prueba.</span></h1>"
                "<h2 class='Titulo_2'><span>Al margen un sello.</span></h2>"
                + _bloque("Artículo 22.", "Primer párrafo original.")
                + "<div class='Texto'><span>Segundo párrafo original.</span></div>"
                + "<h2 class='ANOTACION'><span>Transitorios</span></h2>"
                + _bloque("Único.", "Entrará en vigor al día siguiente.")
                + "</body>"
            )
        }
        reforma = _reforma(
            "Artículo Único.- Se adiciona un segundo párrafo, recorriéndose el "
            "subsecuente, al artículo 22 de la Ley de Prueba, para quedar como sigue:",
            _bloque("Artículo 22.", "...")
            + "<div class='Texto'><span>Nuevo segundo párrafo insertado.</span></div>"
            + "<div class='Texto'><span>...</span></div>",
        )
        mock_fetch.side_effect = [publicacion, reforma]

        dest = reconstruct_legal_provisions([1, 2], self.outdir)
        ley = dest.read_text(encoding="utf-8")

        self.assertIn("Primer párrafo original", ley)
        self.assertIn("Nuevo segundo párrafo insertado", ley)
        self.assertIn("Segundo párrafo original", ley)
        self.assertLess(
            ley.index("Nuevo segundo párrafo insertado"),
            ley.index("Segundo párrafo original"),
        )

    @patch("nota2md.leyes.fetch_nota")
    def test_la_publicacion_original_basta_sin_reformas(self, mock_fetch):
        mock_fetch.return_value = PUBLICACION_ORIGINAL

        dest = reconstruct_legal_provisions([1], self.outdir)
        ley = dest.read_text(encoding="utf-8")

        self.assertIn("Texto original del artículo primero", ley)
        self.assertIn("Texto original del artículo segundo", ley)
        self.assertIn("Texto original del artículo tercero", ley)

    def test_rechaza_una_lista_vacia(self):
        with self.assertRaises(ValueError):
            reconstruct_legal_provisions([], self.outdir)

    @patch("nota2md.leyes.fetch_nota")
    def test_nota_sin_html_es_un_error_claro(self, mock_fetch):
        mock_fetch.return_value = {"cadenaContenido": ""}
        with self.assertRaises(ValueError):
            reconstruct_legal_provisions([1], self.outdir)

    @patch("nota2md.leyes.fetch_nota")
    def test_nota_original_solo_con_titulo_es_no_reconstruible(self, mock_fetch):
        """Un anexo grande (p. ej. una tarifa arancelaria) puede publicarse en
        el DOF como PDF embebido en vez de HTML navegable — la nota entonces
        solo trae su título, sin "Al margen un sello" ni artículos."""
        mock_fetch.return_value = {
            "cadenaContenido": (
                "<body><h1 class='Titulo_1'>"
                "<span>DECRETO por el que se expide la Ley de Prueba.</span>"
                "</h1></body>"
            )
        }
        with self.assertRaises(LeyNoReconstruible) as ctx:
            reconstruct_legal_provisions([1], self.outdir)
        self.assertIn("solo su título", str(ctx.exception))

    @patch("nota2md.leyes.fetch_nota")
    def test_nota_original_con_cuerpo_pero_sin_articulos_es_no_reconstruible(self, mock_fetch):
        mock_fetch.return_value = {
            "cadenaContenido": (
                "<body>"
                "<h1 class='Titulo_1'><span>DECRETO por el que se expide la Ley de Prueba.</span></h1>"
                "<h2 class='Titulo_2'><span>Al margen un sello.</span></h2>"
                "<div class='Texto'><span>Texto sin ningún artículo reconocible.</span></div>"
                "</body>"
            )
        }
        with self.assertRaises(LeyNoReconstruible) as ctx:
            reconstruct_legal_provisions([1], self.outdir)
        self.assertIn("no se reconoció", str(ctx.exception))


class TestNormalizaParaComparar(unittest.TestCase):
    def test_ignora_formato_markdown_acentos_y_mayusculas(self):
        self.assertEqual(
            normaliza_para_comparar("**Artículo 1.** Texto  con   Acentos."),
            normaliza_para_comparar("articulo 1. texto con acentos."),
        )

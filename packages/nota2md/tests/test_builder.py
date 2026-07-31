import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dofjson import dofweb

from nota2md.builder import fetch_day_notes, fetch_nota, legal_provisions, titulo_siguiente

HTML_NOTA = {
    "codNota": 5793655,
    "titulo": "Convenio de Coordinación...",
    "codEdicion": "MAT",
    "fecha": "15-07-2026",
    "cadenaContenido": (
        "<body><div><h1 class='Titulo_1'><span>CONVENIO de Coordinación</span></h1>"
        "<div class='Texto'><span>Cuerpo del convenio.</span></div></div></body>"
    ),
}


class TestTituloSiguiente(unittest.TestCase):
    def test_skips_titleless_stub_and_returns_next_titled_note(self):
        notas = {
            "NotasMatutinas": [
                {"codNota": 100, "titulo": "Nota A"},
                {"codNota": 101},  # title-less stub/twin
                {"codNota": 102, "titulo": "Nota C"},
            ]
        }
        nota = {"codNota": 100, "codEdicion": "MAT"}
        self.assertEqual(titulo_siguiente(nota, notas), "Nota C")

    def test_returns_none_for_last_note(self):
        notas = {"NotasMatutinas": [{"codNota": 100, "titulo": "Nota A"}]}
        nota = {"codNota": 100, "codEdicion": "MAT"}
        self.assertIsNone(titulo_siguiente(nota, notas))


class TestFetchNota(unittest.TestCase):
    """SIDOF is missing whole days (see dofjson.dofweb), and the notes on them
    have no SIDOF record at all — the DOF's website is the only source."""

    WEB_NOTA = {
        "codNota": 4997808,
        "fecha": "03-03-1999",
        "titulo": "DECRETO por el que se concede permiso...",
        "cadenaContenido": "<HTML><BODY><p>Cuerpo del decreto.</p></BODY></HTML>",
        "existeHtml": "S",
        "fuente": dofweb.FUENTE,
    }

    @patch("nota2md.builder.dofweb.get_nota")
    @patch("nota2md.builder.client.get_nota")
    def test_prefers_sidof_when_it_has_the_note(self, mock_sidof, mock_web):
        mock_sidof.return_value = {"Nota": {"codNota": 1, "cadenaContenido": "<p>x</p>"}}

        self.assertEqual(fetch_nota(1)["codNota"], 1)
        mock_web.assert_not_called()

    @patch("nota2md.builder.dofweb.get_nota")
    @patch("nota2md.builder.client.get_nota")
    def test_falls_back_to_the_website_when_sidof_lacks_the_note(self, mock_sidof, mock_web):
        # SIDOF answers an empty list, not an error, for a codNota it lacks.
        mock_sidof.return_value = {"messageCode": 200, "response": "OK", "Nota": []}
        mock_web.return_value = {"Nota": self.WEB_NOTA}

        nota = fetch_nota(4997808)

        mock_web.assert_called_once_with(4997808)
        self.assertEqual(nota["fuente"], dofweb.FUENTE)

    @patch("nota2md.builder.dofweb.get_nota")
    @patch("nota2md.builder.client.get_nota")
    def test_raises_when_neither_source_has_the_note(self, mock_sidof, mock_web):
        mock_sidof.return_value = {"Nota": []}
        mock_web.return_value = {"Nota": []}

        with self.assertRaises(ValueError):
            fetch_nota(999999999)


class TestFetchDayNotes(unittest.TestCase):
    FECHA = dt.date(2026, 7, 15)

    @patch("nota2md.builder.dofweb.get_notas")
    @patch("nota2md.builder.client.get_notas")
    def test_prefers_sidof_when_it_has_notes(self, mock_sidof, mock_web):
        mock_sidof.return_value = {
            "NotasMatutinas": [{"codNota": 1, "titulo": "Nota A"}],
            "NotasVespertinas": [],
            "NotasExtraordinarias": [],
        }

        notas = fetch_day_notes(self.FECHA)

        self.assertEqual(notas["NotasMatutinas"], [{"codNota": 1, "titulo": "Nota A"}])
        mock_web.assert_not_called()

    @patch("nota2md.builder.dofweb.get_notas")
    @patch("nota2md.builder.client.get_notas")
    def test_drops_titleless_stub_entries(self, mock_sidof, mock_web):
        mock_sidof.return_value = {
            "NotasMatutinas": [
                {"codNota": 1, "titulo": "Nota A"},
                {"codNota": 2},  # title-less stub/twin
            ],
            "NotasVespertinas": [],
            "NotasExtraordinarias": [],
        }

        notas = fetch_day_notes(self.FECHA)

        self.assertEqual([n["codNota"] for n in notas["NotasMatutinas"]], [1])

    @patch("nota2md.builder.dofweb.get_notas")
    @patch("nota2md.builder.client.get_notas")
    def test_falls_back_to_the_website_when_sidof_has_nothing(self, mock_sidof, mock_web):
        # SIDOF answers every list empty, not an error, for a day it lacks.
        mock_sidof.return_value = {
            "NotasMatutinas": [], "NotasVespertinas": [], "NotasExtraordinarias": [],
        }
        mock_web.return_value = {
            "NotasMatutinas": [{"codNota": 3, "titulo": "Nota recuperada"}],
            "NotasVespertinas": [],
            "NotasExtraordinarias": [],
            "fuente": dofweb.FUENTE,
        }

        notas = fetch_day_notes(self.FECHA)

        mock_web.assert_called_once_with(self.FECHA)
        self.assertEqual(notas["fuente"], dofweb.FUENTE)
        self.assertEqual([n["codNota"] for n in notas["NotasMatutinas"]], [3])

    @patch("nota2md.builder.dofweb.get_notas")
    @patch("nota2md.builder.client.get_notas")
    def test_returns_empty_when_neither_source_published_that_day(self, mock_sidof, mock_web):
        vacio = {"NotasMatutinas": [], "NotasVespertinas": [], "NotasExtraordinarias": []}
        mock_sidof.return_value = dict(vacio)
        mock_web.return_value = {**vacio, "edicionesSinIndice": []}

        notas = fetch_day_notes(self.FECHA)

        self.assertEqual(notas["NotasMatutinas"], [])
        self.assertEqual(notas["NotasVespertinas"], [])
        self.assertEqual(notas["NotasExtraordinarias"], [])


class TestLegalProvisions(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.outdir = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_rejects_unknown_source(self):
        with self.assertRaises(ValueError):
            legal_provisions(1, self.outdir, source="xml", nota=HTML_NOTA)

    @patch("nota2md.builder.client.download_nota_imagenes")
    def test_html_path_converts_cadena_contenido(self, mock_download):
        dest = legal_provisions(5793655, self.outdir, source="auto", nota=HTML_NOTA)

        self.assertEqual(dest, self.outdir / "nota-5793655.md")
        text = dest.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# CONVENIO de Coordinación"))
        self.assertIn("Cuerpo del convenio.", text)
        mock_download.assert_not_called()

    def test_html_source_without_content_raises(self):
        nota = {"codNota": 1, "codEdicion": "MAT", "cadenaContenido": ""}
        with self.assertRaises(ValueError):
            legal_provisions(1, self.outdir, source="html", nota=nota)

    @patch("dof2md.converter.convert_images_to_markdown")
    @patch("nota2md.builder.client.download_nota_imagenes")
    def test_image_path_ocrs_and_cuts_to_the_note(self, mock_download, mock_convert):
        image_only = {
            "codNota": 200,
            "titulo": "Aviso de deslinde SUP 033 superficie 31.51 Palenque Chis",
            "codEdicion": "MAT",
            "fecha": "15-07-2026",
            "cadenaContenido": "",
        }
        notas = {
            "NotasMatutinas": [
                {"codNota": 200, "titulo": image_only["titulo"]},
                {"codNota": 201, "titulo": "Aviso de deslinde SUP 036 superficie 25.64 Palenque Chis"},
            ]
        }
        mock_download.return_value = [self.outdir / "nota-200-p1.jpg"]

        def fake_ocr(image_paths, md_path, **kwargs):
            md_path.write_text(
                "tail de la nota anterior.\n\n"
                "## Aviso de deslinde SUP 033 superficie 31.51 Palenque Chis\n\n"
                "Contenido de la nota objetivo.\n\n"
                "## Aviso de deslinde SUP 036 superficie 25.64 Palenque Chis\n\n"
                "Siguiente nota, excluir.\n",
                encoding="utf-8",
            )

        mock_convert.side_effect = fake_ocr

        dest = legal_provisions(
            200, self.outdir, source="image", nota=image_only, notas_del_dia=notas
        )

        mock_download.assert_called_once_with(200, self.outdir, nota=image_only)
        text = dest.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("## Aviso de deslinde SUP 033"))
        self.assertIn("Contenido de la nota objetivo.", text)
        self.assertNotIn("tail de la nota anterior", text)
        self.assertNotIn("SUP 036", text)

    @patch("dof2md.converter.convert_to_markdown")
    @patch("nota2md.builder.client.download_nota_pdf")
    def test_pdf_path_ocrs_note_pdf_and_cuts(self, mock_download_pdf, mock_convert):
        nota = {
            "codNota": 300,
            "titulo": "Acuerdo de regularización de títulos",
            "codEdicion": "MAT",
            "fecha": "15-07-2026",
            "cadenaContenido": "<HTML>tiene texto pero forzamos pdf</HTML>",
        }
        notas = {
            "NotasMatutinas": [
                {"codNota": 300, "titulo": nota["titulo"]},
                {"codNota": 301, "titulo": "Norma Oficial Mexicana NOM-042-NUCL"},
            ]
        }
        mock_download_pdf.return_value = self.outdir / "nota-300.pdf"

        def fake_ocr(pdf_path, md_path, **kwargs):
            md_path.write_text(
                "## Acuerdo de regularización de títulos\n\n"
                "Cuerpo del acuerdo.\n\n"
                "## Norma Oficial Mexicana NOM-042-NUCL\n\n"
                "Nota siguiente, excluir.\n",
                encoding="utf-8",
            )

        mock_convert.side_effect = fake_ocr

        dest = legal_provisions(
            300, self.outdir, source="pdf", nota=nota, notas_del_dia=notas
        )

        # source='pdf' forces the PDF path even though the note has HTML content.
        mock_download_pdf.assert_called_once_with(300, self.outdir, nota=nota)
        text = dest.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("## Acuerdo de regularización"))
        self.assertIn("Cuerpo del acuerdo.", text)
        self.assertNotIn("NOM-042-NUCL", text)

    @patch("nota2md.builder.dofweb.get_nota")
    @patch("nota2md.builder.client.get_nota")
    def test_builds_a_note_sidof_does_not_have_from_the_website(self, mock_sidof, mock_web):
        mock_sidof.return_value = {"Nota": []}
        mock_web.return_value = {"Nota": TestFetchNota.WEB_NOTA}

        dest = legal_provisions(4997808, self.outdir)

        self.assertEqual(dest, self.outdir / "nota-4997808.md")
        self.assertIn("Cuerpo del decreto.", dest.read_text(encoding="utf-8"))

    def test_ocr_paths_reject_a_note_that_only_the_website_has(self):
        """Those notes carry no codDiario or page numbers — the OCR paths start
        from SIDOF metadata that does not exist for them."""
        for source in ("image", "pdf"):
            with self.subTest(source=source):
                with self.assertRaises(ValueError) as ctx:
                    legal_provisions(
                        4997808, self.outdir, source=source, nota=TestFetchNota.WEB_NOTA
                    )
                self.assertIn(dofweb.FUENTE, str(ctx.exception))

    @patch("dof2md.converter.convert_images_to_markdown")
    @patch("nota2md.builder.client.download_nota_imagenes")
    def test_keep_pages_writes_full_uncut_copy(self, mock_download, mock_convert):
        image_only = {
            "codNota": 200, "titulo": "T", "codEdicion": "MAT",
            "fecha": "15-07-2026", "cadenaContenido": "",
        }
        mock_download.return_value = [self.outdir / "nota-200-p1.jpg"]
        mock_convert.side_effect = lambda paths, md_path, **kw: md_path.write_text(
            "full page text", encoding="utf-8"
        )

        legal_provisions(
            200, self.outdir, source="image", nota=image_only,
            notas_del_dia={"NotasMatutinas": [{"codNota": 200, "titulo": "T"}]},
            keep_pages=True,
        )

        self.assertEqual(
            (self.outdir / "nota-200.full.md").read_text(encoding="utf-8"),
            "full page text",
        )


if __name__ == "__main__":
    unittest.main()

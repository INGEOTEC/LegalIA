import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dofjson.cli import main


class TestCli(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmpdir.cleanup()

    @patch("dofjson.client.get_notas")
    def test_main_saves_json_to_outdir(self, mock_get_notas):
        mock_get_notas.return_value = {"messageCode": 200, "response": "OK"}

        main(["2026-07-16", "--endpoint", "notas", "--outdir", self.tmpdir.name])

        dest = Path(self.tmpdir.name) / "16072026-notas.json"
        self.assertTrue(dest.exists())
        self.assertEqual(json.loads(dest.read_text()), mock_get_notas.return_value)
        mock_get_notas.assert_called_once()

    def test_main_rejects_invalid_date(self):
        with self.assertRaises(SystemExit):
            main(["16-07-2026", "--outdir", self.tmpdir.name])

    @patch("dofjson.client.download_nota")
    def test_main_downloads_single_nota_by_id(self, mock_download_nota):
        mock_download_nota.return_value = [
            Path(self.tmpdir.name) / "nota-4845455-19800102-21-U-000.jpg",
            Path(self.tmpdir.name) / "nota-4845455-19800102-22-U-000.jpg",
        ]

        main(["--nota", "4845455", "--outdir", self.tmpdir.name])

        mock_download_nota.assert_called_once_with(4845455, Path(self.tmpdir.name))

    def test_main_requires_date_or_nota(self):
        with self.assertRaises(SystemExit):
            main(["--outdir", self.tmpdir.name])

    @patch("dofjson.client.download_pdf")
    def test_main_downloads_pdf_by_diario(self, mock_download_pdf):
        main(["--pdf-diario", "208439", "--outdir", self.tmpdir.name])

        expected_dest = Path(self.tmpdir.name) / "208439.pdf"
        mock_download_pdf.assert_called_once_with(208439, expected_dest)

    @patch("dofjson.client.get_imagenes")
    def test_main_fetches_imagenes_by_diario(self, mock_get_imagenes):
        mock_get_imagenes.return_value = {"messageCode": 200, "imagenesFS": []}

        main(["--imagenes-diario", "208439", "--outdir", self.tmpdir.name])

        dest = Path(self.tmpdir.name) / "208439-imagenes.json"
        self.assertTrue(dest.exists())
        self.assertEqual(json.loads(dest.read_text()), mock_get_imagenes.return_value)
        mock_get_imagenes.assert_called_once_with(208439)

    @patch("dofjson.client.download_imagen")
    def test_main_downloads_single_imagen(self, mock_download_imagen):
        main([
            "--imagen", "19800102-02-U-000", "--edicion", "MAT",
            "--outdir", self.tmpdir.name,
        ])

        expected_dest = Path(self.tmpdir.name) / "19800102-02-U-000.jpg"
        mock_download_imagen.assert_called_once_with(
            "19800102-02-U-000", "MAT", expected_dest
        )

    def test_main_imagen_requires_edicion(self):
        with self.assertRaises(SystemExit):
            main(["--imagen", "19800102-02-U-000", "--outdir", self.tmpdir.name])


if __name__ == "__main__":
    unittest.main()

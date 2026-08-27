import datetime as dt
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dofjson import titulos
from dofjson.cli import main


def hacer_tgz(archivos: dict) -> bytes:
    """Build an in-memory notas-YYYY.tgz from {member_name: dict_contenido}
    -- same shape tests/test_titulos.py's own helper builds."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for nombre, contenido in archivos.items():
            data = json.dumps(contenido).encode("utf-8")
            info = tarfile.TarInfo(name=nombre)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class TestCli(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmpdir.cleanup()

    @patch("dofjson.sidof.get_notas")
    def test_main_saves_json_to_outdir(self, mock_get_notas):
        mock_get_notas.return_value = {
            "messageCode": 200, "response": "OK",
            "NotasMatutinas": [{"codNota": 1, "titulo": "DECRETO"}],
            "NotasVespertinas": [], "NotasExtraordinarias": [],
        }

        main(["2026-07-16", "--endpoint", "notas", "--outdir", self.tmpdir.name])

        dest = Path(self.tmpdir.name) / "16072026-notas.json"
        self.assertTrue(dest.exists())
        guardado = json.loads(dest.read_text())
        self.assertEqual(len(guardado["NotasMatutinas"]), 1)
        self.assertEqual(guardado["fuente"], "sidof")
        mock_get_notas.assert_called_once()

    @patch("dofjson.dofweb.get_notas")
    @patch("dofjson.sidof.get_notas")
    def test_a_day_sidof_lost_is_taken_from_the_dof_website(self, mock_sidof, mock_web):
        # SIDOF answers 200 with no notes for the days it has lost, just as it
        # does for a day with no edition — so an empty answer gets a second
        # opinion before it is saved.
        mock_sidof.return_value = {
            "messageCode": 200, "response": "OK",
            "NotasMatutinas": [], "NotasVespertinas": [], "NotasExtraordinarias": [],
        }
        mock_web.return_value = {
            "messageCode": 200, "response": "OK", "fuente": "dof.gob.mx",
            "NotasMatutinas": [{"codNota": 4997854, "titulo": "Decreto"}],
            "NotasVespertinas": [], "NotasExtraordinarias": [],
            "edicionesSinIndice": [],
        }

        main(["1999-03-08", "--endpoint", "notas", "--outdir", self.tmpdir.name])

        guardado = json.loads(
            (Path(self.tmpdir.name) / "08031999-notas.json").read_text()
        )
        self.assertEqual(guardado["fuente"], "dof.gob.mx")
        self.assertEqual(guardado["NotasMatutinas"][0]["codNota"], 4997854)

    @patch("dofjson.dofweb.get_notas")
    @patch("dofjson.sidof.get_notas")
    def test_respaldo_nunca_saves_sidofs_empty_answer_as_is(self, mock_sidof, mock_web):
        mock_sidof.return_value = {
            "messageCode": 200, "response": "OK",
            "NotasMatutinas": [], "NotasVespertinas": [], "NotasExtraordinarias": [],
        }

        main([
            "1999-03-08", "--endpoint", "notas", "--respaldo", "nunca",
            "--outdir", self.tmpdir.name,
        ])

        mock_web.assert_not_called()
        guardado = json.loads(
            (Path(self.tmpdir.name) / "08031999-notas.json").read_text()
        )
        self.assertEqual(guardado["NotasMatutinas"], [])
        # Tagged "sidof" even though it is empty: `fuente` now always names
        # which source the returned answer actually is (see
        # dofjson.api.get_notas()), not just which one had notes.
        self.assertEqual(guardado["fuente"], "sidof")

    def test_main_rejects_invalid_date(self):
        with self.assertRaises(SystemExit):
            main(["16-07-2026", "--outdir", self.tmpdir.name])

    @patch("dofjson.api.download_nota")
    def test_main_downloads_single_nota_by_id(self, mock_download_nota):
        mock_download_nota.return_value = [
            Path(self.tmpdir.name) / "nota-4845455-19800102-21-U-000.jpg",
            Path(self.tmpdir.name) / "nota-4845455-19800102-22-U-000.jpg",
        ]

        main(["--nota", "4845455", "--outdir", self.tmpdir.name])

        mock_download_nota.assert_called_once_with(4845455, Path(self.tmpdir.name))

    @patch("dofjson.api.download_nota_imagenes")
    def test_main_downloads_nota_imagenes_by_id(self, mock_download_nota_imagenes):
        mock_download_nota_imagenes.return_value = [
            Path(self.tmpdir.name) / "nota-5793655-20260715-080-U-000.jpg",
        ]

        main(["--nota-imagenes", "5793655", "--outdir", self.tmpdir.name])

        mock_download_nota_imagenes.assert_called_once_with(
            5793655, Path(self.tmpdir.name)
        )

    @patch("dofjson.api.download_nota_pdf")
    def test_main_downloads_nota_pdf_by_id(self, mock_download_nota_pdf):
        mock_download_nota_pdf.return_value = Path(self.tmpdir.name) / "nota-5793639.pdf"

        main(["--nota-pdf", "5793639", "--outdir", self.tmpdir.name])

        mock_download_nota_pdf.assert_called_once_with(5793639, Path(self.tmpdir.name))

    def test_main_requires_date_or_nota(self):
        with self.assertRaises(SystemExit):
            main(["--outdir", self.tmpdir.name])

    @patch("dofjson.sidof.download_pdf")
    def test_main_downloads_pdf_by_diario(self, mock_download_pdf):
        main(["--pdf-diario", "208439", "--outdir", self.tmpdir.name])

        expected_dest = Path(self.tmpdir.name) / "208439.pdf"
        mock_download_pdf.assert_called_once_with(208439, expected_dest)

    @patch("dofjson.sidof.get_imagenes")
    def test_main_fetches_imagenes_by_diario(self, mock_get_imagenes):
        mock_get_imagenes.return_value = {"messageCode": 200, "imagenesFS": []}

        main(["--imagenes-diario", "208439", "--outdir", self.tmpdir.name])

        dest = Path(self.tmpdir.name) / "208439-imagenes.json"
        self.assertTrue(dest.exists())
        self.assertEqual(json.loads(dest.read_text()), mock_get_imagenes.return_value)
        mock_get_imagenes.assert_called_once_with(208439)

    @patch("dofjson.sidof.download_imagen")
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

    @patch("dofjson.api.download_edicion_pdf")
    def test_main_downloads_pdf_by_edicion_date(self, mock_download_edicion_pdf):
        mock_download_edicion_pdf.return_value = Path(self.tmpdir.name) / "edicion-208439.pdf"

        main([
            "--pdf-edicion", "16-06-2026", "--edicion", "VES",
            "--outdir", self.tmpdir.name,
        ])

        mock_download_edicion_pdf.assert_called_once_with(
            dt.date(2026, 6, 16), "VES", Path(self.tmpdir.name)
        )

    def test_main_pdf_edicion_requires_edicion(self):
        with self.assertRaises(SystemExit):
            main(["--pdf-edicion", "16-06-2026", "--outdir", self.tmpdir.name])

    def test_main_pdf_edicion_invalid_date(self):
        with self.assertRaises(SystemExit):
            main([
                "--pdf-edicion", "2026-06-16", "--edicion", "VES",
                "--outdir", self.tmpdir.name,
            ])


class TestCliCacheDir(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.cache_tmpdir = tempfile.TemporaryDirectory()
        self.cache_dir = Path(self.cache_tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()
        self.cache_tmpdir.cleanup()

    def _cachear(self):
        contenido = hacer_tgz({"1980/02011980-notas.json": {
            "NotasMatutinas": [{"codNota": 1, "titulo": "DECRETO cacheado"}],
            "NotasVespertinas": [], "NotasExtraordinarias": [],
        }})
        (self.cache_dir / "notas-1980.tgz").write_bytes(contenido)

    @patch("dofjson.sidof.get_notas")
    def test_cache_dir_flag_reads_a_cached_day_without_sidof(self, mock_sidof):
        self._cachear()

        main([
            "1980-01-02", "--endpoint", "notas", "--outdir", self.tmpdir.name,
            "--cache-dir", str(self.cache_dir),
        ])

        mock_sidof.assert_not_called()
        guardado = json.loads(
            (Path(self.tmpdir.name) / "02011980-notas.json").read_text()
        )
        self.assertEqual(guardado["NotasMatutinas"], [{"codNota": 1, "titulo": "DECRETO cacheado"}])

    @patch("dofjson.sidof.get_notas")
    def test_omitting_cache_dir_still_uses_the_cache_dir_global(self, mock_sidof):
        self._cachear()

        with patch.object(titulos, "CACHE_DIR", self.cache_dir):
            main(["1980-01-02", "--endpoint", "notas", "--outdir", self.tmpdir.name])

        mock_sidof.assert_not_called()

    @patch("dofjson.sidof.get_notas")
    def test_cache_dir_none_always_fetches_live(self, mock_sidof):
        self._cachear()
        mock_sidof.return_value = {
            "NotasMatutinas": [{"codNota": 9, "titulo": "Nota en vivo"}],
            "NotasVespertinas": [], "NotasExtraordinarias": [],
        }

        with patch.object(titulos, "CACHE_DIR", self.cache_dir):
            main([
                "1980-01-02", "--endpoint", "notas", "--outdir", self.tmpdir.name,
                "--cache-dir", "none",
            ])

        mock_sidof.assert_called_once()


if __name__ == "__main__":
    unittest.main()

import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dofjson.client import (
    BASE_URL,
    download_imagen,
    download_nota,
    download_pdf,
    get_diario,
    get_imagenes,
    get_indicadores,
    get_nota,
    get_notas,
    infer_paginas,
)


class TestClient(unittest.TestCase):
    def _mock_response(self, payload):
        mock_response = MagicMock()
        mock_response.json.return_value = payload
        mock_response.raise_for_status = MagicMock()
        return mock_response

    @patch("dofjson.client.requests.get")
    def test_get_diario_builds_expected_url(self, mock_get):
        mock_get.return_value = self._mock_response({"messageCode": 200})

        result = get_diario(dt.date(2026, 7, 16))

        mock_get.assert_called_once()
        (url,), _ = mock_get.call_args
        self.assertEqual(url, f"{BASE_URL}/diarios/porFecha/16-07-2026")
        self.assertEqual(result, {"messageCode": 200})

    @patch("dofjson.client.requests.get")
    def test_get_notas_builds_expected_url(self, mock_get):
        mock_get.return_value = self._mock_response({"messageCode": 200})

        get_notas(dt.date(2026, 1, 5))

        (url,), _ = mock_get.call_args
        self.assertEqual(url, f"{BASE_URL}/notas/05-01-2026")

    @patch("dofjson.client.requests.get")
    def test_get_nota_builds_expected_url(self, mock_get):
        mock_get.return_value = self._mock_response({"messageCode": 200})

        get_nota(5793717)

        (url,), _ = mock_get.call_args
        self.assertEqual(url, f"{BASE_URL}/notas/nota/5793717")

    @patch("dofjson.client.requests.get")
    def test_get_indicadores_builds_expected_url(self, mock_get):
        mock_get.return_value = self._mock_response({"messageCode": 200})

        get_indicadores(dt.date(2026, 7, 16))

        (url,), _ = mock_get.call_args
        self.assertEqual(url, f"{BASE_URL}/indicadores/16-07-2026")

    @patch("dofjson.client.requests.get")
    def test_propagates_http_errors(self, mock_get):
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = Exception("500 Server Error")
        mock_get.return_value = mock_response

        with self.assertRaises(Exception):
            get_diario(dt.date(2026, 7, 16))

    @patch("dofjson.client.requests.get")
    def test_get_imagenes_builds_expected_url(self, mock_get):
        mock_get.return_value = self._mock_response({"messageCode": 200})

        get_imagenes(208439)

        (url,), _ = mock_get.call_args
        self.assertEqual(url, f"{BASE_URL}/imagenesFsRecurso/obtieneImagenesFS/208439")


class TestDownloadPdf(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.dest = Path(self.tmpdir.name) / "edicion.pdf"

    def tearDown(self):
        self.tmpdir.cleanup()

    @patch("dofjson.client.requests.get")
    def test_download_pdf_writes_valid_pdf(self, mock_get):
        mock_response = MagicMock()
        mock_response.content = b"%PDF-1.4 fake test content"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        download_pdf(208439, self.dest)

        (url,), _ = mock_get.call_args
        self.assertEqual(url, f"{BASE_URL}/documentos/pdf/208439")
        self.assertEqual(self.dest.read_bytes(), mock_response.content)

    @patch("dofjson.client.requests.get")
    def test_download_pdf_rejects_non_pdf_content(self, mock_get):
        mock_response = MagicMock()
        mock_response.content = b"<html>404 not found</html>"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        with self.assertRaises(ValueError):
            download_pdf(208439, self.dest)

        self.assertFalse(self.dest.exists())


class TestDownloadImagen(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.dest = Path(self.tmpdir.name) / "pagina.jpg"

    def tearDown(self):
        self.tmpdir.cleanup()

    @patch("dofjson.client.requests.get")
    def test_download_imagen_writes_valid_jpeg(self, mock_get):
        mock_response = MagicMock()
        mock_response.content = b"\xff\xd8\xff\xe0 fake test content"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        download_imagen("19800102-02-U-000", "MAT", self.dest)

        (url,), _ = mock_get.call_args
        self.assertEqual(url, f"{BASE_URL}/copiaCertificada/MAT/19800102-02-U-000.jpg")
        self.assertEqual(self.dest.read_bytes(), mock_response.content)

    @patch("dofjson.client.requests.get")
    def test_download_imagen_rejects_non_jpeg_content(self, mock_get):
        mock_response = MagicMock()
        mock_response.content = b"<html>404 not found</html>"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        with self.assertRaises(ValueError):
            download_imagen("19800102-02-U-000", "MAT", self.dest)

        self.assertFalse(self.dest.exists())


class TestInferPaginas(unittest.TestCase):
    def _notas_del_dia(self, paginas_ordenadas):
        return {
            "NotasMatutinas": [
                {"codNota": 1000 + i, "pagina": pagina}
                for i, pagina in enumerate(paginas_ordenadas)
            ]
        }

    def test_single_page_when_next_nota_same_page(self):
        notas_del_dia = self._notas_del_dia([20, 20, 21, 21, 22])
        nota = {"codNota": 1002, "codEdicion": "MAT", "pagina": 21}

        self.assertEqual(infer_paginas(nota, notas_del_dia), [21])

    def test_spans_two_pages_when_next_nota_is_next_page(self):
        # Reproduces codNota=4845455 (pagina 21) followed by codNota=4845457
        # (pagina 22) on 02-01-1980.
        notas_del_dia = self._notas_del_dia([20, 20, 21, 22, 23])
        nota = {"codNota": 1002, "codEdicion": "MAT", "pagina": 21}

        self.assertEqual(infer_paginas(nota, notas_del_dia), [21, 22])

    def test_last_nota_of_the_day_is_a_single_page(self):
        notas_del_dia = self._notas_del_dia([20, 21, 22])
        nota = {"codNota": 1002, "codEdicion": "MAT", "pagina": 22}

        self.assertEqual(infer_paginas(nota, notas_del_dia), [22])

    def test_resorts_by_codnota_when_raw_list_and_orden_dont_match_page_order(self):
        # Real API response shape for 02-01-1980: raw list order and `orden`
        # do not match page order, but codNota ascending does — infer_paginas
        # must re-sort by codNota, not rely on list order or `orden`.
        notas_del_dia = {
            "NotasMatutinas": [
                {"codNota": 4845457, "pagina": 22, "orden": 0.0},
                {"codNota": 4845424, "pagina": 2, "orden": 2.0},
                {"codNota": 4845455, "pagina": 21, "orden": 0.0},
            ]
        }
        nota = {"codNota": 4845455, "codEdicion": "MAT", "pagina": 21}

        self.assertEqual(infer_paginas(nota, notas_del_dia), [21, 22])


class TestDownloadNota(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.outdir = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    @patch("dofjson.client.get_imagenes")
    @patch("dofjson.client.get_nota")
    def test_saves_json_when_content_exists(self, mock_get_nota, mock_get_imagenes):
        mock_get_nota.return_value = {
            "Nota": {"codNota": 5793719, "cadenaContenido": "<HTML>...</HTML>"}
        }

        dests = download_nota(5793719, self.outdir)

        self.assertEqual(dests, [self.outdir / "nota-5793719.json"])
        self.assertTrue(dests[0].exists())
        mock_get_imagenes.assert_not_called()

    @patch("dofjson.client.download_imagen")
    @patch("dofjson.client.get_imagenes")
    @patch("dofjson.client.get_notas")
    @patch("dofjson.client.get_nota")
    def test_falls_back_to_single_imagen_when_no_content(
        self, mock_get_nota, mock_get_notas, mock_get_imagenes, mock_download_imagen
    ):
        mock_get_nota.return_value = {
            "Nota": {
                "codNota": 4845424,
                "cadenaContenido": "",
                "codDiario": 208439,
                "fecha": "02-01-1980",
                "pagina": 2,
                "codEdicion": "MAT",
            }
        }
        mock_get_notas.return_value = {
            "NotasMatutinas": [
                {"codNota": 4845424, "pagina": 2},
                {"codNota": 4845426, "pagina": 2},
            ]
        }
        mock_get_imagenes.return_value = {
            "imagenesFS": [
                {"pagina": 2, "nombreArchivo": "19800102-02-U-000"},
            ]
        }

        dests = download_nota(4845424, self.outdir)

        mock_get_notas.assert_called_once_with(dt.date(1980, 1, 2))
        mock_get_imagenes.assert_called_once_with(208439)
        mock_download_imagen.assert_called_once_with(
            "19800102-02-U-000", "MAT", self.outdir / "nota-4845424-19800102-02-U-000.jpg"
        )
        self.assertEqual(dests, [self.outdir / "nota-4845424-19800102-02-U-000.jpg"])

    @patch("dofjson.client.download_imagen")
    @patch("dofjson.client.get_imagenes")
    @patch("dofjson.client.get_notas")
    @patch("dofjson.client.get_nota")
    def test_falls_back_to_two_imagenes_when_nota_spans_two_pages(
        self, mock_get_nota, mock_get_notas, mock_get_imagenes, mock_download_imagen
    ):
        mock_get_nota.return_value = {
            "Nota": {
                "codNota": 4845455,
                "cadenaContenido": "",
                "codDiario": 208439,
                "fecha": "02-01-1980",
                "pagina": 21,
                "codEdicion": "MAT",
            }
        }
        mock_get_notas.return_value = {
            "NotasMatutinas": [
                {"codNota": 4845455, "pagina": 21},
                {"codNota": 4845457, "pagina": 22},
            ]
        }
        mock_get_imagenes.return_value = {
            "imagenesFS": [
                {"pagina": 21, "nombreArchivo": "19800102-21-U-000"},
                {"pagina": 22, "nombreArchivo": "19800102-22-U-000"},
            ]
        }

        dests = download_nota(4845455, self.outdir)

        self.assertEqual(mock_download_imagen.call_count, 2)
        self.assertEqual(
            dests,
            [
                self.outdir / "nota-4845455-19800102-21-U-000.jpg",
                self.outdir / "nota-4845455-19800102-22-U-000.jpg",
            ],
        )

    @patch("dofjson.client.get_imagenes")
    @patch("dofjson.client.get_notas")
    @patch("dofjson.client.get_nota")
    def test_raises_when_no_matching_page(
        self, mock_get_nota, mock_get_notas, mock_get_imagenes
    ):
        mock_get_nota.return_value = {
            "Nota": {
                "codNota": 4845424,
                "cadenaContenido": "",
                "codDiario": 208439,
                "fecha": "02-01-1980",
                "pagina": 2,
                "codEdicion": "MAT",
            }
        }
        mock_get_notas.return_value = {
            "NotasMatutinas": [{"codNota": 4845424, "pagina": 2}]
        }
        mock_get_imagenes.return_value = {"imagenesFS": []}

        with self.assertRaises(ValueError):
            download_nota(4845424, self.outdir)


if __name__ == "__main__":
    unittest.main()

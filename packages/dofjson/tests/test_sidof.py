import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dofjson.sidof import (
    BASE_URL,
    download_imagen,
    download_pdf,
    get_diario,
    get_imagenes,
    get_indicadores,
    get_nota,
    get_notas,
)


class TestClient(unittest.TestCase):
    def _mock_response(self, payload):
        mock_response = MagicMock()
        mock_response.json.return_value = payload
        mock_response.raise_for_status = MagicMock()
        return mock_response

    @patch("dofjson.sidof.requests.get")
    def test_get_diario_builds_expected_url(self, mock_get):
        mock_get.return_value = self._mock_response({"messageCode": 200})

        result = get_diario(dt.date(2026, 7, 16))

        mock_get.assert_called_once()
        (url,), _ = mock_get.call_args
        self.assertEqual(url, f"{BASE_URL}/diarios/porFecha/16-07-2026")
        self.assertEqual(result, {"messageCode": 200})

    @patch("dofjson.sidof.requests.get")
    def test_get_notas_builds_expected_url(self, mock_get):
        mock_get.return_value = self._mock_response({"messageCode": 200})

        get_notas(dt.date(2026, 1, 5))

        (url,), _ = mock_get.call_args
        self.assertEqual(url, f"{BASE_URL}/notas/05-01-2026")

    @patch("dofjson.sidof.requests.get")
    def test_get_nota_builds_expected_url(self, mock_get):
        mock_get.return_value = self._mock_response({"messageCode": 200})

        get_nota(5793717)

        (url,), _ = mock_get.call_args
        self.assertEqual(url, f"{BASE_URL}/notas/nota/5793717")

    @patch("dofjson.sidof.requests.get")
    def test_get_indicadores_builds_expected_url(self, mock_get):
        mock_get.return_value = self._mock_response({"messageCode": 200})

        get_indicadores(dt.date(2026, 7, 16))

        (url,), _ = mock_get.call_args
        self.assertEqual(url, f"{BASE_URL}/indicadores/16-07-2026")

    @patch("dofjson.sidof.requests.get")
    def test_propagates_http_errors(self, mock_get):
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = Exception("500 Server Error")
        mock_get.return_value = mock_response

        with self.assertRaises(Exception):
            get_diario(dt.date(2026, 7, 16))

    @patch("dofjson.sidof.requests.get")
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

    @patch("dofjson.sidof.requests.get")
    def test_download_pdf_writes_valid_pdf(self, mock_get):
        mock_response = MagicMock()
        mock_response.content = b"%PDF-1.4 fake test content"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        download_pdf(208439, self.dest)

        (url,), _ = mock_get.call_args
        self.assertEqual(url, f"{BASE_URL}/documentos/pdf/208439")
        self.assertEqual(self.dest.read_bytes(), mock_response.content)

    @patch("dofjson.sidof.requests.get")
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

    @patch("dofjson.sidof.requests.get")
    def test_download_imagen_writes_valid_jpeg(self, mock_get):
        mock_response = MagicMock()
        mock_response.content = b"\xff\xd8\xff\xe0 fake test content"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        download_imagen("19800102-02-U-000", "MAT", self.dest)

        (url,), _ = mock_get.call_args
        self.assertEqual(url, f"{BASE_URL}/copiaCertificada/MAT/19800102-02-U-000.jpg")
        self.assertEqual(self.dest.read_bytes(), mock_response.content)

    @patch("dofjson.sidof.requests.get")
    def test_download_imagen_rejects_non_jpeg_content(self, mock_get):
        mock_response = MagicMock()
        mock_response.content = b"<html>404 not found</html>"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        with self.assertRaises(ValueError):
            download_imagen("19800102-02-U-000", "MAT", self.dest)

        self.assertFalse(self.dest.exists())


if __name__ == "__main__":
    unittest.main()

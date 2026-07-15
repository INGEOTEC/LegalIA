import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dof2md.downloader import build_url, download_pdf


class TestBuildUrl(unittest.TestCase):
    def test_build_url_morning_edition(self):
        url, filename = build_url(dt.date(2010, 1, 5), "MAT")
        self.assertEqual(filename, "05012010-MAT.pdf")
        self.assertEqual(
            url,
            "https://www.dof.gob.mx/abrirPDF.php"
            "?archivo=05012010-MAT.pdf&anio=2010&repo=repositorio/",
        )

    def test_build_url_evening_edition(self):
        url, filename = build_url(dt.date(2020, 12, 31), "VES")
        self.assertEqual(filename, "31122020-VES.pdf")
        self.assertIn("archivo=31122020-VES.pdf", url)
        self.assertIn("anio=2020", url)


class TestDownloadPdf(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.dest = Path(self.tmpdir.name) / "test.pdf"

    def tearDown(self):
        self.tmpdir.cleanup()

    @patch("dof2md.downloader.requests.get")
    def test_download_pdf_writes_valid_pdf(self, mock_get):
        mock_response = MagicMock()
        mock_response.content = b"%PDF-1.4 fake test content"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        download_pdf("https://example.com/fake.pdf", self.dest)

        self.assertTrue(self.dest.exists())
        self.assertEqual(self.dest.read_bytes(), mock_response.content)
        mock_get.assert_called_once()
        _, kwargs = mock_get.call_args
        self.assertFalse(kwargs["verify"])

    @patch("dof2md.downloader.requests.get")
    def test_download_pdf_rejects_non_pdf_content(self, mock_get):
        mock_response = MagicMock()
        mock_response.content = b"<html>404 not found</html>"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        with self.assertRaises(ValueError):
            download_pdf("https://example.com/fake.pdf", self.dest)

        self.assertFalse(self.dest.exists())

    @patch("dof2md.downloader.requests.get")
    def test_download_pdf_propagates_http_errors(self, mock_get):
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = Exception("500 Server Error")
        mock_get.return_value = mock_response

        with self.assertRaises(Exception):
            download_pdf("https://example.com/fake.pdf", self.dest)

        self.assertFalse(self.dest.exists())


if __name__ == "__main__":
    unittest.main()

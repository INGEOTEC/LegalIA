import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz

from dof2md.cli import main, parse_args


def _write_digital_pdf(path: Path) -> None:
    """A born-digital PDF: real text, no full-page image."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), "Sample legal text.", fontsize=11, fontname="helv")
    doc.save(str(path))


def _write_scanned_pdf(path: Path) -> None:
    """Simulates a scan: a single image covering the whole page."""
    doc = fitz.open()
    page = doc.new_page()
    blank = fitz.open()
    blank_page = blank.new_page(width=page.rect.width, height=page.rect.height)
    pixmap = blank_page.get_pixmap()
    page.insert_image(page.rect, pixmap=pixmap)
    doc.save(str(path))


class TestParseArgs(unittest.TestCase):
    def test_defaults(self):
        args = parse_args(["2010-01-05"])
        self.assertEqual(args.date, "2010-01-05")
        self.assertEqual(args.edition, "MAT")
        self.assertEqual(args.outdir, "output")

    def test_evening_edition(self):
        args = parse_args(["2010-01-05", "--edition", "VES"])
        self.assertEqual(args.edition, "VES")

    def test_invalid_edition_raises_error(self):
        with self.assertRaises(SystemExit):
            parse_args(["2010-01-05", "--edition", "XXX"])

    def test_custom_outdir(self):
        args = parse_args(["2010-01-05", "--outdir", "/tmp/my_dof"])
        self.assertEqual(args.outdir, "/tmp/my_dof")


class TestMain(unittest.TestCase):
    @patch("dof2md.cli.convert_to_markdown")
    @patch("dof2md.cli.download_pdf")
    def test_main_orchestrates_download_and_conversion(self, mock_download, mock_convert):
        mock_download.side_effect = lambda url, dest: _write_digital_pdf(dest)
        with tempfile.TemporaryDirectory() as tmpdir:
            main(["2010-01-05", "--outdir", tmpdir])

            mock_download.assert_called_once()
            url_arg, pdf_path_arg = mock_download.call_args[0]
            self.assertIn("archivo=05012010-MAT.pdf", url_arg)
            self.assertEqual(pdf_path_arg, Path(tmpdir) / "05012010-MAT.pdf")

            mock_convert.assert_called_once_with(
                Path(tmpdir) / "05012010-MAT.pdf", Path(tmpdir) / "05012010-MAT.md"
            )

    @patch("dof2md.cli.convert_scanned_to_markdown")
    @patch("dof2md.cli.convert_to_markdown")
    @patch("dof2md.cli.download_pdf")
    def test_main_uses_ocr_for_scanned_editions(
        self, mock_download, mock_convert, mock_convert_scanned
    ):
        mock_download.side_effect = lambda url, dest: _write_scanned_pdf(dest)
        with tempfile.TemporaryDirectory() as tmpdir:
            main(["1980-01-02", "--outdir", tmpdir])

            mock_convert_scanned.assert_called_once_with(
                Path(tmpdir) / "02011980-MAT.pdf", Path(tmpdir) / "02011980-MAT.md"
            )
            mock_convert.assert_not_called()

    def test_main_invalid_date_exits_with_error(self):
        with self.assertRaises(SystemExit):
            main(["not-a-date"])


if __name__ == "__main__":
    unittest.main()

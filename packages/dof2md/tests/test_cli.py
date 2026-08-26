import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dof2md.cli import main, parse_args


class TestParseArgs(unittest.TestCase):
    def test_defaults(self):
        args = parse_args(["2010-01-05"])
        self.assertEqual(args.date, "2010-01-05")
        self.assertEqual(args.edition, "MAT")
        self.assertIsNone(args.pdf)
        self.assertIsNone(args.images)
        self.assertIsNone(args.filename)
        self.assertEqual(args.outdir, "output")
        self.assertIsNone(args.titulo)
        self.assertIsNone(args.titulo_siguiente)
        self.assertEqual(args.min_confidence, 0.6)
        self.assertFalse(args.keep_pages)
        self.assertFalse(args.keep_mineru_output)

    def test_date_is_optional(self):
        args = parse_args(["--pdf", "edicion.pdf"])
        self.assertIsNone(args.date)
        self.assertEqual(args.pdf, "edicion.pdf")

    def test_images_and_filename_flags(self):
        args = parse_args(["--images", "a.jpg", "b.jpg", "--filename", "out.md"])
        self.assertEqual(args.images, ["a.jpg", "b.jpg"])
        self.assertEqual(args.filename, "out.md")

    def test_evening_edition(self):
        args = parse_args(["2010-01-05", "--edition", "VES"])
        self.assertEqual(args.edition, "VES")

    def test_invalid_edition_raises_error(self):
        with self.assertRaises(SystemExit):
            parse_args(["2010-01-05", "--edition", "XXX"])

    def test_custom_outdir(self):
        args = parse_args(["2010-01-05", "--outdir", "/tmp/my_dof"])
        self.assertEqual(args.outdir, "/tmp/my_dof")

    def test_title_cropping_flags(self):
        args = parse_args([
            "2010-01-05",
            "--titulo", "ACUERDO por el que...",
            "--titulo-siguiente", "DECRETO por el que...",
            "--min-confidence", "0.8",
            "--keep-pages",
        ])
        self.assertEqual(args.titulo, "ACUERDO por el que...")
        self.assertEqual(args.titulo_siguiente, "DECRETO por el que...")
        self.assertEqual(args.min_confidence, 0.8)
        self.assertTrue(args.keep_pages)


class TestMain(unittest.TestCase):
    @patch("dof2md.cli.BatchConverter")
    @patch("dof2md.cli.download_pdf")
    def test_main_orchestrates_download_and_conversion(self, mock_download, mock_batch_converter):
        mock_convert = mock_batch_converter.return_value.__enter__.return_value
        with tempfile.TemporaryDirectory() as tmpdir:
            main(["2010-01-05", "--outdir", tmpdir])

            mock_download.assert_called_once()
            url_arg, pdf_path_arg = mock_download.call_args[0]
            self.assertIn("archivo=05012010-MAT.pdf", url_arg)
            self.assertEqual(pdf_path_arg, Path(tmpdir) / "05012010-MAT.pdf")

            mock_convert.assert_called_once_with(
                Path(tmpdir) / "05012010-MAT.pdf", Path(tmpdir), "05012010-MAT.md", None, None,
                min_confidence=0.6, keep_pages=False, keep_mineru_output=False,
            )

    def test_main_invalid_date_exits_with_error(self):
        with self.assertRaises(SystemExit):
            main(["not-a-date"])

    def test_main_requires_exactly_one_input_source(self):
        with self.assertRaises(SystemExit):
            main([])
        with self.assertRaises(SystemExit):
            main(["2010-01-05", "--pdf", "edicion.pdf"])

    def test_main_requires_filename_with_images(self):
        with self.assertRaises(SystemExit):
            main(["--images", "a.jpg", "b.jpg"])

    def test_main_pdf_not_found(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(SystemExit):
                main(["--pdf", str(Path(tmpdir) / "missing.pdf")])

    def test_main_images_not_found(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(SystemExit):
                main(["--images", str(Path(tmpdir) / "missing.jpg"), "--filename", "out.md"])

    @patch("dof2md.cli.BatchConverter")
    @patch("dof2md.cli.download_pdf")
    def test_main_converts_local_pdf_without_downloading(self, mock_download, mock_batch_converter):
        mock_convert = mock_batch_converter.return_value.__enter__.return_value
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "edicion.pdf"
            pdf_path.write_bytes(b"%PDF-1.4")

            main(["--pdf", str(pdf_path), "--outdir", tmpdir])

            mock_download.assert_not_called()
            mock_convert.assert_called_once_with(
                pdf_path, Path(tmpdir), "edicion.md", None, None,
                min_confidence=0.6, keep_pages=False, keep_mineru_output=False,
            )

    @patch("dof2md.cli.BatchConverter")
    @patch("dof2md.cli.download_pdf")
    def test_main_converts_local_images_without_downloading(self, mock_download, mock_batch_converter):
        mock_convert = mock_batch_converter.return_value.__enter__.return_value
        with tempfile.TemporaryDirectory() as tmpdir:
            page1 = Path(tmpdir) / "p1.jpg"
            page2 = Path(tmpdir) / "p2.jpg"
            page1.write_bytes(b"")
            page2.write_bytes(b"")

            main(["--images", str(page1), str(page2), "--filename", "out.md", "--outdir", tmpdir])

            mock_download.assert_not_called()
            mock_convert.assert_called_once_with(
                [page1, page2], Path(tmpdir), "out.md", None, None,
                min_confidence=0.6, keep_pages=False, keep_mineru_output=False,
            )

    @patch("dof2md.cli.BatchConverter")
    @patch("dof2md.cli.download_pdf")
    def test_main_passes_keep_mineru_output_flag(self, mock_download, mock_batch_converter):
        mock_convert = mock_batch_converter.return_value.__enter__.return_value
        with tempfile.TemporaryDirectory() as tmpdir:
            main(["2010-01-05", "--outdir", tmpdir, "--keep-mineru-output"])

            self.assertTrue(mock_convert.call_args.kwargs["keep_mineru_output"])

    @patch("dof2md.cli.BatchConverter")
    @patch("dof2md.cli.download_pdf")
    def test_main_passes_title_cropping_flags(self, mock_download, mock_batch_converter):
        mock_convert = mock_batch_converter.return_value.__enter__.return_value
        with tempfile.TemporaryDirectory() as tmpdir:
            main([
                "2010-01-05", "--outdir", tmpdir,
                "--titulo", "ACUERDO por el que...",
                "--titulo-siguiente", "DECRETO por el que...",
                "--min-confidence", "0.8",
                "--keep-pages",
            ])

            args, kwargs = mock_convert.call_args
            self.assertEqual(args[3], "ACUERDO por el que...")
            self.assertEqual(args[4], "DECRETO por el que...")
            self.assertEqual(kwargs["min_confidence"], 0.8)
            self.assertTrue(kwargs["keep_pages"])

    @patch("dof2md.cli.BatchConverter")
    @patch("dof2md.cli.download_pdf")
    def test_main_exits_clearly_when_conversion_times_out(self, mock_download, mock_batch_converter):
        mock_convert = mock_batch_converter.return_value.__enter__.return_value
        mock_convert.side_effect = subprocess.TimeoutExpired(cmd="mineru", timeout=3600)
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(SystemExit):
                main(["2010-01-05", "--outdir", tmpdir])


if __name__ == "__main__":
    unittest.main()

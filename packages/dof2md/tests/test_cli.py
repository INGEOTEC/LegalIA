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
        with tempfile.TemporaryDirectory() as tmpdir:
            main(["2010-01-05", "--outdir", tmpdir])

            mock_download.assert_called_once()
            url_arg, pdf_path_arg = mock_download.call_args[0]
            self.assertIn("archivo=05012010-MAT.pdf", url_arg)
            self.assertEqual(pdf_path_arg, Path(tmpdir) / "05012010-MAT.pdf")

            mock_convert.assert_called_once_with(
                Path(tmpdir) / "05012010-MAT.pdf", Path(tmpdir) / "05012010-MAT.md"
            )

    def test_main_invalid_date_exits_with_error(self):
        with self.assertRaises(SystemExit):
            main(["not-a-date"])


if __name__ == "__main__":
    unittest.main()

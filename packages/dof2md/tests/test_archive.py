import csv
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz

from dof2md.archive import (
    append_manifest_row,
    load_processed_keys,
    main,
    process_edition,
)


def _write_digital_pdf(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), "Sample legal text.", fontsize=11, fontname="helv")
    doc.save(str(path))


class TestManifest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.manifest_path = Path(self.tmpdir.name) / "manifest.csv"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_load_processed_keys_missing_file_is_empty(self):
        self.assertEqual(load_processed_keys(self.manifest_path), set())

    def test_append_then_load_round_trips(self):
        row = {
            "date": "2026-07-15",
            "edition": "MAT",
            "source_url": "https://www.dof.gob.mx/abrirPDF.php?archivo=15072026-MAT.pdf",
            "markdown_filename": "15072026-MAT.md",
            "release_tag": "dof-2026",
        }
        append_manifest_row(self.manifest_path, row)
        self.assertEqual(load_processed_keys(self.manifest_path), {("2026-07-15", "MAT")})

    def test_append_writes_header_only_once(self):
        row = {
            "date": "2026-07-15", "edition": "MAT", "source_url": "https://x",
            "markdown_filename": "a.md", "release_tag": "dof-2026",
        }
        append_manifest_row(self.manifest_path, row)
        append_manifest_row(self.manifest_path, {**row, "edition": "VES"})
        with self.manifest_path.open(newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 2)


class TestProcessEdition(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.outdir = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    @patch("dof2md.archive.convert_to_markdown")
    @patch("dof2md.archive.download_pdf")
    def test_returns_manifest_row_for_a_digital_edition(self, mock_download, mock_convert):
        mock_download.side_effect = lambda url, dest: _write_digital_pdf(dest)

        row = process_edition(dt.date(2026, 7, 15), "MAT", self.outdir)

        self.assertEqual(row["date"], "2026-07-15")
        self.assertEqual(row["edition"], "MAT")
        self.assertEqual(row["markdown_filename"], "15072026-MAT.md")
        self.assertEqual(row["release_tag"], "dof-2026")
        self.assertIn("archivo=15072026-MAT.pdf", row["source_url"])
        mock_convert.assert_called_once()

    @patch("dof2md.archive.download_pdf", side_effect=ValueError("not a PDF"))
    def test_returns_none_when_edition_does_not_exist(self, mock_download):
        row = process_edition(dt.date(2026, 7, 18), "VES", self.outdir)
        self.assertIsNone(row)

    @patch("dof2md.archive.download_pdf")
    def test_propagates_non_value_errors(self, mock_download):
        mock_download.side_effect = ConnectionError("network down")
        with self.assertRaises(ConnectionError):
            process_edition(dt.date(2026, 7, 15), "MAT", self.outdir)


class TestMain(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.base = Path(self.tmpdir.name)
        self.manifest_path = self.base / "manifest.csv"
        self.outdir = self.base / "out"
        self.produced_list = self.base / "produced.txt"

    def tearDown(self):
        self.tmpdir.cleanup()

    @patch("dof2md.archive.convert_to_markdown")
    @patch("dof2md.archive.download_pdf")
    def test_main_records_both_editions_when_available(self, mock_download, mock_convert):
        mock_download.side_effect = lambda url, dest: _write_digital_pdf(dest)

        main([
            "--date", "2026-07-15",
            "--outdir", str(self.outdir),
            "--manifest", str(self.manifest_path),
            "--produced-list", str(self.produced_list),
        ])

        keys = load_processed_keys(self.manifest_path)
        self.assertEqual(keys, {("2026-07-15", "MAT"), ("2026-07-15", "VES")})
        produced = self.produced_list.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(produced), 2)

    @patch("dof2md.archive.convert_to_markdown")
    @patch("dof2md.archive.download_pdf")
    def test_main_skips_dates_already_in_manifest(self, mock_download, mock_convert):
        mock_download.side_effect = lambda url, dest: _write_digital_pdf(dest)
        main([
            "--date", "2026-07-15",
            "--outdir", str(self.outdir),
            "--manifest", str(self.manifest_path),
            "--produced-list", str(self.produced_list),
        ])
        mock_download.reset_mock()

        main([
            "--date", "2026-07-15",
            "--outdir", str(self.outdir),
            "--manifest", str(self.manifest_path),
            "--produced-list", str(self.produced_list),
        ])

        mock_download.assert_not_called()
        self.assertEqual(self.produced_list.read_text(encoding="utf-8"), "")

    @patch("dof2md.archive.convert_to_markdown")
    @patch("dof2md.archive.download_pdf")
    def test_main_handles_weekend_with_no_editions(self, mock_download, mock_convert):
        mock_download.side_effect = ValueError("not a PDF")

        main([
            "--date", "2026-07-18",
            "--outdir", str(self.outdir),
            "--manifest", str(self.manifest_path),
            "--produced-list", str(self.produced_list),
        ])

        self.assertEqual(load_processed_keys(self.manifest_path), set())
        self.assertEqual(self.produced_list.read_text(encoding="utf-8"), "")


if __name__ == "__main__":
    unittest.main()

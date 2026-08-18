import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nota2md.cli import main


class TestCli(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.outdir = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    @patch("nota2md.cli.legal_provisions")
    def test_main_builds_note_with_defaults(self, mock_build):
        mock_build.return_value = self.outdir / "nota-5793655.md"

        main(["5793655", "--outdir", str(self.outdir)])

        mock_build.assert_called_once_with(
            5793655,
            self.outdir,
            source="auto",
            fecha=None,
            notas_del_dia=None,
            min_confidence=0.6,
            keep_pages=False,
            keep_mineru_output=False,
        )

    @patch("nota2md.cli.legal_provisions")
    def test_main_passes_fecha_for_codigos_the_website_needs_it_for(self, mock_build):
        # See issue #109/#111: some 1999-2000 codigos only resolve on the
        # DOF website alongside their own date.
        mock_build.return_value = self.outdir / "nota-4920760.md"

        main(["4920760", "--fecha", "2000-02-29", "--outdir", str(self.outdir)])

        _, kwargs = mock_build.call_args
        self.assertEqual(kwargs["fecha"], dt.date(2000, 2, 29))

    @patch("nota2md.cli.legal_provisions")
    def test_main_passes_source_and_loads_notas_file(self, mock_build):
        notas = {"NotasMatutinas": [{"codNota": 5793655, "titulo": "T"}]}
        notas_path = self.outdir / "15072026-notas.json"
        notas_path.write_text(json.dumps(notas), encoding="utf-8")
        mock_build.return_value = self.outdir / "nota-5793655.md"

        main([
            "5793655", "--source", "image", "--notas", str(notas_path),
            "--min-confidence", "0.8", "--keep-pages", "--keep-mineru-output",
            "--outdir", str(self.outdir),
        ])

        _, kwargs = mock_build.call_args
        self.assertEqual(kwargs["source"], "image")
        self.assertEqual(kwargs["notas_del_dia"], notas)
        self.assertEqual(kwargs["min_confidence"], 0.8)
        self.assertTrue(kwargs["keep_pages"])
        self.assertTrue(kwargs["keep_mineru_output"])


if __name__ == "__main__":
    unittest.main()

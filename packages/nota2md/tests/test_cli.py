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

    @patch("nota2md.cli.build_nota_markdown")
    def test_main_builds_note_with_defaults(self, mock_build):
        mock_build.return_value = self.outdir / "nota-5793655.md"

        main(["5793655", "--outdir", str(self.outdir)])

        mock_build.assert_called_once_with(
            5793655,
            self.outdir,
            source="auto",
            notas_del_dia=None,
            min_confidence=0.6,
            keep_pages=False,
        )

    @patch("nota2md.cli.build_nota_markdown")
    def test_main_passes_source_and_loads_notas_file(self, mock_build):
        notas = {"NotasMatutinas": [{"codNota": 5793655, "titulo": "T"}]}
        notas_path = self.outdir / "15072026-notas.json"
        notas_path.write_text(json.dumps(notas), encoding="utf-8")
        mock_build.return_value = self.outdir / "nota-5793655.md"

        main([
            "5793655", "--source", "image", "--notas", str(notas_path),
            "--min-confidence", "0.8", "--keep-pages", "--outdir", str(self.outdir),
        ])

        _, kwargs = mock_build.call_args
        self.assertEqual(kwargs["source"], "image")
        self.assertEqual(kwargs["notas_del_dia"], notas)
        self.assertEqual(kwargs["min_confidence"], 0.8)
        self.assertTrue(kwargs["keep_pages"])


if __name__ == "__main__":
    unittest.main()

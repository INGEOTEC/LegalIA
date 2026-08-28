import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nota2md import cache
from nota2md.cli import main, parse_args
from nota2md.cli import _resolver_cache_dir


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
            instrumento=None,
            cache_dir=cache.SIN_CACHE_DIR,
            refrescar=False,
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

    @patch("nota2md.cli.legal_provisions")
    def test_main_pasa_las_banderas_de_la_ruta_scjn(self, mock_build):
        mock_build.return_value = self.outdir / "lfca-05-01-1999.md"

        main([
            "4967917", "--source", "dof", "--instrumento", "lfca",
            "--cache-dir", str(self.outdir), "--refrescar",
            "--outdir", str(self.outdir),
        ])

        _, kwargs = mock_build.call_args
        self.assertEqual(kwargs["source"], "dof")
        self.assertEqual(kwargs["instrumento"], "lfca")
        self.assertEqual(kwargs["cache_dir"], self.outdir)
        self.assertTrue(kwargs["refrescar"])

    def test_source_dof_es_una_opcion_valida(self):
        self.assertEqual(parse_args(["1"]).source, "auto")
        self.assertEqual(parse_args(["1", "--source", "dof"]).source, "dof")


class TestResolverCacheDir(unittest.TestCase):
    """--cache-dir sigue la misma convención que `dofjson.cli`."""

    def test_no_dado_deja_que_nota2md_use_su_propio_default(self):
        self.assertIs(_resolver_cache_dir(None), cache.SIN_CACHE_DIR)

    def test_none_salta_la_cache_por_completo(self):
        self.assertIsNone(_resolver_cache_dir("none"))

    def test_una_ruta_se_usa_tal_cual(self):
        self.assertEqual(_resolver_cache_dir("/tmp/mia"), Path("/tmp/mia"))


if __name__ == "__main__":
    unittest.main()

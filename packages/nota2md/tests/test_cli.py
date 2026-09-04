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
    def test_sin_outdir_deja_que_el_builder_escriba_en_el_cache(self, mock_build):
        # Issue #165: --outdir dejo de ser obligatorio; omitirlo es
        # outdir=None, y la ruta resultante se imprime.
        mock_build.return_value = self.outdir / "ccf-14-11-2025.md"

        main(["5773097"])

        self.assertIsNone(mock_build.call_args.args[1])

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


class TestCliDownload(unittest.TestCase):
    """`nota2md download ...` (issue #155): the two GitHub releases on disk,
    each into its own package's cache directory."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.destino = Path(self.tmpdir.name)
        self.log = []

    def tearDown(self):
        self.tmpdir.cleanup()

    @patch("scjn.release.download_scjn_leyes_assets")
    def test_federal_laws_baja_indice_y_tarballs(self, mock_assets):
        ruta = self.destino / "scjn-leyes" / "indice-global.json.gz"
        mock_assets.return_value = [(ruta, True)]

        main(["download", "federal-laws", "--cache-dir", str(self.destino)])

        args, kwargs = mock_assets.call_args
        self.assertIsNone(args[0])
        self.assertEqual(kwargs["cache_dir"], self.destino)
        self.assertFalse(kwargs["refrescar"])

    @patch("scjn.release.download_scjn_leyes_assets")
    def test_federal_laws_acota_con_slug_repetible(self, mock_assets):
        mock_assets.return_value = [(self.destino / "lft.tgz", False)]

        main(["download", "federal-laws", "--slug", "lft", "--slug", "lfca",
              "--cache-dir", str(self.destino), "--refrescar"])

        args, kwargs = mock_assets.call_args
        self.assertEqual(args[0], ["lft", "lfca"])
        self.assertTrue(kwargs["refrescar"])

    @patch("scjn.release.download_scjn_leyes_assets")
    def test_federal_laws_sin_cache_dir_usa_el_default_de_scjn(self, mock_assets):
        # Issue #209: sin --cache-dir explicito esto ya no usa el cache de
        # nota2md -- delega en scjn.cache.CACHE_DIR (None es como
        # scjn.release lo pide).
        mock_assets.return_value = [(self.destino / "x.tgz", True)]

        main(["download", "federal-laws"])

        self.assertIsNone(mock_assets.call_args.kwargs["cache_dir"])

    def test_federal_laws_rechaza_cache_dir_none(self):
        # "no cache" and "write the release to disk" cannot both hold.
        with self.assertRaises(SystemExit):
            main(["download", "federal-laws", "--cache-dir", "none"])

    @patch("dofjson.titulos.download_dof_assets")
    def test_gazette_metadata_escribe_en_la_cache_de_dofjson(self, mock_dof):
        mock_dof.return_value = [self.destino / "notas-1917.tgz"]

        main(["download", "gazette-metadata", "--cache-dir", str(self.destino)])

        args, kwargs = mock_dof.call_args
        self.assertEqual(args[0], self.destino)
        self.assertFalse(kwargs["refrescar"])

    @patch("dofjson.titulos.download_dof_assets")
    def test_gazette_metadata_sin_cache_dir_usa_la_de_dofjson(self, mock_dof):
        from dofjson.titulos import CACHE_DIR as DOFJSON_CACHE_DIR

        mock_dof.return_value = []

        main(["download", "gazette-metadata"])

        self.assertEqual(mock_dof.call_args.args[0], Path(DOFJSON_CACHE_DIR))

    @patch("dofjson.titulos.download_dof_assets")
    @patch("scjn.release.download_scjn_leyes_assets")
    def test_all_baja_los_dos_releases(self, mock_assets, mock_dof):
        mock_assets.return_value = [(self.destino / "scjn-leyes" / "a.tgz", True)]
        mock_dof.return_value = [self.destino / "notas-1917.tgz"]

        main(["download", "all"])

        mock_assets.assert_called_once()
        mock_dof.assert_called_once()
        # Each release keeps its own cache: `all` is a shorthand for two
        # invocations, not a merge of the two directories.
        self.assertIsNone(mock_assets.call_args.kwargs["cache_dir"])
        self.assertFalse(mock_dof.call_args.kwargs["refrescar"])

    @patch("scjn.release.download_scjn_leyes_assets")
    def test_reporta_por_asset_si_se_descargo_o_ya_estaba(self, mock_assets):
        directorio = self.destino / "scjn-leyes"
        mock_assets.return_value = [
            (directorio / "indice-global.json.gz", True),
            (directorio / "lft.tgz", False),
        ]

        from nota2md.cli import _descarga_federal_laws

        _descarga_federal_laws(None, self.destino, False, log=self.log.append)

        self.assertIn("1 downloaded, 1 already cached", self.log[-1])


class TestCliDispatch(unittest.TestCase):
    """The verbless build form predates `download` and keeps working."""

    def test_un_codnota_suelto_sigue_siendo_el_comando_por_omision(self):
        args = parse_args(["5793655", "--outdir", "salida"])
        self.assertIsNone(args.comando)
        self.assertEqual(args.cod_nota, 5793655)
        self.assertEqual(args.outdir, "salida")

    def test_el_verbo_build_explicito_es_equivalente(self):
        implicito = parse_args(["5793655", "--source", "dof"])
        explicito = parse_args(["build", "5793655", "--source", "dof"])
        self.assertEqual(explicito.cod_nota, implicito.cod_nota)
        self.assertEqual(explicito.source, implicito.source)
        self.assertEqual(explicito.comando, "build")

    @patch("nota2md.cli.legal_provisions")
    def test_main_con_el_verbo_build_construye_igual(self, mock_build):
        mock_build.return_value = Path("nota-5793655.md")

        main(["build", "5793655", "--outdir", "salida"])

        self.assertEqual(mock_build.call_args.args[0], 5793655)

    def test_download_se_parsea_como_subcomando(self):
        args = parse_args(["download", "federal-laws"])
        self.assertEqual(args.comando, "download")
        self.assertEqual(args.release, "federal-laws")

    def test_sin_argumentos_pide_un_codnota_o_un_subcomando(self):
        with self.assertRaises(SystemExit):
            parse_args([])


if __name__ == "__main__":
    unittest.main()

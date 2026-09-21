"""`legalvec`'s two verbs, with the network patched out.

No test here touches the network: `legalvec.cache.assets_of_series` and
`legalvec.cache.download` are patched exactly the way
`packages/legalvec/tests/test_release.py` patches them, and the `status`
tests assert both were never called at all — the whole point of that verb is
that it answers from the directory.

Downloading a real collection is not something a test may do under any
patch: the three releases are ~2.08 GB across ~3,067 assets.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from legalvec import cache, cli, release


class ConRedParchada(unittest.TestCase):
    """Every test in this file runs with the HTTP layer patched out, so a
    CLI that reached for the network on its own would fail rather than
    quietly download something."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp))
        self.assets = {
            nombre: f"https://example.invalid/{nombre}" for nombre in (
                release.METADATA_ASSETS
                + ("vectors-lft-qwen3-0.6b-1024.parquet",
                   "vectors-lif-2026-qwen3-0.6b-1024.parquet",
                   "vectors-shared-qwen3-0.6b-1024.parquet",
                   "vectors-lft-qwen3-4b-2560.parquet",
                   "vectors-shared-qwen3-4b-2560.parquet")
            )
        }
        self.assets_of_series = patch.object(
            cache, "assets_of_series", return_value=self.assets)
        self.download = patch.object(
            cache, "download", side_effect=lambda url, timeout: json.dumps(
                {"url": url}).encode("utf-8"))
        self.mock_assets = self.assets_of_series.start()
        self.mock_download = self.download.start()
        self.addCleanup(self.assets_of_series.stop)
        self.addCleanup(self.download.stop)


class TestDownload(ConRedParchada):
    """`download` is an argparse front end and nothing more: what it is
    tested for is which call it makes, not what that call does."""

    def setUp(self):
        super().setUp()
        self.descarga = patch.object(
            cli, "download_vectors_assets",
            return_value=[(self.tmp / "scjn-leyes-vectors" / "units.parquet", True)],
        )
        self.mock_descarga = self.descarga.start()
        self.addCleanup(self.descarga.stop)

    def test_sin_flags_baja_las_tres_colecciones_con_los_dos_modelos(self):
        cli.main(["download", "--cache-dir", str(self.tmp)])

        colecciones = [c.args[0] for c in self.mock_descarga.call_args_list]
        self.assertEqual(colecciones, list(cache.RELEASE_TAGS))
        for llamada in self.mock_descarga.call_args_list:
            self.assertEqual(llamada.kwargs["models"], release.MODELS)
            self.assertIsNone(llamada.args[1])

    def test_una_coleccion_se_baja_sola(self):
        cli.main(["download", "--collection", "lineamientos", "--cache-dir", str(self.tmp)])

        self.mock_descarga.assert_called_once()
        self.assertEqual(self.mock_descarga.call_args.args[0], "lineamientos")

    def test_key_y_model_llegan_como_claves_y_models(self):
        cli.main([
            "download", "--collection", "leyes", "--key", "lft", "--key", "lif-2026",
            "--model", "qwen3-0.6b", "--cache-dir", str(self.tmp),
        ])

        llamada = self.mock_descarga.call_args
        self.assertEqual(llamada.args[1], ["lft", "lif-2026"])
        self.assertEqual(llamada.kwargs["models"], ("qwen3-0.6b",))

    def test_model_acepta_tambien_el_id_completo(self):
        cli.main([
            "download", "--collection", "leyes", "--model", "Qwen/Qwen3-Embedding-4B",
            "--cache-dir", str(self.tmp),
        ])

        self.assertEqual(
            self.mock_descarga.call_args.kwargs["models"], ("Qwen/Qwen3-Embedding-4B",))

    def test_key_sin_una_sola_coleccion_es_systemexit(self):
        # Una clave pertenece a una coleccion: aplicarla a las tres bajaria
        # nada mas los metadatos de las otras dos, en silencio.
        with self.assertRaises(SystemExit) as ctx:
            cli.main(["download", "--key", "lft", "--cache-dir", str(self.tmp)])

        self.assertIn("--key", str(ctx.exception))
        self.mock_descarga.assert_not_called()

    def test_cache_dir_y_refresh_llegan_al_descargador(self):
        cli.main([
            "download", "--collection", "leyes", "--refresh", "--cache-dir", str(self.tmp),
        ])

        llamada = self.mock_descarga.call_args
        self.assertEqual(llamada.kwargs["cache_dir"], str(self.tmp))
        self.assertTrue(llamada.kwargs["refresh"])

    def test_resume_la_coleccion_por_su_tag_de_release(self):
        lineas = []
        args = cli._parser().parse_args(
            ["download", "--collection", "leyes", "--cache-dir", str(self.tmp)])
        cli._main_download(args, log=lineas.append)

        self.assertIn(
            f"scjn-leyes-vectors: 1 assets in {self.tmp / 'scjn-leyes-vectors'} "
            "(1 downloaded, 0 already cached)",
            lineas,
        )


class TestDownloadDeVerdad(ConRedParchada):
    """The same verb without the downloader patched: only the HTTP layer is,
    so this proves the CLI really goes through
    `legalvec.download_vectors_assets` rather than building its own asset
    list."""

    def test_los_assets_caen_en_el_subdirectorio_de_la_coleccion(self):
        cli.main([
            "download", "--collection", "lineamientos", "--key", "lft",
            "--model", "qwen3-0.6b", "--cache-dir", str(self.tmp),
        ])

        directorio = self.tmp / "scjn-lineamientos-vectors"
        nombres = sorted(p.name for p in directorio.iterdir())
        self.assertIn("units.parquet", nombres)
        self.assertIn("vectors-lft-qwen3-0.6b-1024.parquet", nombres)
        self.assertIn("vectors-shared-qwen3-0.6b-1024.parquet", nombres)
        self.assertNotIn("vectors-lft-qwen3-4b-2560.parquet", nombres)


class TestStatus(ConRedParchada):
    def _publica(self, coleccion, nombres):
        directorio = self.tmp / cache.RELEASE_TAGS[coleccion]
        directorio.mkdir(parents=True, exist_ok=True)
        for nombre in nombres:
            (directorio / nombre).write_bytes(b"x" * 1024)
        return directorio

    def test_una_cache_vacia_reporta_las_tres_sin_bajar(self):
        lineas = []
        cli._main_status(
            cli._parser().parse_args(["status", "--cache-dir", str(self.tmp)]),
            log=lineas.append,
        )

        self.assertEqual(
            sum(1 for linea in lineas if "not downloaded" in linea), 3)
        self.assertIn(f"cache: {self.tmp}", lineas)

    def test_cuenta_los_archivos_por_slug_leido_del_nombre(self):
        # El slug se lee del archivo compartido, que no lleva clave: por eso
        # un modelo que no esta en MODELS se cuenta igual, y por eso una
        # clave con guiones (lif-2026) no rompe la cuenta.
        self._publica("leyes", [
            *release.METADATA_ASSETS,
            "vectors-shared-qwen3-0.6b-1024.parquet",
            "vectors-lft-qwen3-0.6b-1024.parquet",
            "vectors-lif-2026-qwen3-0.6b-1024.parquet",
            "vectors-shared-acme-xl-8.parquet",
            "vectors-lft-acme-xl-8.parquet",
        ])

        resumen = cli._cached_summary(self.tmp)

        self.assertTrue(resumen["leyes"]["existe"])
        self.assertEqual(resumen["leyes"]["metadatos"], release.METADATA_ASSETS)
        self.assertEqual(
            resumen["leyes"]["modelos"]["qwen3-0.6b"],
            {"instrumentos": 2, "compartido": True},
        )
        self.assertEqual(
            resumen["leyes"]["modelos"]["acme-xl"],
            {"instrumentos": 1, "compartido": True},
        )
        self.assertEqual(resumen["leyes"]["sin_modelo"], 0)
        self.assertFalse(resumen["reglamentos"]["existe"])

    def test_reporta_un_modelo_sin_su_archivo_compartido(self):
        self._publica("lineamientos", [
            "units.parquet", "vectors-102583-qwen3-4b-2560.parquet",
        ])

        resumen = cli._cached_summary(self.tmp)

        self.assertEqual(
            resumen["lineamientos"]["modelos"]["qwen3-4b"],
            {"instrumentos": 1, "compartido": False},
        )
        self.assertEqual(len(resumen["lineamientos"]["metadatos"]), 1)

    def test_no_cuenta_una_descarga_a_medias(self):
        self._publica("leyes", [
            "units.parquet", "units.parquet" + cache.PARTIAL_SUFFIX,
        ])

        resumen = cli._cached_summary(self.tmp)

        self.assertEqual(resumen["leyes"]["bytes"], 1024)

    def test_imprime_una_linea_por_coleccion_con_los_modelos(self):
        self._publica("leyes", [
            *release.METADATA_ASSETS,
            "vectors-shared-qwen3-0.6b-1024.parquet",
            "vectors-lft-qwen3-0.6b-1024.parquet",
        ])
        lineas = []
        cli._main_status(
            cli._parser().parse_args(["status", "--cache-dir", str(self.tmp)]),
            log=lineas.append,
        )

        linea = next(l for l in lineas if l.strip().startswith("scjn-leyes-vectors"))
        self.assertIn("metadata 5/5", linea)
        self.assertIn("qwen3-0.6b: 1 instrument", linea)

    def test_status_no_toca_la_red(self):
        self._publica("leyes", ["units.parquet"])

        cli.main(["status", "--cache-dir", str(self.tmp)])

        self.mock_assets.assert_not_called()
        self.mock_download.assert_not_called()


class TestCacheDirPorDefecto(ConRedParchada):
    def test_sin_cache_dir_status_lee_el_directorio_del_paquete(self):
        with patch.object(cache, "CACHE_DIR", self.tmp):
            (self.tmp / "scjn-leyes-vectors").mkdir()
            resumen = cli._cached_summary(None)

        self.assertTrue(resumen["leyes"]["existe"])
        self.assertEqual(resumen["leyes"]["directorio"].parent, self.tmp)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

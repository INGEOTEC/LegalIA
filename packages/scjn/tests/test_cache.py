"""scjn.cache: the on-disk cache of the scjn-leyes release's own assets
(issue #209), and `migrate_legacy_assets`, the pure move a downstream
package's own `download` verb calls once on upgrade. Nothing here touches the
network except `TestAssetEnCache`, where every download is a mocked
`requests.get` -- which doubles as the assertion that a cache hit made no
request at all."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from scjn import cache


def _respuesta(contenido: bytes) -> Mock:
    return Mock(content=contenido, raise_for_status=Mock())


class TestResuelveCacheDir(unittest.TestCase):
    def test_none_usa_el_cache_dir_del_paquete(self):
        with patch.object(cache, "CACHE_DIR", Path("/tmp/paquete")):
            self.assertEqual(cache.resuelve_cache_dir(None), Path("/tmp/paquete"))

    def test_lee_cache_dir_al_momento_de_la_llamada_no_al_importar(self):
        with patch.object(cache, "CACHE_DIR", Path("/tmp/despues")):
            self.assertEqual(cache.resuelve_cache_dir(None), Path("/tmp/despues"))

    def test_una_ruta_se_usa_tal_cual(self):
        # A diferencia de nota2md.cache, no hay modo "sin cache": todo lector
        # de scjn.release es de disco (issue #209).
        self.assertEqual(cache.resuelve_cache_dir("/tmp/mia"), Path("/tmp/mia"))


class TestDirectorioCachePredeterminado(unittest.TestCase):
    def test_la_variable_de_entorno_gana(self):
        with patch.dict(os.environ, {"SCJN_CACHE_DIR": "/mnt/datos/scjn"}):
            self.assertEqual(
                cache.directorio_cache_predeterminado(), Path("/mnt/datos/scjn")
            )

    def test_sin_variable_de_entorno_usa_el_directorio_del_sistema(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIn("scjn", str(cache.directorio_cache_predeterminado()))


class TestAssetEnCache(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp))

    @patch("scjn.cache.requests.get")
    def test_baja_el_asset_cuando_no_esta_en_cache(self, mock_get):
        mock_get.return_value = _respuesta(b"contenido")

        ruta = cache.asset_en_cache(
            "scjn-leyes", "lfca.tgz", "https://x/lfca.tgz", cache_dir=self.tmp
        )

        self.assertEqual(ruta, self.tmp / "scjn-leyes" / "lfca.tgz")
        self.assertEqual(ruta.read_bytes(), b"contenido")

    @patch("scjn.cache.requests.get")
    def test_un_asset_ya_en_disco_no_se_vuelve_a_bajar(self, mock_get):
        destino = self.tmp / "scjn-leyes" / "lfca.tgz"
        destino.parent.mkdir(parents=True)
        destino.write_bytes(b"viejo")

        ruta = cache.asset_en_cache(
            "scjn-leyes", "lfca.tgz", "https://x/lfca.tgz", cache_dir=self.tmp
        )

        mock_get.assert_not_called()
        self.assertEqual(ruta.read_bytes(), b"viejo")

    @patch("scjn.cache.requests.get")
    def test_refrescar_vuelve_a_bajar_encima(self, mock_get):
        destino = self.tmp / "scjn-leyes" / "lfca.tgz"
        destino.parent.mkdir(parents=True)
        destino.write_bytes(b"viejo")
        mock_get.return_value = _respuesta(b"nuevo")

        ruta = cache.asset_en_cache(
            "scjn-leyes", "lfca.tgz", "https://x/lfca.tgz",
            cache_dir=self.tmp, refrescar=True,
        )

        self.assertEqual(ruta.read_bytes(), b"nuevo")

    @patch("scjn.cache.requests.get")
    def test_un_parcial_de_una_bajada_cortada_no_cuenta_como_acierto(self, mock_get):
        parcial = self.tmp / "scjn-leyes" / ("lfca.tgz" + cache.SUFIJO_PARCIAL)
        parcial.parent.mkdir(parents=True)
        parcial.write_bytes(b"a med")
        mock_get.return_value = _respuesta(b"completo")

        ruta = cache.asset_en_cache(
            "scjn-leyes", "lfca.tgz", "https://x/lfca.tgz", cache_dir=self.tmp
        )

        mock_get.assert_called_once()
        self.assertEqual(ruta.read_bytes(), b"completo")

    @patch("scjn.cache.requests.get")
    def test_una_bajada_que_falla_no_deja_el_asset_en_su_nombre_final(self, mock_get):
        mock_get.side_effect = RuntimeError("se cayo la red")

        with self.assertRaises(RuntimeError):
            cache.asset_en_cache(
                "scjn-leyes", "lfca.tgz", "https://x/lfca.tgz", cache_dir=self.tmp
            )

        self.assertFalse((self.tmp / "scjn-leyes" / "lfca.tgz").exists())


class TestMigrateLegacyAssets(unittest.TestCase):
    """The one-time move issue #209 needs: this package's cache did not exist
    before, so whatever a downstream package's own `download` verb already
    cached under its own directory has to be handed over once. Always
    exercised against `tmp_path`-style fixtures here, never the real
    filesystem's default cache directories (see `packages/scjn/tests/conftest.py`
    and the run notes: an automated test must not touch a real, possibly
    ~380 MB, cache)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp))
        self.legacy = self.tmp / "legacy"
        self.nuevo = self.tmp / "nuevo"

    def test_sin_legacy_dir_no_hace_nada(self):
        self.assertEqual(cache.migrate_legacy_assets(self.nuevo, self.legacy), 0)
        self.assertFalse(self.nuevo.exists())

    def test_mueve_los_tgz_el_indice_y_el_sha256sums(self):
        self.legacy.mkdir(parents=True)
        (self.legacy / "lfca.tgz").write_bytes(b"lfca")
        (self.legacy / "lft.tgz").write_bytes(b"lft")
        (self.legacy / "indice-global.json.gz").write_bytes(b"indice")
        (self.legacy / "SHA256SUMS.txt").write_text("checksums")

        movidos = cache.migrate_legacy_assets(self.nuevo, self.legacy)

        self.assertEqual(movidos, 4)
        self.assertEqual(
            sorted(p.name for p in self.nuevo.iterdir()),
            ["SHA256SUMS.txt", "indice-global.json.gz", "lfca.tgz", "lft.tgz"],
        )
        self.assertEqual((self.nuevo / "lfca.tgz").read_bytes(), b"lfca")

    def test_un_parcial_tambien_se_mueve(self):
        self.legacy.mkdir(parents=True)
        (self.legacy / ("lfca.tgz" + cache.SUFIJO_PARCIAL)).write_bytes(b"a medias")

        movidos = cache.migrate_legacy_assets(self.nuevo, self.legacy)

        self.assertEqual(movidos, 1)
        self.assertTrue((self.nuevo / ("lfca.tgz" + cache.SUFIJO_PARCIAL)).exists())

    def test_lo_que_no_es_un_asset_del_release_se_queda_atras(self):
        # Un downstream package puede guardar su propio derivado junto a
        # estos assets (p.ej. bajo md/); esta funcion no sabe nada de eso, y
        # justamente por eso lo deja intacto -- no reconoce el nombre.
        self.legacy.mkdir(parents=True)
        (self.legacy / "lfca.tgz").write_bytes(b"lfca")
        (self.legacy / "md").mkdir()
        (self.legacy / "md" / "lfca-05-01-1999.md").write_text("**TEXTO**")

        movidos = cache.migrate_legacy_assets(self.nuevo, self.legacy)

        self.assertEqual(movidos, 1)
        self.assertEqual([p.name for p in self.nuevo.iterdir()], ["lfca.tgz"])
        self.assertTrue((self.legacy / "md" / "lfca-05-01-1999.md").exists())

    def test_ya_migrado_no_encuentra_nada_que_mover(self):
        self.legacy.mkdir(parents=True)
        (self.legacy / "lfca.tgz").write_bytes(b"lfca")
        cache.migrate_legacy_assets(self.nuevo, self.legacy)

        segunda = cache.migrate_legacy_assets(self.nuevo, self.legacy)

        self.assertEqual(segunda, 0)

    def test_un_archivo_que_no_se_puede_mover_no_detiene_a_los_demas(self):
        self.legacy.mkdir(parents=True)
        (self.legacy / "lfca.tgz").write_bytes(b"lfca")
        (self.legacy / "lft.tgz").write_bytes(b"lft")

        with patch("scjn.cache.os.replace", side_effect=[OSError("cross-device"), None]):
            movidos = cache.migrate_legacy_assets(self.nuevo, self.legacy)

        self.assertEqual(movidos, 1)

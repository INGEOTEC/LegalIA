"""nota2md.cache: the on-disk cache of the release assets nota2md reads
(issue #117, Paso 2). Nothing here touches the network -- every download is a
mocked `requests.get`, which doubles as the assertion that a cache hit made no
request at all."""

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from nota2md import cache


def _respuesta(contenido: bytes) -> Mock:
    return Mock(content=contenido, raise_for_status=Mock())


class TestResuelveCacheDir(unittest.TestCase):
    def test_sin_cache_dir_usa_el_cache_dir_del_paquete(self):
        with patch.object(cache, "CACHE_DIR", Path("/tmp/paquete")):
            self.assertEqual(
                cache.resuelve_cache_dir(cache.SIN_CACHE_DIR), Path("/tmp/paquete")
            )

    def test_lee_cache_dir_al_momento_de_la_llamada_no_al_importar(self):
        # Reasignar CACHE_DIR es la forma documentada de mover la cache desde
        # dentro de un proceso; congelarlo al importar la volveria inutil.
        with patch.object(cache, "CACHE_DIR", Path("/tmp/despues")):
            self.assertEqual(
                cache.resuelve_cache_dir(cache.SIN_CACHE_DIR), Path("/tmp/despues")
            )

    def test_none_explicito_significa_sin_cache(self):
        self.assertIsNone(cache.resuelve_cache_dir(None))

    def test_una_ruta_se_usa_tal_cual(self):
        self.assertEqual(cache.resuelve_cache_dir("/tmp/mia"), Path("/tmp/mia"))


class TestAssetEnCache(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(__import__("tempfile").mkdtemp())

    def tearDown(self):
        __import__("shutil").rmtree(self.tmp)

    @patch("nota2md.cache.requests.get")
    def test_baja_el_asset_cuando_no_esta_en_cache(self, mock_get):
        mock_get.return_value = _respuesta(b"contenido")

        ruta = cache.asset_en_cache(
            "scjn-leyes", "lfca.tgz", "https://x/lfca.tgz", cache_dir=self.tmp
        )

        self.assertEqual(ruta, self.tmp / "scjn-leyes" / "lfca.tgz")
        self.assertEqual(ruta.read_bytes(), b"contenido")

    @patch("nota2md.cache.requests.get")
    def test_un_asset_ya_en_disco_no_se_vuelve_a_bajar(self, mock_get):
        destino = self.tmp / "scjn-leyes" / "lfca.tgz"
        destino.parent.mkdir(parents=True)
        destino.write_bytes(b"viejo")

        ruta = cache.asset_en_cache(
            "scjn-leyes", "lfca.tgz", "https://x/lfca.tgz", cache_dir=self.tmp
        )

        mock_get.assert_not_called()
        self.assertEqual(ruta.read_bytes(), b"viejo")

    @patch("nota2md.cache.requests.get")
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

    @patch("nota2md.cache.requests.get")
    def test_un_parcial_de_una_bajada_cortada_no_cuenta_como_acierto(self, mock_get):
        # Una conexion cortada a la mitad deja el .parcial; el nombre final
        # nunca existio, asi que la siguiente corrida vuelve a bajar en vez
        # de leer bytes truncados como si fueran el asset completo.
        parcial = self.tmp / "scjn-leyes" / ("lfca.tgz" + cache.SUFIJO_PARCIAL)
        parcial.parent.mkdir(parents=True)
        parcial.write_bytes(b"a med")
        mock_get.return_value = _respuesta(b"completo")

        ruta = cache.asset_en_cache(
            "scjn-leyes", "lfca.tgz", "https://x/lfca.tgz", cache_dir=self.tmp
        )

        mock_get.assert_called_once()
        self.assertEqual(ruta.read_bytes(), b"completo")

    @patch("nota2md.cache.requests.get")
    def test_una_bajada_que_falla_no_deja_el_asset_en_su_nombre_final(self, mock_get):
        mock_get.side_effect = RuntimeError("se cayo la red")

        with self.assertRaises(RuntimeError):
            cache.asset_en_cache(
                "scjn-leyes", "lfca.tgz", "https://x/lfca.tgz", cache_dir=self.tmp
            )

        self.assertFalse((self.tmp / "scjn-leyes" / "lfca.tgz").exists())


class TestBytesDeAsset(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(__import__("tempfile").mkdtemp())

    def tearDown(self):
        __import__("shutil").rmtree(self.tmp)

    @patch("nota2md.cache.requests.get")
    def test_cache_dir_none_baja_a_memoria_y_no_escribe_nada(self, mock_get):
        mock_get.return_value = _respuesta(b"contenido")

        contenido = cache.bytes_de_asset(
            "scjn-leyes", "lfca.tgz", "https://x/lfca.tgz", cache_dir=None
        )

        self.assertEqual(contenido, b"contenido")
        self.assertEqual(list(self.tmp.iterdir()), [])

    @patch("nota2md.cache.requests.get")
    def test_con_cache_dir_deja_el_asset_en_disco(self, mock_get):
        mock_get.return_value = _respuesta(b"contenido")

        contenido = cache.bytes_de_asset(
            "scjn-leyes", "lfca.tgz", "https://x/lfca.tgz", cache_dir=self.tmp
        )

        self.assertEqual(contenido, b"contenido")
        self.assertTrue((self.tmp / "scjn-leyes" / "lfca.tgz").is_file())


class TestDirectorioCachePredeterminado(unittest.TestCase):
    def test_la_variable_de_entorno_gana(self):
        with patch.dict("os.environ", {"NOTA2MD_CACHE_DIR": "/mnt/datos/nota2md"}):
            self.assertEqual(
                cache.directorio_cache_predeterminado(), Path("/mnt/datos/nota2md")
            )

    def test_sin_variable_de_entorno_usa_el_directorio_del_sistema(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertIn("nota2md", str(cache.directorio_cache_predeterminado()))


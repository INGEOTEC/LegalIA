"""nota2md.cache: the on-disk cache of what `nota2md` itself derives or
fetches for the DOF path (issue #117, Paso 2; scoped down in issue #209, when
the release-asset half moved to `scjn.cache`)."""

import unittest
from pathlib import Path
from unittest.mock import patch

from nota2md import cache


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


class TestDirectorioCachePredeterminado(unittest.TestCase):
    def test_la_variable_de_entorno_gana(self):
        with patch.dict("os.environ", {"NOTA2MD_CACHE_DIR": "/mnt/datos/nota2md"}):
            self.assertEqual(
                cache.directorio_cache_predeterminado(), Path("/mnt/datos/nota2md")
            )

    def test_sin_variable_de_entorno_usa_el_directorio_del_sistema(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertIn("nota2md", str(cache.directorio_cache_predeterminado()))


class TestDirectorioDeSalida(unittest.TestCase):
    """The destinations `legal_provisions` writes to when it is given no
    `outdir` of its own (issue #165)."""

    def setUp(self):
        self.tmp = Path(__import__("tempfile").mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp))

    def test_arma_el_subdirectorio_y_lo_crea(self):
        ruta = cache.directorio_de_salida(self.tmp, *cache.SUBDIR_MD_SCJN)

        self.assertEqual(ruta, self.tmp / "scjn-leyes" / "md")
        self.assertTrue(ruta.is_dir())

    def test_el_dof_es_hermano_del_corpus_no_parte_de_el(self):
        # Borrar el corpus de la SCJN no debe llevarse texto del DOF.
        ruta = cache.directorio_de_salida(self.tmp, *cache.SUBDIR_DOF)

        self.assertEqual(ruta, self.tmp / "dof")

    def test_sin_cache_dir_usa_el_cache_del_paquete(self):
        with patch.object(cache, "CACHE_DIR", self.tmp / "otro"):
            ruta = cache.directorio_de_salida(cache.SIN_CACHE_DIR)

        self.assertEqual(ruta, self.tmp / "otro")

    def test_cache_dir_none_no_tiene_donde_escribir(self):
        with self.assertRaises(ValueError) as ctx:
            cache.directorio_de_salida(None)

        self.assertIn("outdir", str(ctx.exception))


class TestEscribeTexto(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(__import__("tempfile").mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp))

    def test_escribe_por_un_parcial_y_no_lo_deja_atras(self):
        destino = cache.escribe_texto(self.tmp / "ccf-14-11-2025.md", "texto")

        self.assertEqual(destino.read_text(encoding="utf-8"), "texto")
        self.assertEqual(
            [p.name for p in self.tmp.iterdir()], ["ccf-14-11-2025.md"]
        )

import argparse
import json
import tempfile
import unittest
from pathlib import Path

from unittest.mock import patch

from dofjson.titulos import SIN_CACHE_DIR
from leyesmx.cli import _resolver_cache_dir, _titulos, escribe_json
from leyesmx.dof import ReformaEnlazada


def enlazada(no, codNota):
    return ReformaEnlazada(ley="cpeum", no=no, fecha="01-01-2020", codNota=codNota,
                           titulo_dof="", decreto_dip="", confianza=1.0)


class TestEscribeJson(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.destino = Path(self.tmp.name) / "cpeum.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_el_indice_n_es_la_reforma_n(self):
        """El índice 0 es la publicación original y el N la reforma N."""
        escribe_json([enlazada(None, 100), enlazada(1, 111), enlazada(2, 222)],
                     self.destino)

        self.assertEqual(json.load(open(self.destino)), [100, 111, 222])

    def test_coloca_por_numero_no_por_posicion(self):
        """Recibidas en cualquier orden, cada una cae en su propio índice."""
        escribe_json([enlazada(3, 333), enlazada(1, 111), enlazada(2, 222)],
                     self.destino)

        self.assertEqual(json.load(open(self.destino)), [None, 111, 222, 333])

    def test_una_ley_sin_publicacion_original_deja_el_indice_0_en_null(self):
        """`ccf` y `ccom` no la traen en su página; sin reservar el índice 0
        toda la numeración se correría en uno."""
        escribe_json([enlazada(1, 111), enlazada(2, 222)], self.destino)

        self.assertEqual(json.load(open(self.destino)), [None, 111, 222])

    def test_una_reforma_sin_nota_queda_como_null(self):
        """Así la lista sigue alineada con la numeración de Diputados y el
        hueco del DOF se ve en los datos, no sólo en la documentación."""
        escribe_json([enlazada(138, 111), enlazada(139, None), enlazada(140, 333)],
                     self.destino)

        lista = json.load(open(self.destino))
        self.assertEqual(lista[138:141], [111, None, 333])

    def test_crea_el_directorio_destino(self):
        destino = Path(self.tmp.name) / "nuevo" / "sub" / "cpeum.json"

        escribe_json([enlazada(1, 111)], destino)

        self.assertTrue(destino.exists())


class TestCacheDir(unittest.TestCase):
    """--cache-dir replaced --titulos <ruta> (issue #166): the titles are a
    stream over the notas-archivo cache now, not a file of their own."""

    def _args(self, cache_dir):
        return argparse.Namespace(cache_dir=cache_dir)

    def test_sin_valor_usa_el_cache_del_paquete(self):
        self.assertIs(_resolver_cache_dir(None), SIN_CACHE_DIR)

    def test_none_baja_a_memoria(self):
        self.assertIsNone(_resolver_cache_dir("none"))
        self.assertIsNone(_resolver_cache_dir("NONE"))

    def test_una_ruta_se_usa_tal_cual(self):
        self.assertEqual(_resolver_cache_dir("/mnt/datos"), Path("/mnt/datos"))

    def test_cada_pasada_pide_un_flujo_nuevo(self):
        """El generador no es re-iterable: cada consumidor lo vuelve a pedir."""
        with patch("leyesmx.cli.legal_provisions_titles") as mock_titulos:
            _titulos(self._args(None))
            _titulos(self._args(None))

        self.assertEqual(mock_titulos.call_count, 2)
        self.assertIs(mock_titulos.call_args.args[0], SIN_CACHE_DIR)


if __name__ == "__main__":
    unittest.main()

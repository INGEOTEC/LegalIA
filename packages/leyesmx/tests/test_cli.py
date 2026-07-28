import json
import tempfile
import unittest
from pathlib import Path

from leyesmx.cli import escribe_json
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


if __name__ == "__main__":
    unittest.main()

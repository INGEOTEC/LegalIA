"""Live check of `scjn.api` against the real SCOW API.

An integration test, not a unit test: it talks to
`legislacion.scjn.gob.mx`, so it is excluded from this package's own
routine run the same way `nota2md`'s `test_scjn_release_red.py` and
`test_leyes_44.py` are excluded from theirs:

    pytest packages/scjn -q --ignore=packages/scjn/tests/test_api_red.py

`lfca` is the case that motivated the whole migration (issue #172): the
WebForms `scjn.buscar` returns 0 candidates for it, while the new index has
it as `idOrdenamiento` 188805 with 87 articles in its only reform.
"""

import unittest

from scjn.api import ScjnApi


class TestApiEnVivo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api = ScjnApi()

    def test_lfca_se_encuentra_y_trae_sus_87_articulos(self):
        candidatos = self.api.search_ordenamiento("LEY FEDERAL DE CINE Y EL AUDIOVISUAL")
        elegido = next(c for c in candidatos if c.ordenamiento == "LEY FEDERAL DE CINE Y EL AUDIOVISUAL")
        self.assertEqual(elegido.idOrdenamiento, "188805")
        self.assertEqual(elegido.ambito, "FEDERAL")

        reformas = self.api.reformas_of_ordenamiento(elegido.idOrdenamiento)
        self.assertEqual(len(reformas), 1)
        self.assertEqual(reformas[0].fecha_publicacion, "22-05-2026")

        articulos = self.api.articulos_of_reforma(elegido.idOrdenamiento, reformas[0].reformaId)
        self.assertEqual(len(articulos), 87)
        self.assertEqual(articulos[0].referencia, "ENCABEZADO")
        self.assertTrue(all(a.contenido for a in articulos))


if __name__ == "__main__":
    unittest.main()

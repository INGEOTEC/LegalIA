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

from scjn.api import ScjnApi, instrument_metadata


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


    def test_metadatos_por_ley_se_leen_por_id_no_por_ranking(self):
        # Issue #215: `materia`/`vigencia`/`resumen` are fields of the
        # instrument, read off the search but chosen by `idOrdenamiento`.
        # `lft` is the law with all three; `lfca` is the one the SCJN has no
        # `resumen` for, and both are pinned here because this behaviour is
        # the documented exception to the docs page's "every public symbol
        # has a verified example" rule.
        lft = instrument_metadata(self.api, "LEY FEDERAL DEL TRABAJO", 410)
        self.assertEqual(lft.idOrdenamiento, "410")
        self.assertEqual(lft.vigencia, "VIGENTE")
        self.assertTrue(lft.materia)
        self.assertIn("relaciones de trabajo", lft.resumen)

        lfca = instrument_metadata(self.api, "LEY FEDERAL DE CINE Y EL AUDIOVISUAL", 188805)
        self.assertEqual(lfca.materia, "ADMINISTRATIVO")
        self.assertIsNone(lfca.resumen)

    def test_un_id_que_la_busqueda_no_regresa_no_inventa_un_candidato(self):
        self.assertIsNone(
            instrument_metadata(self.api, "LEY FEDERAL DEL TRABAJO", 999999999)
        )


if __name__ == "__main__":
    unittest.main()

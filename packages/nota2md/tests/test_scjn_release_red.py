"""End-to-end check of the SCJN path against the real `scjn-leyes` release.

An integration test, not a unit test: it downloads `indice-global.json.gz` and
one law's own tarball over the network, so it is excluded from the routine
run the same way `test_leyes_44.py` is:

    pytest packages/nota2md -q --ignore=packages/nota2md/tests/test_leyes_44.py \\
        --ignore=packages/nota2md/tests/test_scjn_release_red.py

What it is here to catch is the one thing no fabricated tarball can: that the
asset actually published matches what the readers expect — a corpus
re-packaged without re-uploading `indice-global.json.gz` (see
`scripts/empaqueta_scjn_leyes.py`, and issue #148) resolves a codNota to a
snapshot file that is no longer in the law's tarball, and that shows up here
and nowhere else.

`lfca` is the law used because it is small (10 snapshots) and because it is
one of the two the SCJN does not index at all — its snapshots were built by
hand from the DOF (issue #144), so it also exercises the least typical entry
in the corpus.
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from nota2md import scjn
from nota2md.builder import legal_provisions

SLUG = "lfca"

#: The real reader, captured at import time — before `conftest.py`'s autouse
#: fixture swaps the module attribute for its covers-nothing stub, which is
#: exactly what this module does not want.
_INDICE_REAL = scjn.download_scjn_leyes_index


class TestReleaseReal(unittest.TestCase):
    """One download of the index, shared by every test here — the point is to
    exercise the published assets, not to re-download them four times."""

    @classmethod
    def setUpClass(cls):
        cls._dir = TemporaryDirectory()
        cls.cache_dir = Path(cls._dir.name)
        try:
            cls.indice = _INDICE_REAL(cache_dir=cls.cache_dir)
        except KeyError as exc:
            # Publishing this corpus is a manual step, on purpose (see
            # scripts/empaqueta_scjn_leyes.py), so "the asset is not up yet"
            # is a legitimate state of the world -- not a failing test.
            cls._dir.cleanup()
            raise unittest.SkipTest(f"el release aun no publica el indice: {exc}")

    @classmethod
    def tearDownClass(cls):
        cls._dir.cleanup()

    def setUp(self):
        # conftest's autouse fixture stubs the reader out so no other test
        # reaches the network; here that is precisely the point, so put the
        # real one back for the duration of each test.
        parche = patch.object(scjn, "download_scjn_leyes_index", _INDICE_REAL)
        parche.start()
        self.addCleanup(parche.stop)

    def test_el_indice_publicado_declara_la_coleccion_de_leyes(self):
        self.assertEqual(self.indice["coleccion"], "leyes")
        self.assertIn(SLUG, self.indice["instrumentos"])

    def test_las_claves_del_indice_publicado_son_enteros(self):
        self.assertTrue(all(isinstance(cod, int) for cod in self.indice["codNota"]))

    def test_el_indice_se_cacheo_en_disco(self):
        self.assertTrue(
            (self.cache_dir / "scjn-leyes" / scjn.ASSET_INDICE_GLOBAL).is_file()
        )

    def _un_codnota_de_lfca(self) -> int:
        for cod, entradas in self.indice["codNota"].items():
            if len(entradas) == 1 and entradas[0]["slug"] == SLUG:
                return cod
        self.skipTest(f"el indice publicado no enlaza ningun codNota solo a {SLUG}")

    def test_resuelve_un_codnota_de_lfca_a_su_snapshot_publicado(self):
        cod = self._un_codnota_de_lfca()

        slug, archivo, markdown = scjn.snapshot_de_codNota(cod, cache_dir=self.cache_dir)

        self.assertEqual(slug, SLUG)
        self.assertTrue(archivo.endswith(".md"))
        # Lo que hace auditable el resultado: la cabecera de procedencia.
        self.assertIn("fuente: scjn", markdown)

    def test_legal_provisions_escribe_la_ley_completa_de_la_scjn(self):
        cod = self._un_codnota_de_lfca()

        with TemporaryDirectory() as outdir:
            destino = legal_provisions(cod, outdir, cache_dir=self.cache_dir)

            self.assertTrue(destino.name.startswith(f"{SLUG}-"))
            self.assertIn("fuente: scjn", destino.read_text(encoding="utf-8"))

    def test_source_dof_del_mismo_codnota_va_al_dof(self):
        cod = self._un_codnota_de_lfca()

        with TemporaryDirectory() as outdir:
            destino = legal_provisions(cod, outdir, source="dof", cache_dir=self.cache_dir)

            self.assertEqual(destino.name, f"nota-{cod}.md")


if __name__ == "__main__":
    unittest.main()

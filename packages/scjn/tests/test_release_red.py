"""End-to-end check of `scjn.release` against the real `scjn-leyes` release.

An integration test, not a unit test: it downloads `indice-global.json.gz` and
one law's own tarball over the network, so it is excluded from the routine
run the same way `packages/nota2md/tests/test_leyes_44.py` is:

    pytest packages/scjn -q --ignore=packages/scjn/tests/test_release_red.py \\
        --ignore=packages/scjn/tests/test_api_red.py

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

from scjn.cache import _SCJN_REGLAMENTOS_RELEASE
from scjn.release import (
    LIMITE_ASSETS_POR_RELEASE,
    AssetNotCached,
    _assets_de_partes,
    _assets_de_release,
    _tag_de_parte,
    download_scjn_leyes_assets,
    download_scjn_leyes_index,
    download_scjn_reglamentos_assets,
    download_scjn_reglamentos_index,
    markdown_de_snapshot,
)

SLUG = "lfca"


class TestReleaseReal(unittest.TestCase):
    """One download of the index, shared by every test here — the point is to
    exercise the published assets, not to re-download them for each one.

    Readers are disk-only since issue #209, so getting the index here needs
    an explicit `download_scjn_leyes_assets` call first — this is the one
    place in this module allowed to talk to the network before a reader can
    say anything at all."""

    @classmethod
    def setUpClass(cls):
        cls._dir = TemporaryDirectory()
        cls.cache_dir = Path(cls._dir.name)
        try:
            download_scjn_leyes_assets([], cache_dir=cls.cache_dir)
            cls.indice = download_scjn_leyes_index(cache_dir=cls.cache_dir)
        except (AssetNotCached, KeyError) as exc:
            # Publishing this corpus is a manual step, on purpose (see
            # scripts/empaqueta_scjn_leyes.py), so "the asset is not up yet"
            # is a legitimate state of the world -- not a failing test.
            cls._dir.cleanup()
            raise unittest.SkipTest(f"el release aun no publica el indice: {exc}")

    @classmethod
    def tearDownClass(cls):
        cls._dir.cleanup()

    def test_el_indice_publicado_declara_la_coleccion_de_leyes(self):
        self.assertEqual(self.indice["coleccion"], "leyes")
        self.assertIn(SLUG, self.indice["instrumentos"])

    def test_las_claves_del_indice_publicado_son_enteros(self):
        self.assertTrue(all(isinstance(cod, int) for cod in self.indice["codNota"]))

    def test_el_indice_se_cacheo_en_disco(self):
        self.assertTrue((self.cache_dir / "scjn-leyes" / "indice-global.json.gz").is_file())

    def _un_codnota_de_lfca(self) -> tuple[int, str]:
        for cod, entradas in self.indice["codNota"].items():
            if len(entradas) == 1 and entradas[0]["slug"] == SLUG:
                return cod, entradas[0]["archivo"]
        self.skipTest(f"el indice publicado no enlaza ningun codNota solo a {SLUG}")

    def test_resuelve_un_codnota_de_lfca_a_su_snapshot_publicado(self):
        _, archivo = self._un_codnota_de_lfca()
        # markdown_de_snapshot needs the law's own tarball cached too --
        # setUpClass only downloaded the (much smaller) index.
        download_scjn_leyes_assets([SLUG], cache_dir=self.cache_dir)

        markdown = markdown_de_snapshot(SLUG, archivo, cache_dir=self.cache_dir)

        self.assertTrue(archivo.endswith(".md"))
        # Lo que hace auditable el resultado: la cabecera de procedencia.
        self.assertIn("fuente: scjn", markdown)


class TestPartesScjnReglamentos(unittest.TestCase):
    """The invariant issue #223 exists for: `scjn-reglamentos` outgrew a
    single GitHub release on 2026-09-10 (1082 tarballs across
    `scjn-reglamentos`/`scjn-reglamentos-2`), and `_assets_de_partes` is what
    makes the corpus fully readable again. This only asks GitHub's releases
    API for asset listings (a few requests, no tarball download), so it is
    cheap even against the full 1082-instrument corpus."""

    @classmethod
    def setUpClass(cls):
        cls._dir = TemporaryDirectory()
        cls.cache_dir = Path(cls._dir.name)
        try:
            cls.merged = _assets_de_partes(_SCJN_REGLAMENTOS_RELEASE)
        except (AssetNotCached, KeyError) as exc:
            cls._dir.cleanup()
            raise unittest.SkipTest(f"el release aun no publica el indice: {exc}")

    @classmethod
    def tearDownClass(cls):
        cls._dir.cleanup()

    def test_todo_asset_que_el_indice_declara_resuelve_en_el_mapa_fusionado(self):
        # El indice mismo es solo un asset mas -- basta con el, no con el
        # corpus completo de tarballs, para exigir la invariante.
        download_scjn_reglamentos_assets([], cache_dir=self.cache_dir)
        indice = download_scjn_reglamentos_index(cache_dir=self.cache_dir)

        faltantes = [
            entrada["asset"] for entrada in indice["instrumentos"].values()
            if entrada.get("asset") and entrada["asset"] not in self.merged
        ]

        self.assertEqual(faltantes, [])

    def test_las_partes_son_disjuntas(self):
        vistos: set[str] = set()
        n = 1
        while True:
            assets = _assets_de_release(_tag_de_parte(_SCJN_REGLAMENTOS_RELEASE, n), timeout=30)
            if assets is None:
                break
            nombres = set(assets)
            self.assertEqual(
                vistos & nombres, set(),
                f"la parte {n} repite un nombre ya visto en una parte anterior",
            )
            vistos |= nombres
            n += 1

    def test_ninguna_parte_excede_el_limite_de_assets(self):
        n = 1
        while True:
            assets = _assets_de_release(_tag_de_parte(_SCJN_REGLAMENTOS_RELEASE, n), timeout=30)
            if assets is None:
                break
            self.assertLessEqual(len(assets), LIMITE_ASSETS_POR_RELEASE)
            n += 1


if __name__ == "__main__":
    unittest.main()

"""`legalvec`'s readers, against a synthetic on-disk release.

There is no network here and no released asset to reach for: every test
writes the two or three Parquet files a release would publish into a
temporary cache directory and reads them back, which is the whole of what
this package does. `download_vectors_assets` — the one function that does
talk to the network — is exercised with its HTTP layer patched out, the same
way `packages/scjn/tests/test_release.py` does for the corpus releases.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

import legalvec
from legalvec import cache, release


def _write_vectors(path: Path, filas: list[tuple[str, list[float]]], k: int) -> None:
    if filas:
        plano = pa.array([x for _sha1, vec in filas for x in vec], type=pa.float16())
        columna = pa.FixedSizeListArray.from_arrays(plano, k)
        tabla = pa.table({
            "text_sha1": pa.array([sha1 for sha1, _vec in filas]), "vector": columna,
        })
    else:
        tabla = pa.table({
            "text_sha1": pa.array([], type=pa.string()),
            "vector": pa.array([], type=pa.list_(pa.float16(), k)),
        })
    pq.write_table(tabla, path)


class ConReleaseSintetico(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp))
        self.release_dir = self.tmp / "scjn-leyes-vectors"
        self.release_dir.mkdir(parents=True)

    def _publica_vectores(self, clave="lft", slug="qwen3-0.6b", k=2):
        _write_vectors(
            self.release_dir / f"vectors-{clave}-{slug}-{k}.parquet",
            [("propio", [1.0, 2.0])], k,
        )
        _write_vectors(
            self.release_dir / f"vectors-shared-{slug}-{k}.parquet",
            [("compartido", [3.0, 4.0])], k,
        )


class TestLoadVectors(ConReleaseSintetico):
    def test_une_el_archivo_propio_con_el_compartido(self):
        # El punto de la division: un texto que varios instrumentos comparten
        # se publica una sola vez, asi que ninguno de los dos archivos sirve
        # por si solo.
        self._publica_vectores()

        conjunto = legalvec.load_vectors("leyes", "lft", "qwen3-0.6b", cache_dir=self.tmp)

        self.assertEqual(conjunto.text_sha1, ["propio", "compartido"])
        self.assertEqual(conjunto.k, 2)
        self.assertEqual(conjunto.vectors.shape, (2, 2))
        self.assertEqual(conjunto.vectors.dtype, np.float16)
        self.assertEqual(conjunto.vectors[1].tolist(), [3.0, 4.0])

    def test_acepta_el_id_del_modelo_o_su_slug(self):
        self._publica_vectores()

        por_id = legalvec.load_vectors(
            "leyes", "lft", "Qwen/Qwen3-Embedding-0.6B", cache_dir=self.tmp)
        por_slug = legalvec.load_vectors("leyes", "lft", "qwen3-0.6b", cache_dir=self.tmp)

        self.assertEqual(por_id.text_sha1, por_slug.text_sha1)

    def test_dedup_por_text_sha1(self):
        # Un hash repetido entre los dos archivos se queda con la version del
        # archivo propio, que se lee primero.
        self._publica_vectores()
        _write_vectors(
            self.release_dir / "vectors-shared-qwen3-0.6b-2.parquet",
            [("propio", [9.0, 9.0]), ("compartido", [3.0, 4.0])], 2,
        )

        conjunto = legalvec.load_vectors("leyes", "lft", "qwen3-0.6b", cache_dir=self.tmp)

        self.assertEqual(conjunto.text_sha1, ["propio", "compartido"])
        self.assertEqual(conjunto.vectors[0].tolist(), [1.0, 2.0])

    def test_un_instrumento_sin_texto_propio_trae_solo_lo_compartido(self):
        _write_vectors(self.release_dir / "vectors-lft-qwen3-0.6b-2.parquet", [], 2)
        _write_vectors(
            self.release_dir / "vectors-shared-qwen3-0.6b-2.parquet",
            [("compartido", [3.0, 4.0])], 2,
        )

        conjunto = legalvec.load_vectors("leyes", "lft", "qwen3-0.6b", cache_dir=self.tmp)

        self.assertEqual(conjunto.text_sha1, ["compartido"])

    def test_la_k_se_lee_del_nombre_del_archivo(self):
        _write_vectors(
            self.release_dir / "vectors-lft-qwen3-4b-3.parquet",
            [("propio", [1.0, 2.0, 3.0])], 3,
        )
        _write_vectors(
            self.release_dir / "vectors-shared-qwen3-4b-3.parquet", [], 3,
        )

        conjunto = legalvec.load_vectors("leyes", "lft", "qwen3-4b", cache_dir=self.tmp)

        self.assertEqual(conjunto.k, 3)
        self.assertEqual(conjunto.vectors.shape, (1, 3))

    def test_sin_asset_lanza_assetnotcached(self):
        with self.assertRaises(legalvec.AssetNotCached):
            legalvec.load_vectors("leyes", "lft", "qwen3-0.6b", cache_dir=self.tmp)


class TestLoadUnits(ConReleaseSintetico):
    def test_lee_units_parquet(self):
        pq.write_table(
            pa.table({"clave": ["lft"], "text_sha1": ["propio"], "text": ["Uno"]}),
            self.release_dir / "units.parquet",
        )

        tabla = legalvec.load_units("leyes", cache_dir=self.tmp)

        self.assertEqual(tabla.column("text_sha1").to_pylist(), ["propio"])

    def test_sin_units_lanza_assetnotcached_nombrando_la_descarga(self):
        with self.assertRaises(legalvec.AssetNotCached) as ctx:
            legalvec.load_units("leyes", cache_dir=self.tmp)

        self.assertIn("download_vectors_assets", str(ctx.exception))


class TestColeccionDesconocida(unittest.TestCase):
    def test_load_units_rechaza_una_coleccion_que_no_existe(self):
        with self.assertRaises(ValueError):
            legalvec.load_units("tratados")

    def test_download_rechaza_una_coleccion_que_no_existe(self):
        with self.assertRaises(ValueError):
            legalvec.download_vectors_assets("tratados")


class TestDownloadVectorsAssets(unittest.TestCase):
    """The only function here that talks to the network, with the HTTP layer
    patched out — the same posture `scjn`'s own downloader tests take."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp))
        self.assets = {
            nombre: f"https://example.invalid/{nombre}" for nombre in (
                release.METADATA_ASSETS
                + ("vectors-lft-qwen3-0.6b-1024.parquet",
                   "vectors-lfd-qwen3-0.6b-1024.parquet",
                   "vectors-shared-qwen3-0.6b-1024.parquet",
                   "vectors-lft-qwen3-4b-2560.parquet",
                   "vectors-shared-qwen3-4b-2560.parquet")
            )
        }

    def _descarga(self, url, timeout):
        return json.dumps({"url": url}).encode("utf-8")

    def test_trae_todo_el_release_cuando_no_se_nombran_claves(self):
        with patch.object(cache, "assets_of_series", return_value=self.assets), \
             patch.object(cache, "download", side_effect=self._descarga):
            resultados = legalvec.download_vectors_assets("leyes", cache_dir=self.tmp)

        nombres = sorted(p.name for p, _ in resultados)
        self.assertEqual(len(resultados), len(self.assets))
        self.assertIn("vectors-shared-qwen3-4b-2560.parquet", nombres)

    def test_una_clave_trae_su_archivo_el_compartido_y_los_metadatos(self):
        # Ni los vectores de un instrumento ni sus textos se pueden leer sin
        # el archivo compartido y sin units.parquet, asi que nombrar una
        # clave nunca significa "solo ese archivo".
        with patch.object(cache, "assets_of_series", return_value=self.assets), \
             patch.object(cache, "download", side_effect=self._descarga):
            resultados = legalvec.download_vectors_assets(
                "leyes", ["lft"], models=("Qwen/Qwen3-Embedding-0.6B",), cache_dir=self.tmp,
            )

        nombres = sorted(p.name for p, _ in resultados)
        self.assertIn("vectors-lft-qwen3-0.6b-1024.parquet", nombres)
        self.assertIn("vectors-shared-qwen3-0.6b-1024.parquet", nombres)
        self.assertIn("units.parquet", nombres)
        self.assertNotIn("vectors-lfd-qwen3-0.6b-1024.parquet", nombres)
        self.assertNotIn("vectors-lft-qwen3-4b-2560.parquet", nombres)

    def test_es_idempotente_por_nombre(self):
        with patch.object(cache, "assets_of_series", return_value=self.assets), \
             patch.object(cache, "download", side_effect=self._descarga):
            legalvec.download_vectors_assets("leyes", [], models=(), cache_dir=self.tmp)
            resultados = legalvec.download_vectors_assets("leyes", [], models=(), cache_dir=self.tmp)

        self.assertTrue(all(not descargado for _p, descargado in resultados))

    def test_cae_en_el_subdirectorio_de_la_coleccion(self):
        with patch.object(cache, "assets_of_series", return_value=self.assets), \
             patch.object(cache, "download", side_effect=self._descarga):
            [(ruta, _)] = legalvec.download_vectors_assets(
                "lineamientos", [], models=(), cache_dir=self.tmp,
            )[:1]

        self.assertEqual(ruta.parent.name, "scjn-lineamientos-vectors")


class TestSerieDePartes(unittest.TestCase):
    """Issue #223's scheme, reused unchanged: a collection over GitHub's
    1,000-asset ceiling is published as `<tag>`, `<tag>-2`, ..., and nothing
    published records how many parts there are — GitHub is asked."""

    def test_camina_hasta_que_una_parte_da_404(self):
        partes = {
            "scjn-reglamentos-vectors": {"a.parquet": "u1"},
            "scjn-reglamentos-vectors-2": {"b.parquet": "u2"},
        }
        with patch.object(cache, "_assets_of_release", side_effect=lambda tag, t: partes.get(tag)):
            assets = cache.assets_of_series("scjn-reglamentos-vectors")

        self.assertEqual(sorted(assets), ["a.parquet", "b.parquet"])

    def test_sin_parte_1_lanza_keyerror_diciendo_que_no_esta_publicado(self):
        with patch.object(cache, "_assets_of_release", return_value=None):
            with self.assertRaises(KeyError) as ctx:
                cache.assets_of_series("scjn-leyes-vectors")

        self.assertIn("no se han publicado", str(ctx.exception))


class TestCacheDir(unittest.TestCase):
    def test_la_variable_de_entorno_manda(self):
        import os
        anterior = os.environ.get("LEGALVEC_CACHE_DIR")
        os.environ["LEGALVEC_CACHE_DIR"] = "/tmp/legalvec-de-prueba"
        try:
            self.assertEqual(cache.default_cache_dir(), Path("/tmp/legalvec-de-prueba"))
        finally:
            if anterior is None:
                del os.environ["LEGALVEC_CACHE_DIR"]
            else:
                os.environ["LEGALVEC_CACHE_DIR"] = anterior


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

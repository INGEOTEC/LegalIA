"""`iter_current_federal_laws` (issue #191): a lazy iterator over the current
text of every federal law in the `scjn-leyes` release, without decoding a
law's whole reform history to get there. Everything is fabricated in memory --
no network call in sight."""

import io
import json
import tarfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from nota2md import scjn


def _hacer_tgz(archivos: dict) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for nombre, contenido in archivos.items():
            data = contenido if isinstance(contenido, bytes) else contenido.encode("utf-8")
            info = tarfile.TarInfo(name=nombre)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


#: The real reader, captured before conftest.py's autouse fixture stubs it out
#: to an empty index for every DOF-path test.
_INDICE_REAL = scjn.download_scjn_leyes_index


class ConIndiceReal(unittest.TestCase):
    def setUp(self):
        parche = patch.object(scjn, "download_scjn_leyes_index", _INDICE_REAL)
        parche.start()
        self.addCleanup(parche.stop)
        self.tmp = Path(__import__("tempfile").mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp))
        self.release = self.tmp / "scjn-leyes"
        self.release.mkdir(parents=True)
        scjn._MEMO_INDICE_GLOBAL.clear()
        self.addCleanup(scjn._MEMO_INDICE_GLOBAL.clear)

    def _publica(self, instrumentos: dict, **tarballs):
        payload = {
            "generado": "2026-08-28T00:00:00+00:00",
            "coleccion": "leyes",
            "instrumentos": instrumentos,
            "codNota": {},
        }
        import gzip

        (self.release / scjn.ASSET_INDICE_GLOBAL).write_bytes(
            gzip.compress(json.dumps(payload).encode("utf-8"))
        )
        for slug, archivos in tarballs.items():
            (self.release / f"{slug}.tgz").write_bytes(_hacer_tgz(archivos))


class TestIterCurrentFederalLawsConIndice(ConIndiceReal):
    """A law that has already been linked -- its own `indice.json` decides
    which snapshot is current."""

    def test_regresa_el_snapshot_de_fecha_mas_reciente(self):
        self._publica(
            {"lfca": {"nombre": "LEY Federal de Cine y el Audiovisual"}},
            lfca={
                "lfca/indice.json": json.dumps([
                    {"archivo": "05-01-1999.md", "fecha_publicacion": "05-01-1999",
                     "codNota": 4967917},
                    {"archivo": "22-05-2026.md", "fecha_publicacion": "22-05-2026",
                     "codNota": 8888888},
                ]),
                "lfca/05-01-1999.md": "TEXTO ORIGINAL",
                "lfca/22-05-2026.md": "TEXTO VIGENTE",
            },
        )

        [ley] = list(scjn.iter_current_federal_laws(["lfca"], cache_dir=self.tmp))

        self.assertEqual(ley["slug"], "lfca")
        self.assertEqual(ley["nombre"], "LEY Federal de Cine y el Audiovisual")
        self.assertEqual(ley["fecha_publicacion"], "22-05-2026")
        self.assertEqual(ley["codNota"], 8888888)
        self.assertEqual(ley["archivo"], "22-05-2026.md")
        self.assertEqual(ley["markdown"], "TEXTO VIGENTE")

    def test_la_fecha_se_compara_como_fecha_no_como_texto(self):
        # "05-01-1999" ordena antes que "22-05-1998" como texto (el "0"
        # inicial le gana al "2"), pero enero de 1999 es posterior a mayo de
        # 1998 -- exactamente lo que el orden lexicografico de DD-MM-YYYY
        # arruinaria si se comparara como cadena.
        self._publica(
            {"lft": {"nombre": "LEY Federal del Trabajo"}},
            lft={
                "lft/indice.json": json.dumps([
                    {"archivo": "22-05-1998.md", "fecha_publicacion": "22-05-1998",
                     "codNota": 1},
                    {"archivo": "05-01-1999.md", "fecha_publicacion": "05-01-1999",
                     "codNota": 2},
                ]),
                "lft/22-05-1998.md": "VIEJO",
                "lft/05-01-1999.md": "NUEVO",
            },
        )

        [ley] = list(scjn.iter_current_federal_laws(["lft"], cache_dir=self.tmp))

        self.assertEqual(ley["fecha_publicacion"], "05-01-1999")
        self.assertEqual(ley["markdown"], "NUEVO")

    def test_empate_de_fecha_desempata_por_archivo_deterministicamente(self):
        self._publica(
            {"cpeum": {"nombre": "CONSTITUCION"}},
            cpeum={
                "cpeum/indice.json": json.dumps([
                    {"archivo": "22-05-2026.md", "fecha_publicacion": "22-05-2026",
                     "codNota": 1},
                    {"archivo": "22-05-2026-2.md", "fecha_publicacion": "22-05-2026",
                     "codNota": 2},
                ]),
                "cpeum/22-05-2026.md": "PRIMERA",
                "cpeum/22-05-2026-2.md": "SEGUNDA",
            },
        )

        primero = list(scjn.iter_current_federal_laws(["cpeum"], cache_dir=self.tmp))
        segundo = list(scjn.iter_current_federal_laws(["cpeum"], cache_dir=self.tmp))

        self.assertEqual(primero, segundo)
        # "22-05-2026.md" > "22-05-2026-2.md" lexicographically (`.` > `-`) --
        # not semantically meaningful, just the deterministic pick.
        self.assertEqual(primero[0]["archivo"], "22-05-2026.md")

    def test_slugs_respeta_el_orden_pedido(self):
        self._publica(
            {"lft": {"nombre": "LFT"}, "lfca": {"nombre": "LFCA"}},
            lft={
                "lft/indice.json": json.dumps(
                    [{"archivo": "a.md", "fecha_publicacion": "01-01-2000",
                      "codNota": 1}]
                ),
                "lft/a.md": "LFT",
            },
            lfca={
                "lfca/indice.json": json.dumps(
                    [{"archivo": "b.md", "fecha_publicacion": "01-01-2000",
                      "codNota": 2}]
                ),
                "lfca/b.md": "LFCA",
            },
        )

        resultado = list(
            scjn.iter_current_federal_laws(["lfca", "lft"], cache_dir=self.tmp)
        )

        self.assertEqual([ley["slug"] for ley in resultado], ["lfca", "lft"])

    def test_es_generador_consumir_uno_no_abre_los_demas_tarballs(self):
        self._publica(
            {"lft": {"nombre": "LFT"}, "lfca": {"nombre": "LFCA"}},
            lft={
                "lft/indice.json": json.dumps(
                    [{"archivo": "a.md", "fecha_publicacion": "01-01-2000",
                      "codNota": 1}]
                ),
                "lft/a.md": "LFT",
            },
            # lfca.tgz never written -- reading it would raise KeyError.
        )

        iterador = scjn.iter_current_federal_laws(["lft", "lfca"], cache_dir=self.tmp)

        primero = next(iterador)
        self.assertEqual(primero["slug"], "lft")

    @patch("nota2md.scjn.requests.get")
    def test_slug_inexistente_propaga_key_error(self, mock_get):
        self._publica({})
        mock_get.return_value = Mock(json=lambda: {"assets": []}, raise_for_status=Mock())

        iterador = scjn.iter_current_federal_laws(["no-existe"], cache_dir=self.tmp)

        with self.assertRaises(KeyError):
            next(iterador)


class TestIterCurrentFederalLawsSinIndice(ConIndiceReal):
    """A law that has been crawled but never linked yet: no `indice.json`,
    so the winner comes from the raw snapshots' own file names."""

    def test_ley_sin_indice_usa_el_archivo_de_fecha_mas_reciente_y_codnota_none(self):
        self._publica(
            {"lfea": {"nombre": "LEY Federal de Extincion de Dominio"}},
            lfea={
                "lfea/22-05-1998.md": "VIEJO",
                "lfea/05-01-1999.md": "NUEVO",
                "lfea/estado.json": json.dumps({"rastreado": "2026-09-01"}),
            },
        )

        [ley] = list(scjn.iter_current_federal_laws(["lfea"], cache_dir=self.tmp))

        self.assertIsNone(ley["codNota"])
        self.assertEqual(ley["archivo"], "05-01-1999.md")
        self.assertEqual(ley["fecha_publicacion"], "05-01-1999")
        self.assertEqual(ley["markdown"], "NUEVO")


class TestIterCurrentFederalLawsSlugsPorDefecto(unittest.TestCase):
    """`slugs=None` prefers the cache (issue #205): a warm cache directory's
    own `<slug>.tgz` file names decide which laws to walk, with no HTTP
    request at all. Only a cold/absent cache, or `refrescar=True`, falls back
    to `scjn_leyes_slugs()` (the release's asset listing) -- not
    `indice-global.json.gz`'s `instrumentos`, since a law crawled but not
    linked yet has a `.tgz` and no entry there."""

    def setUp(self):
        self.tmp = Path(__import__("tempfile").mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp))
        self.release = self.tmp / "scjn-leyes"
        self.release.mkdir(parents=True)
        scjn._MEMO_INDICE_GLOBAL.clear()
        self.addCleanup(scjn._MEMO_INDICE_GLOBAL.clear)

    @patch("nota2md.scjn.requests.get")
    def test_sin_slugs_recorre_los_tgz_publicados(self, mock_get):
        import gzip

        payload = {
            "generado": "x", "coleccion": "leyes",
            "instrumentos": {"lft": {"nombre": "LFT"}}, "codNota": {},
        }
        (self.release / scjn.ASSET_INDICE_GLOBAL).write_bytes(
            gzip.compress(json.dumps(payload).encode("utf-8"))
        )
        (self.release / "lft.tgz").write_bytes(_hacer_tgz({
            "lft/indice.json": json.dumps(
                [{"archivo": "a.md", "fecha_publicacion": "01-01-2000", "codNota": 1}]
            ),
            "lft/a.md": "LFT",
        }))
        mock_get.return_value = Mock(
            json=lambda: {"assets": [
                {"name": "lft.tgz", "browser_download_url": "https://x/lft.tgz"},
                {"name": scjn.ASSET_INDICE_GLOBAL,
                 "browser_download_url": "https://x/indice-global.json.gz"},
            ]},
            raise_for_status=Mock(),
        )
        parche = patch.object(scjn, "download_scjn_leyes_index", _INDICE_REAL)
        parche.start()
        self.addCleanup(parche.stop)

        resultado = list(scjn.iter_current_federal_laws(cache_dir=self.tmp))

        self.assertEqual([ley["slug"] for ley in resultado], ["lft"])
        # Both the slug list and the index are already on disk: a warm
        # cache costs no HTTP request at all (issue #205).
        mock_get.assert_not_called()

    @staticmethod
    def _mock_release(mock_get, lft_tgz: bytes):
        """A `requests.get` stand-in serving a full round trip: the releases
        API listing (both `lft.tgz` and the index), then whichever asset's
        URL gets requested -- `descarga`'s `response.content` has to be real
        bytes, not an auto-generated `Mock` attribute, or writing it to disk
        blows up with a `TypeError` that has nothing to do with what the
        test checks."""
        import gzip

        indice_bytes = gzip.compress(json.dumps({
            "generado": "x", "coleccion": "leyes",
            "instrumentos": {"lft": {"nombre": "LFT"}}, "codNota": {},
        }).encode("utf-8"))

        def _responde(url, **_kwargs):
            if url == scjn._SCJN_LEYES_RELEASES_API:
                return Mock(
                    json=lambda: {"assets": [
                        {"name": "lft.tgz", "browser_download_url": "https://x/lft.tgz"},
                        {"name": scjn.ASSET_INDICE_GLOBAL,
                         "browser_download_url": "https://x/indice-global.json.gz"},
                    ]},
                    raise_for_status=Mock(),
                )
            if url == "https://x/indice-global.json.gz":
                return Mock(content=indice_bytes, raise_for_status=Mock())
            return Mock(content=lft_tgz, raise_for_status=Mock())

        mock_get.side_effect = _responde

    @patch("nota2md.scjn.requests.get")
    def test_sin_slugs_con_cache_vacia_pregunta_al_release(self, mock_get):
        """A cold/absent cache still has to ask the release what it
        publishes -- the cache-first path only applies once there is
        something in the cache to prefer."""
        parche = patch.object(scjn, "download_scjn_leyes_index", _INDICE_REAL)
        parche.start()
        self.addCleanup(parche.stop)
        self._mock_release(mock_get, _hacer_tgz({
            "lft/indice.json": json.dumps(
                [{"archivo": "a.md", "fecha_publicacion": "01-01-2000", "codNota": 1}]
            ),
            "lft/a.md": "LFT",
        }))

        resultado = list(scjn.iter_current_federal_laws(cache_dir=self.tmp))

        self.assertEqual([ley["slug"] for ley in resultado], ["lft"])
        mock_get.assert_called()

    @patch("nota2md.scjn.requests.get")
    def test_refrescar_ignora_la_cache_tibia_y_pregunta_al_release(self, mock_get):
        """`refrescar=True` means "reconcile with the release", so it must
        not shortcut through the cache-first slug listing even when the
        cache already holds a (possibly stale) `lft.tgz`."""
        parche = patch.object(scjn, "download_scjn_leyes_index", _INDICE_REAL)
        parche.start()
        self.addCleanup(parche.stop)
        (self.release / "lft.tgz").write_bytes(_hacer_tgz({
            "lft/indice.json": json.dumps(
                [{"archivo": "vieja.md", "fecha_publicacion": "01-01-2000", "codNota": 1}]
            ),
            "lft/vieja.md": "VIEJA",
        }))
        self._mock_release(mock_get, _hacer_tgz({
            "lft/indice.json": json.dumps(
                [{"archivo": "nueva.md", "fecha_publicacion": "01-01-2020", "codNota": 2}]
            ),
            "lft/nueva.md": "NUEVA",
        }))

        [ley] = list(scjn.iter_current_federal_laws(cache_dir=self.tmp, refrescar=True))

        self.assertEqual(ley["markdown"], "NUEVA")
        mock_get.assert_called()


class TestSlugsInCache(unittest.TestCase):
    """`_slugs_in_cache` (issue #205): the cache-first answer to "which laws
    does this machine have", with no HTTP request."""

    def setUp(self):
        self.tmp = Path(__import__("tempfile").mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp))
        self.release = self.tmp / "scjn-leyes"
        self.release.mkdir(parents=True)

    def test_lista_los_slugs_de_los_tgz_ordenados(self):
        (self.release / "lft.tgz").write_bytes(b"x")
        (self.release / "cpeum.tgz").write_bytes(b"x")

        self.assertEqual(scjn._slugs_in_cache(self.tmp), ["cpeum", "lft"])

    def test_ignora_una_descarga_interrumpida(self):
        (self.release / "lft.tgz").write_bytes(b"x")
        (self.release / "lfca.tgz.parcial").write_bytes(b"x")

        self.assertEqual(scjn._slugs_in_cache(self.tmp), ["lft"])

    def test_ignora_el_indice_global_y_el_sha256sums(self):
        (self.release / "lft.tgz").write_bytes(b"x")
        (self.release / scjn.ASSET_INDICE_GLOBAL).write_bytes(b"x")
        (self.release / "SHA256SUMS.txt").write_bytes(b"x")

        self.assertEqual(scjn._slugs_in_cache(self.tmp), ["lft"])

    def test_cache_dir_none_no_tiene_cache_que_ofrecer(self):
        self.assertEqual(scjn._slugs_in_cache(None), [])

    def test_directorio_de_release_inexistente(self):
        vacio = Path(__import__("tempfile").mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(vacio))

        self.assertEqual(scjn._slugs_in_cache(vacio), [])


class IterCurrentFederalLawsOfflineTest(unittest.TestCase):
    """The regression test for issue #205: a fully warm cache must serve
    `iter_current_federal_laws()` with the network genuinely unreachable,
    not merely un-consulted."""

    def setUp(self):
        self.tmp = Path(__import__("tempfile").mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp))
        self.release = self.tmp / "scjn-leyes"
        self.release.mkdir(parents=True)
        scjn._MEMO_INDICE_GLOBAL.clear()
        self.addCleanup(scjn._MEMO_INDICE_GLOBAL.clear)

    @patch("nota2md.scjn.requests.get", side_effect=ConnectionError("network unreachable"))
    def test_cache_completa_no_necesita_red(self, mock_get):
        import gzip

        payload = {
            "generado": "x", "coleccion": "leyes",
            "instrumentos": {"lft": {"nombre": "LFT"}, "lfca": {"nombre": "LFCA"}},
            "codNota": {},
        }
        (self.release / scjn.ASSET_INDICE_GLOBAL).write_bytes(
            gzip.compress(json.dumps(payload).encode("utf-8"))
        )
        (self.release / "lft.tgz").write_bytes(_hacer_tgz({
            "lft/indice.json": json.dumps(
                [{"archivo": "a.md", "fecha_publicacion": "01-01-2000", "codNota": 1}]
            ),
            "lft/a.md": "LFT",
        }))
        (self.release / "lfca.tgz").write_bytes(_hacer_tgz({
            "lfca/indice.json": json.dumps(
                [{"archivo": "b.md", "fecha_publicacion": "01-01-2000", "codNota": 2}]
            ),
            "lfca/b.md": "LFCA",
        }))
        parche = patch.object(scjn, "download_scjn_leyes_index", _INDICE_REAL)
        parche.start()
        self.addCleanup(parche.stop)

        resultado = list(scjn.iter_current_federal_laws(cache_dir=self.tmp))

        self.assertEqual(
            sorted(ley["slug"] for ley in resultado), ["lfca", "lft"]
        )
        mock_get.assert_not_called()

    @patch("nota2md.scjn.requests.get", side_effect=ConnectionError("network unreachable"))
    def test_tgz_sin_indice_global_degrada_nombre_a_none(self, mock_get):
        """The index is a separate asset from the tarballs: a cache that
        never got it must not try an HTTP request just to fill `nombre` in
        (see DECISION 2 in the run's notes) -- it degrades to `None`."""
        (self.release / "lft.tgz").write_bytes(_hacer_tgz({
            "lft/indice.json": json.dumps(
                [{"archivo": "a.md", "fecha_publicacion": "01-01-2000", "codNota": 1}]
            ),
            "lft/a.md": "LFT",
        }))

        [ley] = list(scjn.iter_current_federal_laws(cache_dir=self.tmp))

        self.assertIsNone(ley["nombre"])
        mock_get.assert_not_called()

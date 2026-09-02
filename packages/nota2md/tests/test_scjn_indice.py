"""The reverse index of the scjn-leyes release (issue #117, Pasos 1 y 3):
building `indice-global.json.gz`, reading it back, and resolving a codNota to
the law snapshot it reformed. Everything is fabricated in memory -- the only
network call in sight is a mocked `requests.get`."""

import gzip
import io
import json
import tarfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from nota2md import cache, scjn


def _hacer_tgz(archivos: dict) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for nombre, contenido in archivos.items():
            data = contenido if isinstance(contenido, bytes) else contenido.encode("utf-8")
            info = tarfile.TarInfo(name=nombre)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _indice_global(cod_notas: dict, instrumentos: dict | None = None) -> bytes:
    """A gzipped `indice-global.json.gz`, as the packaging script writes it --
    codNota keys as strings, values as lists."""
    payload = {
        "generado": "2026-08-28T14:35:31+00:00",
        "coleccion": "leyes",
        "instrumentos": instrumentos or {},
        "codNota": cod_notas,
    }
    return gzip.compress(json.dumps(payload).encode("utf-8"))


class TestConstruyeIndiceGlobal(unittest.TestCase):
    def test_invierte_el_indice_por_codnota(self):
        indice, _ = scjn.construye_indice_global(
            [{
                "slug": "lfca", "nombre": "LEY Federal de Cine", "asset": "lfca.tgz",
                "indice": [{"archivo": "05-01-1999.md", "codNota": 4967917,
                            "title_link_status": "linked",
                            "content_diff_confirmed_codNota": 4967917,
                            "content_diff_score": 0.991}],
            }],
            generado="2026-08-28T00:00:00+00:00",
        )

        self.assertEqual(indice["coleccion"], "leyes")
        self.assertEqual(indice["instrumentos"]["lfca"]["snapshots"], 1)
        self.assertEqual(
            indice["codNota"]["4967917"],
            [{"slug": "lfca", "archivo": "05-01-1999.md",
              "title_link_status": "linked",
              "content_diff_confirmed_codNota": 4967917,
              "content_diff_score": 0.991}],
        )

    def test_las_claves_codnota_son_cadenas_porque_json_no_tiene_enteros(self):
        indice, _ = scjn.construye_indice_global(
            [{"slug": "lft", "nombre": "LFT",
              "indice": [{"archivo": "01-04-1970.md", "codNota": 100}]}],
            generado="x",
        )

        self.assertEqual(list(indice["codNota"]), ["100"])

    def test_un_codnota_que_reforma_dos_leyes_conserva_ambas(self):
        # El caso D4 de #117: si el valor fuera un objeto en vez de una lista,
        # la segunda ley pisaria a la primera sin que nadie se enterara.
        indice, _ = scjn.construye_indice_global(
            [
                {"slug": "lft", "nombre": "LFT",
                 "indice": [{"archivo": "01-05-2019.md", "codNota": 500}]},
                {"slug": "lss", "nombre": "LSS",
                 "indice": [{"archivo": "01-05-2019.md", "codNota": 500}]},
            ],
            generado="x",
        )

        self.assertEqual(
            [e["slug"] for e in indice["codNota"]["500"]], ["lft", "lss"]
        )

    def test_solo_entra_lo_enlazado_y_lo_demas_se_cuenta_por_motivo(self):
        indice, conteos = scjn.construye_indice_global(
            [
                {"slug": "lft", "nombre": "LFT", "indice": [
                    {"archivo": "a.md", "codNota": 1, "title_link_status": "linked"},
                    {"archivo": "b.md", "codNota": None, "title_link_status": "ambiguous"},
                    {"archivo": "c.md", "codNota": None, "title_link_status": "unlinked"},
                ]},
                {"slug": "lfea", "nombre": "LFEA", "indice": None, "snapshots": 3},
            ],
            generado="x",
        )

        self.assertEqual(list(indice["codNota"]), ["1"])
        self.assertEqual(conteos["linked"], 1)
        self.assertEqual(conteos["ambiguous"], 1)
        self.assertEqual(conteos["unlinked"], 1)
        self.assertEqual(conteos["sin_indice"], 1)

    def test_una_ley_rastreada_pero_no_enlazada_sigue_apareciendo_en_instrumentos(self):
        indice, _ = scjn.construye_indice_global(
            [{"slug": "lfea", "nombre": "LFEA", "indice": None, "snapshots": 7}],
            generado="x",
        )

        self.assertEqual(indice["instrumentos"]["lfea"]["snapshots"], 7)
        self.assertEqual(indice["instrumentos"]["lfea"]["asset"], "lfea.tgz")

    def test_los_codnota_quedan_ordenados_numericamente_no_como_texto(self):
        # Byte-reproducibilidad del asset: "1000" < "9" como texto.
        indice, _ = scjn.construye_indice_global(
            [{"slug": "lft", "nombre": "LFT", "indice": [
                {"archivo": "a.md", "codNota": 1000},
                {"archivo": "b.md", "codNota": 9},
            ]}],
            generado="x",
        )

        self.assertEqual(list(indice["codNota"]), ["9", "1000"])


#: The real reader, captured before any test runs. `conftest.py` stubs the
#: module attribute out so the DOF-path tests never reach the network; the
#: tests below are the ones that exercise the reader itself, so they put it
#: back (the stub reverts when each test ends, as usual).
_INDICE_REAL = scjn.download_scjn_leyes_index


class ConIndiceReal(unittest.TestCase):
    """Base for the tests that mean to run `download_scjn_leyes_index` for
    real instead of conftest's covers-nothing stub."""

    def setUp(self):
        parche = patch.object(scjn, "download_scjn_leyes_index", _INDICE_REAL)
        parche.start()
        self.addCleanup(parche.stop)


class TestDownloadScjnLeyesIndex(ConIndiceReal):
    @staticmethod
    def _respuestas(contenido: bytes) -> list:
        return [
            Mock(json=lambda: {"assets": [
                {"name": scjn.ASSET_INDICE_GLOBAL,
                 "browser_download_url": "https://x/indice-global.json.gz"}
            ]}, raise_for_status=Mock()),
            Mock(content=contenido, raise_for_status=Mock()),
        ]

    @patch("nota2md.scjn.requests.get")
    def test_convierte_las_claves_codnota_a_entero(self, mock_get):
        mock_get.side_effect = self._respuestas(
            _indice_global({"4967917": [{"slug": "lfca", "archivo": "05-01-1999.md"}]})
        )

        indice = scjn.download_scjn_leyes_index(cache_dir=None)

        self.assertEqual(list(indice["codNota"]), [4967917])

    @patch("nota2md.scjn.requests.get")
    def test_se_memoiza_para_no_releer_el_indice_por_cada_nota(self, mock_get):
        mock_get.side_effect = self._respuestas(_indice_global({"1": []}))

        primero = scjn.download_scjn_leyes_index(cache_dir=None)
        segundo = scjn.download_scjn_leyes_index(cache_dir=None)

        self.assertIs(primero, segundo)
        self.assertEqual(mock_get.call_count, 2)  # listar assets + bajar, una vez

    @patch("nota2md.scjn.requests.get")
    def test_refrescar_ignora_la_memoizacion(self, mock_get):
        mock_get.side_effect = (
            self._respuestas(_indice_global({"1": []}))
            + self._respuestas(_indice_global({"2": []}))
        )

        scjn.download_scjn_leyes_index(cache_dir=None)
        indice = scjn.download_scjn_leyes_index(cache_dir=None, refrescar=True)

        self.assertEqual(list(indice["codNota"]), [2])

    @patch("nota2md.scjn.requests.get")
    def test_lanza_key_error_mientras_el_asset_no_este_publicado(self, mock_get):
        mock_get.return_value = Mock(json=lambda: {"assets": []}, raise_for_status=Mock())

        with self.assertRaises(KeyError):
            scjn.download_scjn_leyes_index(cache_dir=None)

    @patch("nota2md.scjn.requests.get")
    def test_un_indice_ya_en_cache_no_provoca_ni_una_peticion(self, mock_get):
        tmp = Path(__import__("tempfile").mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp))
        destino = tmp / "scjn-leyes" / scjn.ASSET_INDICE_GLOBAL
        destino.parent.mkdir(parents=True)
        destino.write_bytes(_indice_global({"7": [{"slug": "lft", "archivo": "a.md"}]}))

        indice = scjn.download_scjn_leyes_index(cache_dir=tmp)

        mock_get.assert_not_called()
        self.assertEqual(list(indice["codNota"]), [7])


class TestSnapshotDeCodNota(ConIndiceReal):
    def setUp(self):
        super().setUp()
        self.tmp = Path(__import__("tempfile").mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp))
        self.release = self.tmp / "scjn-leyes"
        self.release.mkdir(parents=True)

    def _publica(self, cod_notas: dict, instrumentos: dict | None = None, **tarballs):
        """Fabricate an already-cached release: the reverse index plus a
        `<slug>.tgz` per law, so a lookup makes no request at all."""
        (self.release / scjn.ASSET_INDICE_GLOBAL).write_bytes(
            _indice_global(cod_notas, instrumentos)
        )
        for slug, archivos in tarballs.items():
            (self.release / f"{slug}.tgz").write_bytes(_hacer_tgz(archivos))

    def test_regresa_el_markdown_del_snapshot_de_esa_reforma(self):
        self._publica(
            {"4967917": [{"slug": "lfca", "archivo": "05-01-1999.md"}]},
            lfca={"lfca/05-01-1999.md": "fuente: scjn\n\n**TEXTO ORIGINAL.**"},
        )

        slug, archivo, markdown = scjn.snapshot_de_codNota(4967917, cache_dir=self.tmp)

        self.assertEqual((slug, archivo), ("lfca", "05-01-1999.md"))
        self.assertIn("fuente: scjn", markdown)

    def test_regresa_none_cuando_el_codnota_no_esta_en_el_indice(self):
        self._publica({})

        self.assertIsNone(scjn.snapshot_de_codNota(999, cache_dir=self.tmp))

    def test_lanza_value_error_cuando_el_decreto_reforma_varias_leyes(self):
        self._publica(
            {"500": [{"slug": "lft", "archivo": "a.md"},
                     {"slug": "lss", "archivo": "b.md"}]},
            {"lft": {"nombre": "Ley Federal del Trabajo"},
             "lss": {"nombre": "Ley del Seguro Social"}},
        )

        with self.assertRaises(ValueError) as ctx:
            scjn.snapshot_de_codNota(500, cache_dir=self.tmp)

        # El error tiene que decir entre que elegir, no solo que hubo empate.
        self.assertIn("lft", str(ctx.exception))
        self.assertIn("Ley del Seguro Social", str(ctx.exception))

    def test_instrumento_desempata_entre_varias_leyes(self):
        self._publica(
            {"500": [{"slug": "lft", "archivo": "a.md"},
                     {"slug": "lss", "archivo": "b.md"}]},
            lss={"lss/b.md": "TEXTO DE LA LSS"},
        )

        slug, _, markdown = scjn.snapshot_de_codNota(
            500, instrumento="lss", cache_dir=self.tmp
        )

        self.assertEqual(slug, "lss")
        self.assertEqual(markdown, "TEXTO DE LA LSS")

    def test_instrumento_que_no_reforma_ese_codnota_es_un_error_no_un_none(self):
        # Distinto de "sin cobertura": el llamador afirmo algo que el indice
        # contradice, y caer al DOF en silencio lo esconderia.
        self._publica({"500": [{"slug": "lft", "archivo": "a.md"}]})

        with self.assertRaises(ValueError):
            scjn.snapshot_de_codNota(500, instrumento="lss", cache_dir=self.tmp)

    def test_acepta_un_codnota_en_texto_igual_que_en_entero(self):
        self._publica(
            {"7": [{"slug": "lft", "archivo": "a.md"}]},
            lft={"lft/a.md": "TEXTO"},
        )

        self.assertIsNotNone(scjn.snapshot_de_codNota("7", cache_dir=self.tmp))


class TestDownloadScjnLeyesCorpusConCache(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(__import__("tempfile").mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp))

    @patch("nota2md.scjn.requests.get")
    def test_lee_el_tarball_de_la_cache_sin_tocar_la_red(self, mock_get):
        destino = self.tmp / "scjn-leyes" / "lfca.tgz"
        destino.parent.mkdir(parents=True)
        destino.write_bytes(_hacer_tgz({
            "lfca/indice.json": json.dumps(
                [{"archivo": "05-01-1999.md", "codNota": 4967917}]
            ),
            "lfca/05-01-1999.md": "**TEXTO ORIGINAL.**",
        }))

        resultado = scjn.download_scjn_leyes_corpus("lfca", cache_dir=self.tmp)

        mock_get.assert_not_called()
        self.assertEqual(resultado["snapshots"][0]["markdown"], "**TEXTO ORIGINAL.**")

    @patch("nota2md.scjn.requests.get")
    def test_baja_el_tarball_y_lo_deja_en_la_cache_para_la_proxima(self, mock_get):
        contenido = _hacer_tgz({"lfca/05-01-1999.md": "**TEXTO ORIGINAL.**"})
        mock_get.side_effect = [
            Mock(json=lambda: {"assets": [
                {"name": "lfca.tgz", "browser_download_url": "https://x/lfca.tgz"}
            ]}, raise_for_status=Mock()),
            Mock(content=contenido, raise_for_status=Mock()),
        ]

        scjn.download_scjn_leyes_corpus("lfca", cache_dir=self.tmp)

        self.assertTrue((self.tmp / "scjn-leyes" / "lfca.tgz").is_file())

    @patch("nota2md.scjn.requests.get")
    def test_cache_dir_none_no_escribe_nada_en_disco(self, mock_get):
        contenido = _hacer_tgz({"lfca/05-01-1999.md": "**TEXTO ORIGINAL.**"})
        mock_get.side_effect = [
            Mock(json=lambda: {"assets": [
                {"name": "lfca.tgz", "browser_download_url": "https://x/lfca.tgz"}
            ]}, raise_for_status=Mock()),
            Mock(content=contenido, raise_for_status=Mock()),
        ]

        with patch.object(cache, "CACHE_DIR", self.tmp):
            scjn.download_scjn_leyes_corpus("lfca", cache_dir=None)

        self.assertEqual(list(self.tmp.iterdir()), [])


class TestDownloadScjnLeyesCatalog(ConIndiceReal):
    """The seed read back out of the release (issue #185, Fase 0 of #184):
    `nombre`/`abrev` off `indice-global.json.gz`, `actualizado` off each law's
    own `estado.json`. Fabricated in memory, byte for byte in the shapes the
    published release uses -- the `estado.json` bodies below are the real ones
    of `lfca` and `lfcpq`."""

    def setUp(self):
        super().setUp()
        self.tmp = Path(__import__("tempfile").mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp))
        self.release = self.tmp / "scjn-leyes"
        self.release.mkdir(parents=True)
        scjn._MEMO_INDICE_GLOBAL.clear()
        self.addCleanup(scjn._MEMO_INDICE_GLOBAL.clear)

    def _publica(self, instrumentos: dict, **tarballs):
        (self.release / scjn.ASSET_INDICE_GLOBAL).write_bytes(
            _indice_global({}, instrumentos)
        )
        for slug, archivos in tarballs.items():
            (self.release / f"{slug}.tgz").write_bytes(_hacer_tgz(archivos))

    def test_regresa_nombre_abrev_y_actualizado_ordenado_por_abrev(self):
        self._publica(
            {
                "lft": {"nombre": "LEY Federal del Trabajo", "asset": "lft.tgz",
                        "snapshots": 2},
                "lfca": {"nombre": "LEY Federal de Cine y el Audiovisual",
                         "asset": "lfca.tgz", "snapshots": 1},
            },
            lfca={"lfca/estado.json": json.dumps({
                "actualizado": "2026-05-22", "enlazado": "2026-09-01",
                "id_ordenamiento": "188805", "rastreado": "2026-09-01"})},
            lft={"lft/estado.json": json.dumps({"actualizado": "2025-06-13"})},
        )

        catalogo = scjn.download_scjn_leyes_catalog(cache_dir=self.tmp)

        self.assertEqual(catalogo, [
            {"abrev": "lfca", "nombre": "LEY Federal de Cine y el Audiovisual",
             "actualizado": "2026-05-22"},
            {"abrev": "lft", "nombre": "LEY Federal del Trabajo",
             "actualizado": "2025-06-13"},
        ])

    def test_actualizado_nulo_queda_ausente_no_en_none(self):
        # `lfcpq` is one of the three laws whose `estado.json` records
        # `actualizado: null`. Absent means "freshness unknown, always
        # review" -- exactly what `motivo_pendiente` reads it as; a None
        # would be a value the planner has to special-case instead.
        self._publica(
            {"lfcpq": {"nombre": "LEY Federal de Cinematografia",
                       "asset": "lfcpq.tgz", "snapshots": 8}},
            lfcpq={"lfcpq/estado.json": json.dumps({
                "actualizado": None, "enlazado": "2026-09-01",
                "id_ordenamiento": "11057", "rastreado": "2026-09-01"})},
        )

        catalogo = scjn.download_scjn_leyes_catalog(cache_dir=self.tmp)

        self.assertEqual(catalogo, [{"abrev": "lfcpq",
                                     "nombre": "LEY Federal de Cinematografia"}])

    def test_un_tarball_sin_estado_json_tampoco_inventa_actualizado(self):
        self._publica(
            {"lft": {"nombre": "LEY Federal del Trabajo", "asset": "lft.tgz",
                     "snapshots": 1}},
            lft={"lft/01-04-1970.md": "**TEXTO ORIGINAL.**"},
        )

        catalogo = scjn.download_scjn_leyes_catalog(cache_dir=self.tmp)

        self.assertEqual(catalogo, [{"abrev": "lft",
                                     "nombre": "LEY Federal del Trabajo"}])

    def test_freshness_false_no_abre_ni_un_tarball(self):
        self._publica({"lft": {"nombre": "LEY Federal del Trabajo",
                               "asset": "lft.tgz", "snapshots": 1}})

        catalogo = scjn.download_scjn_leyes_catalog(
            freshness=False, cache_dir=self.tmp
        )

        # No `lft.tgz` was ever written: reading one would raise.
        self.assertEqual(catalogo, [{"abrev": "lft",
                                     "nombre": "LEY Federal del Trabajo"}])

    def test_el_slug_del_release_es_el_abrev_normalizado(self):
        # 14 laws carry an underscore in their historical `abrev`
        # (`lif_2026`, `pef_2026`, the `lrart*` reglamentarias...) and the
        # release slug hyphenates it. The reader returns the slug; whoever
        # merges this into an existing `catalogo.json` matches on
        # `slug_instrumento` and keeps its own `abrev` verbatim (issue #184).
        self._publica({"lif-2026": {"nombre": "LEY de Ingresos de la Federacion",
                                    "asset": "lif-2026.tgz", "snapshots": 1}})

        catalogo = scjn.download_scjn_leyes_catalog(
            freshness=False, cache_dir=self.tmp
        )

        self.assertEqual(catalogo[0]["abrev"], "lif-2026")
        self.assertEqual(
            scjn.slug_instrumento({"abrev": "lif_2026"}), catalogo[0]["abrev"]
        )

    @patch("nota2md.scjn.requests.get")
    def test_un_release_ya_en_cache_no_provoca_ni_una_peticion(self, mock_get):
        self._publica(
            {"lft": {"nombre": "LEY Federal del Trabajo", "asset": "lft.tgz",
                     "snapshots": 1}},
            lft={"lft/estado.json": json.dumps({"actualizado": "2025-06-13"})},
        )

        scjn.download_scjn_leyes_catalog(cache_dir=self.tmp)

        mock_get.assert_not_called()

    @patch("nota2md.scjn.requests.get")
    def test_lanza_key_error_mientras_el_release_no_publique_el_asset(self, mock_get):
        mock_get.return_value = Mock(json=lambda: {"assets": []},
                                     raise_for_status=Mock())

        with self.assertRaises(KeyError):
            scjn.download_scjn_leyes_catalog(cache_dir=None)

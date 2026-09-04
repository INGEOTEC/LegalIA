"""scjn.release: the scjn-leyes release's own readers -- disk-first since
issue #209, so every one of them is fabricated in memory and exercised
against a `tmp_path` fixture, never a mocked `requests.get`. `TestOffline`
at the bottom is the regression test for the disk-first contract itself:
every reader still answers with `requests.get` monkeypatched to raise."""

import gzip
import io
import json
import tarfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scjn import release


def _hacer_tgz(archivos: dict) -> bytes:
    """Build an in-memory tarball from {member_name: raw_bytes_or_str}."""
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
        indice, _ = release.construye_indice_global(
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
        indice, _ = release.construye_indice_global(
            [{"slug": "lft", "nombre": "LFT",
              "indice": [{"archivo": "01-04-1970.md", "codNota": 100}]}],
            generado="x",
        )

        self.assertEqual(list(indice["codNota"]), ["100"])

    def test_un_codnota_que_reforma_dos_leyes_conserva_ambas(self):
        # El caso D4 de #117: si el valor fuera un objeto en vez de una lista,
        # la segunda ley pisaria a la primera sin que nadie se enterara.
        indice, _ = release.construye_indice_global(
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
        indice, conteos = release.construye_indice_global(
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
        indice, _ = release.construye_indice_global(
            [{"slug": "lfea", "nombre": "LFEA", "indice": None, "snapshots": 7}],
            generado="x",
        )

        self.assertEqual(indice["instrumentos"]["lfea"]["snapshots"], 7)
        self.assertEqual(indice["instrumentos"]["lfea"]["asset"], "lfea.tgz")

    def test_los_codnota_quedan_ordenados_numericamente_no_como_texto(self):
        # Byte-reproducibilidad del asset: "1000" < "9" como texto.
        indice, _ = release.construye_indice_global(
            [{"slug": "lft", "nombre": "LFT", "indice": [
                {"archivo": "a.md", "codNota": 1000},
                {"archivo": "b.md", "codNota": 9},
            ]}],
            generado="x",
        )

        self.assertEqual(list(indice["codNota"]), ["9", "1000"])


class ConCacheFixture(unittest.TestCase):
    """Base for the readers below: a `tmp_path`-style `scjn-leyes/` directory
    populated by hand, no network in sight."""

    def setUp(self):
        self.tmp = Path(__import__("tempfile").mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp))
        self.release_dir = self.tmp / "scjn-leyes"
        self.release_dir.mkdir(parents=True)

    def _publica_indice(self, cod_notas: dict, instrumentos: dict | None = None):
        (self.release_dir / release.ASSET_INDICE_GLOBAL).write_bytes(
            _indice_global(cod_notas, instrumentos)
        )

    def _publica_tgz(self, slug: str, **archivos):
        (self.release_dir / f"{slug}.tgz").write_bytes(_hacer_tgz(archivos))


class TestDownloadScjnLeyesIndex(ConCacheFixture):
    def test_convierte_las_claves_codnota_a_entero(self):
        self._publica_indice({"4967917": [{"slug": "lfca", "archivo": "05-01-1999.md"}]})

        indice = release.download_scjn_leyes_index(cache_dir=self.tmp)

        self.assertEqual(list(indice["codNota"]), [4967917])

    def test_se_memoiza_para_no_releer_el_indice_por_cada_nota(self):
        self._publica_indice({"1": []})

        primero = release.download_scjn_leyes_index(cache_dir=self.tmp)
        segundo = release.download_scjn_leyes_index(cache_dir=self.tmp)

        self.assertIs(primero, segundo)

    def test_asset_no_cacheado_lanza_assetnotcached(self):
        with self.assertRaises(release.AssetNotCached) as ctx:
            release.download_scjn_leyes_index(cache_dir=self.tmp)

        self.assertIn(release.ASSET_INDICE_GLOBAL, str(ctx.exception))
        self.assertIn("scjn download", str(ctx.exception))


class TestDownloadScjnLeyesCorpus(ConCacheFixture):
    def test_lanza_assetnotcached_cuando_el_tarball_no_esta_en_cache(self):
        with self.assertRaises(release.AssetNotCached) as ctx:
            release.download_scjn_leyes_corpus("cpeum", cache_dir=self.tmp)

        self.assertIn("cpeum.tgz", str(ctx.exception))

    def test_une_indice_con_el_markdown_de_cada_snapshot(self):
        self._publica_tgz(
            "cpeum",
            **{
                "cpeum/indice.json": json.dumps([
                    {"archivo": "22-01-1994.md", "codNota": 100, "ratio_similitud": 0.9,
                     "sospechoso": False, "title_candidates": [100],
                     "title_link_status": "linked",
                     "content_diff_confirmed_codNota": None, "content_diff_score": None},
                ]),
                "cpeum/22-01-1994.md": "**TEXTO ORIGINAL.**",
            },
        )

        resultado = release.download_scjn_leyes_corpus("cpeum", cache_dir=self.tmp)

        self.assertEqual(resultado["slug"], "cpeum")
        snap = resultado["snapshots"][0]
        self.assertEqual(snap["codNota"], 100)
        self.assertEqual(snap["title_link_status"], "linked")
        self.assertEqual(snap["markdown"], "**TEXTO ORIGINAL.**")

    def test_cada_snapshot_trae_el_texto_dof_de_los_candidatos_considerados(self):
        # Lo que hace auditable el enlace de #126/#127 sin volver a la red:
        # el snapshot llega con el texto de cada candidato que se comparo,
        # no solo con el codNota ganador.
        self._publica_tgz(
            "lft",
            **{
                "lft/indice.json": json.dumps([
                    {"archivo": "22-01-1994.md", "codNota": 100,
                     "title_candidates": [100, 101],
                     "content_diff_confirmed_codNota": 100, "content_diff_score": 0.8},
                    {"archivo": "01-01-1995.md", "codNota": None, "title_candidates": []},
                ]),
                "lft/22-01-1994.md": "**TEXTO ORIGINAL.**",
                "lft/01-01-1995.md": "**REFORMA.**",
                "lft/notas/nota-100.md": "DECRETO uno.",
                "lft/notas/nota-101.md": "DECRETO dos.",
            },
        )

        snapshots = release.download_scjn_leyes_corpus("lft", cache_dir=self.tmp)["snapshots"]

        self.assertEqual(snapshots[0]["notas"], {100: "DECRETO uno.", 101: "DECRETO dos."})
        self.assertEqual(snapshots[1]["notas"], {})

    def test_instrumento_sin_indice_json_regresa_snapshots_sin_enlace_en_vez_de_omitirse(self):
        # Fase 2 (issue #105) pendiente para este instrumento: hay
        # snapshots pero enlaza_scjn_legislacion.py no ha corrido para el.
        self._publica_tgz("lfea", **{"lfea/01-01-2012.md": "**TEXTO ORIGINAL.**"})

        resultado = release.download_scjn_leyes_corpus("lfea", cache_dir=self.tmp)

        snap = resultado["snapshots"][0]
        self.assertEqual(snap["archivo"], "01-01-2012.md")
        self.assertIsNone(snap["codNota"])
        self.assertEqual(snap["markdown"], "**TEXTO ORIGINAL.**")


class TestMarkdownDeSnapshot(ConCacheFixture):
    def test_regresa_el_markdown_del_snapshot(self):
        self._publica_tgz("lfca", **{"lfca/05-01-1999.md": "fuente: scjn\n\n**TEXTO.**"})

        markdown = release.markdown_de_snapshot("lfca", "05-01-1999.md", cache_dir=self.tmp)

        self.assertIn("fuente: scjn", markdown)

    def test_lanza_assetnotcached_sin_tarball(self):
        with self.assertRaises(release.AssetNotCached):
            release.markdown_de_snapshot("lfca", "05-01-1999.md", cache_dir=self.tmp)


class TestLocalSlugs(unittest.TestCase):
    """`local_slugs` (issue #205, made public in #209): the cache-first
    answer to "which laws does this machine have", with no HTTP request."""

    def setUp(self):
        self.tmp = Path(__import__("tempfile").mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp))
        self.release_dir = self.tmp / "scjn-leyes"
        self.release_dir.mkdir(parents=True)

    def test_lista_los_slugs_de_los_tgz_ordenados(self):
        (self.release_dir / "lft.tgz").write_bytes(b"x")
        (self.release_dir / "cpeum.tgz").write_bytes(b"x")

        self.assertEqual(release.local_slugs(self.tmp), ["cpeum", "lft"])

    def test_ignora_una_descarga_interrumpida(self):
        (self.release_dir / "lft.tgz").write_bytes(b"x")
        (self.release_dir / "lfca.tgz.parcial").write_bytes(b"x")

        self.assertEqual(release.local_slugs(self.tmp), ["lft"])

    def test_ignora_el_indice_global_y_el_sha256sums(self):
        (self.release_dir / "lft.tgz").write_bytes(b"x")
        (self.release_dir / release.ASSET_INDICE_GLOBAL).write_bytes(b"x")
        (self.release_dir / "SHA256SUMS.txt").write_bytes(b"x")

        self.assertEqual(release.local_slugs(self.tmp), ["lft"])

    def test_directorio_de_release_inexistente(self):
        vacio = Path(__import__("tempfile").mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(vacio))

        self.assertEqual(release.local_slugs(vacio), [])

    def test_none_usa_el_cache_dir_del_paquete(self):
        with patch("scjn.cache.CACHE_DIR", self.tmp):
            self.assertEqual(release.local_slugs(None), [])


class TestIterCurrentFederalLawsConIndice(ConCacheFixture):
    """A law that has already been linked -- its own `indice.json` decides
    which snapshot is current."""

    def test_regresa_el_snapshot_de_fecha_mas_reciente(self):
        self._publica_indice({}, {"lfca": {"nombre": "LEY Federal de Cine y el Audiovisual"}})
        self._publica_tgz(
            "lfca",
            **{
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

        [ley] = list(release.iter_current_federal_laws(["lfca"], cache_dir=self.tmp))

        self.assertEqual(ley["slug"], "lfca")
        self.assertEqual(ley["nombre"], "LEY Federal de Cine y el Audiovisual")
        self.assertEqual(ley["fecha_publicacion"], "22-05-2026")
        self.assertEqual(ley["codNota"], 8888888)
        self.assertEqual(ley["markdown"], "TEXTO VIGENTE")

    def test_la_fecha_se_compara_como_fecha_no_como_texto(self):
        self._publica_indice({}, {"lft": {"nombre": "LEY Federal del Trabajo"}})
        self._publica_tgz(
            "lft",
            **{
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

        [ley] = list(release.iter_current_federal_laws(["lft"], cache_dir=self.tmp))

        self.assertEqual(ley["fecha_publicacion"], "05-01-1999")
        self.assertEqual(ley["markdown"], "NUEVO")

    def test_empate_de_fecha_desempata_por_archivo_deterministicamente(self):
        self._publica_indice({}, {"cpeum": {"nombre": "CONSTITUCION"}})
        self._publica_tgz(
            "cpeum",
            **{
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

        primero = list(release.iter_current_federal_laws(["cpeum"], cache_dir=self.tmp))
        segundo = list(release.iter_current_federal_laws(["cpeum"], cache_dir=self.tmp))

        self.assertEqual(primero, segundo)
        self.assertEqual(primero[0]["archivo"], "22-05-2026.md")

    def test_slugs_respeta_el_orden_pedido(self):
        self._publica_indice({}, {"lft": {"nombre": "LFT"}, "lfca": {"nombre": "LFCA"}})
        self._publica_tgz("lft", **{
            "lft/indice.json": json.dumps(
                [{"archivo": "a.md", "fecha_publicacion": "01-01-2000", "codNota": 1}]
            ),
            "lft/a.md": "LFT",
        })
        self._publica_tgz("lfca", **{
            "lfca/indice.json": json.dumps(
                [{"archivo": "b.md", "fecha_publicacion": "01-01-2000", "codNota": 2}]
            ),
            "lfca/b.md": "LFCA",
        })

        resultado = list(
            release.iter_current_federal_laws(["lfca", "lft"], cache_dir=self.tmp)
        )

        self.assertEqual([ley["slug"] for ley in resultado], ["lfca", "lft"])

    def test_es_generador_consumir_uno_no_abre_los_demas_tarballs(self):
        self._publica_indice({}, {"lft": {"nombre": "LFT"}, "lfca": {"nombre": "LFCA"}})
        self._publica_tgz("lft", **{
            "lft/indice.json": json.dumps(
                [{"archivo": "a.md", "fecha_publicacion": "01-01-2000", "codNota": 1}]
            ),
            "lft/a.md": "LFT",
        })
        # lfca.tgz never written -- reading it would raise AssetNotCached.

        iterador = release.iter_current_federal_laws(["lft", "lfca"], cache_dir=self.tmp)

        primero = next(iterador)
        self.assertEqual(primero["slug"], "lft")

    def test_slug_inexistente_lanza_assetnotcached(self):
        self._publica_indice({})

        iterador = release.iter_current_federal_laws(["no-existe"], cache_dir=self.tmp)

        with self.assertRaises(release.AssetNotCached):
            next(iterador)


class TestIterCurrentFederalLawsSinIndice(ConCacheFixture):
    """A law that has been crawled but never linked yet: no `indice.json`,
    so the winner comes from the raw snapshots' own file names."""

    def test_ley_sin_indice_usa_el_archivo_de_fecha_mas_reciente_y_codnota_none(self):
        self._publica_tgz("lfea", **{
            "lfea/22-05-1998.md": "VIEJO",
            "lfea/05-01-1999.md": "NUEVO",
            "lfea/estado.json": json.dumps({"rastreado": "2026-09-01"}),
        })

        [ley] = list(release.iter_current_federal_laws(["lfea"], cache_dir=self.tmp))

        self.assertIsNone(ley["codNota"])
        self.assertEqual(ley["archivo"], "05-01-1999.md")
        self.assertEqual(ley["markdown"], "NUEVO")


class TestIterCurrentFederalLawsSlugsPorDefecto(ConCacheFixture):
    """`slugs=None` prefers the cache (issue #205): a warm cache directory's
    own `<slug>.tgz` file names decide which laws to walk. Since issue #209
    this reader never falls back to the network at all -- a cold/absent
    cache simply yields nothing; `download_scjn_leyes_assets` (or `scjn
    download`) is what populates it."""

    def test_sin_slugs_recorre_los_tgz_publicados(self):
        self._publica_indice({}, {"lft": {"nombre": "LFT"}})
        self._publica_tgz("lft", **{
            "lft/indice.json": json.dumps(
                [{"archivo": "a.md", "fecha_publicacion": "01-01-2000", "codNota": 1}]
            ),
            "lft/a.md": "LFT",
        })

        resultado = list(release.iter_current_federal_laws(cache_dir=self.tmp))

        self.assertEqual([ley["slug"] for ley in resultado], ["lft"])

    def test_sin_slugs_con_cache_vacia_no_produce_nada(self):
        resultado = list(release.iter_current_federal_laws(cache_dir=self.tmp))

        self.assertEqual(resultado, [])

    def test_tgz_sin_indice_global_degrada_nombre_a_none(self):
        """The index is a separate asset from the tarballs: a cache that
        never got it degrades `nombre` to `None` rather than raising (issue
        #205's DECISION 2, carried into scjn.release by issue #209)."""
        self._publica_tgz("lft", **{
            "lft/indice.json": json.dumps(
                [{"archivo": "a.md", "fecha_publicacion": "01-01-2000", "codNota": 1}]
            ),
            "lft/a.md": "LFT",
        })

        [ley] = list(release.iter_current_federal_laws(cache_dir=self.tmp))

        self.assertIsNone(ley["nombre"])


class TestDownloadScjnLeyesCatalog(ConCacheFixture):
    """The seed read back out of the release (issue #185, Fase 0 of #184):
    `nombre`/`abrev` off `indice-global.json.gz`, `actualizado` off each law's
    own `estado.json`."""

    def test_regresa_nombre_abrev_y_actualizado_ordenado_por_abrev(self):
        self._publica_indice({}, {
            "lft": {"nombre": "LEY Federal del Trabajo", "asset": "lft.tgz", "snapshots": 2},
            "lfca": {"nombre": "LEY Federal de Cine y el Audiovisual",
                     "asset": "lfca.tgz", "snapshots": 1},
        })
        self._publica_tgz("lfca", **{"lfca/estado.json": json.dumps({
            "actualizado": "2026-05-22", "enlazado": "2026-09-01",
            "id_ordenamiento": "188805", "rastreado": "2026-09-01"})})
        self._publica_tgz("lft", **{"lft/estado.json": json.dumps(
            {"actualizado": "2025-06-13"}
        )})

        catalogo = release.download_scjn_leyes_catalog(cache_dir=self.tmp)

        self.assertEqual(catalogo, [
            {"abrev": "lfca", "nombre": "LEY Federal de Cine y el Audiovisual",
             "actualizado": "2026-05-22"},
            {"abrev": "lft", "nombre": "LEY Federal del Trabajo",
             "actualizado": "2025-06-13"},
        ])

    def test_actualizado_nulo_queda_ausente_no_en_none(self):
        self._publica_indice({}, {"lfcpq": {"nombre": "LEY Federal de Cinematografia",
                                             "asset": "lfcpq.tgz", "snapshots": 8}})
        self._publica_tgz("lfcpq", **{"lfcpq/estado.json": json.dumps({
            "actualizado": None, "enlazado": "2026-09-01",
            "id_ordenamiento": "11057", "rastreado": "2026-09-01"})})

        catalogo = release.download_scjn_leyes_catalog(cache_dir=self.tmp)

        self.assertEqual(catalogo, [{"abrev": "lfcpq",
                                     "nombre": "LEY Federal de Cinematografia"}])

    def test_un_tarball_sin_estado_json_tampoco_inventa_actualizado(self):
        self._publica_indice({}, {"lft": {"nombre": "LEY Federal del Trabajo",
                                          "asset": "lft.tgz", "snapshots": 1}})
        self._publica_tgz("lft", **{"lft/01-04-1970.md": "**TEXTO ORIGINAL.**"})

        catalogo = release.download_scjn_leyes_catalog(cache_dir=self.tmp)

        self.assertEqual(catalogo, [{"abrev": "lft",
                                     "nombre": "LEY Federal del Trabajo"}])

    def test_un_tarball_no_cacheado_se_reporta_pero_no_lanza(self):
        # Issue #209: freshness=True nunca baja un tarball -- una ley sin
        # tarball en cache simplemente pierde su `actualizado`, avisando por
        # `log` en vez de propagar AssetNotCached.
        self._publica_indice({}, {"lft": {"nombre": "LEY Federal del Trabajo",
                                          "asset": "lft.tgz", "snapshots": 1}})
        avisos = []

        catalogo = release.download_scjn_leyes_catalog(cache_dir=self.tmp, log=avisos.append)

        self.assertEqual(catalogo, [{"abrev": "lft", "nombre": "LEY Federal del Trabajo"}])
        self.assertEqual(len(avisos), 1)
        self.assertIn("lft", avisos[0])

    def test_freshness_false_no_abre_ni_un_tarball(self):
        self._publica_indice({}, {"lft": {"nombre": "LEY Federal del Trabajo",
                                          "asset": "lft.tgz", "snapshots": 1}})

        catalogo = release.download_scjn_leyes_catalog(freshness=False, cache_dir=self.tmp)

        # No `lft.tgz` was ever written: reading one would raise.
        self.assertEqual(catalogo, [{"abrev": "lft",
                                     "nombre": "LEY Federal del Trabajo"}])

    def test_el_slug_del_release_es_el_abrev_normalizado(self):
        # 14 laws carry an underscore in their historical `abrev`
        # (`lif_2026`, `pef_2026`, the `lrart*` reglamentarias...) and the
        # release slug hyphenates it.
        self._publica_indice({}, {"lif-2026": {"nombre": "LEY de Ingresos de la Federacion",
                                               "asset": "lif-2026.tgz", "snapshots": 1}})

        catalogo = release.download_scjn_leyes_catalog(freshness=False, cache_dir=self.tmp)

        self.assertEqual(catalogo[0]["abrev"], "lif-2026")

    def test_indice_no_cacheado_lanza_assetnotcached(self):
        with self.assertRaises(release.AssetNotCached):
            release.download_scjn_leyes_catalog(cache_dir=self.tmp)

    def test_estado_backfilled_reporta_el_abrev_verbatim(self):
        # Issue #210: `abrev` now lives in the law's own `estado.json`, and
        # the 14 historical laws whose `abrev` carries an underscore (here
        # `lif_2026`) must come back exactly that way once their tarball has
        # been backfilled -- not the release's normalized `lif-2026` slug.
        self._publica_indice({}, {"lif-2026": {"nombre": "LEY de Ingresos de la Federacion",
                                               "asset": "lif-2026.tgz", "snapshots": 1}})
        self._publica_tgz("lif-2026", **{"lif-2026/estado.json": json.dumps({
            "abrev": "lif_2026", "nombre": "LEY de Ingresos de la Federacion",
            "actualizado": "2026-07-01"})})

        catalogo = release.download_scjn_leyes_catalog(cache_dir=self.tmp)

        self.assertEqual(catalogo, [{"abrev": "lif_2026",
                                     "nombre": "LEY de Ingresos de la Federacion",
                                     "actualizado": "2026-07-01"}])

    def test_estado_viejo_y_estado_con_backfill_dan_la_misma_entrada(self):
        # Definition of done (issue #210): a corpus with an old-format
        # estado.json (only `actualizado`, no `abrev`/`nombre`/... yet) and
        # one whose tarball was already backfilled agree on the catalogue
        # entry for a law whose `abrev` needs no normalization.
        instrumentos = {"lft": {"nombre": "LEY Federal del Trabajo",
                                "asset": "lft.tgz", "snapshots": 1}}
        estado_viejo = {"actualizado": "2025-06-13", "rastreado": "2026-08-27"}
        estado_backfilled = {
            "abrev": "lft", "nombre": "LEY Federal del Trabajo", "nombre_scjn": None,
            "id_ordenamiento": "123", "url": "https://legislacion.scjn.gob.mx/consulta/ordenamiento/123",
            "actualizado_scjn": "2025-06-13", "actualizado_dof": "2025-06-13",
            "actualizado": "2025-06-13", "rastreado": "2026-08-27", "enlazado": "2026-08-27",
        }

        entradas = []
        for i, estado in enumerate((estado_viejo, estado_backfilled)):
            with self.subTest(estado=estado):
                directorio = Path(self.tmp) / f"variante-{i}" / "scjn-leyes"
                directorio.mkdir(parents=True)
                (directorio / release.ASSET_INDICE_GLOBAL).write_bytes(
                    _indice_global({}, instrumentos)
                )
                (directorio / "lft.tgz").write_bytes(
                    _hacer_tgz({"lft/estado.json": json.dumps(estado)})
                )
                entradas.append(
                    release.download_scjn_leyes_catalog(cache_dir=directorio.parent)
                )

        self.assertEqual(entradas[0], entradas[1])
        self.assertEqual(entradas[0], [{"abrev": "lft", "nombre": "LEY Federal del Trabajo",
                                        "actualizado": "2025-06-13"}])


class TestDescargaAssetsScjnLeyes(unittest.TestCase):
    """`download_scjn_leyes_assets` (issue #155): the release materialized on
    disk, and idempotent — a second run costs no download at all. This is
    the one reader-adjacent function that still talks to the network."""

    URLS = {
        "indice-global.json.gz": "https://x/indice-global.json.gz",
        "lfca.tgz": "https://x/lfca.tgz",
        "lft.tgz": "https://x/lft.tgz",
    }

    def setUp(self):
        self.tmp = Path(__import__("tempfile").mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp))

    @patch("scjn.cache.descarga", return_value=b"bytes")
    @patch("scjn.release._assets_scjn_leyes")
    def test_sin_slugs_baja_el_indice_y_todos_los_tgz(self, mock_assets, mock_descarga):
        mock_assets.return_value = dict(self.URLS)

        resultados = release.download_scjn_leyes_assets(cache_dir=self.tmp)

        self.assertEqual(
            [ruta.name for ruta, _ in resultados],
            ["indice-global.json.gz", "lfca.tgz", "lft.tgz"],
        )
        self.assertTrue(all(descargado for _, descargado in resultados))
        self.assertEqual(mock_descarga.call_count, 3)

    @patch("scjn.cache.descarga", return_value=b"bytes")
    @patch("scjn.release._assets_scjn_leyes")
    def test_slugs_acota_pero_el_indice_siempre_viene(self, mock_assets, mock_descarga):
        mock_assets.return_value = dict(self.URLS)

        resultados = release.download_scjn_leyes_assets(["lft"], cache_dir=self.tmp)

        self.assertEqual(
            [ruta.name for ruta, _ in resultados],
            ["indice-global.json.gz", "lft.tgz"],
        )

    @patch("scjn.cache.descarga", return_value=b"bytes")
    @patch("scjn.release._assets_scjn_leyes")
    def test_la_segunda_corrida_no_baja_nada(self, mock_assets, mock_descarga):
        mock_assets.return_value = dict(self.URLS)
        release.download_scjn_leyes_assets(cache_dir=self.tmp)
        mock_descarga.reset_mock()

        resultados = release.download_scjn_leyes_assets(cache_dir=self.tmp)

        self.assertFalse(any(descargado for _, descargado in resultados))
        mock_descarga.assert_not_called()

    @patch("scjn.cache.descarga", return_value=b"bytes")
    @patch("scjn.release._assets_scjn_leyes")
    def test_refrescar_vuelve_a_bajar(self, mock_assets, mock_descarga):
        mock_assets.return_value = dict(self.URLS)
        release.download_scjn_leyes_assets(cache_dir=self.tmp)
        mock_descarga.reset_mock()

        resultados = release.download_scjn_leyes_assets(cache_dir=self.tmp, refrescar=True)

        self.assertTrue(all(descargado for _, descargado in resultados))
        self.assertEqual(mock_descarga.call_count, 3)

    @patch("scjn.cache.descarga", return_value=b"bytes")
    @patch("scjn.release._assets_scjn_leyes")
    def test_un_slug_que_el_release_no_publica_es_un_error(self, mock_assets, _):
        mock_assets.return_value = dict(self.URLS)

        with self.assertRaises(KeyError):
            release.download_scjn_leyes_assets(["no-existe"], cache_dir=self.tmp)

    @patch("scjn.release._assets_scjn_leyes")
    def test_los_slugs_del_release_salen_de_sus_propios_assets(self, mock_assets):
        mock_assets.return_value = dict(self.URLS)

        self.assertEqual(release.scjn_leyes_slugs(), ["lfca", "lft"])

    @patch("scjn.cache.descarga", return_value=b"bytes")
    @patch("scjn.release._assets_scjn_leyes")
    def test_none_usa_el_cache_dir_del_paquete(self, mock_assets, mock_descarga):
        mock_assets.return_value = dict(self.URLS)

        with patch("scjn.cache.CACHE_DIR", self.tmp):
            resultados = release.download_scjn_leyes_assets(cache_dir=None)

        self.assertTrue((self.tmp / "scjn-leyes" / "lfca.tgz").is_file())
        self.assertTrue(all(descargado for _, descargado in resultados))


class TestOffline(unittest.TestCase):
    """The regression test for issue #209's whole point: every reader is
    offline given a populated cache directory, `requests.get` monkeypatched
    to raise -- not merely un-consulted in the happy path above, but unable
    to reach the network even if it tried."""

    def setUp(self):
        self.tmp = Path(__import__("tempfile").mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp))
        self.release_dir = self.tmp / "scjn-leyes"
        self.release_dir.mkdir(parents=True)
        payload = {
            "generado": "x", "coleccion": "leyes",
            "instrumentos": {"lft": {"nombre": "LFT"}}, "codNota": {
                "1": [{"slug": "lft", "archivo": "a.md"}],
            },
        }
        (self.release_dir / release.ASSET_INDICE_GLOBAL).write_bytes(
            gzip.compress(json.dumps(payload).encode("utf-8"))
        )
        (self.release_dir / "lft.tgz").write_bytes(_hacer_tgz({
            "lft/indice.json": json.dumps(
                [{"archivo": "a.md", "fecha_publicacion": "01-01-2000", "codNota": 1}]
            ),
            "lft/a.md": "LFT",
            "lft/estado.json": json.dumps({"actualizado": "2020-01-01"}),
        }))

    @patch("requests.get", side_effect=ConnectionError("network unreachable"))
    def test_cada_lector_responde_sin_tocar_la_red(self, mock_get):
        self.assertIn(1, release.download_scjn_leyes_index(cache_dir=self.tmp)["codNota"])
        self.assertEqual(
            release.download_scjn_leyes_corpus("lft", cache_dir=self.tmp)["slug"], "lft"
        )
        self.assertEqual(release.markdown_de_snapshot("lft", "a.md", cache_dir=self.tmp), "LFT")
        self.assertEqual(release.local_slugs(self.tmp), ["lft"])
        [ley] = list(release.iter_current_federal_laws(cache_dir=self.tmp))
        self.assertEqual(ley["slug"], "lft")
        self.assertEqual(
            release.download_scjn_leyes_catalog(cache_dir=self.tmp),
            [{"abrev": "lft", "nombre": "LFT", "actualizado": "2020-01-01"}],
        )

        mock_get.assert_not_called()


if __name__ == "__main__":
    unittest.main()

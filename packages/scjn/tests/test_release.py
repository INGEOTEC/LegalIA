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

import requests

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


class TestMetadatosPorLey(ConCacheFixture):
    """Issue #215: `materia`/`vigencia`/`resumen` — one value per law, off
    the SCJN's own search — travel in `instrumentos` and come back out of
    both readers."""

    def test_construye_indice_global_los_copia_a_instrumentos(self):
        indice, _ = release.construye_indice_global(
            [{
                "slug": "lft", "nombre": "LEY Federal del Trabajo", "indice": [],
                "materia": "SEGURIDAD SOCIAL, LABORAL", "vigencia": "VIGENTE",
                "resumen": "Ley que rige las relaciones de trabajo.",
            }],
            generado="2026-09-06T00:00:00+00:00",
        )

        self.assertEqual(indice["instrumentos"]["lft"], {
            "nombre": "LEY Federal del Trabajo", "asset": "lft.tgz", "snapshots": 0,
            "materia": "SEGURIDAD SOCIAL, LABORAL", "vigencia": "VIGENTE",
            "resumen": "Ley que rige las relaciones de trabajo.",
        })

    def test_un_campo_sin_valor_queda_ausente_no_en_none(self):
        # La SCJN no tiene resumen para `lfca`; el asset no crece 315 nulls
        # mientras se rellenan los campos.
        indice, _ = release.construye_indice_global(
            [{"slug": "lfca", "nombre": "LEY Federal de Cine y el Audiovisual",
              "indice": [], "materia": "ADMINISTRATIVO", "vigencia": "VIGENTE",
              "resumen": None}],
            generado="x",
        )

        self.assertNotIn("resumen", indice["instrumentos"]["lfca"])
        self.assertEqual(indice["instrumentos"]["lfca"]["materia"], "ADMINISTRATIVO")

    def test_el_catalogo_los_regresa_sin_abrir_un_solo_tarball(self):
        self._publica_indice({}, {"lft": {
            "nombre": "LEY Federal del Trabajo", "asset": "lft.tgz", "snapshots": 2,
            "materia": "SEGURIDAD SOCIAL, LABORAL", "vigencia": "VIGENTE",
            "resumen": "Ley que rige las relaciones de trabajo.",
        }})

        catalogo = release.download_scjn_leyes_catalog(freshness=False, cache_dir=self.tmp)

        self.assertEqual(catalogo, [{
            "abrev": "lft", "nombre": "LEY Federal del Trabajo",
            "materia": "SEGURIDAD SOCIAL, LABORAL", "vigencia": "VIGENTE",
            "resumen": "Ley que rige las relaciones de trabajo.",
        }])

    def test_el_estado_json_de_la_ley_le_gana_al_indice(self):
        # El `estado.json` es el registro que el siguiente reempaquetado
        # publica; el indice puede venir de una corrida anterior.
        self._publica_indice({}, {"lft": {"nombre": "LEY Federal del Trabajo",
                                          "asset": "lft.tgz", "snapshots": 1,
                                          "vigencia": "VIGENTE"}})
        self._publica_tgz("lft", **{"lft/estado.json": json.dumps({
            "actualizado": "2026-05-14", "vigencia": "ABROGADO (A)",
            "materia": "LABORAL", "clasificado": "2026-09-06"})})

        [entrada] = release.download_scjn_leyes_catalog(cache_dir=self.tmp)

        self.assertEqual(entrada["vigencia"], "ABROGADO (A)")
        self.assertEqual(entrada["materia"], "LABORAL")
        self.assertNotIn("clasificado", entrada)

    def test_iter_current_federal_laws_los_entrega_con_el_texto(self):
        self._publica_indice({}, {"lft": {
            "nombre": "LEY Federal del Trabajo", "asset": "lft.tgz", "snapshots": 1,
            "materia": "SEGURIDAD SOCIAL, LABORAL", "vigencia": "VIGENTE",
        }})
        self._publica_tgz("lft", **{
            "lft/indice.json": json.dumps(
                [{"archivo": "a.md", "fecha_publicacion": "01-05-2019", "codNota": 1}]
            ),
            "lft/a.md": "LFT",
        })

        [ley] = list(release.iter_current_federal_laws(cache_dir=self.tmp))

        self.assertEqual(ley["materia"], "SEGURIDAD SOCIAL, LABORAL")
        self.assertEqual(ley["vigencia"], "VIGENTE")
        self.assertIsNone(ley["resumen"])


# --- scjn-reglamentos (issue #220) ----------------------------------------


class TestAssetNotCachedReglamentos(unittest.TestCase):
    """The `coleccion` message (issue #220): `leyes`' own message stays
    exactly what it was (issue #209's contract, see `TestDownloadScjnLeyesIndex`
    above); `reglamentos` gets its own, naming the right command."""

    def test_mensaje_default_es_el_de_leyes_sin_cambios(self):
        exc = release.AssetNotCached("lfca.tgz", Path("/x"))
        self.assertEqual(str(exc), "'lfca.tgz' is not cached under /x -- run `scjn download --slug lfca`")

    def test_mensaje_de_reglamentos_nombra_su_propio_comando(self):
        exc = release.AssetNotCached("104906.tgz", Path("/x"), coleccion="reglamentos")
        self.assertEqual(
            str(exc),
            "'104906.tgz' is not cached under /x -- run "
            "`scjn download --coleccion reglamentos --id 104906`",
        )

    def test_mensaje_de_reglamentos_para_el_indice(self):
        exc = release.AssetNotCached(
            release.ASSET_INDICE_GLOBAL, Path("/x"), coleccion="reglamentos"
        )
        self.assertIn("scjn download --coleccion reglamentos", str(exc))
        self.assertNotIn("--id", str(exc))


class TestConstruyeIndiceGlobalReglamentos(unittest.TestCase):
    def test_no_lleva_seccion_codnota(self):
        indice = release.construye_indice_global_reglamentos(
            [{"id_ordenamiento": "104906", "nombre": "REGLAMENTO...", "snapshots": 2}],
            generado="x",
        )
        self.assertEqual(indice["coleccion"], "reglamentos")
        self.assertNotIn("codNota", indice)

    def test_las_claves_son_id_ordenamiento_no_un_slug(self):
        indice = release.construye_indice_global_reglamentos(
            [{"id_ordenamiento": "104906", "nombre": "REGLAMENTO A", "snapshots": 1},
             {"id_ordenamiento": "96580", "nombre": "REGLAMENTO B", "snapshots": 3}],
            generado="x",
        )
        self.assertEqual(set(indice["instrumentos"]), {"104906", "96580"})
        self.assertEqual(indice["instrumentos"]["96580"]["snapshots"], 3)

    def test_categoria_ordenamiento_y_metadatos_ausentes_si_no_hay_valor(self):
        indice = release.construye_indice_global_reglamentos(
            [{"id_ordenamiento": "1", "nombre": "R", "snapshots": 0}], generado="x",
        )
        entrada = indice["instrumentos"]["1"]
        self.assertNotIn(release.CAMPO_CATEGORIA_ORDENAMIENTO, entrada)
        self.assertNotIn("materia", entrada)

    def test_categoria_ordenamiento_y_metadatos_presentes_cuando_hay_valor(self):
        indice = release.construye_indice_global_reglamentos(
            [{"id_ordenamiento": "1", "nombre": "R", "snapshots": 2,
              "categoria_ordenamiento": "ACUERDO (S)", "vigencia": "VIGENTE",
              "materia": "ADMINISTRATIVO"}],
            generado="x",
        )
        entrada = indice["instrumentos"]["1"]
        self.assertEqual(entrada[release.CAMPO_CATEGORIA_ORDENAMIENTO], "ACUERDO (S)")
        self.assertEqual(entrada["vigencia"], "VIGENTE")
        self.assertEqual(entrada["materia"], "ADMINISTRATIVO")

    def test_snapshots_0_no_lleva_asset_decision_6(self):
        # Issue #222's decision 6: un instrumento que la SCJN clasifica pero
        # para el que no sirve texto se indexa con snapshots=0 y SIN 'asset'
        # -- antes de #222 siempre se le ponia f"{clave}.tgz" aunque ese
        # tarball nunca se empaquetara.
        indice = release.construye_indice_global_reglamentos(
            [{"id_ordenamiento": "96090", "nombre": "SIN TEXTO", "snapshots": 0}],
            generado="x",
        )
        entrada = indice["instrumentos"]["96090"]
        self.assertNotIn("asset", entrada)
        self.assertEqual(entrada["snapshots"], 0)


class ConCacheFixtureReglamentos(unittest.TestCase):
    """The `scjn-reglamentos` sibling of `ConCacheFixture`."""

    def setUp(self):
        self.tmp = Path(__import__("tempfile").mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp))
        self.release_dir = self.tmp / "scjn-reglamentos"
        self.release_dir.mkdir(parents=True)

    def _publica_indice(self, instrumentos: dict):
        payload = {"generado": "x", "coleccion": "reglamentos", "instrumentos": instrumentos}
        (self.release_dir / release.ASSET_INDICE_GLOBAL).write_bytes(
            gzip.compress(json.dumps(payload).encode("utf-8"))
        )

    def _publica_tgz(self, id_ordenamiento: str, **archivos):
        (self.release_dir / f"{id_ordenamiento}.tgz").write_bytes(_hacer_tgz(archivos))


class TestDownloadScjnReglamentosIndex(ConCacheFixtureReglamentos):
    def test_lee_el_indice_publicado(self):
        self._publica_indice({"104906": {"nombre": "REGLAMENTO...", "snapshots": 2}})

        indice = release.download_scjn_reglamentos_index(cache_dir=self.tmp)

        self.assertEqual(indice["coleccion"], "reglamentos")
        self.assertEqual(indice["instrumentos"]["104906"]["snapshots"], 2)

    def test_se_memoiza_por_directorio_de_cache(self):
        self._publica_indice({})

        primero = release.download_scjn_reglamentos_index(cache_dir=self.tmp)
        segundo = release.download_scjn_reglamentos_index(cache_dir=self.tmp)

        self.assertIs(primero, segundo)

    def test_asset_no_cacheado_lanza_assetnotcached_de_reglamentos(self):
        with self.assertRaises(release.AssetNotCached) as ctx:
            release.download_scjn_reglamentos_index(cache_dir=self.tmp)

        self.assertIn("scjn download --coleccion reglamentos", str(ctx.exception))


class TestDownloadScjnReglamentosCorpus(ConCacheFixtureReglamentos):
    """`download_scjn_reglamentos_corpus`, now a thin wrapper over the
    generic `_corpus_de_release` (issue #222's Fase 0). Decision 10's raise
    order is part of the public contract, not an implementation detail: the
    index is read *first*, so a cold cache with no index at all raises
    `AssetNotCached` about the index itself, before the tarball is ever
    reached -- a deliberate behaviour change from the pre-#222 reader, which
    tried the tarball directly."""

    def test_indice_no_cacheado_lanza_assetnotcached_del_indice(self):
        with self.assertRaises(release.AssetNotCached) as ctx:
            release.download_scjn_reglamentos_corpus("104906", cache_dir=self.tmp)

        self.assertIn(release.ASSET_INDICE_GLOBAL, str(ctx.exception))
        self.assertNotIn("--id", str(ctx.exception))

    def test_id_ausente_del_indice_lanza_assetnotcached_del_tarball(self):
        self._publica_indice({})

        with self.assertRaises(release.AssetNotCached) as ctx:
            release.download_scjn_reglamentos_corpus("104906", cache_dir=self.tmp)

        self.assertIn("104906.tgz", str(ctx.exception))
        self.assertIn("--id 104906", str(ctx.exception))

    def test_sin_texto_en_scjn_cuando_el_indice_no_le_da_asset(self):
        # Decision 6/10: el indice lista el instrumento pero con snapshots=0
        # y sin 'asset' -- nunca se llega a intentar el tarball.
        self._publica_indice({"104906": {"nombre": "SIN TEXTO", "snapshots": 0}})

        with self.assertRaises(release.SinTextoEnSCJN) as ctx:
            release.download_scjn_reglamentos_corpus("104906", cache_dir=self.tmp)

        self.assertEqual(ctx.exception.id_ordenamiento, "104906")
        self.assertEqual(ctx.exception.coleccion, "reglamentos")

    def test_nunca_hay_indice_json_ni_notas_dof(self):
        # Scope de #220: sin enlace a DOF, nunca hay indice.json/notas/ que
        # leer -- a diferencia de download_scjn_leyes_corpus, aqui no hay
        # rama "enlazado" en absoluto.
        self._publica_indice({"104906": {"nombre": "REGLAMENTO", "asset": "104906.tgz", "snapshots": 2}})
        self._publica_tgz(
            "104906",
            **{
                "104906/21-01-2015.md": "**REGLAMENTO.**",
                "104906/25-01-2017.md": "**REFORMA.**",
                "104906/estado.json": json.dumps({"rastreado": "2026-09-09"}),
            },
        )

        resultado = release.download_scjn_reglamentos_corpus("104906", cache_dir=self.tmp)

        self.assertEqual(resultado["id_ordenamiento"], "104906")
        archivos = [s["archivo"] for s in resultado["snapshots"]]
        self.assertEqual(archivos, ["21-01-2015.md", "25-01-2017.md"])
        self.assertEqual(resultado["snapshots"][0]["markdown"], "**REGLAMENTO.**")

    def test_acepta_un_id_ordenamiento_entero(self):
        self._publica_indice({"1": {"nombre": "x", "asset": "1.tgz", "snapshots": 1}})
        self._publica_tgz("1", **{"1/01-01-2000.md": "x"})

        resultado = release.download_scjn_reglamentos_corpus(1, cache_dir=self.tmp)

        self.assertEqual(resultado["id_ordenamiento"], "1")


class TestLocalReglamentosIds(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(__import__("tempfile").mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp))
        self.release_dir = self.tmp / "scjn-reglamentos"
        self.release_dir.mkdir(parents=True)

    def test_lista_ordenados_numericamente_no_como_texto(self):
        # "100" < "99" como texto, pero no numericamente.
        (self.release_dir / "100.tgz").write_bytes(b"x")
        (self.release_dir / "99.tgz").write_bytes(b"x")

        self.assertEqual(release.local_reglamentos_ids(self.tmp), ["99", "100"])

    def test_directorio_ausente_regresa_lista_vacia(self):
        self.assertEqual(release.local_reglamentos_ids(self.tmp / "no-existe"), [])


class TestDescargaAssetsScjnReglamentos(unittest.TestCase):
    """`download_scjn_reglamentos_assets` -- the `scjn-reglamentos` sibling
    of `TestDescargaAssetsScjnLeyes`."""

    URLS = {
        "indice-global.json.gz": "https://x/indice-global.json.gz",
        "104906.tgz": "https://x/104906.tgz",
        "96580.tgz": "https://x/96580.tgz",
    }

    def setUp(self):
        self.tmp = Path(__import__("tempfile").mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp))

    @patch("scjn.cache.descarga", return_value=b"bytes")
    @patch("scjn.release._assets_scjn_reglamentos")
    def test_sin_ids_baja_el_indice_y_todos_los_tgz(self, mock_assets, mock_descarga):
        mock_assets.return_value = dict(self.URLS)

        resultados = release.download_scjn_reglamentos_assets(cache_dir=self.tmp)

        self.assertEqual(
            [ruta.name for ruta, _ in resultados],
            ["indice-global.json.gz", "96580.tgz", "104906.tgz"],
        )
        self.assertTrue(all(descargado for _, descargado in resultados))

    @patch("scjn.cache.descarga", return_value=b"bytes")
    @patch("scjn.release._assets_scjn_reglamentos")
    def test_ids_acota_pero_el_indice_siempre_viene(self, mock_assets, mock_descarga):
        mock_assets.return_value = dict(self.URLS)

        resultados = release.download_scjn_reglamentos_assets(["96580"], cache_dir=self.tmp)

        self.assertEqual(
            [ruta.name for ruta, _ in resultados], ["indice-global.json.gz", "96580.tgz"],
        )

    @patch("scjn.cache.descarga", return_value=b"bytes")
    @patch("scjn.release._assets_scjn_reglamentos")
    def test_la_segunda_corrida_no_baja_nada(self, mock_assets, mock_descarga):
        mock_assets.return_value = dict(self.URLS)
        release.download_scjn_reglamentos_assets(cache_dir=self.tmp)
        mock_descarga.reset_mock()

        resultados = release.download_scjn_reglamentos_assets(cache_dir=self.tmp)

        self.assertFalse(any(descargado for _, descargado in resultados))
        mock_descarga.assert_not_called()


class TestTagDeParte(unittest.TestCase):
    """`_tag_de_parte` (issue #223): the one place the part-naming rule
    lives -- `base` itself for part 1, `f"{base}-{n}"` after."""

    def test_la_parte_1_es_el_tag_base(self):
        self.assertEqual(release._tag_de_parte("scjn-reglamentos", 1), "scjn-reglamentos")

    def test_partes_siguientes_llevan_un_sufijo_numerico(self):
        self.assertEqual(release._tag_de_parte("scjn-reglamentos", 2), "scjn-reglamentos-2")
        self.assertEqual(release._tag_de_parte("scjn-reglamentos", 3), "scjn-reglamentos-3")


class _FakeResponse:
    """A minimal stand-in for `requests.Response`, just enough for
    `_assets_de_release`: a status code, a JSON body, and `raise_for_status`
    raising for anything 400+ that is not the 404 `_assets_de_release`
    itself already special-cases."""

    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")


def _pagina_assets(nombres_urls: dict) -> dict:
    """The GitHub releases API's own shape for one release's `assets`."""
    return {
        "assets": [
            {"name": nombre, "browser_download_url": url}
            for nombre, url in nombres_urls.items()
        ]
    }


class TestAssetsDePartes(unittest.TestCase):
    """`_assets_de_partes` (issue #223): resolving a collection's whole
    numbered series of release tags by probing until a 404 ends it --
    `requests.get` mocked at the HTTP boundary, one response per part."""

    @patch("requests.get")
    def test_una_sola_parte_cuesta_un_probe_de_mas(self, mock_get):
        mock_get.side_effect = [
            _FakeResponse(200, _pagina_assets({"a.tgz": "https://x/a.tgz"})),
            _FakeResponse(404),
        ]

        resultado = release._assets_de_partes("base")

        self.assertEqual(resultado, {"a.tgz": "https://x/a.tgz"})
        self.assertEqual(mock_get.call_count, 2)

    @patch("requests.get")
    def test_dos_partes_se_fusionan_y_un_id_de_la_parte_2_se_resuelve(self, mock_get):
        mock_get.side_effect = [
            _FakeResponse(200, _pagina_assets({"a.tgz": "https://x/a.tgz"})),
            _FakeResponse(200, _pagina_assets({"b.tgz": "https://x/b.tgz"})),
            _FakeResponse(404),
        ]

        resultado = release._assets_de_partes("base")

        self.assertEqual(resultado, {"a.tgz": "https://x/a.tgz", "b.tgz": "https://x/b.tgz"})

    @patch("requests.get")
    def test_404_en_la_parte_1_lanza_en_vez_de_corpus_vacio(self, mock_get):
        mock_get.return_value = _FakeResponse(404)

        with self.assertRaises(KeyError):
            release._assets_de_partes("base")

    @patch("requests.get")
    def test_403_en_una_parte_posterior_se_propaga_no_trunca_la_serie(self, mock_get):
        mock_get.side_effect = [
            _FakeResponse(200, _pagina_assets({"a.tgz": "https://x/a.tgz"})),
            _FakeResponse(403),
        ]

        with self.assertRaises(requests.HTTPError):
            release._assets_de_partes("base")

    @patch("requests.get")
    def test_nombre_duplicado_entre_partes_gana_la_mas_baja(self, mock_get):
        mock_get.side_effect = [
            _FakeResponse(200, _pagina_assets({"a.tgz": "https://parte1/a.tgz"})),
            _FakeResponse(200, _pagina_assets({"a.tgz": "https://parte2/a.tgz"})),
            _FakeResponse(404),
        ]

        resultado = release._assets_de_partes("base")

        self.assertEqual(resultado["a.tgz"], "https://parte1/a.tgz")

    @patch("requests.get")
    def test_max_partes_alcanzado_lanza_en_vez_de_ciclar(self, mock_get):
        mock_get.return_value = _FakeResponse(200, _pagina_assets({}))

        with self.assertRaises(RuntimeError):
            release._assets_de_partes("base")

        self.assertEqual(mock_get.call_count, release._MAX_PARTES)


class TestDescargaAssetsScjnReglamentosDosPartesReal(unittest.TestCase):
    """`download_scjn_reglamentos_assets` end-to-end across two real parts
    (issue #223) -- unlike `TestDescargaAssetsScjnReglamentos`, `requests.get`
    is mocked at the HTTP boundary rather than `_assets_scjn_reglamentos`
    itself, so this exercises `_assets_de_partes` for real."""

    def setUp(self):
        self.tmp = Path(__import__("tempfile").mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp))

    @patch("scjn.cache.descarga", return_value=b"bytes")
    @patch("requests.get")
    def test_un_id_publicado_solo_en_la_parte_2_se_descarga(self, mock_get, mock_descarga):
        mock_get.side_effect = [
            _FakeResponse(200, _pagina_assets({
                "indice-global.json.gz": "https://x/indice-global.json.gz",
                "104906.tgz": "https://x/104906.tgz",
            })),
            _FakeResponse(200, _pagina_assets({"96580.tgz": "https://parte2/96580.tgz"})),
            _FakeResponse(404),
        ]

        resultados = release.download_scjn_reglamentos_assets(cache_dir=self.tmp)

        nombres = sorted(ruta.name for ruta, _ in resultados)
        self.assertEqual(nombres, ["104906.tgz", "96580.tgz", "indice-global.json.gz"])
        self.assertTrue((self.tmp / "scjn-reglamentos" / "96580.tgz").is_file())


class TestDescargaAssetsScjnReglamentosAvisaFaltantes(unittest.TestCase):
    """`download_scjn_reglamentos_assets(ids=None)` logs (never raises) an
    instrument the cached index names but no part of the release publishes
    (issue #223) -- a partially published corpus still downloads everything
    it can, but says what is missing."""

    def setUp(self):
        self.tmp = Path(__import__("tempfile").mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp))
        self.release_dir = self.tmp / "scjn-reglamentos"
        self.release_dir.mkdir(parents=True)
        payload = {
            "generado": "x", "coleccion": "reglamentos",
            "instrumentos": {
                "104906": {"nombre": "A", "asset": "104906.tgz", "snapshots": 1},
                "96580": {"nombre": "B", "asset": "96580.tgz", "snapshots": 1},
            },
        }
        (self.release_dir / release.ASSET_INDICE_GLOBAL).write_bytes(
            gzip.compress(json.dumps(payload).encode("utf-8"))
        )

    @patch("scjn.cache.descarga", return_value=b"bytes")
    @patch("scjn.release._assets_scjn_reglamentos")
    def test_avisa_del_asset_que_ninguna_parte_publica(self, mock_assets, mock_descarga):
        mock_assets.return_value = {
            "indice-global.json.gz": "https://x/indice-global.json.gz",
            "104906.tgz": "https://x/104906.tgz",
        }
        mensajes = []

        release.download_scjn_reglamentos_assets(cache_dir=self.tmp, log=mensajes.append)

        self.assertTrue(any("96580.tgz" in m for m in mensajes))
        self.assertFalse(any("104906.tgz" in m and "warning" in m for m in mensajes))


class TestScjnLineamientos(unittest.TestCase):
    """`scjn-lineamientos` (issue #222): the third id-keyed collection, a
    thin set of wrappers over the same generic core `scjn-reglamentos`
    already exercises above -- this class checks the wiring (right
    subdirectory, right `coleccion` name, right exceptions), not the core
    logic itself again."""

    def setUp(self):
        self.tmp = Path(__import__("tempfile").mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp))
        self.release_dir = self.tmp / "scjn-lineamientos"
        self.release_dir.mkdir(parents=True)

    def _publica_indice(self, instrumentos: dict):
        payload = {"generado": "x", "coleccion": "lineamientos", "instrumentos": instrumentos}
        (self.release_dir / release.ASSET_INDICE_GLOBAL).write_bytes(
            gzip.compress(json.dumps(payload).encode("utf-8"))
        )

    def _publica_tgz(self, id_ordenamiento: str, **archivos):
        (self.release_dir / f"{id_ordenamiento}.tgz").write_bytes(_hacer_tgz(archivos))

    def test_construye_indice_global_lineamientos_no_lleva_codnota(self):
        indice = release.construye_indice_global_lineamientos(
            [{"id_ordenamiento": "180528", "nombre": "LINEAMIENTOS...", "snapshots": 1}],
            generado="x",
        )
        self.assertEqual(indice["coleccion"], "lineamientos")
        self.assertNotIn("codNota", indice)

    def test_download_index_lee_el_publicado(self):
        self._publica_indice({"180528": {"nombre": "LINEAMIENTOS...", "asset": "180528.tgz",
                                          "snapshots": 1}})

        indice = release.download_scjn_lineamientos_index(cache_dir=self.tmp)

        self.assertEqual(indice["coleccion"], "lineamientos")
        self.assertEqual(indice["instrumentos"]["180528"]["snapshots"], 1)

    def test_download_corpus_lee_snapshots_ordenados(self):
        self._publica_indice({"180528": {"nombre": "L", "asset": "180528.tgz", "snapshots": 1}})
        self._publica_tgz("180528", **{"180528/12-06-2019.md": "**LINEAMIENTOS.**"})

        resultado = release.download_scjn_lineamientos_corpus("180528", cache_dir=self.tmp)

        self.assertEqual(resultado["id_ordenamiento"], "180528")
        self.assertEqual(resultado["snapshots"][0]["markdown"], "**LINEAMIENTOS.**")

    def test_corpus_sin_texto_lanza_sintextoenscjn(self):
        self._publica_indice({"96090": {"nombre": "SIN TEXTO", "snapshots": 0}})

        with self.assertRaises(release.SinTextoEnSCJN) as ctx:
            release.download_scjn_lineamientos_corpus("96090", cache_dir=self.tmp)

        self.assertEqual(ctx.exception.coleccion, "lineamientos")

    def test_local_ids_lee_scjn_lineamientos_no_scjn_reglamentos(self):
        (self.release_dir / "180528.tgz").write_bytes(b"x")
        (self.tmp / "scjn-reglamentos").mkdir()
        (self.tmp / "scjn-reglamentos" / "104906.tgz").write_bytes(b"x")

        self.assertEqual(release.local_lineamientos_ids(self.tmp), ["180528"])

    @patch("scjn.cache.descarga", return_value=b"bytes")
    @patch("scjn.release._assets_scjn_lineamientos")
    def test_download_assets_baja_al_subdirectorio_propio(self, mock_assets, mock_descarga):
        mock_assets.return_value = {
            "indice-global.json.gz": "https://x/indice-global.json.gz",
            "180528.tgz": "https://x/180528.tgz",
        }

        resultados = release.download_scjn_lineamientos_assets(cache_dir=self.tmp)

        self.assertEqual(
            sorted(ruta.name for ruta, _ in resultados),
            ["180528.tgz", "indice-global.json.gz"],
        )
        self.assertTrue((self.tmp / "scjn-lineamientos" / "180528.tgz").is_file())

    def test_asset_not_cached_nombra_el_comando_de_lineamientos(self):
        exc = release.AssetNotCached("180528.tgz", Path("/x"), coleccion="lineamientos")
        self.assertEqual(
            str(exc),
            "'180528.tgz' is not cached under /x -- run "
            "`scjn download --coleccion lineamientos --id 180528`",
        )

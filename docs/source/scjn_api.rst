:mod:`scjn`
============

.. image:: https://github.com/INGEOTEC/LegalIA/actions/workflows/test.yml/badge.svg
        :target: https://github.com/INGEOTEC/LegalIA/actions/workflows/test.yml

.. image:: https://badge.fury.io/py/scjn.svg
        :target: https://badge.fury.io/py/scjn

Version |scjn_version| — see :doc:`index` for the full package table.

:py:mod:`scjn` is the client for the Suprema Corte de Justicia de la
Nación's SCOW JSON API (:py:mod:`scjn.api`, the backend of
`legislacion.scjn.gob.mx/consulta/buscador
<https://legislacion.scjn.gob.mx/consulta/buscador>`_) and the disk-first
reader for the three GitHub releases it feeds (:py:mod:`scjn.release`) — a
Mexican federal law's reform-dated snapshots, one tarball per law
(``scjn-leyes``); since issue #220, every federal *reglamento* the SCJN has,
one tarball per instrument (``scjn-reglamentos``); and, since issue #222,
every federal *lineamiento* the SCJN has, on the same id-keyed shape
(``scjn-lineamientos``). It was extracted out of :py:mod:`nota2md`'s own
modules (issue #206): Fase 1 (#207) moved the
transport, the catalogue's own algebra (:py:mod:`scjn.catalog`),
per-instrument crawl state (:py:mod:`scjn.state`) and the provenance header's
reader (:py:mod:`scjn.header`); Fase 3 (#209) moved the release's readers
here too, disk-first and with their own cache directory
(:py:mod:`scjn.cache`). :py:mod:`nota2md` depends on this package; this
package depends on nothing in this monorepo — its dependency direction is
one way, enforced by ``tests/test_boundary.py``'s own grep for
``nota2md``/``dofjson``.

Two things worth stating explicitly before the API, because nothing in the
signatures below conveys them:

- **What** ``fuente: scjn`` **means.** The SCJN is not an official source of
  legal text — ``dof.gob.mx``/SIDOF (:py:mod:`dofjson`) remains that. Every
  file this package's crawl writes, and every snapshot the ``scjn-leyes``
  release ships, keeps that header intact, so whoever reads the result can
  tell where it came from — unchanged by this epic.
- **What "reform N" means.** It is the SCJN reform table's own chronological
  order — each law's own ``indice.json`` in the ``scjn-leyes`` release, one
  entry per reform, oldest first, plus ``indice-global.json.gz`` inverting
  that by ``codNota``. It is not an attempt to reproduce the Cámara de
  Diputados' historical numbering, which is gone with the Diputados data it
  counted (issue #184); the two count different things and are never
  compared.

The sections below are ordered the way a caller actually reaches this
corpus: :py:mod:`scjn.release` (the entry points, disk-only), :py:mod:`scjn.api`
(the transport underneath the corpus — the one documented exception to "every
public symbol has a verified example" on this page), :py:mod:`scjn.discovery`
(the shared discovery machinery behind the two id-keyed collections'
discovery scripts, issue #222's Fase 0), :py:mod:`scjn.catalog`
(the federal-law catalogue's own algebra), :py:mod:`scjn.state` (per-instrument
crawl state and completeness), :py:mod:`scjn.header` (reading a crawl's own
output back off disk), :py:mod:`scjn.cache` (the on-disk cache
:py:mod:`scjn.release` reads and the one-time migration out of
:py:mod:`nota2md`'s), :py:mod:`scjn.cli` (the ``scjn`` console script). Every
class and function is documented, including private/internal helpers
(leading-underscore names) — useful when extending or debugging the package,
though they are not part of its public API and can change without notice.

Every example below that touches the ``scjn-leyes`` corpus picks one law by
slug — ``lfca`` (LEY Federal de Cine y el Audiovisual), the smallest law in
the release (one snapshot as of this writing) and one of the two the SCJN
does not index at all (its snapshot was built by hand from the DOF). The
whole release is ~380 MB; a corpus example that walked all of it would make
the doctest job unaffordable.

``scjn.release`` — the entry points
-------------------------------------

Every reader here is disk-only (issue #209): none of them make an HTTP
request, and a missing asset raises :py:exc:`~scjn.AssetNotCached` rather
than attempting one. Only :py:func:`~scjn.download_scjn_leyes_assets` (and,
through it, the ``scjn download`` CLI below) talks to the network — run it
once and every reader afterwards is offline.

>>> import scjn

:py:func:`~scjn.download_scjn_leyes_index` reads the release's reverse
index — every ``codNota`` the corpus can resolve, mapped to the law(s) it
reforms — a few hundred KB against the corpus' 380 MB:

>>> indice = scjn.download_scjn_leyes_index()
>>> sorted(indice.keys())
['codNota', 'coleccion', 'generado', 'instrumentos']
>>> indice["coleccion"]
'leyes'
>>> indice["codNota"][5788357]
[{'slug': 'lfca', 'archivo': '22-05-2026.md', 'title_link_status': 'linked', 'content_diff_confirmed_codNota': None, 'content_diff_score': None}]

:py:func:`~scjn.download_scjn_leyes_corpus` reads one law's own tarball
whole — every snapshot, its ``indice.json`` fields, and the DOF notes that
were considered while linking it, so the link can be audited without going
back to the network:

>>> corpus = scjn.download_scjn_leyes_corpus("lfca")
>>> corpus["slug"]
'lfca'
>>> [(s["codNota"], s["fecha_publicacion"]) for s in corpus["snapshots"]]
[(5788357, '22-05-2026')]

:py:func:`~scjn.markdown_de_snapshot` reads just one snapshot's text out of
that same tarball, without decoding the rest of it:

>>> scjn.markdown_de_snapshot("lfca", "22-05-2026.md").startswith("---\nfuente: scjn")
True

:py:func:`~scjn.iter_current_federal_laws` yields the *current* text of
every federal law the release publishes — one tarball opened, its newest
snapshot read, and its bytes dropped before the next law, so walking the
whole corpus never holds more than one law in memory at a time.
``slugs=["lfca"]`` bounds this example to the one law, the same way a real
caller would narrow it with ``--slug``:

>>> laws = list(scjn.iter_current_federal_laws(slugs=["lfca"]))
>>> len(laws)
1
>>> laws[0]["nombre"]
'LEY Federal de Cine y el Audiovisual'
>>> laws[0]["codNota"]
5788357

Each law also carries the SCJN's own ``materia`` (its subject
classification), ``vigencia`` (whether it is still in force — seven values,
not a boolean) and ``resumen`` (a one-paragraph abstract), read off the same
index ``nombre`` comes from. They are properties of the *law*, not of the
snapshot being yielded, which is what makes this iterator usable as a
classified corpus — stratify by ``materia``, keep only ``VIGENTE`` — without
a second pass or a request to the SCJN (issue #215):

>>> sorted(laws[0])
['archivo', 'codNota', 'fecha_publicacion', 'markdown', 'materia', 'nombre', 'resumen', 'slug', 'vigencia']
>>> laws[0]["materia"]
'ADMINISTRATIVO'

``lfca`` is also the illustration of "absent, never a placeholder": the SCJN
publishes no abstract for it, so ``resumen`` is ``None`` here and the key is
missing outright from the catalogue entry below, rather than carrying a null
that would read as "the SCJN says nothing" when it means "nobody asked".

:py:func:`~scjn.local_slugs` is the disk-first answer to "which laws does
this machine have" — no HTTP request, and what ``slugs=None`` above resolves
through:

>>> "lfca" in scjn.local_slugs()
True

:py:func:`~scjn.download_scjn_leyes_catalog` reads the federal-law catalogue
the release already publishes — the seed the Cámara de Diputados used to be
scraped for (issue #184). ``freshness=False`` skips every law's own tarball
(the default reads all ~315 of them for the ``actualizado`` freshness date)
and answers off the index alone:

>>> catalogo = scjn.download_scjn_leyes_catalog(freshness=False)
>>> len(catalogo) > 100
True
>>> entrada = next(e for e in catalogo if e["abrev"] == "lfca")
>>> entrada["nombre"]
'LEY Federal de Cine y el Audiovisual'
>>> entrada["materia"], entrada["vigencia"]
('ADMINISTRATIVO', 'VIGENTE')
>>> "resumen" in entrada
False

The three metadata fields come from the index too, so they survive
``freshness=False``; with ``freshness=True`` a law's own ``estado.json``
wins over the index for them, since that is the record its next repack
publishes. ``scripts/fetch_federal_law_metadata.py`` is what writes them
(one SCJN search per law, matched by ``idOrdenamiento`` so a wrong document
can never be described as the right one).

A ``codNota``/law/asset not yet cached raises :py:exc:`~scjn.AssetNotCached`,
naming the exact ``scjn download`` command that populates it:

>>> try:
...     scjn.download_scjn_leyes_corpus("no-such-law")
... except scjn.AssetNotCached as exc:
...     print(exc)
'no-such-law.tgz' is not cached under .../scjn -- run `scjn download --slug no-such-law`

``scjn-reglamentos`` — the second collection (issue #220)
------------------------------------------------------------

Every federal *reglamento* the SCJN has, published as a sibling release
alongside ``scjn-leyes`` — same disk-first contract (every reader here is
offline too, and :py:exc:`~scjn.AssetNotCached` names the right ``scjn
download --coleccion reglamentos`` command), but a narrower corpus: no
``abrev`` (the SCJN reissues a reglamento as a brand-new ``idOrdenamiento``
rather than as a reform of the previous one, so a title-derived key would
collide — 137 of 1080 titles repeat), and **no DOF linking at all yet** — a
future issue adds it, for reglamentos and laws alike, so there is no
``indice.json`` per instrument and no ``codNota`` section in this release's
own index.

>>> import scjn.release as release

:py:func:`~scjn.release.construye_indice_global_reglamentos` is the payload
builder for this release's own index — no ``codNota`` key, keyed by
``id_ordenamiento`` (:py:func:`scjn.catalog.reglamento_key`) rather than by a
slug:

>>> indice = release.construye_indice_global_reglamentos(
...     [{"id_ordenamiento": "104906", "nombre": "REGLAMENTO DE LA OFICIALIA "
...       "ELECTORAL DEL INSTITUTO NACIONAL ELECTORAL", "snapshots": 2,
...       "categoria_ordenamiento": "ACUERDO (S)", "vigencia": "VIGENTE"}],
...     generado="2026-09-09T00:00:00+00:00",
... )
>>> sorted(indice.keys())
['coleccion', 'generado', 'instrumentos']
>>> indice["coleccion"]
'reglamentos'
>>> indice["instrumentos"]["104906"]["categoria_ordenamiento"]
'ACUERDO (S)'

``categoria_ordenamiento`` (:py:data:`~scjn.release.CAMPO_CATEGORIA_ORDENAMIENTO`)
is what makes ``104906`` worth carrying at all: the SCJN itself classifies
the *instrument* ``ACUERDO (S)``, but a row of its own reform table is
classified ``REGLAMENTO`` — issue #220's own inclusion rule, load-bearing
for 6 of the corpus' 1087 instruments.

:py:func:`~scjn.catalog.instrumento_key` is the key itself — not a slug of a
title, unlike :py:func:`~scjn.catalog.slug_instrumento` — shared by every
id-keyed collection since issue #222's Fase 0 (``reglamento_key`` is kept as
a working alias of the exact same function, for a caller that has not moved
to the new name yet):

>>> from scjn.catalog import instrumento_key, reglamento_key
>>> instrumento_key({"id_ordenamiento": "104906", "nombre": "..."})
'104906'
>>> reglamento_key is instrumento_key
True

The corpus was published by hand on 2026-09-10 — 1087 instruments, 1082 with
snapshots, 72.2 MB of tarballs — and it outgrew a single GitHub release doing
it (issue #223): a release holds at most 1000 assets, and 1082 tarballs plus
``MANIFEST.md``/``SHA256SUMS.txt``/``indice-global.json.gz`` is 1085, so the
collection lives across two release tags today, ``scjn-reglamentos`` (997
tarballs, part 1) and ``scjn-reglamentos-2`` (the other 85). Every reader
below resolves the whole series transparently — a caller never names a part.

:py:func:`~scjn.download_scjn_reglamentos_assets` is the one function below
that talks to the network — every reader after it assumes it (or the ``scjn
download --coleccion reglamentos`` CLI built on it) already ran:

>>> resultados = release.download_scjn_reglamentos_assets(["104906"])
>>> sorted(ruta.name for ruta, _ in resultados)
['104906.tgz', 'indice-global.json.gz']

:py:func:`~scjn.download_scjn_reglamentos_index` reads the release's own
reverse listing — no ``codNota`` section (this corpus has no DOF linking at
all yet), keyed by ``id_ordenamiento``:

>>> indice = release.download_scjn_reglamentos_index()
>>> indice["coleccion"]
'reglamentos'
>>> indice["instrumentos"]["104906"]["nombre"]
'REGLAMENTO DE LA OFICIALIA ELECTORAL DEL INSTITUTO NACIONAL ELECTORAL'
>>> indice["instrumentos"]["104906"]["categoria_ordenamiento"]
'ACUERDO (S)'

:py:func:`~scjn.download_scjn_reglamentos_corpus` reads one instrument back
— oldest snapshot first, each with its own provenance header (no
``indice.json``/``notas/`` here, unlike a law's own tarball: this corpus has
nothing to link a snapshot's `codNota` against):

>>> corpus = release.download_scjn_reglamentos_corpus("104906")
>>> [s["archivo"] for s in corpus["snapshots"]]
['21-01-2015.md', '25-01-2017.md']
>>> lineas = corpus["snapshots"][0]["markdown"].splitlines()
>>> lineas[0], lineas[1]
('---', 'fuente: scjn')

:py:func:`~scjn.local_reglamentos_ids` is the disk-first "what does this
machine already have", no network at all:

>>> "104906" in release.local_reglamentos_ids()
True

``scjn-lineamientos`` — the third collection (issue #222)
------------------------------------------------------------

Every federal *lineamiento* the SCJN has — 163 instruments, 126 with text —
published as a sibling release alongside ``scjn-leyes``/``scjn-reglamentos``,
on the id-keyed collection path issue #222's Fase 0 refactored so a third
collection costs a descriptor entry and four wrapper functions rather than
another ~226-line copy of :py:mod:`scjn.release`. Same shape as
``scjn-reglamentos`` above: no ``abrev`` (3 of 159 titles repeat), no
``actualizado``, no DOF linking, no ``indice.json``/``notas/``.

:py:func:`~scjn.release.construye_indice_global_lineamientos` is the payload
builder for this release's own index — the exact sibling of
:py:func:`~scjn.release.construye_indice_global_reglamentos` above, sharing
the same private generic core (:py:func:`~scjn.release._indice_global_por_id`)
and the same decision 6 rule: an instrument with ``snapshots: 0`` gets no
``asset`` key at all, since there is nothing to download for it:

>>> indice = release.construye_indice_global_lineamientos(
...     [{"id_ordenamiento": "96090", "nombre": "MANUAL DE LINEAMIENTOS DE LA "
...       "COORDINACION DE RECURSOS HUMANOS Y ENLACE ADMINISTRATIVO",
...       "snapshots": 0, "categoria_ordenamiento": "MANUAL", "vigencia": "VIGENTE"}],
...     generado="2026-09-10T00:00:00+00:00",
... )
>>> indice["coleccion"]
'lineamientos'
>>> indice["instrumentos"]["96090"]["snapshots"]
0
>>> "asset" in indice["instrumentos"]["96090"]
False

:py:func:`~scjn.download_scjn_lineamientos_index`,
:py:func:`~scjn.download_scjn_lineamientos_corpus`,
:py:func:`~scjn.download_scjn_lineamientos_assets`,
:py:func:`~scjn.local_lineamientos_ids` and :py:exc:`~scjn.SinTextoEnSCJN`
are the exact wrappers :py:func:`~scjn.download_scjn_reglamentos_index` and
its siblings are (decision 7: every published name stays per-collection,
symmetric across both) — sharing the same generic core
(:py:func:`~scjn.release._index_de_release`,
:py:func:`~scjn.release._corpus_de_release`,
:py:func:`~scjn.release._local_ids_de_release`,
:py:func:`~scjn.release._download_assets_de_release`) and the exact same
raise-order contract :py:exc:`~scjn.AssetNotCached`/:py:exc:`~scjn.SinTextoEnSCJN`
document on those functions (decision 10): a cold cache raises
:py:exc:`~scjn.AssetNotCached` for the index first, and only once the index
says an id has no asset does :py:func:`~scjn.download_scjn_lineamientos_corpus`
raise :py:exc:`~scjn.SinTextoEnSCJN` instead of reaching for a tarball that
does not exist.

**Not yet live**, unlike ``scjn-reglamentos`` above — issue #222 generated
this collection's publish plan (``PUBLICAR.md``) but a human has not run it
yet (issue #115, Hallazgo C: publishing is never automated), so there is no
``scjn-lineamientos`` release on GitHub for a doctest here to download from.
A live example the same shape as ``scjn-reglamentos``' own (an actual
``download_scjn_lineamientos_assets``/``_index``/``_corpus``/
``local_lineamientos_ids`` walk against a real ``id_ordenamiento``) belongs
here as a follow-up, once the release is published — until then this
behaviour is verified by ``packages/scjn/tests/test_release.py``'s
``TestScjnLineamientos`` class against a synthetic on-disk release, the same
posture :py:mod:`scjn.api` takes toward the live SCJN below.

.. automodule:: scjn.release
   :members:
   :private-members:
   :undoc-members:

``scjn.api`` — the SCOW transport
-------------------------------------

**The one documented exception to "every public symbol has a verified
example" on this page.** :py:mod:`scjn.api` is an unauthenticated client for
three JSON endpoints (``BusquedaFrase``, ``Reforma``, ``Articulos``) that
replaced this project's legacy WebForms crawler (issue #172, retired in
#179). A doctest per endpoint would put the docs gate at the mercy of the
SCJN's own uptime, and crawling is not a caller-facing API the way the
readers above are — a caller never instantiates :py:class:`~scjn.api.ScjnApi`
directly, only the ``scjn download`` CLI (or
``scripts/fetch_scjn_legislacion.py`` /
``scripts/fetch_federal_law_metadata.py``) does. This behaviour is instead
verified for real, against the live service, by
``packages/scjn/tests/test_api_red.py`` — not silently skipped, exactly the
mechanism :doc:`dof2md_api`'s OCR paths already use for ``mineru``.

.. automodule:: scjn.api
   :members:
   :private-members:
   :undoc-members:

``scjn.discovery`` — shared id-keyed collection discovery (issue #222)
---------------------------------------------------------------------------

The three passes behind ``discover_federal_reglamentos.py`` (issue #220) and
``discover_federal_lineamientos.py`` (issue #222), extracted into library
code parameterised by a target SCJN category and a phrase list, so neither
script duplicates them. Every function here takes an object exposing only
``search_ordenamiento``/``reformas_of_ordenamiento`` (an
:py:class:`~scjn.api.ScjnApi`, or the small stub below) — nothing makes a
network request directly, which is what makes this module unit-testable
without the SCJN:

>>> from scjn.api import Ordenamiento, Reforma
>>> from scjn.discovery import (
...     candidates_outside_category, coverage_audit, discover,
...     discover_by_category, pagina_categoria, rescue_by_reform_category,
... )
>>> class StubApi:
...     """Answers one page of hand-picked hits per (frase, categoria), and
...     a fixed reform table per id -- enough to exercise every pass below
...     without a single HTTP request."""
...     def __init__(self):
...         self.hits = {
...             ("lineamientos", "LINEAMIENTOS"): [
...                 Ordenamiento(idOrdenamiento="1", ordenamiento="LINEAMIENTOS DE EJEMPLO",
...                              categoriaOrdenamiento="LINEAMIENTOS"),
...             ],
...             ("lineamientos", ""): [
...                 Ordenamiento(idOrdenamiento="1", ordenamiento="LINEAMIENTOS DE EJEMPLO",
...                              categoriaOrdenamiento="LINEAMIENTOS"),
...                 Ordenamiento(idOrdenamiento="2", ordenamiento="MANUAL DE LINEAMIENTOS",
...                              categoriaOrdenamiento="MANUAL"),
...             ],
...             ("manual", "MANUAL"): [
...                 Ordenamiento(idOrdenamiento="2", ordenamiento="MANUAL DE LINEAMIENTOS",
...                              categoriaOrdenamiento="MANUAL"),
...             ],
...         }
...         self.reformas = {
...             "1": [Reforma(reformaId=1, fecha_publicacion="01-01-2020", categoria="LINEAMIENTOS")],
...             "2": [Reforma(reformaId=2, fecha_publicacion="01-01-2019", categoria="LINEAMIENTOS",
...                            tieneArticulos=False)],
...         }
...     def search_ordenamiento(self, frase, *, tamanio_pagina, categoria, ambito, pagina):
...         return self.hits.get((frase, categoria), []) if pagina == 1 else []
...     def reformas_of_ordenamiento(self, id_ordenamiento):
...         return self.reformas.get(str(id_ordenamiento), [])
>>> api = StubApi()

:py:func:`~scjn.discovery.pagina_categoria` pages one ``(frase, categoria)``
to the end:

>>> [hit.idOrdenamiento for hit in pagina_categoria(api, "lineamientos", "LINEAMIENTOS")]
['1']

:py:func:`~scjn.discovery.discover_by_category` unions a phrase list over
one target category:

>>> por_categoria = discover_by_category(api, "LINEAMIENTOS", ("lineamientos",))
>>> list(por_categoria)
['1']

:py:func:`~scjn.discovery.candidates_outside_category` finds every other
federal hit of one rescue phrase, minus what is already classified:

>>> otros = candidates_outside_category(api, "lineamientos", por_categoria)
>>> list(otros)
['2']

:py:func:`~scjn.discovery.rescue_by_reform_category` applies the
reform-table rule to those candidates — id ``"2"``'s own reform row is
classified ``LINEAMIENTOS`` even though the SCJN classifies the *instrument*
itself ``MANUAL``:

>>> rescatados = rescue_by_reform_category(api, otros, "LINEAMIENTOS")
>>> list(rescatados)
['2']

:py:func:`~scjn.discovery.coverage_audit` is the opt-in, one-time,
exhaustive sweep (issue #222's decision 8) — it finds the same id ``"2"``
by a different route (the ``MANUAL`` category, not the ``"lineamientos"``
phrase), and would cache every reform table it fetches under a real
``cache_dir``; ``cache_dir=None`` here writes nothing to disk:

>>> extra = coverage_audit(api, {"1"}, "LINEAMIENTOS", (("manual", "MANUAL"),), cache_dir=None)
>>> list(extra)
['2']

:py:func:`~scjn.discovery.discover` is the whole pipeline a discovery script
wraps — union, rescue, and (opt-in) the coverage audit, returning a
reviewable list sorted by name:

>>> candidatos = discover(
...     StubApi(), categoria="LINEAMIENTOS", frases_union=("lineamientos",),
...     frase_rescate="lineamientos", auditoria_cobertura=True,
...     frases_auditoria=(("manual", "MANUAL"),), log=lambda *_a, **_k: None,
... )
>>> [(c["id_ordenamiento"], c["nombre"], c["reformas_con_texto"]) for c in candidatos]
[('1', 'LINEAMIENTOS DE EJEMPLO', 1), ('2', 'MANUAL DE LINEAMIENTOS', 0)]

.. automodule:: scjn.discovery
   :members:
   :private-members:
   :undoc-members:

``scjn.catalog`` — the federal-law catalogue's own algebra
---------------------------------------------------------------

Slugs, the ``nombre_scjn`` override, and minting a brand-new law's ``abrev``
— what used to also merge a freshly extracted ``catalogo.json`` against the
one on disk, before issue #210 made a law's own ``estado.json`` the single
per-law record with nothing left to reconcile against a second copy.

.. automodule:: scjn.catalog
   :members:
   :private-members:
   :undoc-members:

``scjn.state`` — per-instrument crawl state and completeness
-------------------------------------------------------------------

``estado.json``, one per law, and whether that law needs crawling again —
either by date (:py:func:`~scjn.state.motivo_pendiente`'s offline fallback)
or, since issue #211, by comparing the SCJN's own reform table row by row
against what is on disk (:py:func:`~scjn.state.reformas_faltantes`), the
only way to see a gap in the middle of an otherwise current-looking law
(``lfd`` had 92 snapshots against 98 reforms — issue #178).

.. automodule:: scjn.state
   :members:
   :private-members:
   :undoc-members:

``scjn.header`` — reading a crawl's own output back off disk
-------------------------------------------------------------------

The provenance header :py:func:`scjn.api.cabecera` writes at the top of
every snapshot, read back without re-fetching it — what lets a later pass
work over a crawl's output independently of the crawl itself.
:py:func:`~scjn.header.parse_header` is that parser over text rather than
over a file, for a caller holding a snapshot read straight out of a
``<slug>.tgz``:

>>> from scjn.header import parse_header
>>> cabecera = parse_header(scjn.markdown_de_snapshot("lfca", "22-05-2026.md"))
>>> cabecera["ordenamiento"]
'LEY FEDERAL DE CINE Y EL AUDIOVISUAL'
>>> cabecera["id_ordenamiento"]
'188805'

:py:func:`~scjn.header.lee_cabecera` is the same thing plus the read, for a
snapshot already on disk.

.. automodule:: scjn.header
   :members:
   :private-members:
   :undoc-members:

``scjn.cache`` — the on-disk cache and its one-time migration
--------------------------------------------------------------------

Its own directory, not a downstream package's (issue #209) — a
``platformdirs``-backed :py:data:`~scjn.cache.CACHE_DIR`, overridable with
``$SCJN_CACHE_DIR``:

>>> import scjn.cache as cache
>>> cache.CACHE_DIR.name
'scjn'
>>> (cache.CACHE_DIR / "scjn-leyes" / "lfca.tgz").exists()
True

:py:func:`~scjn.cache.migrate_legacy_assets` is the one-time consequence of
this package's cache not existing before issue #209: this package has no
notion of where a downstream package used to cache this release (its
dependency direction forbids that), so a caller that does know — a
downstream package's own ``download`` verb — hands that knowledge off,
moving release assets over with ``os.replace`` rather than downloading
~380 MB a machine already has:

>>> cache.migrate_legacy_assets(cache.CACHE_DIR, cache.CACHE_DIR / "does-not-exist")
0

:py:class:`~scjn.cache.Coleccion` (issue #222's Fase 0) describes an
id-keyed collection's own release-tag series and cache subdirectory;
:py:data:`~scjn.cache.COLECCIONES_POR_ID` maps a collection's name to its
descriptor, for :py:mod:`scjn.cli` and the scripts to dispatch ``--coleccion``
through instead of a per-collection literal branch:

>>> sorted(cache.COLECCIONES_POR_ID)
['lineamientos', 'reglamentos']
>>> cache.COLECCIONES_POR_ID["lineamientos"].tag_base
'scjn-lineamientos'
>>> cache.REGLAMENTOS.subdirectorio
'scjn-reglamentos'

.. automodule:: scjn.cache
   :members:
   :private-members:
   :undoc-members:

``scjn.cli`` — command-line entry point
------------------------------------------

One verb: putting a release on disk. A downstream package's own
``download`` subcommands (``nota2md download federal-laws``/``all``)
delegate to this same downloader rather than reimplementing it:

.. code-block:: console

   $ scjn download --slug lfca
   [1/2] indice-global.json.gz: already cached
   [2/2] lfca.tgz: already cached
   scjn-leyes: 2 assets in /home/user/.cache/scjn/scjn-leyes (0 downloaded, 2 already cached)

``--coleccion {leyes,reglamentos,lineamientos}`` (default ``leyes``; issue
#220 added ``reglamentos``, issue #222 added ``lineamientos``) picks which
release; ``--id`` replaces ``--slug`` for either id-keyed collection, since
both are keyed by ``id_ordenamiento`` rather than by a slug:

.. code-block:: console

   $ scjn download --coleccion reglamentos --id 104906
   [1/2] indice-global.json.gz: downloaded
   [2/2] 104906.tgz: downloaded
   scjn-reglamentos: 2 assets in /home/user/.cache/scjn/scjn-reglamentos (2 downloaded, 0 already cached) (2 partes)

Since issue #222's Fase 0, this is dispatched through
:py:data:`~scjn.cache.COLECCIONES_POR_ID` rather than a hand-written
per-flag branch for each id-keyed collection: adding a further one costs a
new dict entry, not a new literal branch. ``leyes`` itself stays its own
separate branch — it takes ``--slug``, not ``--id``, and has no
:py:class:`~scjn.cache.Coleccion` descriptor at all.

``(2 partes)`` (issue #223) appears whenever a download session actually
asked GitHub for this collection's series of release tags and found more
than one — this collection is ``scjn-reglamentos``/``scjn-reglamentos-2``
today. A run that finds everything already cached never asks, so it never
prints a part count at all, same as the ``scjn-leyes`` example above.

.. automodule:: scjn.cli
   :members:
   :private-members:
   :undoc-members:

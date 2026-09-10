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
reader for the two GitHub releases it feeds (:py:mod:`scjn.release`) — a
Mexican federal law's reform-dated snapshots, one tarball per law
(``scjn-leyes``), and, since issue #220, every federal *reglamento* the SCJN
has, one tarball per instrument (``scjn-reglamentos``). It was extracted out
of :py:mod:`nota2md`'s own modules (issue #206): Fase 1 (#207) moved the
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
public symbol has a verified example" on this page), :py:mod:`scjn.catalog`
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

:py:func:`~scjn.catalog.reglamento_key` is the key itself — not a slug of a
title, unlike :py:func:`~scjn.catalog.slug_instrumento`:

>>> from scjn.catalog import reglamento_key
>>> reglamento_key({"id_ordenamiento": "104906", "nombre": "..."})
'104906'

**The readers this release needs a real, published corpus for —
** :py:func:`~scjn.download_scjn_reglamentos_index`,
:py:func:`~scjn.download_scjn_reglamentos_corpus`,
:py:func:`~scjn.local_reglamentos_ids` and
:py:func:`~scjn.download_scjn_reglamentos_assets` — are the second
documented exception to "every public symbol has a verified example" on
this page (the first is :py:mod:`scjn.api` below). Issue #220 built the
code that discovers, seeds, crawls and packages this corpus, but publishing
it is a manual, human-reviewed step (issue #115, Hallazgo C) that had not
happened yet when this page was written — there is no ``scjn-reglamentos``
release for a live doctest to read. Their behaviour is instead verified by
fabricated tarballs and indices in
``packages/scjn/tests/test_release.py`` (``TestDownloadScjnReglamentosIndex``,
``TestDownloadScjnReglamentosCorpus``, ``TestLocalReglamentosIds``,
``TestDescargaAssetsScjnReglamentos``), the same fixture-based style every
other reader in this module is tested with — not silently skipped. Once a
human publishes the release, this exception should be revisited the same
way :py:mod:`scjn.api`'s own live-network exception below never was for the
readers above.

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

``--coleccion {leyes,reglamentos}`` (default ``leyes``, issue #220) picks
which release; ``--id`` replaces ``--slug`` for ``reglamentos``, since that
collection is keyed by ``id_ordenamiento`` rather than by a slug:

.. code-block:: console

   $ scjn download --coleccion reglamentos --id 104906
   [1/2] indice-global.json.gz: downloaded
   [2/2] 104906.tgz: downloaded
   scjn-reglamentos: 2 assets in /home/user/.cache/scjn/scjn-reglamentos (2 downloaded, 0 already cached)

A two-valued parameter, not a registry: adding a third collection here
would mean a third literal branch in :py:mod:`scjn.cli`, the same way
``reglamentos`` added a second one, never a ``COLECCIONES`` dict.

.. automodule:: scjn.cli
   :members:
   :private-members:
   :undoc-members:

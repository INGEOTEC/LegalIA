:mod:`nota2md`
===============

.. image:: https://github.com/INGEOTEC/LegalIA/actions/workflows/test.yml/badge.svg
        :target: https://github.com/INGEOTEC/LegalIA/actions/workflows/test.yml

.. image:: https://badge.fury.io/py/nota2md.svg
        :target: https://badge.fury.io/py/nota2md

Version |nota2md_version| — see :doc:`index` for the full package table.

:py:mod:`nota2md` is the largest package in the monorepo — roughly 130
top-level functions and classes across :py:mod:`nota2md.builder`,
:py:mod:`nota2md.leyes`, :py:mod:`nota2md.scjn`, :py:mod:`nota2md.scjn_api`,
:py:mod:`nota2md.cache`, :py:mod:`nota2md.html_converter` and
:py:mod:`nota2md.cli` — and the one whose behaviour needs the most prose:
:py:func:`~nota2md.legal_provisions` answers from the SCJN's consolidated
text of the whole law by default and only falls back to the DOF's own
source; its release assets are cached in **nota2md's own directory**,
deliberately not :py:mod:`dofjson`'s (two releases, two lifecycles); and a
law's reform history *is* the ``scjn-leyes`` release itself, not a dataset
of its own.

Two things worth stating explicitly before the API, because nothing in the
signatures below conveys them:

- **What** ``fuente: scjn`` **means.** The SCJN is not an official source of
  legal text — ``dof.gob.mx``/SIDOF remains that. Every file this package
  writes from the SCJN corpus keeps that header intact, so whoever reads the
  result can tell where it came from.
- **What "reform N" means now.** It is the SCJN reform table's chronological
  order — each law's own ``indice.json`` in the ``scjn-leyes`` release, one
  entry per reform, oldest first. It is not an attempt to reproduce the
  Cámara de Diputados' historical numbering, which is gone with the Diputados
  data it counted (issue #184); the two count different things and are never
  compared.

The sections below are ordered the way a note's Markdown actually gets
built: :py:mod:`nota2md.builder` (the entry points, :py:func:`~nota2md.legal_provisions`
and :py:func:`~nota2md.get_document`), :py:mod:`nota2md.scjn`/
:py:mod:`nota2md.scjn_api` (the SCOW JSON API crawl and the ``scjn-leyes``
release's own readers), :py:mod:`nota2md.leyes`
(:py:func:`~nota2md.reconstruct_legal_provisions`, built on the DOF alone),
:py:mod:`nota2md.cache` (the on-disk cache both release readers share),
:py:mod:`nota2md.html_converter` (the one note-to-Markdown conversion step),
:py:mod:`nota2md.cli`. Every class and function is documented, including
private/internal helpers (leading-underscore names) — useful when extending
or debugging the package, though they are not part of its public API and can
change without notice. Tests are never documented here, including the
network test files excluded from the routine ``pytest`` run.

Every example below that touches the ``scjn-leyes`` corpus picks one law by
slug — ``lfca`` (LEY Federal de Cine y el Audiovisual), the same small law
(one snapshot as of this writing) ``tests/test_scjn_release_red.py`` already
uses, chosen there because it is small and because it is one of the two laws
the SCJN does not index at all (its snapshots were built by hand from the
DOF). The whole release is ~380 MB; a corpus example that walked all of it
would make the doctest job unaffordable.

``nota2md.builder`` — the entry points
------------------------------------------

>>> import datetime as dt
>>> import tempfile
>>> from pathlib import Path
>>> import nota2md
>>>
>>> outdir = Path(tempfile.mkdtemp())

:py:func:`~nota2md.legal_provisions` answers ``codNota`` 5788357 — the
reform that published ``lfca``'s only snapshot so far — from the SCJN by
default, writing ``{slug}-{fecha}.md`` (the DOF path below writes
``nota-{codNota}.md`` instead):

>>> nota2md.legal_provisions(5788357, outdir).name
'lfca-22-05-2026.md'

With no ``outdir`` at all, the same call writes into ``nota2md``'s own
cache (:py:data:`nota2md.cache.CACHE_DIR` — **not** ``dofjson``'s) and
returns that path instead of raising for "nowhere to write":

>>> nota2md.legal_provisions(5788357)
PosixPath('.../scjn-leyes/md/lfca-22-05-2026.md')

``source="dof"`` skips the SCJN corpus entirely, even for a ``codNota`` it
covers, and builds from the original DOF source instead — HTML here, since
this note (an ordinary permit decree, not a codified law) has digital text:

>>> dest = nota2md.legal_provisions(4648702, outdir, source="dof")
>>> dest.name
'nota-4648702.md'
>>> dest.read_text(encoding="utf-8").startswith("DECRETO por el que se concede")
True

:py:func:`~nota2md.get_document` is the one note-to-Markdown step
underneath the HTML path above — the same record :py:func:`dofjson.get_nota`
returns, with ``cadenaContenido`` converted to Markdown and every other key
passed through untouched:

>>> documento = nota2md.get_document(4648702)
>>> documento["fuente"]
'sidof'
>>> documento["cadenaContenido"].startswith("DECRETO por el que se concede")
True

:py:func:`~nota2md.fetch_daily_legal_provisions` and
:py:func:`~nota2md.legal_provisions_titles` are re-exported straight off
:py:mod:`dofjson` (``dofjson.api``/``dofjson.titulos``) rather than built
here — :py:mod:`nota2md` names them because a caller reaching for "this
package's nine entry points" should not have to know that two of them
live one package down:

>>> for provision in nota2md.fetch_daily_legal_provisions(dt.date(2024, 9, 15), cache_dir=None):
...     provision["edicion"], provision["codNota"]
('VES', 5738985)

>>> import itertools
>>> primeros = list(itertools.islice(nota2md.legal_provisions_titles(log=lambda *a: None), 2))
>>> primeros[0]["codNota"]
4430696

.. automodule:: nota2md.builder
   :members:
   :private-members:
   :undoc-members:

``nota2md.scjn`` / ``nota2md.scjn_api`` — the SCJN corpus
-------------------------------------------------------------

:py:mod:`nota2md.scjn_api` is the transport: an unauthenticated client for
the SCJN's SCOW JSON API (three endpoints — matching ordenamientos, a
law's whole reform table, one reform's consolidated article text), the
backend behind `legislacion.scjn.gob.mx/consulta/buscador
<https://legislacion.scjn.gob.mx/consulta/buscador>`_. It replaced a legacy
WebForms crawler in issue #172/#179 and carries no public name of its own
off :py:mod:`nota2md` — everything reachable from the top level goes
through :py:mod:`nota2md.scjn` instead, which holds everything about the
corpus that is not transport: catalogue slugs, crawl state, the provenance
header's reader, and the ``scjn-leyes`` release's own readers below.

:py:func:`~nota2md.download_scjn_leyes_index` reads the release's reverse
index — every ``codNota`` the corpus can resolve, mapped to the law(s) it
reforms — a few hundred KB against the corpus' 380 MB, and the one call
:py:func:`~nota2md.legal_provisions` makes to decide whether a note is
covered at all:

>>> indice = nota2md.download_scjn_leyes_index()
>>> sorted(indice.keys())
['codNota', 'coleccion', 'generado', 'instrumentos']
>>> indice["coleccion"]
'leyes'
>>> indice["codNota"][5788357]
[{'slug': 'lfca', 'archivo': '22-05-2026.md', 'title_link_status': 'linked', 'content_diff_confirmed_codNota': None, 'content_diff_score': None}]

:py:func:`~nota2md.download_scjn_leyes_corpus` reads one law's own tarball
whole — every snapshot, its ``indice.json`` fields, and the DOF notes that
were considered while linking it, so the link can be audited without going
back to the network:

>>> corpus = nota2md.download_scjn_leyes_corpus("lfca")
>>> corpus["slug"]
'lfca'
>>> [(s["codNota"], s["fecha_publicacion"]) for s in corpus["snapshots"]]
[(5788357, '22-05-2026')]

:py:func:`~nota2md.iter_current_federal_laws` yields the *current* text of
every federal law the release publishes — one tarball opened, its newest
snapshot read, and its bytes dropped before the next law, so walking the
whole corpus never holds more than one law in memory. ``slugs=["lfca"]``
bounds this example to the one law, the same way a real caller would
narrow it with ``--slug``:

>>> laws = list(nota2md.iter_current_federal_laws(slugs=["lfca"]))
>>> len(laws)
1
>>> laws[0]["nombre"]
'LEY Federal de Cine y el Audiovisual'
>>> laws[0]["codNota"]
5788357

:py:func:`~nota2md.download_scjn_leyes_catalog` reads the federal-law
catalogue the release already publishes — the seed the Cámara de Diputados
used to be scraped for (issue #184). ``freshness=False`` skips every law's
own tarball (the default reads all ~315 of them, ~380 MB, for the
``actualizado`` freshness date) and answers off the index alone:

>>> catalogo = nota2md.download_scjn_leyes_catalog(freshness=False)
>>> len(catalogo) > 100
True
>>> {"abrev": "lfca", "nombre": "LEY Federal de Cine y el Audiovisual"} in catalogo
True

.. automodule:: nota2md.scjn
   :members:
   :private-members:
   :undoc-members:

.. automodule:: nota2md.scjn_api
   :members:
   :private-members:
   :undoc-members:

``nota2md.leyes`` — reconstructing a law from the DOF alone
------------------------------------------------------------------

:py:func:`~nota2md.reconstruct_legal_provisions` never reads the SCJN's
consolidated text (that comparison is exactly what
``tests/test_leyes_44.py`` measures, against the SCJN as ground truth):
it replays a law's own reform decrees, oldest first, on top of its original
publication, article by article. ``LEY de Amnistía`` — the module's own
example law, and one of the simplest real histories in the corpus (just the
original decree and one reform) — has ``cod_notas`` ``[5592105, 5730586]``:

>>> leyes_outdir = Path(tempfile.mkdtemp())
>>> dest = nota2md.reconstruct_legal_provisions(
...     [5592105, 5730586], leyes_outdir, nombre_ley="LEY de Amnistía",
... )
>>> dest.name
'ley-5592105.md'
>>> texto = dest.read_text(encoding="utf-8")
>>> "Se expide la Ley de Amnistía" in texto
True
>>> texto.count("Artículo") >= 9
True

``nombre_ley`` scopes every note to this one instrument among the several a
single decree may touch — every note in ``cod_notas`` here is fetched
through :py:func:`~nota2md.legal_provisions` into ``leyes_outdir`` too, as
``nota-{codNota}.md``, so a second law sharing the same ``outdir`` would
not re-fetch what this one already has.

.. automodule:: nota2md.leyes
   :members:
   :private-members:
   :undoc-members:

``nota2md.cache`` — the on-disk cache both release readers share
-----------------------------------------------------------------------

Deliberately the same idea :py:mod:`dofjson.titulos` already uses for the
``notas-archivo`` release — a ``platformdirs``-backed ``CACHE_DIR``, a
``SIN_CACHE_DIR`` sentinel — but its own directory, not ``dofjson``'s: the
``scjn-leyes`` and ``notas-archivo`` releases have two different lifecycles,
and sharing a directory would make clearing one clear the other. This is
where :py:func:`~nota2md.legal_provisions`' no-``outdir`` form writes, and
what ``nota2md download federal-laws`` (below) populates:

>>> import nota2md.cache as cache
>>> cache.CACHE_DIR
PosixPath('.../nota2md')
>>> (cache.CACHE_DIR / "scjn-leyes" / "lfca.tgz").exists()
True

.. automodule:: nota2md.cache
   :members:
   :private-members:
   :undoc-members:

``nota2md.html_converter`` — the one note-to-Markdown step
------------------------------------------------------------------

:py:func:`~nota2md.get_document` above is the only caller of
``html_to_markdown()`` — DOF note HTML is regular enough (block structure
from CSS classes, not tag names; emphasis from inline ``font-weight``/
``font-style`` CSS on ``<span>`` runs) that this one function is enough for
every note, no matter which organ published it:

>>> from nota2md.html_converter import html_to_markdown
>>> html_to_markdown('<div class="Titulo_1">DECRETO</div><div class="Texto">Cuerpo.</div>')
'# DECRETO\n\nCuerpo.'

.. automodule:: nota2md.html_converter
   :members:
   :private-members:
   :undoc-members:

``nota2md.cli`` — command-line entry point
------------------------------------------------

Two verbs, one of them implicit. A bare ``codNota`` is the build form this
CLI had before ``download`` existed, and takes no verb at all:

.. code-block:: console

   $ nota2md 5788357
   Saved to: /home/user/.cache/nota2md/scjn-leyes/md/lfca-22-05-2026.md

``download federal-laws`` puts the ``scjn-leyes`` release on disk —
``--slug`` (repeatable) limits it to one or more laws instead of all ~315:

.. code-block:: console

   $ nota2md download federal-laws --slug lfca
   [1/2] indice-global.json.gz: downloaded
   [2/2] lfca.tgz: downloaded
   scjn-leyes: 2 assets in /home/user/.cache/nota2md/scjn-leyes (2 downloaded, 0 already cached)

``download gazette-metadata`` puts the ``notas-archivo`` release on disk —
**into dofjson's own cache directory, not nota2md's** (the two releases
share no directory, on purpose; see ``nota2md.cache``'s module docstring):

.. code-block:: console

   $ nota2md download gazette-metadata
   [1/117] notas-1917.tgz: downloaded
   [...]
   [117/117] notas-2026-08.tgz: downloaded
   notas-archivo: 117 assets in /home/user/.cache/dofjson (117 downloaded, 0 already cached)

``download all`` runs both, each into its own cache directory — a shorthand
for the two invocations above, not a merge of the two caches:

.. code-block:: console

   $ nota2md download all --slug lfca
   [1/2] indice-global.json.gz: already cached
   [2/2] lfca.tgz: already cached
   scjn-leyes: 2 assets in /home/user/.cache/nota2md/scjn-leyes (0 downloaded, 2 already cached)
   [1/117] notas-1917.tgz: already cached
   [...]
   [117/117] notas-2026-08.tgz: already cached
   notas-archivo: 117 assets in /home/user/.cache/dofjson (0 downloaded, 117 already cached)

.. automodule:: nota2md.cli
   :members:
   :private-members:
   :undoc-members:

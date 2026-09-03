:mod:`dofjson`
===============

.. image:: https://github.com/INGEOTEC/LegalIA/actions/workflows/test.yml/badge.svg
        :target: https://github.com/INGEOTEC/LegalIA/actions/workflows/test.yml

.. image:: https://badge.fury.io/py/dofjson.svg
        :target: https://badge.fury.io/py/dofjson

Version |dofjson_version| — see :doc:`index` for the full package table.

:py:mod:`dofjson` is the client for SIDOF, the *Diario Oficial de la
Federación*'s (DOF, Mexico's official gazette) undocumented JSON open-data
service, plus a fallback onto the DOF's own website (``www.dof.gob.mx``) for
the days SIDOF silently loses — reported as an empty, valid day, same as a
Sunday — and a reader for the ``notas-archivo`` release, the compact
``codNota``/``titulo``/``fecha`` record of every legal provision ever
published.

The sections below are ordered the way data actually flows:
:py:mod:`dofjson.api` (the entry points every caller should use),
:py:mod:`dofjson.sidof` (the raw SIDOF REST client),
:py:mod:`dofjson.dofweb` (the HTML fallback), :py:mod:`dofjson.notas` (pure
helpers over an already-fetched day), :py:mod:`dofjson.titulos` (the
``notas-archivo`` release and the titles stream), :py:mod:`dofjson.archivo`
(the resumable whole-history downloader), :py:mod:`dofjson.cli`.
:py:mod:`dofjson.sidof`/:py:mod:`dofjson.dofweb` are never imported directly
from *another package* (``nota2md``, ``dof2md``, ``md2akn``) — the top-level
re-exports below are the contract those callers rely on instead.
:py:mod:`dofjson.api` is the one place inside this package allowed to depend
on both, since it alone makes the SIDOF-or-``dofweb`` decision
(:py:func:`~dofjson.get_nota`/:py:func:`~dofjson.get_notas`); other modules
here reach into :py:mod:`dofjson.sidof` directly only for endpoints
``dofweb`` has no equivalent for at all — edition images/PDFs by a raw
``codDiario`` (:py:mod:`dofjson.cli`'s ``--imagenes-diario``/``--imagen``/
``--pdf-diario``) — so there is no SIDOF-vs-``dofweb`` decision to make in
the first place. Every class and function is documented, including
private/internal helpers (leading-underscore names) — useful when extending
or debugging the package, though they are not part of its public API and can
change without notice.

``dofjson.api`` — the entry points
------------------------------------

Every example below hits the network for real — SIDOF, or the DOF website
when SIDOF has nothing — and shares one scratch ``outdir`` so a PDF fetched
once (e.g. the edition PDF behind :py:func:`~dofjson.download_nota_pdf`) is
not downloaded again by a later example that needs the same edition.

>>> import datetime as dt
>>> import tempfile
>>> from pathlib import Path
>>> import dofjson
>>>
>>> outdir = Path(tempfile.mkdtemp())

:py:func:`~dofjson.get_nota` resolves a bare ``codNota`` to its full record,
from SIDOF or — only when SIDOF has no record of it at all — the DOF
website, tagging the result with ``fuente`` (:py:data:`~dofjson.FUENTE_WEB`
or SIDOF's own marker) either way:

>>> nota = dofjson.get_nota(4648702)
>>> nota["fuente"], nota["titulo"][:29], nota["existeImagen"]
('sidof', 'DECRETO por el que se concede', 'S')

This same note has real HTML content (``cadenaContenido``), so
:py:func:`~dofjson.download_nota` — "whatever it takes to read this note,
preferring its HTML" — saves it as JSON:

>>> dofjson.download_nota(4648702, outdir)
[...Path('.../nota-4648702.json')]

:py:func:`~dofjson.download_nota_imagenes`, unlike ``download_nota``,
*always* fetches the scanned page image(s) regardless of whether HTML
content exists too — this is what keeps dof2md's OCR path available for
every note, HTML or not:

>>> paths = dofjson.download_nota_imagenes(4648702, outdir)
>>> [p.name for p in paths]
['nota-4648702-19900315-003-U-000.jpg', 'nota-4648702-19900315-004-U-000.jpg']

:py:func:`~dofjson.download_nota_imagen_o_pdf` picks whichever of the two
the note actually has — images here, since it does:

>>> dofjson.download_nota_imagen_o_pdf(4648702, outdir) == paths
True

:py:func:`~dofjson.download_nota_pdf` downloads the note's own PDF instead —
the whole edition, cached in `outdir` as ``edicion-{codDiario}.pdf``, sliced
down to just this note's page(s):

>>> dest = dofjson.download_nota_pdf(4648702, outdir)
>>> dest.name
'nota-4648702.pdf'
>>> (outdir / "edicion-200106.pdf").exists()
True

:py:func:`~dofjson.download_edicion_pdf` downloads a whole edition's PDF by
date and edition (MAT/VES/EXT) instead of by note — the same edition as
above, so it is already cached in `outdir` and this call makes no request at
all:

>>> dofjson.download_edicion_pdf(dt.date(1990, 3, 15), "MAT", outdir).name
'edicion-200106.pdf'

:py:func:`~dofjson.get_notas` is the day-level counterpart of ``get_nota``,
tagged with `fuente` the same way. ``cache_dir=None`` skips the
``notas-archivo`` on-disk cache (see :doc:`index`'s package table) so this
example's `fuente` reflects a live SIDOF answer regardless of what a
previous example on this page may have already cached:

>>> notas = dofjson.get_notas(dt.date(1990, 3, 15), cache_dir=None)
>>> notas["fuente"], len(notas["NotasMatutinas"])
('sidof', 26)

:py:func:`~dofjson.fetch_daily_legal_provisions` is the project's one
day-level entry point: the same day, as one flat list, each provision
naming its own edition —

>>> for provision in dofjson.fetch_daily_legal_provisions(
...     dt.date(2024, 9, 15), cache_dir=None
... ):
...     provision["edicion"], provision["codNota"]
('VES', 5738985)

.. automodule:: dofjson.api
   :members:
   :private-members:
   :undoc-members:

``dofjson.sidof`` — the raw SIDOF client
--------------------------------------------

:py:mod:`dofjson.sidof` only ever talks to SIDOF and depends on nothing else
in the package — the plain REST client :py:mod:`dofjson.api` builds its own
SIDOF-or-``dofweb`` decisions on, and :py:mod:`dofjson.cli` also calls
directly for the edition-level endpoints ``dofweb`` cannot serve at all (see
above).

>>> diario = dofjson.get_diario(dt.date(1990, 3, 15))
>>> cod_diario = diario["Matutina"][0]["codDiario"]
>>> cod_diario
200106

>>> indicadores = dofjson.get_indicadores(dt.date(2024, 9, 16))
>>> indicadores["ListaIndicadores"][0]["codIndicador"]
36431

:py:func:`~dofjson.get_imagenes` lists every scanned page of an edition;
match a note's own ``pagina`` against this to find its image, then hand
``nombreArchivo`` and the edition (MAT/VES/EXT) to
:py:func:`~dofjson.download_imagen`:

>>> imagenes = dofjson.get_imagenes(cod_diario)
>>> primera = imagenes["imagenesFS"][0]
>>> primera["pagina"], primera["nombreArchivo"]
(1, '19900315-001-U-011')

>>> dest = outdir / "pagina-1.jpg"
>>> dofjson.download_imagen(primera["nombreArchivo"], "MAT", dest)
>>> dest.exists()
True

There is no per-note PDF endpoint — only the whole edition's, via
:py:func:`~dofjson.download_pdf` (:py:func:`~dofjson.download_nota_pdf` above
is the note-scoped, cached wrapper around this same call):

>>> dest = outdir / "edicion-cruda.pdf"
>>> dofjson.download_pdf(cod_diario, dest)
>>> dest.exists()
True

.. automodule:: dofjson.sidof
   :members:
   :private-members:
   :undoc-members:

``dofjson.dofweb`` — the fallback for the days SIDOF loses
----------------------------------------------------------------

SIDOF does not report a missing day as an error: it answers **200 OK with
every note list empty**, indistinguishable on its face from a Sunday. On
**08-03-1999**, for instance, SIDOF reports nothing while the DOF ran the
decree amending articles 16, 19, 22 and 123 of the Constitution — the note
is unreachable from SIDOF by any route. ``dofjson.dofweb`` reads the same
day from the DOF's own website, a separate system with a separate database
that does have it, and :py:func:`~dofjson.get_notas` folds the two into one
call: a caller only ever needs to check the `fuente` this returns, never
which module actually answered.

``cache_dir=None`` again keeps this example honest about hitting SIDOF/the
website live, rather than a `notas-archivo` day already cached from a
previous run of this page:

>>> notas = dofjson.get_notas(dt.date(1999, 3, 8), cache_dir=None)
>>> notas["fuente"] == dofjson.FUENTE_WEB
True

:py:func:`~dofjson.notas_del_dia` (:py:mod:`dofjson.notas`, below) flattens
that same per-edition response into one list, each note carrying the day's
`fuente` too — the recovered decree is the very first one, in publication
order:

>>> provisions = dofjson.notas_del_dia(notas)
>>> len(provisions)
22
>>> provisions[0]["codNota"], provisions[0]["fuente"]
(4997854, 'dof.gob.mx')
>>> "reformados los art" in provisions[0]["titulo"]
True

.. automodule:: dofjson.dofweb
   :members:
   :private-members:
   :undoc-members:

``dofjson.notas`` — pure helpers over an already-fetched day
--------------------------------------------------------------------

None of these make a network request, and none care whether the day came
from SIDOF or the website — :py:func:`~dofjson.notas_del_dia` and
:py:func:`~dofjson.get_notas`'s own `fuente` tagging are demonstrated
together above, in ``dofjson.dofweb``'s recovered-day example.

:py:func:`~dofjson.infer_paginas` infers how many pages a note spans from
where the *next* note (in publication order) starts — codNota 4845455
(pagina 21) followed by codNota 4845457 (pagina 22), both real notes from
02-01-1980:

>>> notas_del_dia = {
...     "NotasMatutinas": [
...         {"codNota": 4845424, "pagina": 2},
...         {"codNota": 4845455, "pagina": 21},
...         {"codNota": 4845457, "pagina": 22},
...     ]
... }
>>> nota = {"codNota": 4845455, "codEdicion": "MAT", "pagina": 21}
>>> dofjson.infer_paginas(nota, notas_del_dia)
[21, 22]

:py:func:`~dofjson.quita_notas_sin_titulo` drops title-less stub entries —
most are duplicates of an adjacent, same-page note:

>>> notas_crudas = {
...     "NotasMatutinas": [
...         {"codNota": 1, "titulo": "DECRETO por el que se reforma..."},
...         {"codNota": 2, "titulo": ""},
...     ],
...     "NotasVespertinas": [],
... }
>>> dofjson.quita_notas_sin_titulo(notas_crudas)
{'NotasMatutinas': [{'codNota': 1, 'titulo': 'DECRETO por el que se reforma...'}], 'NotasVespertinas': []}

.. automodule:: dofjson.notas
   :members:
   :private-members:
   :undoc-members:

``dofjson.titulos`` — the notas-archivo release and the titles stream
------------------------------------------------------------------------------

The ``notas-archivo`` release (117 assets as of this writing, ~59 MB total —
small enough to fetch in full) publishes one ``.tgz`` per year (and, for the
current year, one per month so far), each holding every day's raw notes
index. :py:func:`~dofjson.download_dof_assets` downloads the whole release
into an on-disk cache — the same cache directory :py:func:`~dofjson.get_nota`
and :py:func:`~dofjson.get_notas` read from first, before ever asking SIDOF:

>>> assets = dofjson.download_dof_assets(log=lambda *a: None)
>>> len(assets)
117
>>> assets[0].name
'notas-1917.tgz'

:py:func:`~dofjson.notas_de_tgz` reads one such asset — nothing is written to
disk, everything comes straight out of its bytes already in memory — and
yields every note it holds whole, in publication order:

>>> primeras = list(dofjson.notas_de_tgz(assets[0].read_bytes()))[:2]
>>> [(n["codNota"], n["fecha"]) for n in primeras]
[(4430696, '02-01-1917'), (4430783, '02-01-1917')]

:py:func:`~dofjson.iterador_de_assets` streams every note in the whole
release, one asset after another — the building block
:py:func:`~dofjson.legal_provisions_titles` is written over. ``itertools.islice``
stops it early here; a real caller would let it run to completion (see
CLAUDE.md's own note on ``nota2md download gazette-metadata`` for how the
project usually consumes it):

>>> import itertools
>>> primeras = list(itertools.islice(dofjson.iterador_de_assets(log=lambda *a: None), 3))
>>> [n["codNota"] for n in primeras]
[4430696, 4430783, 4430789]

:py:func:`~dofjson.legal_provisions_titles` is the same stream, projected
down to just ``codNota``/``titulo``/``fecha``/``codOrgaUno`` — the compact
dataset every title-level analysis in the project reads:

>>> primeros_titulos = list(
...     itertools.islice(dofjson.legal_provisions_titles(log=lambda *a: None), 2)
... )
>>> primeros_titulos[0]["codOrgaUno"]
'PE'

:py:func:`~dofjson.organigrama` builds the ``codOrgaUno`` -> `nombreCodOrgaUno`
map by consuming the whole release once — a full pass takes several minutes
(~1.2 million notes across every asset), too slow to run on every push, so
this example patches :py:func:`dofjson.titulos.listar_assets` down to two of
the smallest real assets first. Nothing else about the call is faked: it
still downloads and parses those two assets for real, just fewer of them:

>>> from unittest.mock import patch
>>> import dofjson.titulos as titulos
>>> dos_assets = [a for a in titulos.listar_assets() if a["name"] in ("notas-1917.tgz", "notas-1918.tgz")]
>>> with patch.object(titulos, "listar_assets", return_value=dos_assets):
...     org = dofjson.organigrama(cache_dir=None, log=lambda *a: None)
>>> sorted(org.items())
[('PE', 'PODER EJECUTIVO'), ('PJ', 'PODER JUDICIAL'), ('PL', 'PODER LEGISLATIVO')]

.. automodule:: dofjson.titulos
   :members:
   :private-members:
   :undoc-members:

``dofjson.archivo`` — the resumable whole-history downloader
--------------------------------------------------------------------

Nothing here is re-exported off :py:mod:`dofjson` itself (it has no name in
``__all__``): this module is the ``--archivo`` mode of the CLI below, an
incremental, resumable download of every day's notes index over a whole date
range into ``<outdir>/YYYY/DDMMYYYY-notas.json``, registering completed days
in ``<outdir>/.completados`` so a re-run only fetches what is missing. It
applies the same SIDOF-then-``dofweb`` policy as
:py:func:`~dofjson.get_notas` (see ``respaldo`` there) to every day in range,
day 08-03-1999 included.

.. automodule:: dofjson.archivo
   :members:
   :private-members:
   :undoc-members:

``dofjson.cli`` — command-line entry point
------------------------------------------------

The ``dofjson`` console script. A bare date queries a day's notes index,
folding in the ``dofweb`` fallback the same way :py:func:`~dofjson.get_notas`
does:

.. code-block:: console

   $ dofjson 1999-03-08 --outdir .
   SIDOF no tiene 1999-03-08; recuperada de dof.gob.mx
   Saved to: 08031999-notas.json

``--nota`` downloads a single note by its ``codNota`` alone, HTML JSON or
scanned images, whichever it has:

.. code-block:: console

   $ dofjson --nota 4648702 --outdir .
   Saved to: nota-4648702.json

``--archivo`` runs the resumable whole-range download
(:py:mod:`dofjson.archivo`); a short range around 08-03-1999 shows the same
recovery in its own summary:

.. code-block:: console

   $ dofjson --archivo --desde 1999-03-07 --hasta 1999-03-09 --outdir ./archivo-demo --pausa 0
   Descargando índices de notas del DOF: 1999-03-07 -> 1999-03-09  (destino: archivo-demo/)
   Días ya completados: 0
   Respaldo desde dof.gob.mx cuando SIDOF no trae notas: habiles

   [1999-03-08] SIDOF no la tiene; recuperada de dof.gob.mx

   Resumen:
     dias_procesados: 3
     dias_con_indice: 1
     dias_recuperados: 1
     dias_solo_imagen: 0
     dias_sin_edicion: 1
     dias_error: 0

   1 día(s) que SIDOF da por vacíos sí se publicaron; se tomaron de dof.gob.mx (marcados con "fuente": "dof.gob.mx").

.. automodule:: dofjson.cli
   :members:
   :private-members:
   :undoc-members:

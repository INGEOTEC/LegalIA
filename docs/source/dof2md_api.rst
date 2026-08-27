:mod:`dof2md`
=============

.. image:: https://github.com/INGEOTEC/LegalIA/actions/workflows/test.yml/badge.svg
        :target: https://github.com/INGEOTEC/LegalIA/actions/workflows/test.yml

.. image:: https://badge.fury.io/py/dof2md.svg
        :target: https://badge.fury.io/py/dof2md

:py:mod:`dof2md` converts a PDF or a set of scanned page images — from
Mexico's official gazette (DOF, *Diario Oficial de la Federación*) or any
other document — into Markdown, optionally cropped down to a single note.
It wraps `mineru <https://github.com/opendatalab/MinerU>`_ for the OCR and
layout analysis itself; :py:mod:`dof2md`'s own contribution is keeping
mineru's ``mineru-api`` server warm across a batch of documents, stitching
several scanned pages of the same note into one continuous Markdown
document, rewriting mineru's raw HTML table fallback into Markdown tables,
and cropping the result down to a single note by locating its title and the
next note's title in the OCR'd text.

The sections below are ordered the same way as :doc:`index`'s pipeline
diagram: the two entry points first, then each module in the order a
conversion actually flows through them. Every class and function is
documented, including private/internal helpers (leading-underscore names) —
useful when extending or debugging the package, though they are not part of
its public API and can change without notice.

``dof2md.cli`` — command-line entry point
------------------------------------------

The ``dof2md`` console script. Parses arguments and drives one
:py:class:`~dof2md.batch.BatchConverter` conversion; see :doc:`index` for
example invocations.

.. automodule:: dof2md.cli
   :members:
   :private-members:
   :undoc-members:

``dof2md.batch`` — Python entry point
---------------------------------------

:py:class:`~dof2md.BatchConverter` is the package's public entry point when
used from Python, re-exported off :py:mod:`dof2md` itself. As a context
manager it starts a persistent ``mineru-api`` server on ``__enter__``
(skipped if a caller further up already has one running via
``MINERU_API_URL`` — see ``dof2md.mineru_server`` below) and stops it on
``__exit__``; calling it converts one document — a single PDF path, or a
list of image paths for a document spanning several scanned pages — to
Markdown.

>>> from dof2md import BatchConverter
>>>
>>> jobs = [
...     ("a.pdf", "output", "a.md"),
...     (["b-p1.jpg", "b-p2.jpg"], "output", "b.md"),
... ]
>>> with BatchConverter() as convert:
...     for path_or_paths, outdir, filename in jobs:
...         convert(path_or_paths, outdir, filename)

Passing ``titulo``/``titulo_siguiente`` crops the OCR'd Markdown down to the
text between the two titles, as they appear in the gazette's own index —
useful because a scanned edition page usually holds the tail of one note and
the head of the next:

>>> with BatchConverter() as convert:
...     convert(
...         "edicion.pdf", "output", "nota.md",
...         titulo="ACUERDO por el que se...",
...         titulo_siguiente="DECRETO por el que se...",
...     )

Title matching is fuzzy (OCR text rarely matches an index title exactly), so
a match below ``min_confidence`` (default ``0.6``) is treated as not found
and the crop falls back to keeping more text rather than dropping content
(see ``dof2md.cutter`` below). ``keep_pages=True`` also keeps the uncropped
Markdown, as ``<outdir>/<pdf stem>.full.md``; ``keep_mineru_output=True``
keeps mineru's own raw output instead of discarding it (see
``dof2md.converter`` below).

:py:func:`nota2md.legal_provisions` accepts an already-``__enter__``'d
:py:class:`~dof2md.BatchConverter` as its own ``converter`` parameter, so a
batch of DOF legal provisions can share the same warm server too.

.. automodule:: dof2md.batch
   :members:
   :private-members:
   :undoc-members:

``dof2md.mineru_server`` — keeping mineru-api warm
-----------------------------------------------------

``BatchConverter.__enter__`` starts a :py:class:`~dof2md.mineru_server.MineruServer`,
which launches ``mineru-api`` as a subprocess, waits for it to report
healthy, and points every conversion in the batch at it via the
``MINERU_API_URL`` environment variable — instead of the ``mineru`` CLI
spinning up (and reloading all layout/OCR models into) a fresh temporary
server on every single invocation.

.. automodule:: dof2md.mineru_server
   :members:
   :private-members:
   :undoc-members:

``dof2md.converter`` — running mineru
-----------------------------------------

Each ``BatchConverter.__call__`` shells out to the ``mineru`` CLI —
reusing the ``MINERU_API_URL`` server above when set — to OCR a PDF
(:py:func:`~dof2md.converter.convert_to_markdown`) or one or more scanned
page images (:py:func:`~dof2md.converter.convert_images_to_markdown`), then
hands mineru's raw Markdown to ``dof2md.tables`` below before writing the
result to disk.

.. automodule:: dof2md.converter
   :members:
   :private-members:
   :undoc-members:

``dof2md.tables`` — HTML tables to Markdown tables
-------------------------------------------------------

mineru renders simple tables as Markdown but falls back to raw HTML
(``<table>…</table>`` with rowspan/colspan) for anything complex; this
module rewrites those into GitHub Markdown tables so both conversion
functions above return Markdown all the way through.

.. automodule:: dof2md.tables
   :members:
   :private-members:
   :undoc-members:

``dof2md.cutter`` — cropping to a single note
---------------------------------------------------

When ``BatchConverter`` is called with ``titulo``, the last step before the
Markdown is written is slicing it down to the text between this note's title
and the next note's title, as they appear in the gazette's own per-day
index — the OCR'd text otherwise spans whatever notes shared that scanned
page.

.. automodule:: dof2md.cutter
   :members:
   :private-members:
   :undoc-members:

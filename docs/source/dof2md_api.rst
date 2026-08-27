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

:py:class:`~dof2md.BatchConverter` is the package's single public entry
point, re-exported off :py:mod:`dof2md` itself. As a context manager it
starts a persistent ``mineru-api`` server on ``__enter__`` (skipped if a
caller further up already has one running via ``MINERU_API_URL``) and stops
it on ``__exit__``; calling it converts one document — a single PDF path, or
a list of image paths for a document spanning several scanned pages — to
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
and the crop falls back to keeping more text rather than dropping content.
``keep_pages=True`` also keeps the uncropped Markdown, as
``<outdir>/<pdf stem>.full.md``; ``keep_mineru_output=True`` keeps mineru's
own raw output instead of discarding it.

:py:func:`nota2md.legal_provisions` accepts an already-``__enter__``'d
:py:class:`~dof2md.BatchConverter` as its own ``converter`` parameter, so a
batch of DOF legal provisions can share the same warm server too.

.. automodule:: dof2md
   :members:

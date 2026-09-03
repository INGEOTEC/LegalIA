:mod:`dof2md`
=============

.. image:: https://github.com/INGEOTEC/LegalIA/actions/workflows/test.yml/badge.svg
        :target: https://github.com/INGEOTEC/LegalIA/actions/workflows/test.yml

.. image:: https://badge.fury.io/py/dof2md.svg
        :target: https://badge.fury.io/py/dof2md

Version |dof2md_version| — see :doc:`index` for the full package table.

:py:mod:`dof2md` converts a PDF or a set of scanned page images — from
Mexico's official gazette (DOF, *Diario Oficial de la Federación*) or any
other document — into Markdown, optionally cropped down to a single note.
It wraps `mineru <https://github.com/opendatalab/MinerU>`_ for the OCR and
layout analysis itself; :py:mod:`dof2md`'s own contribution is keeping
mineru's ``mineru-api`` server warm across a batch of documents, stitching
several scanned pages of the same note into one continuous Markdown
document, rewriting mineru's raw HTML table fallback into Markdown tables,
and cropping the result down to a single note by locating its title and the
next note's title in the OCR'd text. It has no notion of a "note"/legal
provision of its own, and no download of its own — getting a whole DOF
edition's PDF by date and edition is :py:func:`dofjson.download_edicion_pdf`'s
job; :py:mod:`nota2md` is what calls into :py:mod:`dof2md` at all, and only
as its OCR fallback for legal provisions predating the HTML era.

dof2md's architecture
========================

Both entry points below — the ``dof2md`` command line (:py:mod:`dof2md.cli`)
and :py:class:`~dof2md.BatchConverter` (:py:mod:`dof2md.batch`) used directly
from Python — go through the same pipeline. :py:class:`~dof2md.mineru_server.MineruServer`
keeps a single ``mineru-api`` process warm across a batch instead of paying
its startup cost per document; :py:mod:`dof2md.converter` shells out to it,
:py:mod:`dof2md.tables` rewrites mineru's raw HTML table fallback into
Markdown tables, and :py:mod:`dof2md.cutter` optionally crops the result down
to one note by title.

.. graphviz::
   :alt: dof2md's conversion pipeline, from entry points to Markdown output.

   digraph dof2md_flow {
       rankdir=LR;
       fontname="sans-serif";
       node [fontname="sans-serif", fontsize=11, shape=box, style="rounded,filled",
             fillcolor="#f4f4f4", color="#888888"];
       edge [fontname="sans-serif", fontsize=9, color="#888888"];

       cli [label="cli.py\n(dof2md command)"];
       batch [label="batch.py\nBatchConverter"];
       server [label="mineru_server.py\nMineruServer"];
       mineru [label="mineru CLI\n(external OCR/layout)", style="rounded,dashed", fillcolor="#ffffff"];
       converter [label="converter.py\nconvert_to_markdown()\nconvert_images_to_markdown()"];
       tables [label="tables.py\nhtml_tables_to_markdown()"];
       cutter [label="cutter.py\ncut_markdown_by_titles()\n(optional, if titulo given)"];
       output [label="Markdown output", shape=note, style=filled, fillcolor="#ffffff"];

       cli -> batch;
       batch -> server [label="__enter__ / __exit__"];
       server -> converter [label="MINERU_API_URL", style=dashed];
       batch -> converter [label="__call__"];
       converter -> mineru [label="subprocess"];
       converter -> tables [label="rewrite HTML tables"];
       tables -> batch [label="Markdown"];
       batch -> cutter [label="titulo given"];
       cutter -> output;
       batch -> output [label="titulo omitted"];
   }

The sections below are ordered the way a conversion actually flows through
the package: the two entry points first, then each module in turn. Every
class and function is documented, including private/internal helpers
(leading-underscore names) — useful when extending or debugging the package,
though they are not part of its public API and can change without notice.

**The one documented exception to "every public symbol has a verified
example" in this whole epic.** Entering :py:class:`~dof2md.BatchConverter`
starts a real ``mineru-api`` server, and calling it shells out to the real
``mineru`` CLI — neither is installed in the doctest job on purpose
(``mineru[pipeline]`` is heavy, and keeping it out is exactly why
``.readthedocs.yaml``/``test.yml`` install ``dof2md`` with ``--no-deps``; see
:doc:`index`'s package table). Every example below that would actually invoke
mineru is marked ``# doctest: +SKIP`` and is instead exercised for real by
``packages/dof2md/tests/test_batch.py`` and ``test_cli.py``, which mock only
the mineru boundary (``dof2md.converter.convert_to_markdown``/
``convert_images_to_markdown``, ``dof2md.batch.MineruServer``) and run
everything else — argument forwarding, title cropping, ``keep_pages``,
``keep_mineru_output`` — for real. ``dof2md.cutter`` below, needing neither
mineru nor any file on disk, is genuinely executed.

``dof2md.cli`` — command-line entry point
------------------------------------------

The ``dof2md`` console script. Parses arguments and drives one
:py:class:`~dof2md.batch.BatchConverter` conversion, printing where the
result was saved:

.. code-block:: console

   $ dof2md --pdf edicion.pdf --outdir output
   Converting to Markdown (mineru)...
   Markdown saved to: output/edicion.md

``--filename`` defaults to the ``--pdf`` file's own name (with a ``.md``
extension); it is required with ``--images``, since a list of scanned pages
has no single input name to derive one from:

.. code-block:: console

   $ dof2md --images nota-200-p1.jpg nota-200-p2.jpg --filename nota-200.md --outdir output
   Converting to Markdown (mineru)...
   Markdown saved to: output/nota-200.md

``--titulo``/``--titulo-siguiente`` crop the result to one note;
``--keep-pages`` also keeps the uncropped conversion alongside it, as
``<outdir>/<pdf stem>.full.md``:

.. code-block:: console

   $ dof2md --pdf edicion.pdf --outdir output \
       --titulo "ACUERDO por el que se..." \
       --titulo-siguiente "DECRETO por el que se..." \
       --keep-pages
   Converting to Markdown (mineru)...
   Markdown saved to: output/edicion.md

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
Markdown:

>>> from dof2md import BatchConverter
>>>
>>> jobs = [
...     ("a.pdf", "output", "a.md"),
...     (["b-p1.jpg", "b-p2.jpg"], "output", "b.md"),
... ]
>>> with BatchConverter() as convert:  # doctest: +SKIP
...     for path_or_paths, outdir, filename in jobs:
...         convert(path_or_paths, outdir, filename)

Passing ``titulo``/``titulo_siguiente`` crops the OCR'd Markdown down to the
text between the two titles, as they appear in the gazette's own index —
useful because a scanned edition page usually holds the tail of one note and
the head of the next. Title matching is fuzzy (OCR text rarely matches an
index title exactly), so ``min_confidence`` (default ``0.6``) sets how
confident a match has to be before it is trusted — a weaker one is treated as
not found and the crop falls back to keeping more text rather than dropping
content (see ``dof2md.cutter`` below). ``keep_pages=True`` also keeps the
uncropped Markdown, as ``<outdir>/<pdf stem>.full.md``; ``keep_mineru_output=True``
keeps mineru's own raw output instead of discarding it (see
``dof2md.converter`` below):

>>> with BatchConverter() as convert:  # doctest: +SKIP
...     convert(
...         "edicion.pdf", "output", "nota.md",
...         titulo="ACUERDO por el que se...",
...         titulo_siguiente="DECRETO por el que se...",
...         min_confidence=0.8,
...         keep_pages=True,
...         keep_mineru_output=True,
...     )

:py:func:`nota2md.legal_provisions` accepts an already-``__enter__``'d
:py:class:`~dof2md.BatchConverter` as its own ``converter`` parameter, so a
batch of DOF legal provisions can share the same warm server too — the way
to OCR many pre-HTML-era notes without paying mineru's startup cost once per
note:

>>> import nota2md
>>> codigos_sin_html = [4430696, 4430697]
>>> with BatchConverter() as ins:  # doctest: +SKIP
...     for cod_nota in codigos_sin_html:
...         nota2md.legal_provisions(cod_nota, "output", source="image", converter=ins)

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
page. Needing neither mineru nor a file on disk, this is the one example on
this page that runs for real rather than under ``# doctest: +SKIP``:

>>> from dof2md.cutter import cut_markdown_by_titles
>>> markdown = (
...     "resto de la nota anterior.\n\n"
...     "## Acuerdo de regularizacion de titulos\n\n"
...     "Cuerpo del acuerdo.\n\n"
...     "## Norma Oficial Mexicana NOM-042-NUCL\n\n"
...     "Nota siguiente, excluir.\n"
... )
>>> cut = cut_markdown_by_titles(
...     markdown,
...     "Acuerdo de regularizacion de titulos",
...     "Norma Oficial Mexicana NOM-042-NUCL",
... )
>>> print(cut)
## Acuerdo de regularizacion de titulos
<BLANKLINE>
Cuerpo del acuerdo.

.. automodule:: dof2md.cutter
   :members:
   :private-members:
   :undoc-members:

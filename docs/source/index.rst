.. _LegalIA:

=======
LegalIA
=======

.. image:: https://img.shields.io/badge/GitHub-LegalIA-black?logo=github
        :target: https://github.com/INGEOTEC/LegalIA

.. image:: https://github.com/INGEOTEC/LegalIA/actions/workflows/test.yml/badge.svg
        :target: https://github.com/INGEOTEC/LegalIA/actions/workflows/test.yml

.. image:: https://readthedocs.org/projects/legalia/badge/?version=latest
        :target: https://legalia.readthedocs.io/en/latest/?badge=latest

LegalIA is a monorepo of independently-versioned Python packages, developed
by `INGEOTEC <https://github.com/INGEOTEC>`_, for analyzing legal texts in
the Mexican context. Its first target is the *Diario Oficial de la
Federación* (DOF), Mexico's official gazette: more than 1.2 million legal
provisions published without interruption since 1917.

**This site is developer documentation**: how each package's code is put
together, and its full API — public and private — for anyone extending or
debugging it. It is not a usage guide; for that (installing and using a
package as-is, worked examples, datasets, findings) see `the LegalIA website
<https://ingeotec.github.io/LegalIA/>`_, including
`website/pages/dof2md.ipynb
<https://github.com/INGEOTEC/LegalIA/blob/master/website/pages/dof2md.ipynb>`_
for ``dof2md`` specifically.

Each package under ``packages/<name>/`` has its own ``pyproject.toml``,
version, and PyPI release, and builds on the ones before it in this read
order: ``dofjson`` -> ``nota2md`` -> ``dof2md`` -> ``md2akn``. This site
currently documents only ``dof2md`` — the most stable of the four at the
moment; ``dofjson``, ``nota2md`` and ``md2akn`` get their own page once each
is similarly stable (see `issue #119
<https://github.com/INGEOTEC/LegalIA/issues/119>`_).

dof2md's architecture
======================

:py:mod:`dof2md` converts a PDF or a set of scanned page images to Markdown
via OCR (`mineru <https://github.com/opendatalab/MinerU>`_). It has no
notion of a "note"/legal provision, and no download of its own — getting a
whole DOF edition's PDF by date and edition is
:py:func:`dofjson.download_edicion_pdf`'s job.

Both entry points — the ``dof2md`` command line (:py:mod:`dof2md.cli`) and
:py:class:`~dof2md.BatchConverter` (:py:mod:`dof2md.batch`) used directly
from Python — go through the same pipeline below.
:py:class:`~dof2md.mineru_server.MineruServer` keeps a single ``mineru-api``
process warm across a batch instead of paying its startup cost per document;
:py:mod:`dof2md.converter` shells out to it, :py:mod:`dof2md.tables`
rewrites mineru's raw HTML table fallback into Markdown tables, and
:py:mod:`dof2md.cutter` optionally crops the result down to one note by
title.

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

See :doc:`dof2md_api` for the full module-by-module API, ordered to match
the diagram above, including title-based cropping and mineru
output-retention options. Usage examples (CLI, :py:class:`~dof2md.BatchConverter`)
live on `the LegalIA website's dof2md page
<https://ingeotec.github.io/LegalIA/>`_, not here.

API
===

.. toctree::
   :maxdepth: 1

   dof2md_api

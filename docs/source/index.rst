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
together, and its full API — public and private, with a worked example for
every public symbol — for anyone extending or debugging it. It is not a
results guide; for that (datasets, findings, the analysis of the gazette)
see `the LegalIA website <https://ingeotec.github.io/LegalIA/>`_.

Each package under ``packages/<name>/`` has its own ``pyproject.toml``,
version, and PyPI release, and builds on the ones before it in this read
order: ``dofjson`` -> ``scjn`` -> ``nota2md`` -> ``dof2md`` -> ``md2akn``.

.. list-table:: The five packages
   :header-rows: 1

   * - Package
     - Purpose
     - Version
     - PyPI
     - API page
   * - ``dofjson``
     - Client for SIDOF's undocumented JSON open-data service, with a
       ``www.dof.gob.mx`` fallback for the days SIDOF loses.
     - |dofjson_version|
     - `dofjson <https://pypi.org/project/dofjson/>`_
     - :doc:`dofjson_api`
   * - ``scjn``
     - Client for the SCJN's SCOW JSON API and the disk-first reader for the
       ``scjn-leyes`` release: a federal law's reform-dated snapshots.
     - |scjn_version|
     - `scjn <https://pypi.org/project/scjn/>`_
     - :doc:`scjn_api`
   * - ``nota2md``
     - One DOF note (or a whole law's reform history) to Markdown, backed by
       ``scjn``'s consolidated texts and the ``codNota`` linking seam
       between the two.
     - |nota2md_version|
     - `nota2md <https://pypi.org/project/nota2md/>`_
     - :doc:`nota2md_api`
   * - ``dof2md``
     - OCRs a PDF or a set of scanned page images to Markdown via mineru —
       ``nota2md``'s fallback for legal provisions predating the HTML era.
     - |dof2md_version|
     - `dof2md <https://pypi.org/project/dof2md/>`_
     - :doc:`dof2md_api`
   * - ``md2akn``
     - Segments a law's Markdown into a hierarchy labelled with Akoma
       Ntoso's vocabulary. No dependency on the other four packages.
     - |md2akn_version|
     - `md2akn <https://pypi.org/project/md2akn/>`_
     - :doc:`md2akn_api`

How the packages relate
========================

``dofjson`` is the only package that talks to SIDOF/``www.dof.gob.mx``;
``scjn`` is the only one that talks to the SCJN's SCOW API and reads the
``scjn-leyes`` release, disk-first, into its own cache; ``nota2md`` depends
on both — building a note's Markdown off ``dofjson``, and matching a
snapshot ``scjn`` reads back to the DOF ``codNota`` that produced it
(:py:mod:`nota2md.linking`, the one SCJN-adjacent concern that needs both
sides, so it stays a layer up from ``scjn`` rather than inside it) — and
reaches into ``dof2md`` only as the OCR fallback for pre-HTML-era provisions
(pre-1999ish). ``md2akn`` reads ``nota2md``'s Markdown output from disk and
depends on none of the other four.

.. graphviz::
   :alt: How dofjson, scjn, nota2md, dof2md and md2akn relate, and the
         external systems each one talks to.

   digraph legalia_flow {
       rankdir=LR;
       fontname="sans-serif";
       node [fontname="sans-serif", fontsize=11, shape=box, style="rounded,filled",
             fillcolor="#f4f4f4", color="#888888"];
       edge [fontname="sans-serif", fontsize=9, color="#888888"];

       sidof [label="SIDOF\n(sidof.segob.gob.mx)", style="rounded,dashed", fillcolor="#ffffff"];
       dofweb [label="www.dof.gob.mx", style="rounded,dashed", fillcolor="#ffffff"];
       scjn_api_ext [label="SCJN SCOW API", style="rounded,dashed", fillcolor="#ffffff"];
       notas_archivo [label="notas-archivo release", style="rounded,dashed", fillcolor="#ffffff"];
       scjn_leyes [label="scjn-leyes release", style="rounded,dashed", fillcolor="#ffffff"];
       mineru [label="mineru\n(external OCR/layout)", style="rounded,dashed", fillcolor="#ffffff"];

       dofjson [label="dofjson\nDOF/SIDOF client"];
       scjn [label="scjn\nSCOW API client,\nscjn-leyes reader"];
       nota2md [label="nota2md\nnote -> Markdown,\ncodNota linking, reform replay"];
       dof2md [label="dof2md\nPDF/image OCR"];
       md2akn [label="md2akn\nMarkdown -> Akoma Ntoso\nvocabulary tree"];

       sidof -> dofjson;
       dofweb -> dofjson [label="recovers days SIDOF loses"];
       notas_archivo -> dofjson [label="legal_provisions_titles"];
       scjn_api_ext -> scjn;
       scjn_leyes -> scjn;
       dofjson -> nota2md;
       scjn -> nota2md;
       nota2md -> dof2md [label="OCR fallback\n(pre-HTML-era notes)", style=dashed];
       dof2md -> mineru;
       nota2md -> md2akn [label="Markdown"];
   }

API
===

.. toctree::
   :maxdepth: 1

   dofjson_api
   scjn_api
   nota2md_api
   dof2md_api
   md2akn_api

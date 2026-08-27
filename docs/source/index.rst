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

This site is the developer documentation — installation and the public API
of each package. For the project's research-facing site (datasets, findings,
worked examples), see `the LegalIA website
<https://ingeotec.github.io/LegalIA/>`_.

Each package under ``packages/<name>/`` has its own ``pyproject.toml``,
version, and PyPI release, and builds on the ones before it in this read
order: ``dofjson`` -> ``nota2md`` -> ``dof2md`` -> ``leyesmx``. This site
currently documents only ``dof2md`` — the most stable of the four at the
moment; ``dofjson``, ``nota2md`` and ``leyesmx`` get their own page once each
is similarly stable (see `issue #119
<https://github.com/INGEOTEC/LegalIA/issues/119>`_).

Quickstart: dof2md
===================

:py:mod:`dof2md` converts a PDF or a set of scanned page images to Markdown
via OCR (`mineru <https://github.com/opendatalab/MinerU>`_). It has no
notion of a "note"/legal provision, and no download of its own — getting a
whole DOF edition's PDF by date and edition is
:py:func:`dofjson.download_edicion_pdf`'s job.

Installing dof2md
^^^^^^^^^^^^^^^^^^

.. code-block:: bash

    pip install -e ".[test]"

Converting a document
^^^^^^^^^^^^^^^^^^^^^^

From the command line, ``dof2md`` takes exactly one input source — a local
PDF or a set of local page images — and converts it to Markdown:

.. code-block:: bash

    dof2md --pdf edicion.pdf   # a local PDF

    dof2md --images pagina-1.jpg pagina-2.jpg \
        --filename out.md      # scanned pages, in order

From Python, :py:class:`~dof2md.BatchConverter` keeps a single ``mineru-api``
server warm across a batch of documents instead of restarting it per
document:

.. code-block:: python

    from dof2md import BatchConverter

    jobs = [
        ("a.pdf", "output", "a.md"),
        (["b-p1.jpg", "b-p2.jpg"], "output", "b.md"),
    ]

    with BatchConverter() as convert:
        for path_or_paths, outdir, filename in jobs:
            convert(path_or_paths, outdir, filename)

See :doc:`dof2md_api` for the full API, including title-based cropping down
to a single note and mineru output-retention options.

Running the tests
^^^^^^^^^^^^^^^^^^

.. code-block:: bash

    pytest packages/dof2md -q

API
===

.. toctree::
   :maxdepth: 1

   dof2md_api

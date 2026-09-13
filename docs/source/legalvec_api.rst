:mod:`legalvec`
================

.. image:: https://github.com/INGEOTEC/LegalIA/actions/workflows/test.yml/badge.svg
        :target: https://github.com/INGEOTEC/LegalIA/actions/workflows/test.yml

.. image:: https://badge.fury.io/py/legalvec.svg
        :target: https://badge.fury.io/py/legalvec

Version |legalvec_version| — see :doc:`index` for the full package table.

:py:mod:`legalvec` reads back this project's own **vector releases**: one
embedding per distinct text of every Mexican federal law, *reglamento* and
*lineamiento*, where "a text" is a retrieval unit
:py:func:`md2akn.text_units` produced — an article, a piece of a long one, a
container epigraph, a preamble, a block of transitorios (issue #227, Fase 4;
issue #218's Fase 3 widened to three corpora).

It is deliberately **not** part of :py:mod:`scjn`. ``scjn`` means "client for
the Court", and these vectors are this project's own — derived on a GPU from
a specific model revision, not something the SCJN publishes. Reading them
needs ``pyarrow`` and ``numpy``, which ``scjn`` has no other reason to take,
and this package imports nothing else in the monorepo
(``packages/legalvec/tests/test_boundary.py`` greps for that).

Three releases, one per corpus — ``scjn-leyes-vectors``,
``scjn-reglamentos-vectors``, ``scjn-lineamientos-vectors`` — mirroring the
three corpus releases, because those are crawled, packaged and republished
independently and a reader who wants only lineamientos should not resolve two
thousand assets to find 254 (issue #227's decision 10). Each holds:

.. code-block:: text

    units.parquet                          every text, with its text_sha1
    leaves.parquet                         every leaf eId -> the unit carrying it
    corpus-manifest.json                   how the units were built
    vectors-manifest.json                  how the vectors were built
    SHA256SUMS.txt
    vectors-<clave>-<model>-<K>.parquet    one per instrument, per model
    vectors-shared-<model>-<K>.parquet     the text several instruments share

``clave`` is a law's slug and the ``id_ordenamiento`` of a reglamento or a
lineamiento (issue #227's decision 7), so a reader never branches on which
collection it is reading — the same reason the corpus build writes that
column at all.

**Not yet published**, and that is a decision, not an omission: issue #115's
Hallazgo C says a human publishes anything derived from the SCJN, and issue
#227 generated each release's upload plan
(``scripts/embeddings/package_vectors.py``'s ``PUBLICAR.md``) without running
it. So the examples below build a tiny release on disk and read it back
rather than downloading one — which is also exactly how the package's own
tests exercise it. The one function with no offline example is
:py:func:`~legalvec.download_vectors_assets`, for the same reason there is
nothing to download from yet; ``packages/legalvec/tests/test_release.py``'s
``TestDownloadVectorsAssets`` covers it against a patched HTTP layer, and a
live example belongs here once the releases exist.

Reading a release
-----------------

Everything is disk-first, the posture :py:mod:`scjn` took in issue #209:
a reader raises :py:exc:`~legalvec.AssetNotCached` rather than reaching for
the network. The cache is this package's own directory —
``$LEGALVEC_CACHE_DIR``, ``~/.cache/legalvec`` by default — separate from
``scjn``'s and ``nota2md``'s, because these are three independently published
corpora of derived data.

>>> import tempfile
>>> from pathlib import Path
>>> import pyarrow as pa, pyarrow.parquet as pq
>>> cache_dir = Path(tempfile.mkdtemp())
>>> release = cache_dir / "scjn-leyes-vectors"
>>> release.mkdir()
>>> def publica(nombre, filas, k):
...     plano = pa.array([x for _sha1, vec in filas for x in vec], type=pa.float16())
...     pq.write_table(pa.table({
...         "text_sha1": pa.array([sha1 for sha1, _vec in filas]),
...         "vector": pa.FixedSizeListArray.from_arrays(plano, k),
...     }), release / nombre)
>>> publica("vectors-lft-qwen3-0.6b-2.parquet", [("propio", [1.0, 2.0])], 2)
>>> publica("vectors-shared-qwen3-0.6b-2.parquet", [("compartido", [3.0, 4.0])], 2)
>>> pq.write_table(
...     pa.table({"clave": ["lft", "lft"], "text_sha1": ["propio", "compartido"],
...               "text": ["Artículo 1o. ...", "Único. Entrará en vigor..."]}),
...     release / "units.parquet",
... )

:py:func:`~legalvec.load_vectors` always reads **two** files and unions them,
deduplicated by ``text_sha1``. That is the whole point of the split: a text
several instruments share — the identical transitorio a dozen reforms carry —
is embedded and published once, in ``vectors-shared-<model>-<K>.parquet``,
and the per-instrument file holds only what is that instrument's alone, so
neither file is usable on its own.

>>> import legalvec
>>> conjunto = legalvec.load_vectors(
...     "leyes", "lft", "Qwen/Qwen3-Embedding-0.6B", cache_dir=cache_dir)
>>> conjunto.text_sha1
['propio', 'compartido']
>>> conjunto.k, conjunto.vectors.shape, conjunto.vectors.dtype
(2, (2, 2), dtype('float16'))

``K`` is read off the published file name rather than assumed from the model,
so a rerun at another dimension is a different file rather than a silent
mismatch. The model may be named either way —
:py:func:`~legalvec.model_slug` is the same transformation the build applies
when it writes the files:

>>> legalvec.model_slug("Qwen/Qwen3-Embedding-4B"), legalvec.model_slug("qwen3-0.6b")
('qwen3-4b', 'qwen3-0.6b')

A vector file carries no text at all — that is what keeps a 2,560-dimension
corpus at gigabytes rather than tens of them. :py:func:`~legalvec.load_units`
is what maps a hash back to the text it came from, and to the instrument,
article and unit type it belongs to:

>>> unidades = legalvec.load_units("leyes", cache_dir=cache_dir)
>>> por_hash = dict(zip(unidades.column("text_sha1").to_pylist(),
...                     unidades.column("text").to_pylist()))
>>> por_hash[conjunto.text_sha1[0]]
'Artículo 1o. ...'

An asset that is not on disk raises rather than downloading, and says what to
run:

>>> try:
...     legalvec.load_units("lineamientos", cache_dir=cache_dir)
... except legalvec.AssetNotCached as exc:
...     print("download_vectors_assets" in str(exc), exc.coleccion)
True lineamientos

Downloading a release
---------------------

:py:func:`~legalvec.download_vectors_assets` is the only function here that
touches the network. ``claves=None`` fetches every vector file the release
publishes, resolved across the whole series of release parts
(``scjn-reglamentos-vectors``, ``-2``, ``-3``: 2,166 vector files do not fit
one release — issue #223's ceiling, and its probe-until-404 scheme reused
unchanged). Naming ``claves`` fetches only those instruments' own files —
plus, always, each model's shared file and the five metadata assets, since
neither an instrument's vectors nor their texts can be read without them:

.. code-block:: python

    legalvec.download_vectors_assets("leyes", ["lft", "cpeum"])
    legalvec.download_vectors_assets("lineamientos")            # the whole corpus

Both models stay live — ``Qwen/Qwen3-Embedding-0.6B`` at K=1,024 and
``Qwen/Qwen3-Embedding-4B`` at K=2,560 — in separate files
(:py:data:`~legalvec.MODELS`). Issue #217's proxy evaluation has not been
run, so nothing has earned the right to drop either; and putting both in one
file per instrument would make whoever wants the 0.6B download the 4B's 2,560
floats to throw them away (issue #227's decisions 9 and 10).

.. automodule:: legalvec.release
   :members:
   :private-members:
   :undoc-members:

``legalvec.cache`` — the on-disk cache
--------------------------------------

.. automodule:: legalvec.cache
   :members:
   :private-members:
   :undoc-members:

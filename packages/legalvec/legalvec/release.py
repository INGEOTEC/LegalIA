"""Reading this project's own vector releases (issue #227's Fase 4).

Three releases, one per corpus — `scjn-leyes-vectors`,
`scjn-reglamentos-vectors`, `scjn-lineamientos-vectors` — mirroring the three
corpus releases `scjn` publishes, because the corpora are crawled, packaged
and republished independently and a reader who wants only lineamientos should
not resolve two thousand assets to find 254 (issue #227's decision 10).

What a release holds, per collection:

    units.parquet                                every text, with its sha1
    leaves.parquet                               every leaf eId -> its unit
    corpus-manifest.json                         how the units were built
    vectors-manifest.json                        how the vectors were built
    SHA256SUMS.txt
    vectors-<clave>-<model>-<K>.parquet          one per instrument, per model
    vectors-shared-<model>-<K>.parquet           the text several share

`clave` is a law's slug and the `id_ordenamiento` of a reglamento or a
lineamiento (issue #227's decision 7), so a reader never branches on which
collection it is reading. A vector file holds `text_sha1` and a fixed-size
`float16` list; `units.parquet` is what maps those hashes back to text,
articles and laws — kept in one file rather than repeated per instrument.

Both models stay live (decision 9): `Qwen/Qwen3-Embedding-0.6B` at K=1,024
and `Qwen/Qwen3-Embedding-4B` at K=2,560 are separate files, never one file
holding both — whoever wants the 0.6B would otherwise download the 4B's
2,560 floats to throw them away.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

from legalvec import cache

#: The two models issue #227 ran, and the `K` each was run at — recorded
#: here only to name the default set `download_vectors_assets` fetches;
#: nothing in this module assumes either of them, and `K` is always read off
#: the file name rather than from this table.
MODELS = ("Qwen/Qwen3-Embedding-0.6B", "Qwen/Qwen3-Embedding-4B")

#: The four non-vector assets every vector release carries, plus its
#: checksums — what `download_vectors_assets` always fetches.
METADATA_ASSETS = (
    "units.parquet",
    "leaves.parquet",
    "corpus-manifest.json",
    "vectors-manifest.json",
    "SHA256SUMS.txt",
)


class AssetNotCached(Exception):
    """The asset is not on disk yet. Every reader here raises this rather
    than downloading (the disk-first posture issue #209 set for `scjn`):
    only `download_vectors_assets` talks to the network."""

    def __init__(self, name: str, coleccion: str, path: Path):
        super().__init__(
            f"'{name}' no esta en la cache ({path}) -- corre "
            f"legalvec.download_vectors_assets('{coleccion}') primero"
        )
        self.name = name
        self.coleccion = coleccion
        self.path = path


@dataclass(frozen=True)
class VectorSet:
    """One instrument's vectors, as `text_sha1` plus the matrix itself.

    `vectors[i]` is the embedding of `text_sha1[i]`, and `units.parquet`
    (`load_units`) is what maps that hash to the text it came from — the
    vector files carry no text at all, which is what keeps a 2,560-dimension
    corpus at gigabytes rather than tens of them.
    """

    coleccion: str
    clave: str
    model: str
    #: The embedding dimension, read off the file name rather than assumed.
    k: int
    text_sha1: list[str]
    #: `(len(text_sha1), k)`, `float16` — exactly as published, with no
    #: normalization or requantization applied on the way in.
    vectors: np.ndarray


def model_slug(model: str) -> str:
    """The short name a published file carries for `model`
    (`Qwen/Qwen3-Embedding-0.6B` -> `qwen3-0.6b`).

    The same transformation `scripts/embeddings/encode_shard.py` applies when
    the files are written — duplicated here rather than imported, since those
    scripts are cluster orchestration and not an installable package. An
    already-slugged name passes through unchanged, so a caller may hand
    either form to `load_vectors`.

    >>> import legalvec
    >>> legalvec.model_slug("Qwen/Qwen3-Embedding-4B")
    'qwen3-4b'
    >>> legalvec.model_slug("qwen3-4b")
    'qwen3-4b'
    """
    tail = model.rsplit("/", 1)[-1]
    tail = re.sub(r"[Ee]mbedding-?", "", tail)
    return re.sub(r"[^A-Za-z0-9.]+", "-", tail).strip("-").lower()


def _read_cached(coleccion: str, name: str, cache_dir=None) -> Path:
    path = cache.release_dir(coleccion, cache_dir) / name
    if not path.exists():
        raise AssetNotCached(name, coleccion, path)
    return path


def load_units(coleccion: str, *, cache_dir=None):
    """`units.parquet` for one collection, as a `pyarrow.Table` — every text
    that was embedded, with the `text_sha1` a vector file is keyed by, its
    `unit_type`/`eId`/`num`/`path`, and the instrument (`clave`) it belongs
    to.

    Raises `AssetNotCached` while the file is not downloaded yet.
    """
    return pq.read_table(_read_cached(coleccion, "units.parquet", cache_dir))


def _k_of(directory: Path, clave: str, slug: str) -> int:
    """The `K` a published vector file was written at, read off its own name
    — never assumed from the model, so a rerun at another dimension is a
    different file rather than a silent mismatch."""
    prefijo = f"vectors-{clave}-{slug}-"
    ks = sorted(
        int(p.name[len(prefijo):-len(".parquet")])
        for p in directory.glob(f"{prefijo}*.parquet")
    )
    if not ks:
        raise AssetNotCached(f"{prefijo}<K>.parquet", "", directory)
    return ks[-1]


def load_vectors(coleccion: str, clave: str, model: str, *, cache_dir=None) -> VectorSet:
    """One instrument's vectors: its own file, unioned with the collection's
    shared file and deduplicated by `text_sha1`.

    The union is the whole point of the split. A text several instruments
    share — the identical transitorio a dozen reforms carry — is embedded and
    published once, in `vectors-shared-<model>-<K>.parquet`, rather than once
    per instrument; the per-instrument file holds only what is that
    instrument's alone. Neither file is usable on its own, so this reader
    always reads both.

    `model` is either the published slug (`qwen3-0.6b`) or the model id it
    came from (`Qwen/Qwen3-Embedding-0.6B`); `K` is read off the file name.

    Raises `AssetNotCached` for whichever of the two files is not on disk.
    """
    directorio = cache.release_dir(coleccion, cache_dir)
    slug = model_slug(model)
    k = _k_of(directorio, clave, slug)

    hashes: list[str] = []
    filas: list[np.ndarray] = []
    vistos: set[str] = set()
    for nombre in (f"vectors-{clave}-{slug}-{k}.parquet", f"vectors-shared-{slug}-{k}.parquet"):
        tabla = pq.read_table(_read_cached(coleccion, nombre, cache_dir))
        if not tabla.num_rows:
            continue
        columna = tabla.column("vector").combine_chunks()
        bloque = np.asarray(columna.flatten()).reshape(tabla.num_rows, k)
        for i, sha1 in enumerate(tabla.column("text_sha1").to_pylist()):
            if sha1 in vistos:
                continue
            vistos.add(sha1)
            hashes.append(sha1)
            filas.append(bloque[i])

    matriz = (
        np.stack(filas).astype(np.float16)
        if filas
        else np.empty((0, k), dtype=np.float16)
    )
    return VectorSet(
        coleccion=coleccion, clave=clave, model=model, k=k,
        text_sha1=hashes, vectors=matriz,
    )


def download_vectors_assets(
    coleccion: str,
    claves: list[str] | None = None,
    *,
    models: tuple[str, ...] = MODELS,
    cache_dir=None,
    refresh: bool = False,
    timeout: int = 60,
    log=None,
) -> list[tuple[Path, bool]]:
    """Put a vector release's assets on disk — the only thing here that
    touches the network.

    `claves=None` (the default) fetches every vector file the release
    publishes, resolved across the whole series of release parts
    (`scjn-reglamentos-vectors`, `-2`, `-3`: 2,166 vector files do not fit
    one release, issue #223's scheme). Naming `claves` fetches only those
    instruments' own files — plus, always, the shared file of each model in
    `models` and the five metadata assets, since neither an instrument's
    vectors nor their texts can be read without them.

    Idempotent by name: an asset already on disk is left alone unless
    `refresh=True`. Returns `(path, downloaded)` per asset, in the order they
    were fetched.
    """
    directorio = cache.resolve_cache_dir(cache_dir)
    base = cache.RELEASE_TAGS[coleccion] if coleccion in cache.RELEASE_TAGS else None
    if base is None:
        raise ValueError(
            f"unknown collection: {coleccion!r} (expected one of {tuple(cache.RELEASE_TAGS)})"
        )

    urls = cache.assets_of_series(base, timeout)
    slugs = [model_slug(m) for m in models]
    if claves is None:
        nombres = list(METADATA_ASSETS) + sorted(
            n for n in urls
            if n.startswith("vectors-") and any(f"-{s}-" in n for s in slugs)
        )
    else:
        nombres = list(METADATA_ASSETS)
        for slug in slugs:
            nombres += sorted(n for n in urls if n.startswith(f"vectors-shared-{slug}-"))
            for clave in claves:
                nombres += sorted(n for n in urls if n.startswith(f"vectors-{clave}-{slug}-"))

    resultados = []
    for i, nombre in enumerate(nombres, 1):
        destino = cache.release_dir(coleccion, directorio) / nombre
        ya_estaba = destino.exists() and not refresh
        if ya_estaba:
            ruta = destino
        else:
            if nombre not in urls:
                raise KeyError(f"el release '{base}' no publica ningun asset '{nombre}'")
            ruta = cache.asset_in_cache(
                coleccion, nombre, urls[nombre],
                cache_dir=directorio, refresh=refresh, timeout=timeout,
            )
        if log is not None:
            log(f"[{i}/{len(nombres)}] {nombre}: {'already cached' if ya_estaba else 'downloaded'}")
        resultados.append((ruta, not ya_estaba))
    return resultados

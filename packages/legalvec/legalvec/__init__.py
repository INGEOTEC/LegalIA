"""A vector for every text of every federal instrument — the reader.

This project embeds the current text of three SCJN corpora — every federal
law, every federal *reglamento*, every federal *lineamiento* — one vector per
distinct text, and publishes the result as three GitHub releases
(`scjn-leyes-vectors`, `scjn-reglamentos-vectors`,
`scjn-lineamientos-vectors`). This package is how they are read back.

It is deliberately not part of `scjn`: `scjn` means "client for the Court",
and these vectors are *this project's own*, derived on a GPU from a specific
model revision rather than anything the SCJN publishes. Reading them needs
`pyarrow` and `numpy`, which `scjn` has no other reason to take.

Disk-first, like `scjn` since issue #209: every reader raises `AssetNotCached`
rather than downloading, and `download_vectors_assets` is the only thing here
that talks to the network. Its cache is its own directory
(`$LEGALVEC_CACHE_DIR`, `~/.cache/legalvec` by default), separate from
`scjn`'s and `nota2md`'s.

>>> import legalvec
>>> legalvec.model_slug("Qwen/Qwen3-Embedding-0.6B")
'qwen3-0.6b'

Everything public is reachable off the package itself — nobody imports
`legalvec.release` from outside.
"""

from legalvec.release import (
    MODELS,
    AssetNotCached,
    VectorSet,
    download_vectors_assets,
    load_units,
    load_vectors,
    model_slug,
)

__version__ = "0.1.0"

__all__ = [
    "download_vectors_assets",
    "load_vectors",
    "load_units",
    "VectorSet",
    "AssetNotCached",
    "model_slug",
    "MODELS",
]

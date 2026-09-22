"""Atomic writes, shared by every script in this directory.

Every artifact this build produces — `units.parquet`, a shard's own
`shard-XXXX.parquet`, a `.done`/`.failed` marker, a merged vector table —
lands as `<name>.parcial` and is renamed into place only after `fsync`, so an
interrupted write can never look complete. This is the same convention
`scjn.cache.SUFIJO_PARCIAL` already uses for exactly this reason (a download
cut short must never read back as a cache hit); replicated here rather than
imported, since `scjn`'s own cache is keyed by release/asset name and has
nothing to do with a Slurm run's work directory.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Same suffix `scjn.cache.SUFIJO_PARCIAL` uses — not imported, so this
#: directory stays runnable against a `scjn` whose cache layout has moved,
#: but spelled identically on purpose: a `.parcial` file anywhere in this
#: project means the same thing.
PARTIAL_SUFFIX = ".parcial"


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write `data` to `path` atomically: `<path>.parcial`, `fsync`, rename.

    A reader can only ever see `path` fully written or not at all — never a
    truncated file from a job killed mid-write.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + PARTIAL_SUFFIX)
    with open(partial, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    partial.replace(path)


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def atomic_write_table(path: Path, table) -> None:
    """`pyarrow.parquet.write_table` through `<path>.parcial` (issue #241).

    `pyarrow` is imported here rather than at module level so a script that
    only writes markers does not pay for it.
    """
    import pyarrow.parquet as pq

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + PARTIAL_SUFFIX)
    pq.write_table(table, partial)
    partial.replace(path)


def atomic_write_npy(path: Path, array) -> None:
    """`numpy.save` through `<path>.parcial` (issue #241): `vectors.npy` is
    ~780 MB, long enough to write that a job killed half-way through it is a
    real possibility."""
    import numpy as np

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + PARTIAL_SUFFIX)
    with open(partial, "wb") as handle:
        np.save(handle, array, allow_pickle=False)
        handle.flush()
        os.fsync(handle.fileno())
    partial.replace(path)

"""Client for the SCJN's SCOW JSON API (`scjn.api`), the federal-law
catalogue's own algebra (`scjn.catalog`), per-instrument crawl state
(`scjn.state`), the provenance header a crawl writes to every snapshot
(`scjn.header`), and the `scjn-leyes` release's own disk-first readers
(`scjn.release`), re-exported here as the package's eight entry points.

The SCJN is not an official source of legal text — the Diario Oficial de la
Federación remains that; this package's own crawl is a convenience corpus
its consolidated view makes possible, not a replacement for it.

This is Fase 3 of a larger split (issue #206/#209). `codNota` linking —
matching a snapshot to the DOF `codNota` that produced it — stays one layer
up, in the downstream package that depends on this one: a `codNota` is a DOF
concept, so that seam sits there, calling the readers below (this package
never imports back — see `tests/test_boundary.py`).
"""

from scjn.release import (
    AssetNotCached,
    download_scjn_leyes_assets,
    download_scjn_leyes_catalog,
    download_scjn_leyes_corpus,
    download_scjn_leyes_index,
    iter_current_federal_laws,
    local_slugs,
    markdown_de_snapshot,
)

__version__ = "0.1.0"

__all__ = [
    "download_scjn_leyes_corpus",
    "download_scjn_leyes_index",
    "download_scjn_leyes_catalog",
    "iter_current_federal_laws",
    "markdown_de_snapshot",
    "download_scjn_leyes_assets",
    "local_slugs",
    "AssetNotCached",
]

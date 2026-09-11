"""Client for the SCJN's SCOW JSON API (`scjn.api`), the federal-law
catalogue's own algebra (`scjn.catalog`), per-instrument crawl state
(`scjn.state`), the provenance header a crawl writes to every snapshot
(`scjn.header`), and the disk-first readers (`scjn.release`) for the three
GitHub releases this package feeds -- `scjn-leyes` and, since issue #220 and
#222, the id-keyed `scjn-reglamentos`/`scjn-lineamientos` -- re-exported
here as the package's own entry points.

The SCJN is not an official source of legal text — the Diario Oficial de la
Federación remains that; this package's own crawl is a convenience corpus
its consolidated view makes possible, not a replacement for it.

This is Fase 3 of a larger split (issue #206/#209). `codNota` linking —
matching a snapshot to the DOF `codNota` that produced it — stays one layer
up, in the downstream package that depends on this one: a `codNota` is a DOF
concept, so that seam sits there, calling the readers below (this package
never imports back — see `tests/test_boundary.py`). Neither id-keyed
collection has such linking at all yet (issue #220's own Scope, unchanged by
#222) — a future issue adds it for reglamentos, lineamientos and laws alike.
"""

from scjn.release import (
    AssetNotCached,
    SinTextoEnSCJN,
    download_scjn_leyes_assets,
    download_scjn_leyes_catalog,
    download_scjn_leyes_corpus,
    download_scjn_leyes_index,
    download_scjn_lineamientos_assets,
    download_scjn_lineamientos_corpus,
    download_scjn_lineamientos_index,
    download_scjn_reglamentos_assets,
    download_scjn_reglamentos_corpus,
    download_scjn_reglamentos_index,
    iter_current_federal_laws,
    local_lineamientos_ids,
    local_reglamentos_ids,
    local_slugs,
    markdown_de_snapshot,
)

__version__ = "0.2.0"

__all__ = [
    "download_scjn_leyes_corpus",
    "download_scjn_leyes_index",
    "download_scjn_leyes_catalog",
    "iter_current_federal_laws",
    "markdown_de_snapshot",
    "download_scjn_leyes_assets",
    "local_slugs",
    "download_scjn_reglamentos_index",
    "download_scjn_reglamentos_corpus",
    "download_scjn_reglamentos_assets",
    "local_reglamentos_ids",
    "download_scjn_lineamientos_index",
    "download_scjn_lineamientos_corpus",
    "download_scjn_lineamientos_assets",
    "local_lineamientos_ids",
    "AssetNotCached",
    "SinTextoEnSCJN",
]

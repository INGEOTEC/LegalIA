"""Client for the SCJN's SCOW JSON API (`scjn.api`), the federal-law
catalogue's own algebra (`scjn.catalog`), per-instrument crawl state
(`scjn.state`), and the provenance header a crawl writes to every snapshot
(`scjn.header`).

The SCJN is not an official source of legal text — the Diario Oficial de la
Federación remains that; this package's own crawl is a convenience corpus
its consolidated view makes possible, not a replacement for it.

This is Fase 1 of a larger split (issue #206/#207): the `scjn-leyes`
release's own readers (`download_scjn_leyes_corpus`,
`iter_current_federal_laws`, ...) and the `codNota` linking step move into
this package in a later phase, which is also when it gains a public
`__all__`.
"""

__version__ = "0.1.0"

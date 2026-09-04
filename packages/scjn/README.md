# scjn

Client for the SCJN's SCOW JSON API (`https://legislacion.scjn.gob.mx`) and
reader for the `scjn-leyes` GitHub release: a Mexican federal law's
reform-dated snapshots, each tagged with a `fuente: scjn` provenance header.
The SCJN is **not** an official source of legal text — the *Diario Oficial de
la Federación* (DOF), read through [`dofjson`](../dofjson), remains that;
this package's crawl is a convenience corpus SCJN's own consolidated view
makes possible, never a replacement for it.

This package is being extracted out of
[`nota2md`](../nota2md)'s `scjn.py`/`scjn_api.py` (issue #206). This first
phase (issue #207) moves the transport (`scjn.api`), the catalogue's own
algebra (`scjn.catalog`), per-instrument crawl state (`scjn.state`), and the
provenance header's reader (`scjn.header`) — no behaviour change, no
release-format change. The `scjn-leyes` release's own readers
(`download_scjn_leyes_corpus`, `iter_current_federal_laws`, ...) and the
`codNota` linking step still live in `nota2md.scjn` for now; they move here,
with a public `scjn.__all__`, in a later phase of the same epic.

## Development

```bash
pip install -e "packages/scjn[test]"
pytest packages/scjn
```

## License

Apache License 2.0. See [LICENSE](../../LICENSE).

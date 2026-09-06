# scjn

Client for the SCJN's SCOW JSON API (`https://legislacion.scjn.gob.mx`) and
reader for the `scjn-leyes` GitHub release: a Mexican federal law's
reform-dated snapshots, each tagged with a `fuente: scjn` provenance header.
The SCJN is **not** an official source of legal text — the *Diario Oficial de
la Federación* (DOF), read through [`dofjson`](../dofjson), remains that;
this package's crawl is a convenience corpus SCJN's own consolidated view
makes possible, never a replacement for it.

This package was extracted out of a downstream package's own modules (issue
#206). Fase 1 (issue #207) moved the transport (`scjn.api`), the catalogue's
own algebra (`scjn.catalog`), per-instrument crawl state (`scjn.state`), and
the provenance header's reader (`scjn.header`) — no behaviour change, no
release-format change. Fase 3 (issue #209) moved the `scjn-leyes` release's
own readers here too (`scjn.release`: `download_scjn_leyes_corpus`,
`download_scjn_leyes_index`, `download_scjn_leyes_catalog`,
`iter_current_federal_laws`, `markdown_de_snapshot`,
`download_scjn_leyes_assets`, `local_slugs`), now in the package's own public
`scjn.__all__`, disk-first and with their own cache directory
(`scjn.cache`, `$SCJN_CACHE_DIR` — a separate lifecycle from the downstream
package's cache, migrated once automatically from wherever it used to live).
`codNota` linking — matching a snapshot to the DOF `codNota` that produced it
— stays one layer up: a `codNota` is a DOF concept, and this package's
dependency direction is one way (see `tests/test_boundary.py`).

Since issue #215 every law also carries the SCJN's own `materia` (subject
classification), `vigencia` (whether it is still in force — seven values, not
a boolean) and `resumen` (a one-paragraph abstract). They are per law, not
per reform, so they live in each law's own `estado.json` and in the release
index, and `download_scjn_leyes_catalog`/`iter_current_federal_laws` return
them with no extra request — which makes `scjn-leyes` a *classified* corpus.
`scripts/fetch_federal_law_metadata.py` is what fills them in, one SCJN
search per law, matched by `idOrdenamiento` so a wrong document can never be
described as the right one (issue #115, Hallazgo C).

    scjn download [--slug SLUG] [--cache-dir DIR] [--refrescar]

puts the release on disk; every reader in `scjn.release` then reads off it
with no network request at all, raising `scjn.release.AssetNotCached` for
whatever is not there yet.

## Development

```bash
pip install -e "packages/scjn[test]"
pytest packages/scjn
```

## License

Apache License 2.0. See [LICENSE](../../LICENSE).

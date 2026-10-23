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
whatever is not there yet. Resolving a release's parts asks the GitHub API,
where an unauthenticated address gets 60 requests an hour; when `GH_TOKEN`
(or `GITHUB_TOKEN`) is set, those requests carry it as a bearer token, and
without one nothing changes.

## Changelog

- **0.5.1** — a bug fix (issue #291): `articulos_a_markdown` bolds a decimal
  article lead whole. The T-MEC and six other treaties write `Artículo 1.1:`
  (chapter 1, article 1), which used to come out `**Artículo 1.**1:` — so a
  reader of the Markdown saw every `Artículo 1.N` as article 1. It is now
  `**Artículo 1.1**:` (the `:` stays outside the bold). Every other lead
  (`1.-`, `1o.`, `27 Bis`, an ordinal) is byte-identical to 0.5.0; a snapshot
  written before this fix keeps its old shape until it is re-downloaded.
  **Review fix-1 of #291 (a finding made after the fact), still 0.5.1:** the
  lead takes the *whole* article number inside the bold — `**Artículo
  18.-A.-**`, `**Artículo 10 Bis.-**`, a lowercase or ordinal letter, the
  Latin suffixes up to `Novodecies`/`Novendecies`, a letter before `(SIC)` —
  where it used to bold only the digits and leave the rest outside. `md2akn`
  matches a block with the `**` stripped, so this was cosmetic for the parse
  (the ablation of `scripts/embeddings/README.md`, "Measured, repeated
  numbers", gives the writer a share of 0 of the repeated numbers); it makes
  the Markdown itself say what the article number is. The grammar is a copy
  of `md2akn`'s, since `scjn` imports nothing else of this monorepo, and
  every previous lead stays byte-identical, blanks before the separator
  included.
  **Review fix-2 of #291 (found after the fact, still 0.5.1):** fix-1 had
  concluded that the repeated article numbers were the SCJN's own. They were
  not: the API's `Articulos` blocks carry an outline (`referencia`, `numero`,
  `orden`) that the writer threw away, so `md2akn` guessed a structure from
  flattened text. `articulos_a_markdown(..., outline=True)` — what `snapshot`
  now passes — writes one marker line (`<!-- scjn:block orden=… numero=…
  kind=… label="…" -->`, the new `scjn.outline`) before each block that yields
  text; strip the markers (`scjn.outline.strip_markers`, or
  `markdown_de_snapshot(..., outline=False)`) and the text is byte-identical
  to before. `descarga_ordenamiento(..., raw_dir=...)` keeps what the API
  answered, gzipped, so the writer can change without a second crawl
  (`guarda_crudo`/`articulos_de_crudo`). A snapshot written before this keeps
  its old shape until it is re-downloaded; the four corpora were re-crawled
  with `scripts/recrawl_scjn_outline.py` (provenance headers kept verbatim, an
  instrument swapped in only when every snapshot it had came back).

- **0.5.0** — the fourth id-keyed collection, `scjn-tratados` (issue #277):
  every international treaty the SCJN serves (~1,456 instruments, members
  by the SCJN's *ámbito* `TRATADOS INTERNACIONALES`, any category).
  `download_scjn_tratados_index` / `_corpus` / `_assets`,
  `local_tratados_ids` and `iter_current_tratados` are the same thin
  wrappers over the generic core the reglamentos/lineamientos ones are
  (same `AssetNotCached`/`SinTextoEnSCJN` raise order), `scjn.cache.TRATADOS`
  joins `COLECCIONES_POR_ID`, `scjn download --coleccion tratados` fetches
  it, and `scjn.release.construye_indice_global_tratados` is its index
  builder. The release is a numbered series of tags (`scjn-tratados`,
  `scjn-tratados-2`, ...). `scjn.discovery` gains an `ambito` keyword
  (`discover`/`discover_by_category`, default `"FEDERAL"`, existing calls
  unchanged), a by-ámbito mode (`categoria=""`: no reform-category rescue
  and no coverage audit) and `report_outside_ambito`.

- **0.4.0** — `articulos_a_markdown` recovers table rows from a
  blank-line-separated block of an article's `contenido` that carries a tab
  (issue #253): the SCJN's plain text uses a run of tabs as its only column
  separator, and a wrapped multi-line cell used to come back as one
  Markdown paragraph per source line instead of one per row. A block with
  no tab is unaffected, byte for byte. Measured on the cached `scjn-leyes`
  release: 35 of 315 laws, 567 of 3,707 snapshots — a snapshot already on
  disk only picks up the fix via `scripts/fetch_scjn_legislacion.py
  --reintenta <slug>`, never automatically.

- **0.3.0** — `iter_current_reglamentos` / `iter_current_lineamientos`
  (issue #227, Fase 2): `iter_current_federal_laws`' id-keyed siblings, the
  *current* text of every instrument of either collection, one tarball open
  at a time. No `codNota` and no `abrev` (neither collection is DOF-linked),
  the newest snapshot chosen from the file names since an id-keyed tarball
  ships no `indice.json`, and an instrument the index lists with no
  consolidated text at all is skipped rather than raising `SinTextoEnSCJN`.

- **0.2.0** — the package's first release: the SCOW transport (`scjn.api`),
  the catalogue algebra, the crawl state, the provenance header and the
  disk-first readers of the `scjn-leyes`/`scjn-reglamentos`/
  `scjn-lineamientos` releases (issues #206-#212, #220, #222).

## Development

```bash
pip install -e "packages/scjn[test]"
pytest packages/scjn
```

## License

Apache License 2.0. See [LICENSE](../../LICENSE).

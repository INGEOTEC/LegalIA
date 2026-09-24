# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

LegalIA is a monorepo of independently-versioned Python packages, developed by
INGEOTEC, for analyzing legal texts in the Mexican context. The first target
is the *Diario Oficial de la Federación* (DOF), Mexico's official gazette:
over 1.2 million legal provisions published since 1917.

## Packages (`packages/<name>/`)

Each package has its own `pyproject.toml`, dynamic version (`<pkg>.__version__`),
tests, and PyPI release — installed independently. Read order matters: they
build on each other in this sequence.

- **`dofjson`** — client for SIDOF's undocumented JSON open-data service
  (`sidof.segob.gob.mx`). Everything is reachable off the `dofjson` package
  itself (`dofjson.get_nota`, `dofjson.get_notas`, ...) — never import
  `dofjson.sidof`/`dofjson.dofweb` directly from another package. SIDOF
  silently loses whole days (reports them as an empty, valid day, same as a
  Sunday); `dofjson.dofweb` recovers those from `www.dof.gob.mx` and callers
  never need to know which source actually answered (see `fuente` field).
  Also streams the compact `codNota`+`titulo`+`fecha` record of every legal
  provision ever published (`legal_provisions_titles`), off the on-disk cache
  of the `notas-archivo` GitHub release — it writes no dataset of its own
  (issue #166).
- **`scjn`** — client for the SCJN's **SCOW JSON API** (`scjn.api`, the
  backend of `legislacion.scjn.gob.mx/consulta/buscador`; replaced a legacy
  WebForms crawler in issue #172, retired in #179) and the disk-first reader
  for the `scjn-leyes` GitHub release it feeds (`scjn.release`): a federal
  law's reform-dated snapshots, one tarball per law. Extracted out of
  `nota2md`'s own modules (issue #206, done — see its own section below);
  depends on nothing else in this monorepo, and nothing here imports back
  from `nota2md`/`dofjson` (`packages/scjn/tests/test_boundary.py`'s own grep
  enforces it). Two sibling id-keyed releases, `scjn-reglamentos` (issue
  #220) and `scjn-lineamientos` (issue #222), share `scjn.release`'s own
  generic core since #222's Fase 0 — see their own sections below for
  `download_scjn_reglamentos_*`/`download_scjn_lineamientos_*` and
  `SinTextoEnSCJN`.
  Eight entry points for `scjn-leyes` itself, re-exported off the package: `download_scjn_leyes_index`/
  `download_scjn_leyes_corpus`/`markdown_de_snapshot` (the release's readers,
  by reverse index / whole law / one snapshot), `download_scjn_leyes_catalog`
  (the federal-law catalogue — `abrev`+`nombre` off the index and
  `actualizado` off each law's own `estado.json`, the seed that used to be
  scraped from the Cámara de Diputados, read back rather than rebuilt; issue
  #185), `iter_current_federal_laws` (a lazy iterator over the *current* text
  of every federal law — one snapshot per law, the newest by
  `fecha_publicacion`, without decoding the rest of that law's history; issue
  #191), `local_slugs`/`download_scjn_leyes_assets`/`AssetNotCached` (the
  disk-first cache: every reader raises `AssetNotCached` rather than
  downloading — only `download_scjn_leyes_assets`, or the `scjn download`
  CLI, talks to the network; issue #209). Its own cache directory
  (`scjn.cache.CACHE_DIR`, `$SCJN_CACHE_DIR`) is separate from `nota2md`'s,
  with a one-time migration out of the pre-#209 `nota2md` cache
  (`scjn.cache.migrate_legacy_assets`, called from `nota2md download`).
  Since issue #215 every law also carries the SCJN's own `materia`,
  `vigencia` and `resumen` — per law, never per reform, so they live in that
  law's `estado.json` and in the release index, and **not** in a snapshot's
  provenance header (`vigencia` is about the law today, a snapshot about one
  reform in the past); `download_scjn_leyes_catalog` and
  `iter_current_federal_laws` return them with no extra request, which is
  what makes `scjn-leyes` a classified corpus.
  `scripts/fetch_federal_law_metadata.py` fills them in from the cached
  release — one SCJN search per law, resolved by `idOrdenamiento` and never
  by rank (issue #115, Hallazgo C) — and patches the published
  `indice-global.json.gz` rather than rebuilding it from local scratch. The
  API is public but has no stability contract, and the SCJN is still not an
  official source of legal text: `fuente: scjn` means what it always did, the
  DOF/SIDOF remains the official source.
- **`nota2md`** — nine entry points, all re-exported off the package:
  `legal_provisions` (one note → Markdown, `legal_provisions(codNota)` with no
  other argument writing into `nota2md.cache.CACHE_DIR` and returning the
  `Path` (issue #165); **by default the SCJN's
  consolidated text of the whole law at that reform** when the `scjn-leyes`
  release covers the `codNota`, else the DOF's own HTML/image/PDF source —
  `source="dof"` forces the original source; issue #117),
  `reconstruct_legal_provisions` (a law's current text, replayed from its own
  reform decrees),
  `fetch_daily_legal_provisions` (a whole day's legal provisions as one flat
  list, each naming its edition — re-exported from `dofjson.api`, which is
  where it lives since issue #180 collapsed it with
  `legal_provisions_of_day`),
  `get_document` (a `get_nota` record whose `cadenaContenido` is Markdown —
  the one note→Markdown step, issue #170),
  `legal_provisions_titles` (re-exported from `dofjson.titulos`),
  `download_scjn_leyes_corpus`/`download_scjn_leyes_index`/
  `download_scjn_leyes_catalog`/`iter_current_federal_laws` (the `scjn`
  package's own readers, re-exported here unchanged for a caller — see
  `scjn` above). Its own CLI's `download all` puts all four of the project's
  GitHub releases on disk since issue #225 — `federal-laws`/
  `federal-regulations`/`federal-guidelines` (the three SCJN releases, into
  `scjn`'s own cache) plus `gazette-metadata` (`notas-archivo`, into
  `dofjson`'s) — though `nota2md` still reads nothing from
  `scjn-reglamentos`/`scjn-lineamientos` itself: `download` only widens as a
  convenience downloader, it is not a new Python-level dependency on either
  corpus. Depends on `scjn` for all of the above; `nota2md.linking` is
  the one SCJN-adjacent concern that stays here instead — matching a
  snapshot `scjn` reads back to the DOF `codNota` that produced it, and the
  reverse (a `codNota` to the snapshot it produced, for `legal_provisions`'
  own SCJN path) — because a `codNota` is a DOF concept, so this seam needs
  both sides and cannot live one-way inside `scjn` (issue #206 section 5,
  #208). `legal_provisions`' own derived output (a snapshot's extracted text,
  a DOF note's Markdown) is cached on disk under `nota2md.cache.CACHE_DIR` —
  `nota2md`'s own directory, deliberately not `dofjson`'s or `scjn`'s.
- **`document2md`** — *not a package of this monorepo any more.* It OCRs a PDF
  or a set of scanned images to Markdown via `mineru`, and lives in
  https://github.com/INGEOTEC/document2md since issues #233/#234 (see its own
  section below); here it is only `nota2md`'s optional `ocr` extra
  (`document2md>=0.3.0`), imported lazily, and the read order is otherwise
  unchanged. Only needed as `nota2md`'s OCR fallback for legal provisions
  predating the HTML era (pre-1999ish) — a modern note only needs `dofjson` +
  `nota2md`.
- **`md2akn`** — segments a Mexican federal law's Markdown (what `nota2md`
  produces) into a navigable hierarchy — articles inside their chapter,
  fracciones inside their article — labelled with Akoma Ntoso's *vocabulary*
  (`akn_type`, `eId`, `refersTo`). It reads Markdown and has no dependency on
  the other packages, so it comes last in the read order. The project emits no
  Akoma Ntoso XML anywhere: only the vocabulary is borrowed, and the earlier
  XML converter that lived in `nota2md` was removed (issue #168).
  `text_units()` (issue #218) is the retrieval-unit view the embedding work
  builds on, under **nine** rules since issue #227 added two: **rule 8**, an
  instrument that never writes `Artículo N` numbers its provisions
  `**PRIMERO.-**` or `1.` / `2.1` instead, decided once per *document*
  (`md2akn.structure.modo_sin_articulos`) and therefore incapable of changing
  a law — proved, not argued: regenerating the 315 laws' `units.parquet` with
  rule 8 on and rule 9 off reproduced the pre-#227 file to the byte, and the
  release sweep asserts the gate is off for every law; and **rule 9**, no unit
  over the cap — one still over it is cut again at paragraph boundaries, never
  mid-sentence, with whatever cannot be cut (a single over-cap paragraph, or a
  chapeau prefix rule 3 repeats) counted by `max_unit_chars`, `coverage()`'s
  sibling invariant. `split_over_cap=False` reproduces the pre-#227 output.

- **`legalvec`** — the disk-first reader for this project's *own* vector
  releases (issue #227's Fase 4, which is #218's Fase 3 widened to three
  corpora): `scjn-leyes-vectors`, `scjn-reglamentos-vectors`,
  `scjn-lineamientos-vectors`, one embedding per distinct text of each
  corpus. Deliberately not part of `scjn` — `scjn` means "client for the
  Court", and these vectors are this project's own, derived on a GPU from a
  specific model revision — and it imports nothing else in this monorepo
  (`packages/legalvec/tests/test_boundary.py` greps for that), which is why
  it can take `pyarrow` and `numpy`. Minimum surface:
  `download_vectors_assets` (the only network, resolving a numbered release
  series the way #223 does), `load_vectors` (an instrument's own file unioned
  with the shared one, deduplicated by `text_sha1` — neither is usable
  alone), `load_units`, `VectorSet`, `AssetNotCached`, `model_slug`, and its
  own `$LEGALVEC_CACHE_DIR`. Since issue #239 it also has a console script,
  `legalvec download` (an argparse front end over `download_vectors_assets`,
  `--collection` defaulting to `all` — ~2.08 GB for the three releases, both
  models — and one `--key` for all three collections, since `units.parquet`
  has one uniform `clave` column) plus the strictly offline `legalvec
  status`; both helpers stay private in `legalvec.cli`, so the public
  surface above is unchanged, and the flags are English
  (`--collection`/`--key`/`--refresh`) where `scjn`'s are Spanish. It is not
  wired into `nota2md download all`: `nota2md` reads nothing from these
  releases, and the boundary test says so.

**Data is never committed to git.** The SCJN corpus of consolidated law
texts, `dofjson`'s notes archive, the vectors derived from either, and
downloaded titles datasets all live only in GitHub releases (`scjn-leyes`,
`notas-archivo`, `scjn-*-vectors`, `atlas-pairs`) or are `.gitignore`d local
scratch directories (`/output/`, `/notas-archivo/`, `scripts/scjn/`,
`scripts/legal_provisions/`, `/emb-run*/`, `/website/pages/atlas/pairs/`).
Read them back via `download_scjn_leyes_corpus`/`download_scjn_leyes_index` /
`legal_provisions_titles` (the latter over the cache `nota2md download
gazette-metadata` populates), never by looking for a file in the repo.

**A law's reform history is the `scjn-leyes` release itself** (issue #187):
each law's own `indice.json`, one entry per reform oldest-first with the
`codNota` that published it, plus `indice-global.json.gz` inverting that by
`codNota`. There is no separate history dataset any more —
`download_legal_provisions_provenance_ids` and the `historial-legislativo`
release's `leyes.tgz` are gone with the Diputados data they carried, with no
shim. **"Reform N" now means the SCJN reform table's chronological order**,
not Diputados' numbering; the two count different things and the old one is
not reproduced or measured against.

## Sources: SCJN + DOF only (issue #184, done)

**This section now describes the tree, not a direction.** All six phases
landed (#185–#190): the catalogue was built from the SCJN and the DOF
(`scripts/extract_scjn_titles.py`, itself retired by issue #210 once its
job moved into each law's own `estado.json`),
`download_legal_provisions_provenance_ids`
and `nota2md.utils` are deleted, the `leyesmx` package and the
four-collection abstraction are gone, and `.github/workflows/reformas.yml`
with them. The decisions below are kept because they are the reasons, and
because a grep for `diputados` in this repo now hits only history: read them
before re-introducing anything they rule out.

The decision: every dependency on the Cámara de Diputados (LeyesBiblio) is
removed, and federal-law functionality rests on the **SCJN** (the SCOW JSON
API, `scjn.api` since issue #206/#207 moved it out of `nota2md`) plus the
**DOF/SIDOF** (`dofjson`) alone. LeyesBiblio
was three HTML page layouts whose markup changes without notice, and it was
scraped for one thing: the initial seed of federal legislation — which laws
exist, their name, their abbreviation.

- **The seed is read, not rebuilt.** It is already published: the `scjn-leyes`
  release's `indice-global.json.gz` carries every law's slug and `nombre`, the
  slug *is* the `abrev` (`scjn.catalog.slug_instrumento`), and each
  `<slug>.tgz` ships an `estado.json` recording the `actualizado` it was
  crawled against. So `download_scjn_leyes_index` answers the question the
  scraper used to. Existing `abrev` values are preserved **verbatim** — an
  `abrev` is the release's slug and asset name, so re-deriving one would be a
  breaking change dressed up as a cleanup.
- **The source hierarchy does not change.** The DOF/SIDOF remains the official
  source of legal text; the SCJN's consolidated texts keep their `fuente: scjn`
  header, meaning exactly what it has always meant.
- **Only the `leyes` collection survives.** Reglamentos, tratados and Normas
  Oficiales Mexicanas are out of scope, so their code is deleted rather than
  migrated — unused machinery still has to be read, tested and reasoned about.
  With one collection left, the four-collection abstraction collapses:
  `nota2md.utils`' `COLECCIONES`/`_ASSETS`/`_INDICES`/`_une_con_historial`, the
  `--coleccion` flags of the SCJN scripts, `empaqueta_historial.py`'s asset
  map, and the collection branches in what is now `scjn.catalog`/`scjn.api`.
  The public
  `download_legal_provisions_provenance_ids` was **deleted outright** in #187 —
  no shim, no deprecation, no name kept for compatibility (changelog note in
  `packages/nota2md/README.md` regardless of whether a version bump follows). A law's reform history then lives in the
  `scjn-leyes` release itself (each law's `indice.json`, plus
  `indice-global.json.gz`); no new dataset and no new asset is created for it.
  Consequently the `historial-legislativo` release lost its `leyes.tgz` asset
  (deleted in #190), while `reglamentos.tgz`, `normas.tgz` and `tratados.tgz`
  stay downloadable, labelled by name in the release notes
  (`.github/historial-legislativo.md`, which *is* the release body) as a
  frozen record nothing in the repo can regenerate.
- **Reform numbering is redefined, loudly.** "Reform N" becomes the SCJN reform
  table's chronological order. It is not an attempt to reproduce Diputados'
  numbering, and it is not measured against it — the old numbering is gone with
  its source.
- **No GitHub Action publishes SCJN-derived data.** That rule predates this
  work and is written down in `scripts/empaqueta_scjn_leyes.py` (issue #115,
  Hallazgo C: the SCJN's own search can return a completely wrong document for
  an instrument, so a human decides what is safe to publish). This epic does
  not get to override it — which is why `.github/workflows/reformas.yml` is
  **deleted rather than repointed**, with no workflow replacing it. The leyes
  rebuild becomes a manual publish, like `scjn-leyes` already is.

Phases, each its own sub-issue, in order:

| Phase | Issue | What it does |
|---|---|---|
| Fase 0 | #185 | Audit the Diputados footprint; read the seed already in `scjn-leyes` |
| Fase 1 | #186 | `catalogo.json` without Diputados: discovery and `actualizado` |
| Fase 2 | #187 | Reform history of leyes from SCJN + DOF |
| Fase 3 | #188 | Replace `texto_vigente`'s ground truth for `reconstruct_legal_provisions()` |
| Fase 4 | #189 | Delete `leyesmx`, the Diputados code, the collection abstraction |
| Fase 5 | #190 | Delete the workflow; freeze the release; update the docs |

## The `scjn` package: extracted from `nota2md` (issue #206, done)

**This section now describes the tree, not a direction.** All six phases
landed (#207–#212): the SCJN's transport, catalogue algebra, crawl state,
provenance header and the `scjn-leyes` release's own readers all live in a
new top-level package, `scjn` — read before `nota2md` in the read order
above. The decisions below are kept as the reasons a later change should
weigh against, not a task list still open.

- **One-way dependency, enforced.** `scjn` imports neither `nota2md` nor
  `dofjson` — a `codNota` is a DOF concept, so anything that needs both
  sides (matching a snapshot to the DOF note that produced it) stays a layer
  up, in `nota2md.linking` (issue #208). `nota2md` depends on `scjn`, never
  the reverse; `packages/scjn/tests/test_boundary.py` greps for
  `nota2md`/`dofjson` inside `scjn`'s own source so the direction cannot
  regress silently.
- **A pure move first, behaviour changes after.** Fase 1 (#207) relocated
  `scjn.api` (the SCOW transport, formerly `nota2md.scjn_api`),
  `scjn.catalog`, `scjn.state` and `scjn.header` (formerly parts of
  `nota2md.scjn`) with no rename of existing Spanish identifiers and no
  behaviour change — `nota2md.scjn` kept re-exporting every moved name until
  Fase 3 deleted it. Fase 2 (#208) then moved `codNota` linking into its own
  leaf module, `nota2md.linking`, breaking an import cycle
  (`nota2md.builder` no longer needs a deferred import to reach it).
- **The release readers became disk-first, with their own cache.** Fase 3
  (#209) moved `scjn.release` here too: every reader
  (`download_scjn_leyes_index`, `download_scjn_leyes_corpus`,
  `download_scjn_leyes_catalog`, `iter_current_federal_laws`,
  `markdown_de_snapshot`, `local_slugs`) now only ever reads
  `scjn.cache.CACHE_DIR` (`$SCJN_CACHE_DIR`) — a directory separate from
  `nota2md`'s, migrated once, automatically, out of the pre-#209 `nota2md`
  cache (`scjn.cache.migrate_legacy_assets`, called from `nota2md download
  federal-laws`/`all`, since `scjn` itself must never know `nota2md`'s cache
  layout). A missing asset raises `AssetNotCached` instead of attempting a
  download; only `download_scjn_leyes_assets` (and the `scjn download` CLI
  built on it) talks to the network. `nota2md download federal-laws`/`all`
  genuinely delegate to that downloader rather than reimplementing it.
- **One record per law.** Fase 4 (#210) retired the separate `catalogo.json`
  seeding the whole pipeline: each law's own `estado.json`
  (`abrev`/`nombre`/`nombre_scjn`/`id_ordenamiento`/`actualizado_scjn`/
  `actualizado_dof`/`actualizado`/`rastreado`/`enlazado`) is now the one
  per-law record, migrated by a one-time, human-run backfill rather than
  guessed at. `scripts/discover_federal_laws.py` replaced
  `extract_scjn_titles.py --discover`: it reports a candidate new law and
  stops, never writing state — a new federal law is a handful a year, and an
  `abrev` is a release asset name, so adding one stays a human's decision.
- **Completeness by row comparison, not just by date.** Fase 5 (#211) added
  `scjn.state.reformas_faltantes`, diffing a law's whole reform table
  against its on-disk snapshots — the only way to see a gap in the middle of
  an otherwise current-looking law (`lfd` had 92 snapshots against 98
  reforms, issue #178). The older date-only comparison
  (`scjn.state.motivo_pendiente` with no `reformas` given) stays as an
  explicit, cheaper, offline fallback — `fetch_scjn_legislacion.py
  --solo-fecha` — not a default for a plan anyone acts on.
  `actualizado_dof`/`actualizado_scjn` are not replaced by either mode: row
  comparison catches a gap in an indexed table, the DOF half still catches a
  reform the SCJN has not indexed at all yet (issue #124's `lfca`).
- **Docs and packaging catch up last.** Fase 6 (#212) gave `scjn` its own
  Read the Docs page (`docs/source/scjn_api.rst`, built to the same standard
  as every other package's) and its first PyPI release
  (`.github/workflows/publish-pypi.yml` already had `scjn-v*`/`scjn` since
  Fase 1) — `nota2md`'s own next release has to go out at the same time or
  after `scjn`'s, never before, or `pip install nota2md` breaks for everyone
  outside this repo.

Phases, each its own sub-issue, in order:

| Phase | Issue | What it does |
|---|---|---|
| Fase 1 | #207 | Create the `scjn` package: transport, catalogue, state, header |
| Fase 2 | #208 | Extract `codNota` linking into `nota2md.linking`; break the import cycle |
| Fase 3 | #209 | Move the release readers to `scjn.release`, disk-first, own cache |
| Fase 4 | #210 | One record per law: `estado.json` absorbs `catalogo.json` |
| Fase 5 | #211 | Completeness by row comparison against the SCJN reform table |
| Fase 6 | #212 | Docs, packaging, first PyPI release, this section |

## The `scjn-reglamentos` corpus (issue #220, done)

**Every federal *reglamento* the SCJN has** — 1087 instruments, keyed by
`idOrdenamiento` — published as a sibling GitHub release alongside
`scjn-leyes`. It reuses the crawl/packaging machinery `scjn-leyes` already
has, narrowed to what this collection actually needs, rather than
parameterising the `leyes` path itself.

- **Inclusion rule**: `categoriaOrdenamiento == "REGLAMENTO"`, or any row of
  the instrument's own reform table has `categoriaReforma == "REGLAMENTO"`.
  The second half is load-bearing — it rescues 6 instruments the SCJN itself
  classifies `ACUERDO (S)`/`ESTATUTO`/`RESULTADOS` (all six titled
  "REGLAMENTO ..." regardless) while correctly rejecting the ~181 other
  non-REGLAMENTO federal hits for the phrase "reglamento" (mostly acuerdos
  generales of the CJF/INE that merely *reglamentan* something).
- **No `abrev`, ever.** The SCJN reissues a reglamento as a brand-new
  `idOrdenamiento` rather than as a reform of the previous one (137 of 1080
  titles repeat, covering 355 distinct instruments), so any title-derived
  key collides — `id_ordenamiento` is the only key the SCJN guarantees
  stable, and it is what the corpus uses everywhere: directory name, asset
  name, `indice-global.json.gz` key. `scjn.catalog.instrumento_key` (issue
  #222's Fase 0; `reglamento_key` is kept as a working alias, see that
  issue's own section below) is that key, not a slug of a title.
- **No `actualizado`, no DOF linking, no `--actualiza` chain.** This corpus
  is out of scope for DOF linking entirely (a future issue adds it, for
  reglamentos and laws alike) — so its `estado.json` has no
  `actualizado`/`actualizado_scjn`/`actualizado_dof`, no `nombre_scjn`
  override, and there is nothing for `fetch_scjn_legislacion.py`'s
  `--actualiza`/`--solo-fecha`/`--dof-only`/`--reintenta`/
  `--sin-refrescar-catalogo` to feed — passing any of them with
  `--coleccion reglamentos` is a `SystemExit`, not a silent no-op. The only
  way to know an instrument needs (re)crawling is a row comparison against
  the SCJN's own reform table (`scjn.state.reformas_faltantes`), which needs
  no date at all.
- **A sibling release, not a parameter — superseded by issue #222's Fase 0.**
  Originally (this issue): `scjn.cache._SCJN_REGLAMENTOS_RELEASE` was a
  second release tag under the same `CACHE_DIR`, and `scjn.release` gained
  new, separate reader functions (`download_scjn_reglamentos_index`,
  `download_scjn_reglamentos_corpus`, `local_reglamentos_ids`,
  `download_scjn_reglamentos_assets`) rather than a `coleccion` parameter on
  the existing `download_scjn_leyes_*` ones — "a third collection would mean
  a third literal branch, not a new dict entry" (this section's own words,
  before #222). That held for exactly one more collection: when
  `scjn-lineamientos` (issue #222) made it a *third* id-keyed collection, a
  third hand-duplicated ~226-line copy of index/corpus/local-ids/assets was
  where duplication stopped paying, so #222's Fase 0 refactored the shared
  shape (keyed by `id_ordenamiento`, no `abrev`, no `actualizado`, no DOF
  link, no `indice.json`) into one private generic core plus a `Coleccion`
  descriptor (`scjn.cache.Coleccion`/`REGLAMENTOS`/`LINEAMIENTOS`) and
  ~4-line public wrappers per collection — see the `scjn-lineamientos`
  section below for the shape this left behind. The four
  `download_scjn_reglamentos_*`/`local_reglamentos_ids` names themselves did
  not change, are still separate functions from their `leyes` counterparts,
  and still ship no `codNota` section or per-instrument `indice.json` — only
  what generated their bodies moved. `scjn.cli`'s `scjn download --coleccion`
  is now dispatched through `scjn.cache.COLECCIONES_POR_ID` instead of a
  hand-written per-flag two-way branch, so a further id-keyed collection
  costs one dict entry, not a new literal branch.
- **Discovery is a listing, seeding is separate from a full sweep's
  freshness.** `scripts/discover_federal_reglamentos.py` pages the SCJN by
  category and applies the reform-category rescue rule; unlike
  `discover_federal_laws.py` it does **not** confirm candidates against the
  DOF (there is no DOF link for this collection) and includes a one-time,
  opt-in, exhaustive coverage audit (`--auditoria-cobertura`, ~7900 reform
  tables, ~65 minutes) for an instrument whose title never says
  "reglamento" at all. It reports and stops, same as `discover_federal_laws.py`;
  `scripts/seed_federal_reglamentos.py` turns a reviewed list into
  `estado.json` files, idempotently, never touching an instrument already
  crawled. `fetch_scjn_legislacion.py --coleccion reglamentos` (plain crawl,
  or `--plan` for the row-comparison plan) and
  `scripts/empaqueta_scjn_coleccion.py --coleccion reglamentos` (packaging)
  round out the pipeline — no automated publish, same as `scjn-leyes` (issue
  #115, Hallazgo C). Since issue #222's Fase 0, the phrase-union paging, the
  reform-category rescue rule and the coverage audit itself live in
  `scjn.discovery` (shared with `scjn-lineamientos`), the seeding function
  is shared too (`scripts/seed_federal_reglamentos.py --coleccion
  {reglamentos,lineamientos}`), and packaging is the shared
  `empaqueta_scjn_coleccion.py` — this script's own name and its
  `--auditoria-cobertura` phrase list are the only `reglamentos`-specific
  parts left.
- **Published as a numbered series of release tags, since issue #223.**
  Publishing the corpus on 2026-09-10 hit two GitHub limits neither this
  issue nor `scjn-leyes` had come close to: a release holds at most 1000
  assets (1082 tarballs + 3 more assets is 1085), and a release body at most
  125 000 characters (`MANIFEST.md`'s 1082-row table is 179 749 bytes). So
  the collection is `scjn-reglamentos`/`scjn-reglamentos-2` today — part 1
  keeps the bare tag, a continuation adds `-<n>` — and `scjn.release`
  resolves the whole series by asking GitHub directly, probing
  `_tag_de_parte(base, 1)`, `(base, 2)`, ... until one 404s
  (`_assets_de_partes`); nothing published or cached records a part count,
  so a repartition can never desync from what GitHub actually serves. The
  partition itself **is** recorded, at packaging time only
  (`empaqueta_scjn_coleccion.py`'s own `partes.json`, local scratch, never
  a release asset): today's split is arbitrary (whichever upload batches
  happened to fail), so recomputing it from a sorted/hashed rule would
  disagree with reality for hundreds of already-published assets. The same
  multi-part resolution covers `scjn-leyes` too (one part today), so the
  1000-asset wall stops being reglamentos-specific folklore.
- **An instrument with no consolidated text ships no tarball (issue #222's
  decision 6, applied retroactively).** 5 of the 1087 instruments have a
  reform table where every row carries `tieneArticulos=false` — the SCJN
  classifies and crawled them (their own `estado.json` has `rastreado`), but
  serves no text at all. Before #222 these were indistinguishable from
  "never crawled" and silently dropped from `indice-global.json.gz` (1082
  entries, not 1087). Packaging (`empaqueta_scjn_coleccion.py`) now tells
  the two apart purely from `rastreado`, indexes the 5 with `snapshots: 0`
  and no `asset`, and lists them in `MANIFEST.md` under their own heading —
  the published index grows from 1082 to 1087 entries, the only deliberate
  change #222's Fase 0 makes to this already-published corpus's content.
- **`nota2md download all` fetches it too, since issue #225.** A new
  `nota2md download federal-regulations` subcommand (`--id`, repeatable,
  never `--slug`) delegates to `download_scjn_reglamentos_assets`, and `all`
  now includes it — `nota2md` itself still reads nothing from this corpus.

## The id-keyed collection path, refactored (issue #222's Fase 0, done)

`scjn-reglamentos` (issue #220) was implemented by deliberate duplication —
"a sibling release, not a parameter" — because two collections were a
defensible cost. `scjn-lineamientos` (below) made it a *third*, and a third
~226-line copy of `scjn.release`'s index/corpus/local-ids/assets is where
that duplication stops paying. Fase 0 refactored the shared shape instead,
**behaviour-preserving against `scjn-reglamentos` as already published**
(1087 instruments, 1082 tarballs, live since 2026-09-10) except for one
deliberate change, decision 6 above (index 1082 → 1087).

- **`scjn.cache.Coleccion`** (`dataclass(frozen=True)`, fields `nombre`,
  `tag_base`, `subdirectorio`) describes an id-keyed collection's own
  release-tag series and cache subdirectory; `REGLAMENTOS`/`LINEAMIENTOS`
  are its two instances, and `COLECCIONES_POR_ID` maps a collection's name
  to its descriptor for `scjn.cli`/`fetch_scjn_legislacion.py`/
  `empaqueta_scjn_coleccion.py` to dispatch through. Deliberately **not**
  the four-collection registry issue #189 deleted: it carries no
  `_ASSETS`/`_INDICES`/`_une_con_historial` equivalent, no per-collection
  behaviour flag, and `leyes` (its own `abrev`/`actualizado`/`indice.json`/
  `notas/`/codNota-reverse-index path) is not, and will not be, described by
  it — growing it to cover `leyes` too is the sign this abstraction went too
  far.
- **`scjn.release`** replaced ~226 hand-duplicated lines with one private
  generic core — `_indice_global_por_id`, `_index_de_release`,
  `_corpus_de_release`, `_local_ids_de_release`, `_download_assets_de_release`
  — plus ~4-line public wrappers per collection
  (`download_scjn_reglamentos_index`/`_corpus`/`_assets`,
  `local_reglamentos_ids`, and the symmetric `_lineamientos` names below):
  decision 7 keeps every published name exactly where it was, so nothing on
  PyPI breaks and `@patch("scjn.release._assets_scjn_reglamentos")`-style
  test mocking keeps working (the generic core takes the collection's own
  "list assets" function as an explicit parameter rather than deriving it
  from the descriptor, so Python's late-bound module-attribute lookup still
  lets a test substitute it).
- **`SinTextoEnSCJN`** (decision 10): raised by `download_*_corpus` for an
  id listed in the index with `snapshots: 0` (no asset — decision 6) — a
  sibling of `AssetNotCached`, never that class itself, so
  `except AssetNotCached` keeps meaning exactly "not downloaded yet". The
  raise order is part of the contract, not an implementation detail: a cold
  cache raises `AssetNotCached` for the **index** first (`download_*_corpus`
  must read it before it can tell a text-less id apart from a missing
  tarball), and only once the index says an id has no asset does it raise
  `SinTextoEnSCJN`; an id absent from the index, or one the index lists with
  an asset that is not on disk, still raises `AssetNotCached` for the
  tarball.
- **`scjn.catalog.instrumento_key`** replaces `reglamento_key` as the
  current name (`reglamento_key` stays a working alias — see that
  function's own docstring); still just `str(entrada["id_ordenamiento"])`.
- **`scjn.discovery`** is new library code extracted out of the
  `reglamentos`-only `discover_federal_reglamentos.py` (issue #220):
  `discover_by_category`/`candidates_outside_category`/
  `rescue_by_reform_category` (the phrase-union listing plus the
  reform-category rescue rule) and `coverage_audit` (the opt-in, one-time,
  exhaustive sweep, decision 8) all take a target `categoria` and phrase
  list as parameters instead of hardcoding REGLAMENTO, and are
  unit-testable against a stub API with no network at all
  (`packages/scjn/tests/test_discovery.py`). `discover_federal_reglamentos.py`
  and `discover_federal_lineamientos.py` are now both thin argparse wrappers
  naming their own category/phrases. The coverage audit caches every reform
  table it fetches under `--cache-dir` (default `scripts/scjn/`, gitignored
  scratch, never a release asset) so a sibling collection's own audit reuses
  an already-swept instrument's table and re-applies its own `categoria`
  predicate offline instead of paying for the same ~65-70 minute sweep
  twice.
- **`scripts/seed_federal_reglamentos.py`** and
  **`scripts/empaqueta_scjn_coleccion.py`** are shared by both collections,
  parameterised by `--coleccion`, replacing the `reglamentos`-only
  `empaqueta_scjn_reglamentos.py`.
- **`fetch_scjn_legislacion.py`**'s `rastrea_reglamentos`/`planea_reglamentos`
  became `rastrea_por_id(coleccion, ...)`/`planea_por_id(coleccion, ...)`;
  the `SystemExit` guard over the six `leyes`-only flags now checks
  membership in `scjn.cache.COLECCIONES_POR_ID` instead of naming
  `reglamentos`.

Net effect, per issue #222: a further id-keyed collection costs one
descriptor entry, four wrapper functions, one discovery wrapper script and
its docs — not another ~800 duplicated lines.

## The `scjn-lineamientos` corpus (issue #222, done)

**Every federal *lineamiento* the SCJN has** — 163 instruments, keyed by
`idOrdenamiento` — published as a sibling GitHub release alongside
`scjn-leyes`/`scjn-reglamentos`, on the id-keyed collection path the section
above refactored to make this collection cheap. A very small, very sparse
corpus next to its siblings:

| corpus | instruments | snapshots | snapshots/instrument |
|---|---|---|---|
| `scjn-leyes` | 315 | 3707 | 11.77 |
| `scjn-reglamentos` | 1087 | 2920 | 2.69 |
| `scjn-lineamientos` | 163 | 173 | 1.07 (median 1, max 3) |

- **Inclusion rule**: same shape as `reglamentos` — `categoriaOrdenamiento
  == "LINEAMIENTOS"`, or any row of the instrument's own reform table has
  `categoriaReforma == "LINEAMIENTOS"`. 159 by category, plus 4 rescued
  (3 titled "LINEAMIENTOS ...", one a "MANUAL DE LINEAMIENTOS..." with a
  single LINEAMIENTOS reform row and no text at all). None of the 159 has a
  REGLAMENTO reform row, so the two id-keyed collections never overlap.
- **No `abrev`, no `actualizado`, no DOF linking** — same reasoning as
  `reglamentos` above, milder in degree (3 of 159 titles repeat, covering 6
  records, against 137 of 1080), still non-zero, so `id_ordenamiento`
  (`scjn.catalog.instrumento_key`) is the key regardless.
- **37 of 163 have no consolidated text at all** (36 of the 159 category
  members, plus the "MANUAL DE LINEAMIENTOS..." rescue) — 23% of this
  corpus, against 5 of 1087 for `reglamentos`. Indexed with `snapshots: 0`
  and no `asset` (decision 6, the same rule this issue wrote and applied
  retroactively to `reglamentos` too), never silently dropped.
- **`scripts/discover_federal_lineamientos.py`** is a thin wrapper over
  `scjn.discovery`, naming `categoriaF=LINEAMIENTOS` and its own phrase
  list; `scripts/seed_federal_reglamentos.py --coleccion lineamientos` and
  `fetch_scjn_legislacion.py --coleccion lineamientos` (plain crawl, or
  `--plan`) and `scripts/empaqueta_scjn_coleccion.py --coleccion
  lineamientos` round out the pipeline — the same shared scripts
  `reglamentos` uses, not new copies.
- **Public reader names stay per-collection** (decision 7, symmetric with
  `reglamentos`): `download_scjn_lineamientos_index`/`_corpus`/`_assets`,
  `local_lineamientos_ids`, re-exported off `scjn` alongside their
  `reglamentos` counterparts.
- **No DOF linking, no automated publish** — unchanged from `reglamentos`
  and issue #115, Hallazgo C: a human decides what is safe to publish, via
  the `PUBLICAR.md` `empaqueta_scjn_coleccion.py` generates.
- **The one-time coverage audit (decision 8) was written but not run in
  this pass.** `scjn.discovery.coverage_audit` is fully implemented and
  tested, and `discover_federal_lineamientos.py --auditoria-cobertura` is
  ready to run (~65-70 minutes, ~8800 requests, sharing its cache with
  `discover_federal_reglamentos.py --auditoria-cobertura` under
  `scripts/scjn/`) — it was deliberately not executed as part of landing
  this issue, given its cost; a human runs it later, once, whenever the
  extra confidence is worth ~70 minutes against the live SCJN.
- **`nota2md download all` fetches it too, since issue #225.** A new
  `nota2md download federal-guidelines` subcommand (`--id`, repeatable,
  never `--slug`) delegates to `download_scjn_lineamientos_assets`, and
  `all` now includes it — `nota2md` itself still reads nothing from this
  corpus.

## A vector for every text of all three corpora (issue #227, done)

**This section describes the tree, not a direction.** All four phases landed
in one branch: `md2akn`'s rules 8 and 9 (Fase 1), `scjn`'s
`iter_current_reglamentos`/`iter_current_lineamientos` (Fase 2),
`scripts/embeddings/`'s `--coleccion` and the six model × corpus runs on
`cemieredes` (Fase 3), and the `legalvec` package plus the three releases'
upload plans (Fase 4). Publishing stayed a human step, as issue #115's
Hallazgo C requires — a person ran the `PUBLICAR.md`
`scripts/embeddings/package_vectors.py` generates, and the three releases
have been live since 2026-09-13 (which is what lets issue #239's CLI and the
docs page's own download example fetch a real, tiny slice of one). No
workflow publishes them, and `legalvec` has no PyPI release either.

- **The unit contract of #218 was not renegotiated**, only extended: cap
  2,000, the article as the unit, the chapeau-prefixed split, the `unit_type`
  vocabulary, `normalize()`, `text_sha1` and `bare`/`contextual` are
  unchanged, and rules 8 and 9 were *added* to the same numbered list and
  recorded in each corpus' own `corpus-manifest.json`
  (`split_over_cap`, `md2akn_version`, `instruments_by_numbering`,
  `units_over_cap`/`units_over_cap_unsplittable`).
- **Rule 8 is gated on the document, never on the pattern.** `ARTICULO_ORDINAL`
  and the new `NUMERAL_DECIMAL` are read as article openers only where
  `modo_sin_articulos` finds no `ARTICULO` match outside the transitorios.
  Measured: rule 8 fires for 0 of 315 laws, 7 of 1,082 reglamentos and 83 of
  126 lineamientos.
- **`clave`, not `slug`, is what names a published file.** `units.parquet`
  gained `coleccion` and `clave` (a law's slug, an `id_ordenamiento`
  otherwise — #220's key, unchanged), and `merge_shards.py` writes
  `vectors-<clave>-<model>-<K>.parquet`, so a reader never branches.
  `slug`/`codNota` stay null for the id-keyed collections: there is no DOF
  link for them and inventing one was out of scope.
- **Dedup is inside a collection, never across them** (0.9 % saved, against a
  shared file three independently republished corpora would all have to be
  published against), one work directory per collection (`emb-run-<coleccion>/`,
  gitignored), and **both models stay live** — #217's proxy evaluation has
  not been run, so nothing has earned the right to drop the 4B, and the two
  never share a file per instrument.
- **Three releases, not one**, mirroring the three corpus releases;
  `scjn-reglamentos-vectors` needs the numbered series #223 built (2,171
  assets against GitHub's 1,000-asset cap), and `legalvec` resolves a series
  by probing GitHub until a part 404s, recording a part count nowhere.
- The run itself, on `cemieredes`' three A100s: 12 + 30 + 1 shards per model,
  0 failed, weights downloaded and deleted per model. 381,349 distinct texts
  across the three corpora, ~2.0 GB of vectors.
- **Someone finally looked at them, in issue #241.** Four more scripts under
  `scripts/embeddings/` — `prepare_umap_input.py`, `project_umap.py`,
  `submit_umap.py`/`submit_umap.sh`, `build_umap_html.py`, plus a root
  `[dependency-groups] viz` (`umap-learn`, `pynndescent`, `altair`,
  `pandas`, `vl-convert-python`, deliberately not in `dev`) — fit UMAP on
  **all** 381,349 of the
  0.6B model's vectors, four `n_neighbors` configurations (16/32/64/128, the
  shared `project_umap.DEFAULT_N_NEIGHBORS`; the earlier sweeps' 4/8 and
  15/50/200 stay on disk and come back with `build_umap_html.py
  --projections all`), one
  exclusive CPU node each on a *different* Slurm cluster (`geoint`: three
  ~60-core, ~245 GB nodes, no GPU, shared `/home`, so the jobs run this
  repository's own `.venv`), and turn the result into one standalone
  Vega-Lite HTML explorer — `scripts/embeddings/build_umap_html.py` is the
  file that generates the page, and since the second pass the page says so
  itself, in a footer naming the script, the commit, the date, the work
  directory and the command line (the same record the `.vl.json` carries in
  `usermeta.provenance`). **Exactly one instrument is highlighted at a time**
  since the third pass — the last click wins, wired by clearing each
  selection from the other view's marks
  (`clear="dblclick, @centroids_1_marks:click"` and its mirror) and checked
  by compiling the spec to Vega with `vl_convert`, which fails the build
  rather than shipping a page whose two selections stay on at once; the
  nearest-neighbour rings are off by default (`--neighbors 0`), off rather
  than deleted, since the kNN table and `project_umap.py --knn` are
  untouched. No sample, no fallback and no retry: a
  configuration that dies is reported and the HTML is built from what
  finished. Nothing derived is committed (`emb-run-umap/`, `output/`),
  nothing in `packages/` changed, and no `legalvec` API was added — the
  measured per-configuration table, the cluster's own facts, the mark legend
  and why `nearest` is off (it makes Vega-Lite insert a Voronoi layer that
  turns every overview tooltip into `undefined`) live in
  `scripts/embeddings/README.md`.
- **And then at the instruments they belong to, in issue #242.** Two more
  scripts in the same directory — `instrument_matrix.py` (one Slurm job,
  `--submit`/`--wait`/`--report`/`--dry-run` in `submit_umap.py`'s style,
  whose `queued_jobs` it imports) and `build_instrument_umap_html.py` —
  answer, for **every unit row of every instrument**, "which *other*
  instrument owns the text nearest to this one?", and weigh the answers into
  a directed 1,523 × 1,523 matrix (`emb-run-umap/instrument-matrix/`, still
  gitignored). The rules are the issue's, not defaults: all six unit types;
  every tied winner counts (1e-6 on float32 cosine, because identical texts
  across collections tie exactly); **a unit row distributes a total weight of
  1, `1/m` to each of the `m` instruments owning a winner**, so `A.sum()` is
  the unit-row count (408,804) and every row sums to that instrument's own —
  the second pass' rule, after the first pass' +1-to-each made one boilerplate
  row ("Se deroga.", owned by up to **847** instruments) credit hundreds of
  cells at once and `A` count article–instrument incidences rather than
  articles; per unit row, so a transitorio repeated *m* times counts *m*
  times; and only columns owned *exclusively* by the source are masked — a
  text it shares with another instrument is that unit's strongest foreign
  neighbour, not something to hide. Exact cosine by blocked matmul, never
  `neighbors.parquet`, whose k=15 cannot see past a 3,600-article code's own
  articles. The rows are then L2-normalised and embedded (`n_neighbors`
  4/8/16/32, `random_state=0` — seconds at this size, so a reproducible page
  is worth the single-threaded fit) into
  `output/umap-instruments-qwen3-0.6b.html`, where a click rings an
  instrument and the five it points at hardest — the same five the tooltip
  names, one `target N` row each, weight first. `build_umap_html`'s join and
  footer helpers are **imported**, never copied, so the two pages cannot
  disagree about which vector row a unit got.
- **The website's Atlas reads one committed file, since issue #244.**
  `scripts/embeddings/export_atlas_data.py` turns #242's outputs into
  `website/pages/atlas/atlas.json` (~0.5 MB): `meta`, one short-keyed entry
  per instrument (`c`/`k`/`n`, `p` = its **provisions** — the export and the
  page say *provisions*, never *units*, which a general reader does not
  understand — `in`, and `out`/`inc` as `[id, weight]` pairs from
  `build_instrument_umap_html.strongest_targets` over the row and over the
  column), and the four projections as `[x, y]` pairs. It is a pure read —
  no refit, no `--force`, `matrix.npy`/`umap.parquet` untouched — and it is
  the one derived file committed for the page, the same category as
  `website/pages/data/scjn-leyes-summary.json` (a sub-MB summary the site
  cannot be built without, not a corpus or a vector set). Regenerating it is
  this exporter run by a human, never a workflow (issue #115, Hallazgo C);
  `meta.commit` is provenance for the file and is never displayed.

## The Atlas page (issues #244, #245)

The website's **Atlas** (`website/pages/atlas.qmd`, navbar entry *Atlas*
right after *Federal Laws*, titled *An Atlas of Mexican Federal Law: Laws,
Regulations and Guidelines*) is #242's instrument map made public. Two issues,
split on purpose: #244 exports the data (`website/pages/atlas/atlas.json`,
see the #242 bullet above), #245 draws it.

- **A hand-written D3 v7 page, not a notebook.** `atlas.qmd` has no code
  cells — only the `<!-- atlas:app -->` block mounting
  `website/pages/atlas/atlas.js`/`atlas.css` (D3 from jsdelivr, no build
  step, no other library) and the explanatory prose. The site's notebooks are
  frozen results to reproduce; this is a tool for people to use, and the
  publish runner has Quarto only, so what is committed is exactly what the
  browser runs. It needs no freeze entry; `_quarto.yml`'s
  `project.resources: ["pages/atlas/**"]` is what copies the JSON, JS and CSS
  into `_site`, and every path is relative (`atlas/atlas.json`) so the page
  works from `_site/pages/atlas.html` and a local `http.server` alike.
- **What it fixes about the #242 page**: circle *area* is proportional to
  provisions (square-root radius, 2.5 px floor — the old log scale made
  almost every point look the same size); a detail panel lists all five
  closest instruments and the five that point here (vega-tooltip had clipped
  them to three), each name selecting that instrument; numbered lines join a
  selection to its five targets; a search box folds accents and case; the
  `n_neighbors` radio is a labelled *Neighbourhood size* segmented control
  with a caption; zoom keeps radii in screen pixels. The interface is
  English and says **provisions**, never *units*, *tooltip* or
  *n_neighbors*; instrument names stay as the corpus spells them. No
  provenance footer, script name or commit on the page.
- **The site's palette, not Vega's**: laws `#2a78d6`, regulations
  `#008300`, guidelines `#e87ba4` — the site's own rule that a colour means
  the same thing on every page.
- **Verified in a real browser.** `scripts/embeddings/tests/test_atlas_page.py`
  drives headless Chromium through Playwright (in the root `viz` dependency
  group; `uv run --group viz playwright install chromium` once per machine)
  over a harness page built from the qmd's own app block, and writes a
  screenshot to `output/atlas-chapingo.png`; without the browser those tests
  skip with the reason and the static checks still run. No Home teaser card:
  `website/index.ipynb` is frozen, and editing it is a re-freeze pass of its
  own.
- **The harness is not the site (issue #247).** As first published, the map
  showed no point: the detail panel was an `<aside>`, and Quarto's page CSS
  sends every `aside:not(.footnotes):not(.sidebar)` to the page margin with
  `grid-column: body-end/page-end !important` (and greys and shrinks its
  text). Inside `.atlas-main` those named lines do not exist, so the grid grew
  implicit tracks and the map's `1fr` track got 0 px. The harness never loads
  the site's stylesheet, so it could not see this. The panel is now a
  `<div role="complementary">`, `atlas.css` resets `grid-column` on every
  child of `.atlas-main` (and Quarto's h2 rule / h3 font on the panel's
  headings), and the `test_rendered_*` tests render `pages/atlas.qmd` with a
  local Quarto into the gitignored `website/_site/` and drive that page. They
  skip only when no Quarto is found — on `PATH`, else the newest
  `~/.local/opt/quarto-*/bin/quarto` (1.9.38, CI's version, lives there on
  the development machine, outside `PATH`).
- **A weight explains itself (issues #249, #250).** Each *Closest
  instruments* weight is a button that opens a native `<dialog>`: the
  provisions behind the number, each beside the closest text the target has,
  both in full, with similarity and its `1` or `1/m`, 50 rows at a time.
  *Points here* stays plain. The data is one `pairs/<i>-<j>.json` per pair
  (7,604, ~356 MB), written by `scripts/embeddings/export_atlas_pairs.py`
  (#249) and never committed: a human publishes it as the `atlas-pairs`
  release (`PUBLICAR.md`, issue #115, Hallazgo C); `website.yml` downloads,
  verifies and unpacks that asset into `website/pages/atlas/pairs/` before
  publishing, and **fails if it is missing** — so the release must exist
  before a change reaches `master`. Locally, `export_atlas_pairs.py --install
  website/pages/atlas/pairs` fills the same gitignored directory. The page
  fetches a file on the first click only, and shows a "not available" or
  "different version of the map" message (checked by both instruments' `k`)
  rather than failing.

## Tables in SCJN snapshots (issue #253, done)

The SCOW API hands `scjn.api.articulos_of_reforma` each article's `contenido`
as **plain text**, no HTML. A table survives in it as three signals: a blank
line (`\r\n\r\n`) ends a row, a bare newline (`\r\n`) is a wrapped line
inside a cell, and a run of tabs separates columns. `articulos_a_markdown`
used to split on **every** newline alike, so a wrapped multi-line cell came
back as several separate Markdown paragraphs instead of the one table row it
always was.

- **Only a blank-line-separated block that carries a tab changes.** Every
  other block converts exactly as before, byte for byte — the existing
  tests in `packages/scjn/tests/test_api.py` pass unmodified, and the
  module's long-standing promise of reproducing the retired `.docx` path
  still holds wherever there is no tab.
- **One recovered row is one Markdown paragraph**, `"| cell | cell |"`,
  blank-line separated from the next — no GFM header separator, no header
  inference, no column padding. Chosen over a GFM table on 2026-09-23
  because `md2akn` reads consecutive lines as one block and has no rule to
  cut a table by rows: a GFM table would turn `ligie-2022`'s tariff (393,640
  tab lines in one snapshot) into one giant unit, and a `|---|` line would
  itself become a noise unit. A GFM shape plus an `md2akn` table rule can be
  a later issue.
- **A wrapped multi-line header is rebuilt by merging, cell by cell.** A
  tabbed line whose own first cell is empty continues the row already being
  built rather than starting a new one — this is how a header spread over
  several tabbed lines (`Cobertura` / `Cuota por cada kilohertz` /
  `concesionado o` / `permisionado 1MHz=1000 KHz`) comes back as the single
  row it always was. Headers are best-effort this way; data rows are what
  the fix is actually for.
- **An `Artículo N`/ordinal lead on a tabbed line is never read as a row.**
  2 of 23,592 measured tab lines open with `**ARTICULO N.-**`; turning one
  into a row would drop that article from `md2akn`'s own tree, which is
  worse than a fee value left inline as prose.
- **A recovered row bypasses `_formatea_parrafo`.** An all-caps row would
  otherwise be bolded whole, and a cell led by `a).-` would be read as a
  list marker — both wrong for a table row. A reform annotation
  (`(REFORMADO, D.O.F. ...)`) inside a tabbed block keeps its usual bold
  treatment, since that is the shape `md2akn.patterns.ANOTACION` already
  recognises.
- **Measured on the cached `scjn-leyes` release (2026-09-23): 35 of 315
  laws, 567 of 3,707 snapshots** carry at least one tab-bearing block —
  `ligie-2022`/`lfd`/`lfisan` are the three heaviest.
  `scjn-reglamentos`/`scjn-lineamientos` were not measured (their tarballs
  were not in the local cache that day), but both are written by the same
  `scjn.api.snapshot` and inherit the fix.
- **Nothing rewrites an already-published snapshot on its own.** The raw
  `contenido` is not cached anywhere, so a snapshot written before this fix
  keeps its old shape until it is re-downloaded — see "Re-downloading
  table-bearing SCJN instruments (issue #255, review fix)" below for how
  (not `--reintenta`, which deletes `estado.json`/`indice.json` and
  re-searches without the recorded `id_ordenamiento` — issue #115's
  wrong-document path), then
  `scripts/empaqueta_scjn_leyes.py`/`empaqueta_scjn_coleccion.py` (whose
  `MANIFEST.md` lists exactly the rewritten instruments), then a human
  republish (issue #115, Hallazgo C).
  `nota2md`'s own derived cache
  (`<CACHE_DIR>/scjn-leyes/md/<slug>-<archivo>.md`) is keyed by file name and
  reused when present, so it goes stale for a rewritten law until deleted or
  `refrescar=True` is passed.
- **`md2akn` needed no change.** A row starting with `|` matches none of
  `clasifica`'s patterns (falls through to an ordinary `content` leaf) and
  `MARCADOR_LISTA` requires its marker at the head of the block, which `|`
  is not — so `a).-` at the head of a row never opens an inciso. A test
  pins this reading (`packages/md2akn/tests/test_units.py`) so a later
  change to either package cannot regress it silently.
- **`scjn` went to 0.4.0** — a behaviour change in every future snapshot of
  the 35 laws above; `nota2md`'s floor `scjn>=0.2.0` is unaffected.

## Re-downloading table-bearing SCJN instruments (issue #255, review fix)

Issue #255's first attempt (a since-deleted script) re-converted a snapshot
**in place**, body only, and only wrote it back when a word-order check passed (`words(old) == words(new)`, ignoring
`*`/`|`/`\`/whitespace). That check was too strict: when a table's column
header is wrapped over several source lines, #253's merge rule rebuilds it
as one row, and a header's own words can come out in a different order than
the old line-by-line dump had them (`Dia Tipo de Vialidad Noche` vs `Tipo de
Vialidad Dia Noche` — same words, same table, different order because the
old dump was never in reading order to begin with). That rejected roughly
half of every affected snapshot in `leyes`/`reglamentos` as a false
`mismatch`, leaving those instruments inconsistently converted. A per-word
order check cannot tell a genuine upstream text edit apart from a faithful
but differently-ordered reassembly of the same table — so the repository
owner replaced the whole approach rather than loosening the check.

**The procedure now, authoritative since this fix:**

1. For every instrument of all three collections, ask the SCJN for its
   **latest version only** (`scjn.api.ScjnApi.reformas_of_ordenamiento`, the
   newest reform with `tieneArticulos` true, then
   `articulos_of_reforma` for that one reform) — `scripts/find_scjn_table_instruments.py`.
2. **Has a table** iff any article's raw `contenido` contains a tab — #253's
   own signal, checked on the fresh API answer, never on what is on disk.
3. An instrument whose *latest* version has no table is left exactly as
   published, even if an older snapshot of it still has one. Confirmed with
   the repository owner explicitly: a `mismatch` from the abandoned approach
   is not itself a reason to touch an instrument now.
4. A selected instrument (latest version has a table) gets **all** of its
   snapshots re-downloaded from scratch — `fetch_scjn_legislacion.py
   --instrumento <key>` after deleting only its `*.md` files (never
   `estado.json`/`indice.json`/`notas/`) — which also picks up any reform
   published since the last crawl, on purpose: a half-updated instrument is
   worse than a fully current one.
5. `leyes` reforms newly picked up this way are linked
   (`enlaza_scjn_legislacion.py`), same as any other new reform; the
   id-keyed collections have no DOF linking at all, unchanged.
6. Packaging (`empaqueta_scjn_leyes.py`/`empaqueta_scjn_coleccion.py
   --instrumento`) and publishing stay exactly as before — manual, issue
   #115 Hallazgo C — over only the selected instruments.

There is no word-level verification step any more: a full re-download from
the SCJN's own current answer is trusted the same way any other crawl is,
and a genuine upstream edit (if any) is exactly what a fresh crawl is
supposed to pick up.

- **Hand-off**: `scripts/scjn/table-instruments-PUBLISH.md` (gitignored,
  regenerated by re-running the pipeline) — probed/selected/re-downloaded/
  failed counts per collection, the codNota-change report for `leyes`, and
  the exact `gh release upload`/`gh release edit` commands. Nothing is
  published automatically.
- **Out of scope, deliberately**: derived data (`md2akn` `units.parquet`,
  the three `*-vectors` releases, the Atlas matrix/`atlas.json`/
  `atlas-pairs`) is untouched and stays stale until a future issue plans
  regenerating it — nothing here does that.

## `dof2md` is now `document2md` (issue #228, done)

*Both packages left this repository in issues #233/#234 and now live in
https://github.com/INGEOTEC/document2md — the paths below (`packages/dof2md/`,
`docs/source/document2md_api.rst`, `publish-pypi.yml`'s tag rules) no longer
exist here. The section is kept as frozen history: it records why the rename
happened and the rules the tombstone still lives by in its new home.*

The package that OCRs a PDF or a set of page images to Markdown was renamed:
distribution, directory, import package, console script, release tag, Read
the Docs page (`docs/source/document2md_api.rst`) and website page
(`website/pages/document2md.ipynb`) all say `document2md` now. Nothing else
changed — same `BatchConverter`, same CLI flags (including the Spanish
`--titulo`/`--titulo-siguiente`, deliberately untouched), same output, same
version line (`0.3.0`, continued rather than reset).

- **A package is named after what it does, not after the corpus that first
  needed it.** That is the criterion this rename settles, and the one the
  project's later package renames follow. `dof2md` had had no notion of the
  DOF since issue #134 moved the edition download out into
  `dofjson.download_edicion_pdf`; nothing in it knows what a note or a legal
  provision is. The name also has to survive the backend: `mineru` is an
  implementation detail and a cloud OCR/layout service is a plausible second
  one, so `ocr2md` was rejected along with `scan2md` (excludes born-digital
  PDFs) and `pdf2md` (excludes the image path, which is the one `nota2md`
  actually uses for pre-1999 notes).
- **The multi-backend seam is not here.** No `backend=` parameter, no
  registry, no second converter — the *possibility* of one is the reason for
  the name, not part of the rename; it gets its own issue.
- **`packages/dof2md/` still exists, as a tombstone that raises.** It is a
  code-less final release for the old PyPI name: `__version__ = "0.3.0"`,
  then an unconditional `ImportError` naming `document2md` and
  `pip install document2md`. It does **not** depend on `document2md` — a
  working shim was rejected, because it would keep the old package alive as
  real, maintained code, and would let `pip install dof2md` quietly keep
  working through a transitive import. Silence was rejected too: PyPI would
  otherwise keep serving 0.2.0 forever with nothing saying it was renamed.
- **`__version__` must stay above the `raise`, as a plain literal.** Both
  setuptools' `attr:` resolution and `scripts/check_package_versions.py`'s
  `local_version()` read it statically (AST, no import) — that is what makes
  a module which raises on import buildable and publishable at all.
  `packages/dof2md/tests/test_tombstone.py` guards the ordering, alongside
  the test that the import raises.
- **The version gate learned about tombstones.**
  `[tool.legalia] tombstone = true`, in the package's own `pyproject.toml`,
  exempts it from `check_package_versions.py`'s one-step-ahead rule (issue
  #194): once `dof2md-v0.3.0` is published, local and PyPI agree forever,
  which the gate would otherwise report as a failure on every pull request
  from then on. The marker is data in the package it describes, so the next
  rename costs a key, not a code edit.
- **Publish order:** `document2md` 0.3.0 goes to PyPI **before or at the
  same time as** any `nota2md` release whose `ocr` extra requires it
  (`document2md>=0.3.0`), or `pip install nota2md[ocr]` breaks for everyone
  outside this repo — the same rule this file already states for `scjn` and
  `nota2md`. `dof2md-v0.3.0` (the tombstone) can go at any point after;
  `publish-pypi.yml` keeps its `dof2md-v*` tag rule for exactly that one
  last release.
- The website page was **not** re-executed (it needs `mineru` and a real
  edition PDF): the rename changes no output, so the `website/_freeze/`
  payloads were edited textually, which is what `freeze: true` publishes.
  `.github/migracion-diputados.md` keeps its `dof2md` mentions — it is a
  frozen historical record, like this file's own history sections.

## `document2md` moved to its own repository (issues #233, #234, done)

**This section describes the tree, not a direction.** `packages/document2md/`
and `packages/dof2md/` are gone; both live in
https://github.com/INGEOTEC/document2md, which #233 created as a single import
commit taken from this repository at `e1f258c` (history was not extracted — it
stays readable here, and LegalIA is public). #234 then made this repository
consistent with that. `document2md` is the **first** package to leave, and the
criterion it sets is the one a later candidate is measured against.

- **The criterion: no dependency on the monorepo, plus its own release
  cadence.** `document2md` imports nothing from any package here, has had no
  notion of the DOF since #134, and was renamed away from `dof2md` in #228
  precisely because it is a general tool. The only thing here that uses it is
  `nota2md`'s optional `ocr` extra, imported lazily. Sharing a repository
  bought it nothing and cost everyone a ~100 MB `.git` and a seven-package CI
  matrix. A package that *does* depend on a sibling (`nota2md` on `dofjson`
  and `scjn`) has not met this bar and is not a candidate.
- **What left, and what stayed.** The package, its tests, its Read the Docs
  page (now that repository's `docs/source/index.rst`) and the `dof2md`
  tombstone all left — the tombstone is `document2md`'s concern, being its own
  old PyPI name, so LegalIA keeps no trace of either. What **stayed** is
  `website/pages/document2md.ipynb` and its `_freeze/` payload: the website is
  results about the gazette, which is a LegalIA concern regardless of where
  the code that produced them lives (#119's rule, unchanged). It was not even
  re-executed — editing its prose means re-freezing, which is a docs pass of
  its own.
- **The bridge, and the condition that removed it.** `document2md` 0.3.0 was
  not on PyPI when #234 landed (publishing is a human step: a `TWINE` secret
  and a tag), so everything that needed it importable *in this repository's own
  environments* took it from GitHub **pinned to #233's import commit**, never a
  branch, so a later push there could not change what LegalIA's CI tested: the
  root `pyproject.toml`'s `[tool.uv.sources]` (a root-level source applies to
  every workspace member, so it was declared once),
  `.github/workflows/test.yml`'s `nota2md` job, and `.devcontainer/python.sh`.
  That bridge is **gone**: 0.3.0 reached PyPI on 2026-09-13 and issue #235
  replaced all three with the plain PyPI name the same day, so this repository
  now depends on `document2md` exactly the way any outside user does. The
  devcontainer names no requirement of its own for it — it installs
  `packages/nota2md[ocr,test]`, and the `ocr` extra is the single place the
  requirement is declared.
- **The published `nota2md` never carries a URL.** Its `ocr` extra is, and
  stays, `document2md>=0.3.0`: PyPI rejects direct-URL dependencies in a
  published package, so the bridge lives only where this repository builds its
  own environments. This is also why `nota2md`'s own error message says `pip
  install document2md (or nota2md[ocr])` — the path it used to name is gone,
  and the PyPI name is the durable instruction.
- **Publish order is now a cross-repository rule.** `document2md` 0.3.0 must
  reach PyPI **before or at the same time as** any `nota2md` release whose
  `ocr` extra requires it, or `pip install nota2md[ocr]` breaks for everyone
  outside these repositories — the same rule this file already states for
  `scjn` and `nota2md`, except the two sides are no longer released from one
  tree.
- **The docs split, per #119's rule.** LegalIA's Read the Docs stops
  installing and documenting the package: `docs/source/document2md_api.rst` is
  deleted, `conf.py` no longer imports it, `.readthedocs.yaml` no longer
  installs it, and `requests` left `docs/requirements.txt` with it (it was
  there only for that `--no-deps` install). `index.rst` keeps a `document2md`
  row whose cells link **out** — repository, PyPI, its own docs site — with
  `—` for the version, and the architecture diagram keeps the node, redrawn
  dashed like the external systems. A row that links out is what a monorepo's
  docs owe a package that lives elsewhere. The new repository's own Sphinx
  site refers back here for `dofjson`/`nota2md`; it would use intersphinx, but
  `legalia.readthedocs.io` publishes no `objects.inv` today, so those are
  plain links and its `conf.py` records the one-line change that restores
  intersphinx.
- **`is_tombstone` stays in `scripts/check_package_versions.py`**, with no
  package using it. The marker exists so "the next rename costs a key, not a
  code edit" — deleting the mechanism along with its first user would undo
  that on purpose. `legalvec`'s boundary test, by contrast, **did** drop
  `document2md`/`dof2md` from the tuple it greps for: that guard says "nothing
  else in this monorepo", and naming packages that are no longer in it would
  make it assert something false.

## Documentation: two sites, one division of labour (issue #119, done)

The project has two documentation sites, and what goes on which one is a
rule, not a per-page judgment call — stated identically in `README.md`,
`docs/source/index.rst` and `website/_quarto.yml`'s navbar, so a reader
lands on the same story regardless of which one they open first:

- **Read the Docs** (`docs/source/`, Sphinx, built from this repo's default
  branch at legalia.readthedocs.io) is the **developer reference**: every
  package's full API, public and private, with a worked example for every
  public symbol. Usage examples live here — they do not live on the website.
- **`website/`** (Quarto, `ingeotec.github.io/LegalIA`) is **results**:
  datasets, findings, the analysis of the gazette itself. A page there uses
  the packages to build something (a figure, a corpus, a number worth
  reporting) rather than teaching how to call them; if a page starts
  explaining a function's arguments one by one, that content belongs on the
  function's Read the Docs page instead, with a link down to it.

Rules for a package's Read the Docs page (`docs/source/<pkg>_api.rst`),
settled by issues #195-200 while building the site the original four
packages shared, and inherited unchanged by `scjn` when it joined (#212):

- **Every public symbol gets a verified example.** Concretely: every name in
  a package's `__all__`, every public method of an exported class, every CLI
  subcommand. "Verified" means a live doctest, run by the separate
  `docs-doctest` job in `.github/workflows/test.yml` (network access
  allowed; the Read the Docs build itself stays HTML-only and must not
  depend on the network). Where that is genuinely not possible — as with the
  OCR paths of `document2md`, which need `mineru` and which took this rule
  with them to their own repository (#234) — the exception is written down on
  the page itself, with the package's own pytest suite named as what verifies
  that behaviour instead; it is not silently skipped.
- **Private/internal helpers** (leading-underscore names) get a full
  docstring and appear via `:private-members:`, but need no worked example —
  they are documented for whoever is extending or debugging the package, not
  exemplified for a caller.
- **Tests are never documented**: no `automodule` over a package's `tests/`,
  no doctest collection under `packages/*/tests/`.
- Any example that touches a large GitHub Release corpus (`scjn-leyes`,
  `notas-archivo`) picks one small law/asset rather than walking the whole
  release, and the doctest job caches `nota2md.cache.CACHE_DIR`/
  `dofjson.titulos.CACHE_DIR` between runs — the releases are hundreds of MB.

A future package added to this monorepo inherits this rule: it gets a
`docs/source/<pkg>_api.rst` page built to the same standard, not a "usage"
section bolted onto `website/`.

## Commands

Three test files make real network calls and are excluded from routine runs
(CI's `test.yml` does include them by default — check before assuming a
failure there is unrelated). Since issue #209 moved the `scjn-leyes` release
readers into the `scjn` package, two of the three now live there:

```bash
pytest packages/nota2md -q --ignore=packages/nota2md/tests/test_leyes_44.py
pytest packages/scjn -q --ignore=packages/scjn/tests/test_api_red.py \
    --ignore=packages/scjn/tests/test_release_red.py
```

A fourth test file is excluded for a different reason — not network, but
wall-clock time plus a ~380 MB local cache not every machine has: issue
#218's `md2akn.units.coverage()` invariant and issue #227's
`max_unit_chars()` one, swept over all three cached SCJN releases (~12
minutes, measured) rather than over a handful of fixtures.

```bash
pytest packages/md2akn -q --ignore=packages/md2akn/tests/test_units_release_sweep.py
```

There is no `pytest packages/document2md` any more: that package has its own
repository and its own suite (#234). `uv sync` still puts it in this
repository's `.venv`, from PyPI via `nota2md`'s `ocr` extra (issue #235
removed the pinned-git bridge), because
`packages/nota2md/tests/test_builder.py` patches `document2md.converter.*`.

The website's notebooks are committed without their outputs, via an
`nbstripout` clean filter (`.gitattributes` maps `*.ipynb` to it). The filter
itself lives in `.git/config`, which is not versioned. The devcontainer
installs it for you (`.devcontainer/postCreate.sh`); a clone outside the
container has to do it once — otherwise notebook outputs get committed and
the pack grows by megabytes per commit (`website/pages/titles.ipynb` alone
was 7.7 MB with outputs, 42 KB without):

```bash
pip install nbstripout
nbstripout --install --attributes .gitattributes
```

The site renders from the committed `website/_freeze/` cache, not from the
notebooks, so stripping outputs costs the site nothing — but only because
`_quarto.yml` sets `execute: freeze: true`. Under the default `freeze: auto`
it silently breaks the publish workflow: `auto` compares the notebook's md5
against the one stored in the freeze entry, and those can never agree once
the clean filter is installed (the freeze is written from the local copy,
which has outputs; git stores the stripped one). That is exactly how the
`Publish website` run on the #150 merge failed — Quarto tried to execute
`pages/titles.ipynb` on a runner with no Python.

Consequence for local work: `quarto render` will *not* pick up edits to a
notebook's code. Re-execute explicitly and commit the refreshed `_freeze/`.
`--no-freeze` is a project-level flag only — on a single file quarto (1.9.36)
hands it to pandoc, which dies with `Unknown option --no-freeze`. So:

```bash
quarto render --no-freeze                                  # the whole site
QUARTO_FREEZE=false quarto render pages/dataset.ipynb --execute   # one file
```

A clone that skips the `nbstripout` install is not broken — an undefined
filter is a pass-through — it just stops shrinking what it commits.

## Git workflow: branches, issues and PRs

- A branch is created to work on one particular issue. It is created only at
  the moment, while on the main branch, an issue is read and about to be
  implemented — that is the single point where a new branch gets created.
- While working on that issue, it may turn out necessary to read and
  implement another (secondary) issue to complete the main one. In that
  case, do **not** create a new branch — you are already on a working
  branch, and the secondary issue's work happens there too.
- Do not open a pull request as a side effect of finishing the work. A PR is
  only created when the user explicitly asks for it, after both the main
  issue and every secondary issue pulled in to complete it are done. Never
  open a PR proactively "because the work is done."

## Language policy

- The project's implementation must always be in English: identifiers
  (function/variable/class names), code comments, docstrings, commit
  messages, and any new documentation. This applies regardless of what
  language the instructions for the task were given in — instructions can be
  in Spanish or English, but what gets written into the repo must be English.
- The codebase currently has a mix of English and Spanish identifiers and
  comments (see `scjn.header`'s `versiones_de_directorio`/`lee_cabecera`,
  `scjn.api`'s `descarga_ordenamiento`, and the Spanish CLI
  help/script output throughout `scripts/`). Leave existing Spanish code
  as-is when touching unrelated lines — do not do drive-by mass renames. But new code,
  and any code you're already rewriting for other reasons, should be
  written in English so the codebase converges over time.
- Domain terms that are proper nouns or established Mexican legal/legislative
  vocabulary (`DOF`, `codNota`, `SIDOF`, `NOM-...` codes, law abbreviations
  like `cpeum`/`lft`) are not translated — they aren't Spanish-vs-English,
  they're names.

## Working conventions

- Naming favors precision over brevity even when verbose: `codNota`,
  `legal_provisions`, `download_scjn_leyes_corpus` are the
  domain's actual vocabulary (a DOF "nota" is a legal provision, not a
  short informal note) — don't shorten it for its own sake.
- Comments in this codebase often record a specific past incident or a
  numeric check that justified a design choice (e.g. why NOM codes are kept
  as opaque strings, why treaty-name matching is rarity-weighted, why a
  version floor is pinned in a `pyproject.toml` dependency) — read them
  before "simplifying" the code they're attached to.
- `nota2md`'s OCR paths depend on `document2md` (its own repository since
  #234) and through it on `mineru`, which is heavy; `nota2md`'s HTML path
  (`beautifulsoup4` + `dofjson` + `requests`) works standalone and is the
  preferred/default source — `document2md` is an optional extra
  (`nota2md[ocr]`) and is imported lazily, so installing `nota2md` alone
  doesn't pull it in.

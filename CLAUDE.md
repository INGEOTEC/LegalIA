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
- **`document2md`** — OCRs a PDF or a set of scanned images to Markdown via
  `mineru` (renamed from `dof2md` in issue #228, see its own section below).
  Has no notion of "note"/"legal provision", and no download of
  its own — it only ever converts a PDF/images already on disk; getting a
  whole DOF edition's PDF by date and edition is `dofjson.download_edicion_pdf`'s
  job now (issue #134). `BatchConverter` keeps one `mineru-api` server warm
  across a batch instead of paying startup cost per document; `nota2md`'s
  image/PDF OCR paths accept an already-`__enter__`'d instance via their own
  `converter` parameter. Only needed as `nota2md`'s OCR fallback for legal
  provisions predating the HTML era (pre-1999ish) — a modern note only needs
  `dofjson` + `nota2md`.
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
  own `$LEGALVEC_CACHE_DIR`.

**Data is never committed to git.** The SCJN corpus of consolidated law
texts, `dofjson`'s notes archive, the vectors derived from either, and
downloaded titles datasets all live only in GitHub releases (`scjn-leyes`,
`notas-archivo`, `scjn-*-vectors`) or are `.gitignore`d local scratch
directories (`/output/`, `/notas-archivo/`, `scripts/scjn/`,
`scripts/legal_provisions/`, `/emb-run*/`).
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
upload plans (Fase 4). What is **not** done, deliberately: nothing is
published. Issue #115's Hallazgo C stands — a human runs the `PUBLICAR.md`
`scripts/embeddings/package_vectors.py` generates, and `legalvec` has no
PyPI release yet either.

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

## `dof2md` is now `document2md` (issue #228, done)

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
  depend on the network). Where that is genuinely not possible — `document2md`'s
  OCR paths need `mineru`, deliberately kept out of the doctest job for its
  weight — the exception is written down on the page itself, with the
  package's own pytest suite named as what verifies that behaviour instead;
  it is not silently skipped.
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
- `document2md` and `nota2md`'s OCR paths depend on `mineru`, which is heavy;
  `nota2md`'s HTML path (`beautifulsoup4` + `dofjson` + `requests`) works
  standalone and is the preferred/default source — `document2md` is imported
  lazily so installing `nota2md` alone doesn't pull it in.

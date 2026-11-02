# CLAUDE.md

This file guides Claude Code (claude.ai/code) in this repository. It says what the tree **is**, in the
present tense. Why it became so, issue by issue, is in `docs/HISTORY.md`; the parser's rules are in
`packages/md2akn/RULES.md`. A figure here is kept only when it defines the tree, and carries its date.

## What this is

LegalIA is a monorepo of independently-versioned Python packages, developed by INGEOTEC, for analyzing
legal texts in the Mexican context. Two sources: the *Diario Oficial de la Federación* (DOF), Mexico's
official gazette (over 1.2 million legal provisions since 1917, through `dofjson`), and the Supreme
Court's (SCJN) consolidated texts of federal laws, regulations, guidelines and international treaties
(through `scjn`). The DOF is the official source of legal text; the SCJN is not, and `fuente: scjn` in a
provenance header means exactly that.

On top of the corpora sit retrieval units (`md2akn`), vectors (`legalvec` and `scripts/embeddings/`) and
a public map of the instruments (the website's Atlas and Instruments pages).

## Packages and read order

Each `packages/<name>/` has its own `pyproject.toml`, dynamic version (`<pkg>.__version__`), tests and
PyPI release, and is installed independently. Read them in this order; each builds on the ones before.
Versions are what `scripts/check_package_versions.py` reads: `dofjson`, `scjn` and `nota2md` 0.5.1 (PyPI has
0.5.0), `md2akn` 0.5.1 and `legalvec` 0.2.0 (never published).

- **`dofjson`**: client for SIDOF's undocumented JSON open-data service (`sidof.segob.gob.mx`).
  Everything is reachable off the package (`dofjson.get_nota`, `dofjson.get_notas`, ...); another package
  never imports `dofjson.sidof`/`dofjson.dofweb` directly. SIDOF silently loses whole days (it reports
  them as an empty, valid day, like a Sunday); `dofjson.dofweb` recovers them from `www.dof.gob.mx`, and
  callers never need to know which source answered (the `fuente` field says). It streams the compact
  `codNota`+`titulo`+`fecha` record of every legal provision ever published (`legal_provisions_titles`)
  off the on-disk cache of the `notas-archivo` release, and writes no dataset of its own.
- **`scjn`**: client for the SCJN's SCOW JSON API (`scjn.api`, the backend of
  `legislacion.scjn.gob.mx/consulta`), the writer of the Markdown snapshots (`scjn.markdown`,
  `scjn.outline`) and the disk-first reader of the four SCJN releases (`scjn.release`). It depends on
  nothing else in the monorepo and nothing there is imported back from `nota2md`/`dofjson`
  (`packages/scjn/tests/test_boundary.py` greps for it). Entry points for `scjn-leyes`:
  `download_scjn_leyes_index`/`_corpus`/`_catalog`, `markdown_de_snapshot`, `iter_current_federal_laws`,
  `local_slugs`, `download_scjn_leyes_assets`, `AssetNotCached`; the three id-keyed siblings have the same
  names with `reglamentos`/`lineamientos`/`tratados` (`iter_current_*` included) and raise
  `SinTextoEnSCJN` for an instrument with no text. Its CLI is `scjn download`.
- **`nota2md`**: one DOF note to Markdown. `legal_provisions(codNota)` returns the SCJN's consolidated
  text of the whole law at that reform when `scjn-leyes` covers the `codNota`, else the DOF's own
  HTML/image/PDF source (`source="dof"` forces it); `reconstruct_legal_provisions` replays a law's current
  text from its reform decrees; `fetch_daily_legal_provisions`, `get_document`, `legal_provisions_titles`
  and the `scjn` readers are re-exported. `nota2md.linking` is the one SCJN-adjacent concern kept here:
  it matches a snapshot to the DOF `codNota` that produced it, and a `codNota` is a DOF concept. Its
  derived output is cached under `nota2md.cache.CACHE_DIR`, deliberately not `dofjson`'s or `scjn`'s.
  `nota2md download all` fetches the five GitHub releases (`federal-laws`, `federal-regulations`,
  `federal-guidelines`, `international-treaties`, `gazette-metadata`); `nota2md` itself reads only
  `scjn-leyes` and `notas-archivo`.
- **`md2akn`**: reads a Markdown instrument (what `scjn`/`nota2md` write) into a tree labelled with Akoma
  Ntoso's *vocabulary* (`akn_type`, `eId`, `refersTo`; the project emits no Akoma Ntoso XML) and turns the
  tree into retrieval units, `text_units()`. No dependency on another package. See "How md2akn reads a
  document".
- **`legalvec`**: disk-first reader for the project's own vector releases (`*-vectors`). Not part of
  `scjn` ("the Court's client"): the vectors are derived on a GPU from a specific model revision. It
  imports nothing else in the monorepo (grepped by `test_boundary.py`), which is why it may take `pyarrow`
  and `numpy`. Surface: `download_vectors_assets` (the only network), `load_vectors`, `load_units`,
  `VectorSet`, `AssetNotCached`, `model_slug`; console script `legalvec download`/`legalvec status`.
  `nota2md download` does not fetch these releases.
- **`document2md`** is not in this monorepo: it OCRs a PDF or scanned images to Markdown via `mineru` and
  lives in https://github.com/INGEOTEC/document2md. Here it is only `nota2md`'s optional `ocr` extra
  (`document2md>=0.3.0`), imported lazily, needed for notes predating the HTML era (pre-1999).

Decisions:

- A package is named after what it does, not after the corpus that first needed it.
- A package leaves the monorepo only when it depends on no sibling and has its own release cadence.
- A published package never carries a URL dependency; publish order is `scjn` and `dofjson` before the
  `nota2md` release that raises their floor, and `document2md` before any `nota2md` whose `ocr` extra
  requires it.
- No source other than the SCJN and the DOF/SIDOF: the Cámara de Diputados (LeyesBiblio) is not a source.
  The federal-law catalogue is the `scjn-leyes` release itself, and an `abrev` is that release's slug and
  asset name, never re-derived.
- "Reform N" of a law is the SCJN reform table's chronological order.
- Only `leyes` has an `abrev`, an `actualizado` (`actualizado_scjn`, `actualizado_dof`), an `indice.json`
  and DOF linking; reglamentos, lineamientos and tratados are keyed by `id_ordenamiento`
  (`scjn.catalog.instrumento_key`) and have none.

## Data is never committed: releases and caches

Corpora, vectors and derived datasets live only in GitHub releases or in gitignored local scratch
(`/output/`, `/notas-archivo/`, `scripts/scjn/`, `/emb-run*/`, `/website/pages/atlas/pairs*/`, `/website/pages/instruments/files/`). Read them back
through the readers above, never by looking for a file in the repository.

| release | what | key | produced by |
|---|---|---|---|
| `scjn-leyes` | 315 federal laws, reform-dated snapshots, one tarball per law, `indice-global.json.gz` | `abrev` (slug) | `scripts/fetch_scjn_legislacion.py`, `scripts/empaqueta_scjn_leyes.py` |
| `scjn-reglamentos` (series `-2`, ...) | 1,087 federal reglamentos | `id_ordenamiento` | `scripts/empaqueta_scjn_coleccion.py` |
| `scjn-lineamientos` | 163 federal lineamientos | `id_ordenamiento` | same |
| `scjn-tratados` (series `-2`, ...) | 1,455 international treaties (the SCJN's *ámbito* `TRATADOS INTERNACIONALES`, any category) | `id_ordenamiento` | same |
| `notas-archivo` | the DOF notes archive, monthly | `codNota` | `.github/workflows/notas-archivo.yml` (the one release a workflow publishes) |
| `scjn-leyes-vectors`, `scjn-reglamentos-vectors`, `scjn-lineamientos-vectors`, `scjn-tratados-vectors` (numbered series) | one embedding per distinct text, both models (Qwen3-Embedding 0.6B and 4B) | `clave` | `scripts/embeddings/package_vectors.py` |
| `atlas-pairs` | the pair explanations behind each Atlas weight, three sets (0.6B, 4B, BM25) | pair | `scripts/embeddings/export_atlas_pairs.py` |
| `atlas-instruments` | the Instruments page's per-instrument files (4B) | `coleccion/clave` | `scripts/embeddings/export_instrument_files.py` |

Corpus counts are those of the 2026-10-26 re-crawl. A release series is resolved by asking GitHub: a
part keeps the bare tag, a continuation adds `-<n>`, and a reader probes `-1`, `-2`, ... until one 404s,
so nothing records a part count (a release holds at most 1,000 assets and a body 125,000 characters).
An instrument with no consolidated text is indexed with `snapshots: 0` and no tarball.

Caches, one per package, none shared: `scjn.cache.CACHE_DIR` (`$SCJN_CACHE_DIR`, the four SCJN releases),
`nota2md.cache.CACHE_DIR` (`$NOTA2MD_CACHE_DIR`, derived Markdown), `legalvec.cache` (`$LEGALVEC_CACHE_DIR`,
the vector releases) and `dofjson.titulos.CACHE_DIR` (platformdirs; `notas-archivo`). Every reader reads
only its cache and raises `AssetNotCached` rather than downloading; only `download_*_assets`,
`scjn download`, `legalvec download` and `nota2md download` talk to the network. `nota2md`'s derived
Markdown is keyed by file name and reused, so a rewritten snapshot needs `refrescar=True` or a deleted file.

Decisions:

- **No workflow publishes SCJN-derived data** (issue #115, Hallazgo C: the SCJN's search can return a
  completely wrong document for an instrument, so a human decides what is safe to publish). Every
  packaging script writes a `PUBLICAR.md` for a human and uploads nothing. Only `notas-archivo` is built
  by a workflow, and `website.yml` only *downloads* releases.
- A release the page or `legalvec download` needs must exist **before** the pull request that needs it
  merges: `website.yml` and `legalvec download` fail without it, on purpose.
- A regenerated release is replaced in place (same tag, `--clobber`), and `package_vectors.py` lists the
  assets a new set no longer contains.
- Dedup of vectors is inside a collection, never across.

## The SCJN outline contract

The SCOW API serves an instrument's reform as ordered *blocks* (`orden`, `numero`, `referencia`,
`contenido`, `tamanio`). `scjn` writes one marker line before each block that yields text:

    <!-- scjn:block orden=N numero=N kind=<kind> [type=] [num=] label="..." -->

`kind` is one of header, recitals, preamble, toc, transitorios, decree, container, article, provision,
other. The marker is an HTML comment on purpose: no character of the text changes, `strip_markers`
returns the text byte for byte, and `markdown_de_snapshot(..., outline=False)` is what `nota2md` reads.
The contract (`packages/md2akn/RULES.md`, layer W): every block that yields text has exactly one marker; a
block that yields none (an editorial `N. DE E.` aside) leaves its `orden` as a gap the raw answer
explains; a marker is metadata, never embedded.

- **Raw answers are kept.** Every reform's answer is stored gzipped (`scripts/scjn/raw-api/<c>/<key>/`,
  local scratch), so the writer can change without a re-crawl (`scripts/rewrite_scjn_from_raw.py`).
- **The completeness guard** (`scjn.api.ScjnApi.articulos_of_reforma`): an answer is complete when its
  `orden` values are exactly `1..max` and it carries at least `tamanio` rows. An incomplete answer is
  requested again and then refused with `ScjnApiIncompleteError`, so `descarga_ordenamiento` counts the
  reform as failed and writes no file. A verbatim repeated row is dropped, first kept; two *different*
  blocks sharing an `orden` both stay. An instrument is swapped in whole or not at all (a re-crawl
  never half-updates one); three instruments of 2,920 are still refused as incomplete (`leyes/lft`,
  `reglamentos/81587`, `reglamentos/67602`: the API's own data lacks those blocks) and keep their old files.
- **Tables.** A tab-bearing block is a table: a blank line ends a row, a bare newline is a wrapped line
  inside a cell, a run of tabs separates columns. One recovered row is one Markdown paragraph
  `| cell | cell |`, with no header separator; an `Artículo N` lead on a tabbed line is never a row.
- **Lead of an article.** `scjn.api._LEAD_ARTICULO` bolds the whole number (`**Artículo 1.1**:`,
  `Artículo 2.A.1`).
- **`scripts/verify_scjn_outline.py`** checks the contract over the four local corpora and their raw
  answers, read-only: writer drift (rebuilt body against the one on disk), marker completeness,
  attribute round-trip and text invariance. Exit 1 only on a round-trip or invariance violation, never
  on drift or an explained gap. `scripts/measure_block_gaps.py` counts the snapshots whose `orden` markers
  have a gap and which are explained. The shared fixture
  `packages/md2akn/tests/fixtures_units/marker_vocabulary.json` is the bridge to `md2akn`, which imports
  nothing from `scjn`.

## How md2akn reads a document

`packages/md2akn/RULES.md` is the specification, one layered list: **W** the writer's contract, **S** the
skeleton the outline states, **B** the rules inside an API block, **U** the units. The design in one
sentence: *the structure the SCJN states is the structure; the rules come after, inside it.*

- **Two phases.** `md2akn.skeleton` reads the markers first and settles what each API block *is* (its
  node type, number, rank and heading); `md2akn.structure` then places the Markdown blocks in a tree and
  runs the text rules *inside* the block it is reading; `md2akn.units` turns the tree into retrieval units
  (`text_units()`, rules U1 to U7: article as the unit, `heading`/`intro`/`table`/`loose`/`preamble`/
  `conclusions`/`toc` units, `cap` 2,000 for everything that is not an article, `text_sha1` and
  `bare`/`contextual` templates, `envelope`). `md2akn.hits.hit(rule, branch)` counts where a rule takes
  effect (`scripts/embeddings/measure_trees.py --all-snapshots --hits` sums it over every snapshot, which
  is how a branch that can no longer fire is found).
- **A document with no markers** is one implicit block: layer S has nothing to say and layer B runs
  unbounded, and the output is byte-identical to the pre-marker parser
  (`tests/fixtures_units/marker_free_digests.json`).
- **No rule names an instrument, a collection or an eId.**
- **Invariants**, each a test and, over the real corpora, a release-sweep assertion:
  **I1** an API block is the unit of ownership (everything up to the next marker hangs from the node the
  block opened; `md2akn.validate._block_escapes`); **I2** no `loose` unit outside the transitorios;
  **I3** no repeated identifier (`duplicate_number` is 0: a number is repeated only when it is the same
  identifier, and a second claimant takes a `_2` suffix); **I4** every numbering gap is classified with raw
  evidence in `tests/fixtures_units/genuine_gaps.json`, and every fixture entry is still a gap; **I5** the
  node a block opens is of the type, number and rank its marker says (`md2akn.validate.block_conformance`),
  with a short documented list of exceptions (`marker_documented.json`).
- **The release sweep** (`packages/md2akn/tests/test_units_release_sweep.py`, 19 tests, about an hour)
  runs the four cached corpora through `text_units` and asserts `coverage()` (every character of the
  instrument is accounted for once), `max_unit_chars()`, the four invariants above and the
  corpus-level counts. It needs the corpora in `~/.cache/scjn` and is excluded from routine runs; run it
  whenever the parser, the writer or a corpus changes.
- **Units are the vectors' contract.** `normalize()`, `text_sha1` and the unit vocabulary are not
  renegotiated; a change that alters what a *future* vector means is a version bump of `md2akn`. Whole
  articles are embedded (`split_articles=False`), truncated at the model's own
  `max_position_embeddings`, never split or windowed; `encode_shard.py` records every truncated text.

Decisions:

- A table outside an article is a node and yields `table` units cut at row boundaries; inside an
  article it stays article text.
- A container's introduction is an `intro` node; a childless container is a `heading` unit.
- The article number is read whole (`18.-A.-`, `1.1`, roman `ARTÍCULO I`, `Bis`, `Art. 8` running heads
  excluded); an articleless instrument numbers its provisions `PRIMERO.-` or `1.`/`2.1`, decided once per
  document.
- Nothing is merged, dropped or renumbered after the fact to hide a repeat.

## Embeddings and the Atlas chain

`scripts/embeddings/` is orchestration for one research run on one cluster (`headmaster` + `cemieredes`
for the GPUs, `geoint` for CPU nodes), not a package; `scripts/embeddings/README.md` is its manual, with
a dated *Measured* section for every run.

- **Work directories.** One gitignored directory per collection (`emb-run-<collection>[-NNN]/`), a
  numbered copy per rebuild; a published one is copied, never touched. `build_units.py` writes
  `units.parquet` (columns include `coleccion`, `clave`, `text_sha1`, `unit_type`, `path`, `envelope`) and
  `corpus-manifest.json`; `plan_shards.py` dedups by `text_sha1` and balances shards; `submit_jobs.py`
  runs one model at a time (`--max-wait-minutes` returns 75 "still running" instead of blocking);
  `merge_shards.py` writes `vectors-<clave>-<model>-<K>.parquet`; `package_vectors.py` writes the release
  assets and `PUBLICAR.md`. `compare_merges.py` proves a rebuilt merge differs from the published one only
  in the files whose set of owned texts changed.
- **The Atlas chain.** `stage_vectors_cache.py` builds a `legalvec`-shaped gitignored cache from the
  merges, verified against their `SHA256SUMS.txt`, and every later script reads it with `--cache-dir`.
  `prepare_umap_input.py --unique-names` builds `emb-run-atlas/` (0.6B; `emb-run-atlas-4b/` for the 4B;
  `prepare_bm25_input.py` builds `emb-run-atlas-bm25/`); `instrument_matrix.py` weighs, for every
  searched excerpt, which *other* instrument owns the nearest text into a directed instrument × instrument
  matrix; `build_instrument_umap_html.py`/`export_atlas_data.py` project and export it;
  `nearest_texts.py` keeps the five nearest foreign texts per excerpt (4B only, and it proves rank 1 is
  the matrix's winner before writing `.done`); `export_atlas_pairs.py` and `export_instrument_files.py`
  write the two release payloads. `scoring.py` is the one place a text-to-text score is computed.
- **What the matrix compares.** `instrument_matrix.searched_mask` is the one rule, imported by every
  consumer: a **heading**, a `toc`, a **transitorio** (its `path` holds a `TRANSITORIOS…` label) and an
  **envelope** unit (the title block and a treaty's promulgation decree and certification, read by
  position) are neither a source nor a candidate. Every tied winner counts (1e-6 on float32 cosine); a
  unit row distributes a total weight of 1, `1/m` to each of the `m` instruments owning a winner; a row
  whose winner is a word-for-word match owned by several instruments is not counted; only columns owned
  *exclusively* by the source are masked. Cosine is exact (blocked matmul), never the k=15 neighbours.
  A units file with no `envelope` column compares everything and says so; a mixture is a `SystemExit`.
- **Three data sets over the same comparison:** `atlas.json` (Qwen3-Embedding-0.6B, the default),
  `atlas-qwen3-4b.json`, `atlas-bm25.json` (a lexical baseline: `bm25s`, Lucene `k1` 1.5, `b` 0.75, defaults
  kept after one tuning run). `export_atlas_data.py --instruments-as atlas.json` refuses a file whose
  instruments differ at any position, because the page swaps files keeping its selection.
- **Unique instruments.** `unique_instruments.py` keeps one instrument per identical name in
  `reglamentos` and `lineamientos` (the newest original publication date wins; NFKD, accents, whitespace
  and case folded) and, in every collection, one per identical multiset of compared `text_sha1`. Leyes and
  treaties are never grouped by name. It runs before the matrix is stacked.
- **As it stands (28 October 2026, `atlas.json`):** 2,670 instruments (314 laws, 864 regulations, 125
  guidelines, 1,367 treaties), 227,132 excerpts, 156,432 counted, with 36,137 headings, 22,575
  transitorios and 10,093 envelope rows out and 1,895 shared word-for-word rows not counted.
- **Evaluation.** `gold_links.py` builds an external gold (an instrument whose own name or "objeto"
  sentence names another instrument; any direction, never itself): 253 instruments, 277 links.
  `evaluate_links.py` scores the three matrices against it at instrument level under a bootstrap + McNemar
  rule fixed beforehand; `evaluate_citations.py` scores, one unit row at a time, explicit citations
  ("artículo N de la Ley X"). The 0.6B, the 4B and BM25 are tied on the closest instrument; the 4B is
  better row by row. Outputs are local and gitignored.
- **Topics (draft).** `cluster_instruments.py` clusters the instruments from the rows of the matrix (four
  algorithms, a selection rule fixed beforehand, Louvain at resolution 1.0 wins); `export_topics_page.py`
  writes `website/pages/topics/`. The Topics page is `draft: true` and excluded from the site by decision
  of the repository owner (2026-10-13); re-enabling it takes the steps listed in `website/_quarto.yml`.

Decisions:

- Both models stay live; nothing has earned the right to drop the 4B.
- Excerpt is the reader's word; *unit* is the Python side's; a DOF note is a *legal provision*.
- Regenerating a committed derived file (`atlas*.json`, `instruments.json`, `topics.json`) is a human
  running the exporter, never a workflow.
- Nothing derived is committed but sub-MB summaries the site cannot be built without; no `legalvec` API
  was added for the visual explorers (`build_umap_html.py`, `project_umap.py`, `submit_umap.py` fit and draw
  UMAP over all vectors as one standalone Vega-Lite page; `viz` is a root dependency group, not in `dev`).

## The website

Two sites, one division of labour, stated identically in `README.md`, `docs/source/index.rst` and
`website/_quarto.yml`'s navbar:

- **Read the Docs** (`docs/source/`, Sphinx, legalia.readthedocs.io) is the **developer reference**: every
  package's full API, public and private, with a worked example for every public symbol. Usage examples
  live here, not on the website.
- **`website/`** (Quarto, `ingeotec.github.io/LegalIA`) is **results**: datasets, findings, the analysis of
  the gazette. A page uses the packages to build something rather than teaching how to call them; a page
  that explains a function's arguments belongs on its Read the Docs page.

Rules for a package's Read the Docs page (`docs/source/<pkg>_api.rst`; a future package inherits them):

- Every name in `__all__`, every public method of an exported class and every CLI subcommand gets a
  verified example: a live doctest run by the `docs-doctest` job of `.github/workflows/test.yml` (network
  allowed; the Read the Docs build itself is HTML-only and never touches the network). An exception is
  written on the page itself, naming the pytest suite that verifies the behaviour instead.
- Private helpers (leading underscore) get a full docstring and appear through `:private-members:` but need
  no example. Tests are never documented.
- An example touching a large release picks one small law or asset; the doctest job caches
  `nota2md.cache.CACHE_DIR` and `dofjson.titulos.CACHE_DIR`.
- `docs/HISTORY.md` is plain Markdown outside the Sphinx toctree.

The pages of the website:

- Notebooks (`index`, `titles`, `dataset`, `leyes`, `document2md`) are frozen results: `website/_freeze/`
  is what is published, never re-executed in CI (see Commands). Do not edit a notebook or its freeze to
  change prose without a re-freeze pass of its own.
- **Atlas** (`pages/atlas.qmd`, `pages/atlas/atlas.js`/`atlas.css`): a hand-written D3 v7 page with no
  code cells and no build step; `_quarto.yml`'s `project.resources` copies `pages/atlas/**`. Circle area is
  the counted excerpts; a detail panel lists the five closest instruments and the five that point there;
  *Neighbourhood size* switches the four UMAP layouts and *Similarity* the three data sets (0.6B default,
  4B, BM25); each weight opens a `<dialog>` with the excerpts behind it, read from
  `pairs*/<i>-<j>.json`. The panel is a `<div role="complementary">`, never an `<aside>` (Quarto sends an
  `<aside>` to the page margin). Colours: laws `#2a78d6`, regulations `#008300`, guidelines `#e87ba4`,
  treaties `#eda100`; the legend comes from `meta.collections`.
- **Instruments** (`pages/instruments.qmd`, `pages/instruments/instruments.js`/`.css`): a reading page, no
  library. A landing (search, sortable table) opens an instrument at `?c=<collection>&k=<key>[#<eId>]`:
  an outline, the document as headings, paragraphs and tables, and on a *compared* excerpt its five
  nearest texts. No eId, unit type or flag is printed; `stripMarkers` removes the marker lines on display
  (a documented copy of `scjn.outline.MARKER`, compared by a test).
- **Topics**: a draft, not rendered (above).
- **Committed:** `atlas.json`, `atlas-qwen3-4b.json`, `atlas-bm25.json`, `_gold-links.md`,
  `instruments/instruments.json`, `topics/` (draft), `data/scjn-leyes-summary.json`. **Fetched by
  `website.yml` from releases, never committed, and the build fails if missing:** `pairs/`,
  `pairs-qwen3-4b/`, `pairs-bm25/` (`atlas-pairs`) and `instruments/files/` (`atlas-instruments`).
- **Literals.** The pages quote measured numbers as literals copied from the dated *Measured* sections of
  `scripts/embeddings/README.md`, never recomputed; static checks in `test_atlas_page.py` and
  `test_instruments_page.py` pin them against the committed data, and the page says the date.
- **Verified in a real browser.** `scripts/embeddings/tests/test_atlas_page.py`, `test_instruments_page.py`
  and `test_topics_page.py` drive headless Chromium through Playwright (`uv run --group viz playwright
  install chromium` once) and render the qmd with a local Quarto (the newest
  `~/.local/opt/quarto-*/bin/quarto` when not on `PATH`; CI pins 1.9.38). Without them the tests skip with
  a reason and the static checks still run. The harness page never loads the site's stylesheet, so the
  rendered-page tests are what see a Quarto CSS interaction.

Decisions:

- The English interface says *excerpt*, *compared excerpt*, *nearest texts*, *parse*; never *unit*,
  *provision*, *tooltip*, *n_neighbors*. No provenance footer, script name or commit on a page.
- The prose is as wide as the app, flowing academic English, with no lead-phrase-and-colon sentence (a test
  pins it). UMAP, Qwen3 Embedding and BM25 are cited through `website/references.bib`; `references.qmd`'s
  `nocite` carries the keys and `website/scripts/fix_citation_links.py` retargets the links.
- The retired SCJN *Buscador* URL appears nowhere in the tree except two frozen history documents
  (`packages/scjn/tests/test_site_references.py` fails on it).

## Processes

Each is a numbered sequence; a human runs the publish steps.

**A. Crawl a corpus (SCJN).**

1. `scripts/discover_federal_laws.py` / `discover_federal_reglamentos.py` / `discover_federal_lineamientos.py`
   / `discover_federal_tratados.py` list candidates and stop (they write no state; an `abrev` is a release
   asset name, so a new law is a human's decision). `scjn.discovery` holds the shared paging and rescue
   rules; reglamentos and lineamientos are found by category (plus the reform-category rescue), treaties
   by *ámbito*.
2. `scripts/seed_federal_reglamentos.py --coleccion {reglamentos,lineamientos,tratados}` turns a reviewed
   list into `estado.json` files, idempotently.
3. `scripts/fetch_scjn_legislacion.py` crawls (`--coleccion`, `--plan` for the row-comparison plan,
   `--actualiza`/`--solo-fecha`/`--dof-only`/`--reintenta` for `leyes` only); `scripts/recrawl_scjn_outline.py`
   re-crawls a whole corpus with the markers on (a ledger, raw answers kept, an instrument swapped in only
   when every snapshot came back; `--instrumento` forces keys). `scripts/rewrite_scjn_from_raw.py` rewrites
   Markdown from the kept raw answers without the network.
4. `scripts/enlaza_scjn_legislacion.py` links new `leyes` reforms to their `codNota` (DOF); the id-keyed
   collections have no DOF linking.
5. `scripts/verify_scjn_outline.py`, then `scripts/measure_block_gaps.py`.
6. `scripts/empaqueta_scjn_leyes.py` / `scripts/empaqueta_scjn_coleccion.py --coleccion ...` package the
   release (tarballs, `indice-global.json.gz`, `SHA256SUMS.txt`, `MANIFEST.md`, `partes.json`,
   `PUBLICAR.md`).
7. **A human** reads `PUBLICAR.md` and publishes; only changed tarballs need `--clobber`. This happens
   before anything derived from it is published.

**B. From the corpora to the Atlas and the Instruments page.**

1. Build units (`build_units.py --coleccion ... --no-split-articles`), plan shards, embed with both models,
   merge, package (`PUBLICAR.md`), `compare_merges.py` against the published merge.
2. `stage_vectors_cache.py`; then, in each Atlas work directory, `prepare_umap_input.py --unique-names`,
   `instrument_matrix.py`, `nearest_texts.py` (4B), `build_instrument_umap_html.py`.
3. `export_atlas_data.py` (the three `atlas*.json`), `export_atlas_pairs.py` (three pair sets),
   `export_instrument_files.py` (files + `instruments.json`).
4. Evaluations: `gold_links.py`, `evaluate_links.py`, `evaluate_citations.py`; topics:
   `cluster_instruments.py`, `export_topics_page.py`.
5. Update the pages' literals from the new *Measured* section and run the website tests.
6. **A human** publishes, in this order: the four corpora (A.7), the four vectors releases
   (`emb-run-<c>-NNN/publish/PUBLICAR.md`), the three `atlas-pairs` sets and `atlas-instruments`; or
   `website.yml` fails or pairs the new JSON with old files.

**C. When the parser changes.** Run the `md2akn` suite, `scripts/embeddings/measure_trees.py` (trees and
hits before against after), `compare_units.py` against the published units, and the release sweep; a
change that alters any unit's text or hash means B from step 1, and a version bump of `md2akn` if it
changes what a future vector means.

## Commands

Three test files make real network calls and are excluded from routine runs (CI's `test.yml` includes
them; check before assuming a CI failure there is unrelated). Their GitHub calls carry `GH_TOKEN` when set
(`_github_api_headers` in `dofjson.titulos`, `legalvec.cache` and `scjn.release`): unauthenticated, a
shared runner IP's 60 requests an hour run out.

```bash
pytest packages/dofjson -q
pytest packages/scjn -q --ignore=packages/scjn/tests/test_api_red.py \
    --ignore=packages/scjn/tests/test_release_red.py        # the two excluded files are network
pytest packages/nota2md -q --ignore=packages/nota2md/tests/test_leyes_44.py
pytest packages/md2akn -q --ignore=packages/md2akn/tests/test_units_release_sweep.py
pytest packages/legalvec -q
pytest scripts/embeddings/tests scripts/tests -q
python -m sphinx -b doctest docs/source docs/build/doctest   # needs network and cached lfca / reglamento 104906
python scripts/check_docs_references.py                      # paths, dotted names, flags, #N a document cites
pytest packages/md2akn/tests/test_units_release_sweep.py     # about an hour, ~380 MB cache; see the parser section
python scripts/test_inventory.py                             # duplicated and parametrisable tests
```

The sweep is excluded for wall-clock time plus a local cache not every machine has. There is no
`pytest packages/document2md`: that package has its own repository, but `uv sync` still installs it
(through `nota2md`'s `ocr` extra) because `packages/nota2md/tests/test_builder.py` patches
`document2md.converter.*`. **Except on an Intel Mac**, where the root `pyproject.toml`'s dev group markers
that extra out (`onnxruntime` has no macOS x86_64 wheel past 1.23.2 and listing it makes every `uv run`
fail to resolve); the four tests that patch it skip themselves there. The `viz` group does not resolve
there either (`llvmlite`): run the website tests with `uv run --with playwright --with pytest --with
pandas --with pyarrow --with numpy --with scipy --with bm25s --with networkx pytest ...`.

The website's notebooks are committed without outputs, through an `nbstripout` clean filter
(`.gitattributes` maps `*.ipynb`; the filter lives in `.git/config`, which is not versioned). The
devcontainer installs it (`.devcontainer/postCreate.sh`); a clone outside it does it once:

```bash
pip install nbstripout
nbstripout --install --attributes .gitattributes
```

The site renders from the committed `website/_freeze/`, not from the notebooks, and only because
`_quarto.yml` sets `execute: freeze: true`: under the default `freeze: auto` the md5 of a stripped
notebook never matches its freeze entry and the publish workflow tries to execute it on a runner with no
Python. So `quarto render` does not pick up edits to a notebook's code; re-execute explicitly and commit
the refreshed `_freeze/` (`--no-freeze` is a project-level flag only):

```bash
quarto render --no-freeze                                          # the whole site
QUARTO_FREEZE=false quarto render pages/dataset.ipynb --execute   # one file
```

A clone that skips the `nbstripout` install is not broken: an undefined filter is a pass-through.

## Git workflow: branches, issues and PRs

- A branch is created to work on one particular issue, only at the moment, while on the main branch, an
  issue is read and about to be implemented.
- While working on it, a secondary issue may turn out necessary; do **not** create a new branch, the
  secondary work happens on the working branch.
- Do not open a pull request as a side effect of finishing the work. A PR is only created when the user
  explicitly asks for it, after the main issue and every secondary one are done.

## Language policy

- The project's implementation is always in English: identifiers, code comments, docstrings, commit
  messages and any new documentation, whatever language the instructions were given in.
- The codebase still mixes English and Spanish identifiers and comments (`scjn.header`'s
  `versiones_de_directorio`, `scjn.api`'s `descarga_ordenamiento`, the Spanish CLI help in `scripts/`).
  Leave existing Spanish code as it is when touching unrelated lines, no drive-by renames; new code, and
  code you are rewriting anyway, is English so the codebase converges.
- Domain terms that are proper nouns or established Mexican legal vocabulary (`DOF`, `codNota`, `SIDOF`,
  `NOM-...` codes, law abbreviations like `cpeum`/`lft`) are names, not translated.

## Working conventions

- Naming favors precision over brevity: `codNota`, `legal_provisions`, `download_scjn_leyes_corpus` are the
  domain's vocabulary (a DOF "nota" is a legal provision), not to be shortened for its own sake.
- Comments often record a specific past incident or a numeric check that justified a design choice (why
  NOM codes are opaque strings, why treaty-name matching is rarity-weighted, why a version floor is
  pinned): read them before "simplifying" the code they are attached to.
- `nota2md`'s HTML path (`beautifulsoup4` + `dofjson` + `requests`) works standalone and is the default
  DOF source; the OCR paths depend on `document2md` and `mineru`, which are heavy, so `document2md` is an
  optional extra imported lazily.
- A behaviour that moves a document's output is proved, not argued: by digests over every snapshot and
  fixture, by `pyarrow.Table.equals` against the published vectors, or by the release sweep.
- Decisions that outlive an issue are recorded here as a present-tense bullet; the narrative of how they
  were reached goes to `docs/HISTORY.md`.

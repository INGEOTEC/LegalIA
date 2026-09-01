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
- **`nota2md`** — nine entry points, all re-exported off the package:
  `legal_provisions` (one note → Markdown, `legal_provisions(codNota)` with no
  other argument writing into `nota2md.cache.CACHE_DIR` and returning the
  `Path` (issue #165); **by default the SCJN's
  consolidated text of the whole law at that reform** when the `scjn-leyes`
  release covers the `codNota`, else the DOF's own HTML/image/PDF source —
  `source="dof"` forces the original source; issue #117),
  `reconstruct_legal_provisions` (a law's current text, replayed from its own
  reform decrees), `download_legal_provisions_provenance_ids` (a law's reform
  history, from the `historial-legislativo` release),
  `fetch_daily_legal_provisions` (a whole day's legal provisions as one flat
  list, each naming its edition — re-exported from `dofjson.api`, which is
  where it lives since issue #180 collapsed it with
  `legal_provisions_of_day`),
  `get_document` (a `get_nota` record whose `cadenaContenido` is Markdown —
  the one note→Markdown step, issue #170),
  `legal_provisions_titles` (re-exported from `dofjson.titulos`),
  `download_scjn_leyes_corpus`/`download_scjn_leyes_index`/
  `download_scjn_leyes_catalog` (the `scjn-leyes` release's readers — the last
  one is the federal-law catalogue, `abrev`+`nombre` off the index and
  `actualizado` off each law's `estado.json`, which is the LeyesBiblio seed
  read back rather than rebuilt; issue #185). Its release assets are cached on disk under
  `nota2md.cache.CACHE_DIR` — `nota2md`'s own directory, deliberately not
  `dofjson`'s. The SCJN corpus behind `scjn-leyes` is crawled through the
  SCJN's **SCOW JSON API** (`nota2md.scjn_api`, issue #172); the legacy
  WebForms Buscador crawler was removed in #179, so `nota2md.scjn` now holds
  only what is not transport — catalogue slugs, crawl state, the provenance
  header's reader, the snapshot→`codNota` link, and the release's readers.
  The API is public but has no stability contract, and the SCJN is still not
  an official source of legal text: `fuente: scjn` means what it always did,
  the DOF/SIDOF remains the official source.
- **`dof2md`** — OCRs a PDF or a set of scanned images to Markdown via
  `mineru`. Has no notion of "note"/"legal provision", and no download of
  its own — it only ever converts a PDF/images already on disk; getting a
  whole DOF edition's PDF by date and edition is `dofjson.download_edicion_pdf`'s
  job now (issue #134). `BatchConverter` keeps one `mineru-api` server warm
  across a batch instead of paying startup cost per document; `nota2md`'s
  image/PDF OCR paths accept an already-`__enter__`'d instance via their own
  `converter` parameter. Only needed as `nota2md`'s OCR fallback for legal
  provisions predating the HTML era (pre-1999ish) — a modern note only needs
  `dofjson` + `nota2md`.
- **`leyesmx`** (*being retired* — see "Sources: SCJN + DOF only" below) —
  joins the Cámara de Diputados' LeyesBiblio (which decree
  reformed which law) with DOF `codNota`s, for laws, regulations (NOMs need
  no second source — the DOF title contains the NOM's own code), and
  international treaties (paired by rarity-weighted name similarity, since
  no authoritative source exists at all).
- **`md2akn`** — segments a Mexican federal law's Markdown (what `nota2md`
  produces) into a navigable hierarchy — articles inside their chapter,
  fracciones inside their article — labelled with Akoma Ntoso's *vocabulary*
  (`akn_type`, `eId`, `refersTo`). It reads Markdown and has no dependency on
  the other packages, so it comes last in the read order. The project emits no
  Akoma Ntoso XML anywhere: only the vocabulary is borrowed, and the earlier
  XML converter that lived in `nota2md` was removed (issue #168).

**Data is never committed to git.** `leyesmx`'s reform history, `dofjson`'s
notes archive, and downloaded titles datasets all live only in GitHub
releases (`historial-legislativo`, `notas-archivo`) or are `.gitignore`d
local scratch directories (`/output/`, `/notas-archivo/`,
`packages/leyesmx/data/`, `scripts/scjn/`, `scripts/legal_provisions/`).
Read them back via `download_legal_provisions_provenance_ids` /
`legal_provisions_titles` (the latter over the cache `nota2md download
gazette-metadata` populates), never by looking for a file in the repo.

## Sources: SCJN + DOF only (issue #184, in progress)

**Read this section as the direction, not as a description of the tree.** The
Cámara de Diputados code is still present and still working; it goes away only
when phases #189 and #190 land. Until then, what is written above about
`leyesmx` and about `download_legal_provisions_provenance_ids` is what the code
actually does.

The decision: every dependency on the Cámara de Diputados (LeyesBiblio) is
removed, and federal-law functionality rests on the **SCJN** (the SCOW JSON
API, `nota2md.scjn_api`) plus the **DOF/SIDOF** (`dofjson`) alone. LeyesBiblio
was three HTML page layouts whose markup changes without notice, and it was
scraped for one thing: the initial seed of federal legislation — which laws
exist, their name, their abbreviation.

- **The seed is read, not rebuilt.** It is already published: the `scjn-leyes`
  release's `indice-global.json.gz` carries every law's slug and `nombre`, the
  slug *is* the `abrev` (`nota2md.scjn.slug_instrumento`), and each
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
  map, and the collection branches in `nota2md.scjn`/`scjn_api`. The public
  `download_legal_provisions_provenance_ids` is **deleted outright** — no shim,
  no deprecation, no name kept for compatibility (changelog note regardless of
  whether a version bump follows). A law's reform history then lives in the
  `scjn-leyes` release itself (each law's `indice.json`, plus
  `indice-global.json.gz`); no new dataset and no new asset is created for it.
  Consequently the `historial-legislativo` release loses its `leyes.tgz` asset,
  while `reglamentos.tgz`, `normas.tgz` and `tratados.tgz` stay downloadable,
  labelled in the release notes as a frozen record nothing in the repo can
  regenerate.
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
| Fase 5 | #190 | Delete the workflow; update the release, docs and website |

## Commands

Four test files make real network calls and are excluded from routine runs
(CI's `test.yml` does include them by default — check before assuming a
failure there is unrelated):

```bash
pytest packages/nota2md -q --ignore=packages/nota2md/tests/test_leyes_44.py \
    --ignore=packages/nota2md/tests/test_scjn_release_red.py \
    --ignore=packages/nota2md/tests/test_scjn_api_red.py
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
  comments (see `leyesmx`'s `diputados.py`/`normas.py`, and Spanish CLI
  help/script output throughout). Leave existing Spanish code as-is when
  touching unrelated lines — do not do drive-by mass renames. But new code,
  and any code you're already rewriting for other reasons, should be
  written in English so the codebase converges over time.
- Domain terms that are proper nouns or established Mexican legal/legislative
  vocabulary (`DOF`, `codNota`, `SIDOF`, `NOM-...` codes, law abbreviations
  like `cpeum`/`lft`) are not translated — they aren't Spanish-vs-English,
  they're names.

## Working conventions

- Naming favors precision over brevity even when verbose: `codNota`,
  `legal_provisions`, `download_legal_provisions_provenance_ids` are the
  domain's actual vocabulary (a DOF "nota" is a legal provision, not a
  short informal note) — don't shorten it for its own sake.
- Comments in this codebase often record a specific past incident or a
  numeric check that justified a design choice (e.g. why NOM codes are kept
  as opaque strings, why treaty-name matching is rarity-weighted, why a
  version floor is pinned in a `pyproject.toml` dependency) — read them
  before "simplifying" the code they're attached to.
- `dof2md` and `nota2md`'s OCR paths depend on `mineru`, which is heavy;
  `nota2md`'s HTML path (`beautifulsoup4` + `dofjson` + `requests`) works
  standalone and is the preferred/default source — `dof2md` is imported
  lazily so installing `nota2md` alone doesn't pull it in.

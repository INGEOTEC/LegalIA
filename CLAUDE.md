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
  Also builds the compact `codNota`+`titulo`+`fecha` dataset of every legal
  provision ever published, read from the `notas-archivo` GitHub release.
- **`nota2md`** — seven entry points, all re-exported off the package:
  `legal_provisions` (one note → Markdown, `legal_provisions(codNota)` with no
  other argument writing into `nota2md.cache.CACHE_DIR` and returning the
  `Path` (issue #165); **by default the SCJN's
  consolidated text of the whole law at that reform** when the `scjn-leyes`
  release covers the `codNota`, else the DOF's own HTML/image/PDF source —
  `source="dof"` forces the original source; issue #117),
  `reconstruct_legal_provisions` (a law's current text, replayed from its own
  reform decrees), `download_legal_provisions_provenance_ids` (a law's reform
  history, from the `historial-legislativo` release), `fetch_daily_legal_provisions`,
  `download_legal_provisions_titles` (re-exported from `dofjson.titulos`),
  `download_scjn_leyes_corpus`/`download_scjn_leyes_index` (the `scjn-leyes`
  release's readers). Its release assets are cached on disk under
  `nota2md.cache.CACHE_DIR` — `nota2md`'s own directory, deliberately not
  `dofjson`'s. Also has an experimental Akoma Ntoso (OASIS LegalDocML)
  converter in `nota2md/akoma_ntoso.py`.
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
- **`leyesmx`** — joins the Cámara de Diputados' LeyesBiblio (which decree
  reformed which law) with DOF `codNota`s, for laws, regulations (NOMs need
  no second source — the DOF title contains the NOM's own code), and
  international treaties (paired by rarity-weighted name similarity, since
  no authoritative source exists at all).

**Data is never committed to git.** `leyesmx`'s reform history, `dofjson`'s
notes archive, and downloaded titles datasets all live only in GitHub
releases (`historial-legislativo`, `notas-archivo`) or are `.gitignore`d
local scratch directories (`/output/`, `/notas-archivo/`,
`packages/leyesmx/data/`, `scripts/scjn/`, `scripts/legal_provisions/`).
Read them back via `download_legal_provisions_provenance_ids` /
`download_legal_provisions_titles`, never by looking for a file in the repo.

## Commands

Three test files make real network calls and are excluded from routine runs
(CI's `test.yml` does include them by default — check before assuming a
failure there is unrelated):

```bash
pytest packages/nota2md -q --ignore=packages/nota2md/tests/test_leyes_44.py \
    --ignore=packages/nota2md/tests/test_akoma_ntoso_red.py \
    --ignore=packages/nota2md/tests/test_scjn_release_red.py
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
notebook's code. Re-execute explicitly with `quarto render --no-freeze`
(or `--no-freeze` on the single file) and commit the refreshed `_freeze/`.

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

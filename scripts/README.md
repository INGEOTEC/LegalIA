# scripts

Repo-level utilities. They add the package(s) they need (`packages/dofjson`,
`packages/nota2md`) to the import path themselves, so they run straight from
a clone without an editable install first, needing only `requests`. (The
"scjn" extra the SCJN scripts used to need went away with the `.docx` crawl
path in issue #179.)

## `empaqueta_historial.py`

Packs a data directory built by `leyesmx --ley todas|reglamentos|normas|tratados`
into the four tarballs published as assets of the
[`historial-legislativo`](https://github.com/INGEOTEC/LegalIA/releases/tag/historial-legislativo)
release — `leyes.tgz`, `reglamentos.tgz`, `normas.tgz`, `tratados.tgz` — plus a
`SHA256SUMS.txt`. That release is the data's only home; it is never committed
to git, so `--datos` always names a scratch directory built just for the run
(see `nota2md.utils.download_legal_provisions_provenance_ids` to read the release back).

```bash
./scripts/empaqueta_historial.py --datos packages/leyesmx/data --outdir historial
./scripts/empaqueta_historial.py --datos packages/leyesmx/data --verificar historial   # which assets changed
```

The tarballs are **byte-reproducible**: gzip is stamped with mtime 0, members
are added sorted, and their timestamps and ownership are fixed. Identical data
therefore produces an identical file, which is what lets the monthly workflow
tell an unchanged collection from a changed one by comparing bytes rather than
guessing — and what makes `--verificar` meaningful. It exits non-zero when
anything differs.

## SCJN pipeline: `extract_scjn_titles.py` → `fetch_scjn_legislacion.py` → `enlaza_scjn_legislacion.py` → `empaqueta_scjn_leyes.py`

Recovers, from the SCJN's own SCOW JSON API
([legislacion.scjn.gob.mx/consulta/buscador](https://legislacion.scjn.gob.mx/consulta/buscador),
issue #172), the reform-dated Markdown snapshots of a law/reglamento/tratado that
`nota2md.legal_provisions` would otherwise have to OCR, and links each one to
the DOF `codNota` that published it — see
`packages/nota2md/nota2md/scjn.py` for why this is a legitimate source (each
snapshot is a consolidated-text-as-of-that-reform, not just a summary) and
why it is never mistaken for an official DOF/SIDOF Markdown (`fuente: scjn`
header on every file). For `leyes`, the SCJN is the primary reform source —
Diputados' own reform history (`historial`) is never consulted, only used to
know which instruments exist and their `nombre` (issue #123's correction to
the original #105 design).

```bash
./scripts/extract_scjn_titles.py --outdir scjn-legislacion                         # step 0: seed titles

./scripts/fetch_scjn_legislacion.py --outdir scjn-legislacion                      # Fase 1: crawl

nota2md download gazette-metadata                                                  # DOF titles cache
./scripts/enlaza_scjn_legislacion.py --outdir scjn-legislacion                     # Fase 2: match

./scripts/empaqueta_scjn_leyes.py --outdir scjn-legislacion --destino leyes-release  # leyes only: package
```

**Keeping it up to date afterwards is two commands, not four** (issue #148).
The first says what changed and touches the SCJN for nothing; the second runs
the whole chain above for exactly those laws, and exits without effects when
nothing is pending — the expected case most of the time:

```bash
python scripts/fetch_scjn_legislacion.py --outdir scripts/scjn --coleccion leyes --plan
python scripts/fetch_scjn_legislacion.py --outdir scripts/scjn --coleccion leyes --actualiza
```

Then read `MANIFEST.md` — an incremental run lists the laws it actually
rewrote in their own section, so it is a handful of rows — and publish by
hand. That last step stays manual on purpose (issue #115, Hallazgo C):

```bash
cd scripts/scjn/leyes-release
gh release upload scjn-leyes <slug>.tgz indice-global.json.gz \
    SHA256SUMS.txt MANIFEST.md --repo INGEOTEC/LegalIA --clobber
```

The four assets travel together even when a single law changed:
`indice-global.json.gz` is the union of every `indice.json`, so any change
makes the published one stale.

### `extract_scjn_titles.py`

Every other script below reads `<outdir>/<coleccion>/catalogo.json` instead
of calling `download_legal_provisions_provenance_ids` itself — this is the
only script that does. It writes that file: each instrument's own `nombre`,
(when the collection has one) `abrev`, and `actualizado` — Diputados'
`historial` itself never reaches downstream scripts, and the
`historial-legislativo` release is downloaded once per collection instead of
once per script. Re-run it to refresh the catalogue (e.g. after Diputados
adds a new instrument); every downstream script fails fast, naming the exact
command to run, if it hasn't been run yet.

Two fields exist to close coverage gaps a crawl alone cannot (issue #124's
follow-up, "Dos casos disparadores" — a catalogue entry the SCJN's search
returns nothing at all for):

- **`actualizado`** — the ISO date of the instrument's own most recent
  reform (Diputados' `historial`'s own last `codNota`, resolved via
  `dofjson`; one extra request per instrument, so a run of this script is
  noticeably slower/more network-bound than before). `fetch_scjn_legislacion.py`
  uses it to skip re-searching the SCJN, on a refresh, for an instrument
  nothing has changed on since the collection's own last full crawl — see
  that script's own section below.
- **`nombre_scjn`** — an optional manual override: the exact string to
  search the SCJN with instead of `nombre`, for the rare instrument the
  SCJN's own full-text search never finds under Diputados' exact wording.
  Nothing in this script ever sets it — it is added by hand to
  `catalogo.json` — but a re-run now reads back whatever `catalogo.json`
  already exists and carries every entry's own `nombre_scjn` forward
  instead of overwriting the file from scratch, so a manual override
  survives a refresh. Applied so far to `lisipl` (`abrev`), whose `nombre`
  carries a 250+ character trailing parenthetical alternate name the SCJN's
  search never matches.

### `fetch_scjn_legislacion.py`

Fase 1: covers `leyes`, `reglamentos` and `tratados` only — the SCJN does not
catalogue NOM technical standards as ordenamientos of their own (issue
#105's Fase 0). Resumable at two levels — a file already on disk is left
alone, and, per collection, the index of the last instrumento fully
attempted is checkpointed to `<outdir>/<coleccion>/.progreso.json` and
cleared once that collection finishes, so a run killed partway (crash,
network drop, Ctrl-C) picks back up right after that index instead of
re-walking every already-done instrumento's reform table from the top
(`--reiniciar` discards the checkpoint and sweeps the collection from the
beginning again) — and rate-limited (`--espera`, default 1s) against this
unofficial site's own session-scoped URLs. `--reintenta SLUG` (repeatable)
re-downloads only the named instrumentos from scratch, for fixing a handful
of wrong-document matches (issue #115) without re-walking a whole
collection.

Two more mechanisms (issue #124's follow-up) close the case a previous crawl
found nothing at all for — driven entirely by `catalogo.json` fields
`extract_scjn_titles.py` writes, nothing to pass on this script's own command
line:

- **Manual override** — when a catalogue entry carries `nombre_scjn`, it is
  searched instead of `nombre`.
- **Incremental refresh** — once a collection has been crawled
  start-to-finish, that date is recorded to
  `<outdir>/<coleccion>/.rastreo_completo.json`. A later refresh skips an
  instrumento without touching the SCJN only when it already has a snapshot
  on disk *and* its own `actualizado` is no later than that checkpoint — an
  instrumento with no snapshot yet is always retried, so a law the SCJN has
  not indexed yet keeps getting retried automatically on every refresh,
  with nothing to configure by hand once the SCJN catches up.
  `--reiniciar` bypasses this skip too.

Issue #140 fixed two rough edges found running the above for real. The
incremental refresh does nothing, silently, if `catalogo.json` was never
extracted with `actualizado` on it (extracted before that field existed, or
hand-edited) — a run now warns on stderr, once per collection, when it sees
zero `actualizado` entries, naming the exact `extract_scjn_titles.py`
command to fix it. And a large instrumento's own crawl — confirmed live
against the CPEUM, 301 rows across 31 grid pages — used to print nothing at
all while it ran, indistinguishable from a hung process; it now narrates
one line per grid page and per row as it goes.

### `enlaza_scjn_legislacion.py`

Fase 2: pairs each already-downloaded snapshot with the `codNota` of the DOF
note that published it, writing an `indice.json` per instrument directory.
Two signals, always both computed — there is no flag to turn either off,
since a weaker link is never what you actually want:

1. **Title mention** (issue #126): among the DOF notes published the same
   day as a snapshot, only those whose own title explicitly names the
   instrument are candidates; a date links only when exactly one candidate
   remains after excluding whatever an earlier same-day snapshot already
   claimed (`title_link_status`: `linked`/`none`/`claimed`/`ambiguous`).
2. **Content-diff confirmation** (issue #127): for each candidate with
   digital DOF text, whether its own content actually accounts for what
   changed between this SCJN snapshot and the previous one — resolving the
   `ambiguous` case title mention alone cannot. Needs each candidate's own
   DOF Markdown, fetched via `dofjson` and saved under each instrument's own
   `notas` subdirectory (`<outdir>/<coleccion>/<slug>/notas`, created
   automatically) — next to that instrument's own `indice.json`, not in a
   directory shared across instruments. Kept, not scratch: issue #128 ships
   them inside each instrument's own tarball.

Needs the notas-archivo cache populated (`nota2md download gazette-metadata`):
the DOF titles (`codNota`+`titulo`+`fecha`) are streamed off it with
`dofjson.legal_provisions_titles` to find each same-day candidate.

Issue #132 retired the offline `audita_scjn_legislacion.py` script that used
to exist here: `_cabecera` now writes `nombre_buscado` only when it differs
from `ordenamiento` after normalizing (accents/case/whitespace), so a
snapshot whose title is worth a second look is visible directly in the file
— `grep -l nombre_buscado: scjn-legislacion/**/*.md` finds the same
instruments that script used to print, without recomputing anything or
needing `catalogo.json` at hand. `ratio_similitud`/`sospechoso` (issue #115)
are unaffected and still always present, giving the magnitude of how far
off a flagged title is.

### Caso aislado: `fetch_lfiiedb_dof.py`

A catalogue entry the SCJN does not index at all (issue #124's coverage
gaps), closed by its own single-law script rather than by a general
mechanism. It is **isolated**: it touches no catalogue, no checkpoint and no
general script — it only writes inside `<outdir>/leyes/<abrev>/` — and it
runs **after** `enlaza_scjn_legislacion.py`, since it owns the last word on
its own `indice.json` (the normal sweep would otherwise overwrite it). The
script's own module docstring carries the full, web-page-ready procedure;
read it there.

```bash
./scripts/fetch_lfiiedb_dof.py --outdir scripts/scjn
```

- **`fetch_lfiiedb_dof.py`** (issue #145) — a brand-new law with no reform
  history at all. One `codNota` (5784517, DOF 09-04-2026), one file, an
  `indice.json` whose link is known by construction instead of inferred.

It writes `fuente: dof` in the header of every file taken from the DOF —
`grep -rl 'fuente: dof' scripts/scjn/` is how these exceptions are found.
The script disappears when the SCJN finally indexes its law: delete the
directory, run `fetch_scjn_legislacion.py --reintenta lfiiedb` plus
`enlaza_scjn_legislacion.py`, and retire the script.

**Retirado: `construye_lfca.py`** (issue #144, retired in #179). It was the
reference case for *a new law that abrogates another and is not indexed
yet*: it built `lfca`'s corpus in two halves, its reform history from the
abrogated **LEY FEDERAL DE CINEMATOGRAFIA** — which the old Buscador *did*
index — plus its current text from the DOF (`codNota` 5788357). Exactly the
disappearance described above then happened: the SCJN's new index has the
LEY FEDERAL DE CINE Y EL AUDIOVISUAL as an ordenamiento of its own
(`idOrdenamiento` 188805, issue #172), the ordinary crawl reaches it, and
the script's own transport (the WebForms search, grid and `.docx` download)
no longer exists. One consequence is recorded in issue #178: `lfca` now
holds only the successor law's snapshots — keeping the predecessor's would
mean giving it a slug of its own, which is a catalogue decision and its own
issue.

### `empaqueta_scjn_leyes.py`

`leyes` only (issue #128): packages every crawled+linked instrument into its
own byte-reproducible `<slug>.tgz` — one asset per law, not one for the
collection, so a consumer downloads only the law it wants and an update only
re-uploads the law that changed. Each tarball carries that instrument's
snapshots, its `indice.json` and its `notas/nota-<cod>.md` (the DOF text
every link was decided against, #126/#127), all prefixed with `<slug>/`.
Also writes a `MANIFEST.md` listing every instrument by name
(`enlaza_scjn_legislacion.py`'s link percentage and content-diff-confirmed
count, plus each instrument's asset name, compressed size and DOF-note
count; issue #115's ratio/classification is gone from it — the titles were
reviewed and fixed by hand) and a `SHA256SUMS.txt` with
one line per asset — same pattern as `empaqueta_historial.py`.

Alongside the tarballs it writes **`indice-global.json.gz`** (issue #117):
the union of every `indice.json`, inverted by `codNota` and stripped of all
text, so `nota2md` can answer "which law does this decree reform, and which
snapshot is it" for a few hundred KB instead of the corpus' 380 MB. Only
snapshots with a `codNota` we are certain of go in; the manifest reports how
many entered and how many stayed out by motive (`ambiguous`, `unlinked`,
`sin_indice`). `--sin-indice-global` skips writing it. **This asset has to be
re-uploaded every time any law changes** — it is the union of all of them, so
one updated law makes the published index stale, and a stale index resolves a
`codNota` to a snapshot file that is no longer in the tarball.

`nota2md.download_scjn_leyes_corpus(slug)` reads one law back and
`nota2md.download_scjn_leyes_index()` reads the reverse index. Both
paths default to where the corpus actually lives today (`--outdir
scripts/scjn`, `--destino scripts/scjn/leyes-release`), so the usual run
takes no arguments at all.
**Publishing is manual, always**:
the SCJN's search can return a wrong document entirely (issue #115), so this
script never calls `gh`, and no workflow should ever call it and then
publish on its own. Read `MANIFEST.md` in full before running the `gh
release create`/`upload` command the script prints.

### Retirado: `repara_notas_editoriales_scjn.py`

Issue #129 retired the one-time `repara_notas_editoriales_scjn.py` that used
to live here. It re-processed what `fetch_scjn_legislacion.py` had downloaded
*before* the crawl started stripping the SCJN's own
editorial commentary ("N. DE E." / "NOTA N", issue #114) at crawl time —
a migration, not a step of the pipeline. Run over the whole published corpus
its `--dry-run` now reports `0 parrafo(s) de nota editorial se quitarian en 0
archivo(s)`: it has nothing left to repair anywhere, and every snapshot a new
crawl writes is already stripped at the source. `quita_notas_editoriales`
stays idempotent over already-clean, already-bolded output, so re-running the
repair was never the thing keeping the corpus correct.

## `reparar_notas_archivo.py`

Refills the days SIDOF lost into the published
[`notas-archivo`](https://github.com/INGEOTEC/LegalIA/releases/tag/notas-archivo)
assets, taking them from `www.dof.gob.mx`.

SIDOF answers `200 OK` with no legal provisions for a day it is missing — the
same answer it gives for a Sunday — so those days were archived as empty. The
script walks the published assets, finds every **weekday** stored with no
legal provisions, asks the DOF website whether the gazette actually came out,
and rewrites only those days. Everything else is copied through untouched:
same member names, order, mode, ownership and mtime.

```bash
./scripts/reparar_notas_archivo.py --anios 1999,2006 --dry-run   # report only
./scripts/reparar_notas_archivo.py --anios auto                  # every asset
./scripts/reparar_notas_archivo.py --anios 1999,2000,2001,2004,2005,2006,2007 \
    --outdir reparados
```

| Flag | |
|---|---|
| `--anios` | Years to check, comma-separated, or `auto` for every asset |
| `--outdir` | Where to write the rebuilt `.tgz` (default `reparados/`) |
| `--dry-run` | Report what would change; write nothing |

Rebuilt assets land in `--outdir` together with a `reparacion.json` listing
what changed. The gzip header is stamped with mtime 0, so the same inputs
always produce the same bytes and checksums stay comparable across runs.

Two things are checked rather than assumed. A recovered day is accepted only
when the page's printed date matches the one requested — the site has been
seen answering with another day's page under concurrency. And after writing,
the tarball is read back and the legal provision counts re-verified.

Recovered days carry `"fuente": "dof.gob.mx"`, on the day and on each legal
provision; SIDOF's days carry no marker. They also carry `notasIncompletas`:
the DOF website's index does not list convocatorias (`CV`, `VG`) or avisos
(`AV`).

### Publishing

The script never uploads and never needs a token. To publish:

```bash
gh release upload notas-archivo reparados/*.tgz --clobber
```

`--clobber` replaces each asset in place, so the release never sits with a
missing file the way delete-then-upload would.

# scripts

Repo-level utilities. They add the package(s) they need (`packages/dofjson`,
`packages/nota2md`) to the import path themselves, so they run straight from
a clone without an editable install first, needing only `requests`. (The
"scjn" extra the SCJN scripts used to need went away with the `.docx` crawl
path in issue #179.)

## `check_package_versions.py`

Checks every publishable package's local version (`pyproject.toml`'s dynamic
`<pkg>.__version__` attribute, read straight from source without importing
the package) against the latest PyPI has published, and fails if the local
version is not a valid single-step jump ahead — the next patch, or the next
minor with patch reset to 0 (issue #194). A package PyPI has never published
(`md2akn` as of this writing) always passes: there is nothing to jump ahead
of yet.

```bash
python scripts/check_package_versions.py
python scripts/check_package_versions.py --package dofjson --package nota2md
```

Wired into `test.yml` as its own `check-package-versions` job, but with
`continue-on-error: true`: `dofjson` and `nota2md` are already two minor
versions ahead of PyPI, so making this a hard gate today would fail every PR
until that drift is reconciled by an actual release — a release decision,
not this script's.

## A law's reform history lives in the `scjn-leyes` release

There used to be an `empaqueta_historial.py` here, packing four tarballs of
Cámara de Diputados-derived data into the
[`historial-legislativo`](https://github.com/INGEOTEC/LegalIA/releases/tag/historial-legislativo)
release. Issue #187 deleted it along with `download_legal_provisions_provenance_ids`,
the reader that went with it: the project rests on the SCJN and the DOF alone
now (#184), and **no new dataset replaces it**. A law's reform history *is*
the `scjn-leyes` release — each law's own `indice.json`, one entry per reform
with the `codNota` that published it, plus `indice-global.json.gz` inverting
that by `codNota`. `empaqueta_scjn_leyes.py` below is what builds it.

`historial-legislativo`'s `reglamentos.tgz`, `normas.tgz` and `tratados.tgz`
stay downloadable as a frozen record, labelled as such by name and last build
date in the release notes (`.github/historial-legislativo.md`, which *is* the
release body). Nothing in this repo can regenerate them, no workflow
republishes them, and `leyes.tgz` was **deleted from the release** in issue
#190 with `SHA256SUMS.txt` regenerated over the three that remain, so the
`sha256sum -c` the notes tell a reader to run still passes.

The monthly workflow that used to rebuild all four
(`.github/workflows/reformas.yml`) is deleted too, and **nothing replaces
it**: the surviving collection is SCJN-sourced, and SCJN-sourced data is
never published by a workflow (issue #115, Hallazgo C — the Court's own
search can return a completely wrong document, so a human decides what is
safe to publish). The two things that workflow carried which are still worth
having are documented below, as commands rather than automation: the
**seeding step** and the **byte-reproducibility** of the tarballs.

### "Reform N" is redefined

The old dataset's key invariant was *index N is reform N*, with Diputados
doing the numbering. That numbering is gone with its source, and is
**replaced, not reproduced**: a reform's number is now its position in the
law's own `indice.json`, which is the chronological order of the SCJN's own
reform table.

The two are not interchangeable and the difference is not a rounding error.
Diputados' reform column also filed errata (`_fe`), peso restatements
(`_cant`), Court rulings (`_sent`, `_voto`) and entry-into-force
declarations; the SCJN's table has its own notion of what counts as a reform.
Where the counts differ, every number after the difference shifts. Anything
that recorded "reform 139 of the Constitution" against the old dataset has to
be re-resolved against the new one by *date*, not by number. No measurement
against the old numbering exists or will: the source that produced it is no
longer consulted.

## `regenera_fixtures_leyes.py`

Rebuilds `reconstruct_legal_provisions()`'s ground-truth fixtures —
`packages/nota2md/tests/fixtures/leyes/<abrev>.md` and `historial_44.json`,
what `tests/test_leyes_44.py` reads — from the `scjn-leyes` release. Nothing
is crawled: it reads the published corpus back.

```bash
./scripts/regenera_fixtures_leyes.py             # report what it would write
./scripts/regenera_fixtures_leyes.py --escribe
```

Until issue #188 those fixtures were the Cámara de Diputados' consolidated
"texto vigente" PDF, cleaned up by `nota2md.texto_vigente`, deleted with them.
The replacement is the SCJN's own consolidated text at each law's most recent
reform, plus that law's `indice.json` as the reform history to replay — the
two written together, so the history replayed ends at exactly the reform the
fixture is the text of.

**These ~3 MB of fixtures are the one exception to "data is never committed to
git"** (`CLAUDE.md`): they are test fixtures, and freezing them is the point —
a regression test of a replay algorithm should not change its answer because a
law was reformed. This script is what keeps that freeze reproducible.

A law whose history the corpus cannot fully resolve is excluded and reported
(`lfgr` today: three `ambiguous` snapshots, none content-diff confirmed).
Replaying a history with a hole measures the hole, not the algorithm. See the
test module's own docstring for what the check does and does not establish —
in particular that the ground truth is no longer independent of the SCJN.

## SCJN pipeline: `extract_scjn_titles.py` → `fetch_scjn_legislacion.py` → `enlaza_scjn_legislacion.py` → `empaqueta_scjn_leyes.py`

Recovers, from the SCJN's own SCOW JSON API
([legislacion.scjn.gob.mx/consulta/buscador](https://legislacion.scjn.gob.mx/consulta/buscador),
issue #172), the reform-dated Markdown snapshots of a federal law that
`nota2md.legal_provisions` would otherwise have to OCR, and links each one to
the DOF `codNota` that published it — see
`packages/nota2md/nota2md/scjn.py` for why this is a legitimate source (each
snapshot is a consolidated-text-as-of-that-reform, not just a summary) and
why it is never mistaken for an official DOF/SIDOF Markdown (`fuente: scjn`
header on every file). The SCJN is the primary reform source, and since issue #186 nothing in this
pipeline touches the Cámara de Diputados at all: which laws exist and their
`nombre` are read back off the `scjn-leyes` release, and `actualizado` comes
from the SCJN's own reform table and the DOF's titles (issue #184).

```bash
./scripts/extract_scjn_titles.py --outdir scjn-legislacion                         # step 0: seed titles

./scripts/fetch_scjn_legislacion.py --outdir scjn-legislacion                      # Fase 1: crawl

nota2md download gazette-metadata                                                  # DOF titles cache
./scripts/enlaza_scjn_legislacion.py --outdir scjn-legislacion                     # Fase 2: match

./scripts/empaqueta_scjn_leyes.py --outdir scjn-legislacion --destino leyes-release  # leyes only: package
```

**Keeping it up to date afterwards is two commands, not four** (issue #148).
The first says what changed and, since #186, costs one reform-table request
per law to refresh `actualizado` (`--sin-refrescar-catalogo` skips that); the second runs
the whole chain above for exactly those laws, and exits without effects when
nothing is pending — the expected case most of the time:

```bash
python scripts/fetch_scjn_legislacion.py --outdir scripts/scjn --plan
python scripts/fetch_scjn_legislacion.py --outdir scripts/scjn --actualiza
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

Writes `<outdir>/leyes/catalogo.json` — each law's `nombre`, `abrev` and
`actualizado` — and every other script below reads that file instead of
going to a source of its own. Re-run it to refresh the catalogue; every
downstream script fails fast, naming the exact command, if it has not been
run yet.

Since issue #186 it makes no request to `diputados.gob.mx` and has no
`--coleccion` flag: `leyes` is the only collection left (#184), so `abrev`
is always present.

```bash
./scripts/extract_scjn_titles.py --outdir scripts/scjn
./scripts/extract_scjn_titles.py --outdir scripts/scjn --dof-only   # offline
./scripts/extract_scjn_titles.py --outdir scripts/scjn --discover   # report only
```

- **`nombre` and `abrev`** come from the `scjn-leyes` release itself
  (`download_scjn_leyes_catalog` over `indice-global.json.gz`) — the seed
  Diputados used to give has been published all along as a by-product of the
  corpus, so it is read, not rebuilt. The file is written **sorted by
  `slug_instrumento`**, the same order the release's index has; Diputados'
  own index order is gone with its source. An existing `abrev` is carried
  over **verbatim**, which is why the previous catalogue is matched by slug
  and not by `abrev`: `lif_2026` in the catalogue is `lif-2026` in the
  release, for 14 laws. The previous `catalogo.json` is the **floor** — a law
  missing from the index is kept and reported, never dropped.
- **`actualizado`** — the ISO date of the law's own most recent reform, and
  the whole input to `fetch_scjn_legislacion.py`'s planner. It is the
  **newest** of two independent answers, because each misses what the other
  sees: the SCJN's own reform table (`reformas_of_ordenamiento`, addressed by
  the `id_ordenamiento` the law's `estado.json` already records) and the
  newest DOF provision whose title both names the law and opens with
  DECRETO/LEY. Measured over the 316-law catalogue: the DOF alone under-dates
  **91** laws, because an omnibus decree reforms dozens of laws without
  naming any of them; the SCJN alone silently freezes a law it has not
  indexed yet, which is the `lfca` case. A law neither can date keeps
  `actualizado` **absent** — never a placeholder, since absent is what the
  planner reads as "always re-check". `--dof-only` skips the SCJN half and
  makes the run offline, at the cost of those 91.
- **`nombre_scjn`** — an optional manual override: the exact string to search
  the SCJN with instead of `nombre`, for the rare law the SCJN's own
  full-text search never finds under its catalogue wording. Nothing here ever
  sets it — it is added by hand — and a re-run reads back the existing
  `catalogo.json` and carries every entry's own `nombre_scjn` forward, so the
  override survives. Applied so far only to `lisipl`, whose `nombre` carries
  a 250+ character trailing parenthetical alternate name.

`--discover` reports federal laws the SCJN lists and the catalogue does not,
then stops without writing anything: a new federal law is a handful a year,
and an `abrev` is a release asset name, so adding one is a human's decision.
Each candidate must be confirmed by a DOF title that names it and opens with
DECRETO/LEY — without that the SCJN's own `CODIGO` category alone contributes
~180 "CÓDIGO DE CONDUCTA DE ..." administrative documents. A suggested
`abrev` is printed with each candidate (`nota2md.scjn.mint_abrev`: initials
of the name's meaningful words, `-2`/`-3` on collision); it is written down
once and never recomputed, since re-minting one renames that law's release
asset and orphans it.

### `fetch_scjn_legislacion.py`

Fase 1: covers `leyes`, the only collection left (issue #189) — which is
why `leyes` is a literal path segment in these scripts and no longer
something to pass on the command line. Resumable at two levels — a file
already on disk is left alone, and the index of the last instrumento fully
attempted is checkpointed to `<outdir>/leyes/.progreso.json` and
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
  `<outdir>/leyes/.rastreo_completo.json`. A later refresh skips an
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
   `notas` subdirectory (`<outdir>/leyes/<slug>/notas`, created
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
one line per asset.

Alongside the tarballs it writes **`indice-global.json.gz`** (issue #117):
the union of every `indice.json`, inverted by `codNota` and stripped of all
text, so `nota2md` can answer "which law does this decree reform, and which
snapshot is it" for a few hundred KB instead of the corpus' 380 MB. Only
snapshots with a `codNota` we are certain of go in; the manifest reports how
many entered and how many stayed out by motive (`ambiguous`, `unlinked`,
`sin_indice`). `--sin-indice-global` skips writing it. Since issue #187 an entry linked by content diff rather than by title carries `title_link_status: "content_diff"` and goes into the index like any other link — that is 834 more snapshots across 188 laws, taking the collection from 2,457 linked to 3,291 of 3,707. **This asset has to be
re-uploaded every time any law changes** — it is the union of all of them, so
one updated law makes the published index stale, and a stale index resolves a
`codNota` to a snapshot file that is no longer in the tarball.

#### Seeding, and why an incremental run needs it

`--instrumento` rewrites only the named law's tarball; every other law is
measured from the `.tgz` already sitting in `--destino`, and repackaged from
`--outdir` when there is none there. So two things have to be whole before an
incremental publish, and both are the seeding step the retired workflow used
to perform on a fresh runner:

```bash
# 1. --destino seeded with what the release already serves, so the assets this
#    run does not touch stay exactly as published and still get measured
gh release download scjn-leyes --repo INGEOTEC/LegalIA \
    --dir scripts/scjn/leyes-release --clobber

# 2. --outdir holding the whole corpus, not one law: SHA256SUMS.txt and
#    indice-global.json.gz are recomputed over everything on disk, so packing
#    a partial corpus publishes a partial index
./scripts/empaqueta_scjn_leyes.py --instrumento lft
```

Skipping step 1 is not silent — the run reports every law it rewrote, and a
run that rewrote all 315 when one law changed is the symptom.

#### Byte-reproducibility, and how to check it

The tarballs are byte-reproducible on purpose (gzip stamped with mtime 0,
members added in sorted order, mode/ownership/times fixed), which is what
makes "re-upload only what changed" a byte comparison instead of a guess.
Verified in issue #190 by packaging the same two laws twice into different
destinations: `lft.tgz` (10.2 MB, 55 snapshots) and `cnpcf.tgz` came out
byte-identical.

```bash
./scripts/empaqueta_scjn_leyes.py --destino /tmp/verifica-reproducibilidad
cmp /tmp/verifica-reproducibilidad/lft.tgz scripts/scjn/leyes-release/lft.tgz
```

`MANIFEST.md`, `SHA256SUMS.txt` and `indice-global.json.gz` are **not** in
that guarantee: the index carries its own `generado` timestamp and the
checksum file covers it, so both differ between two runs over identical data.
The tarballs are the byte-identical ones, and they are the ones an
"upload only what changed" step compares.

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

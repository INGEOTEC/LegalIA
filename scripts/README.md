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

Wired into `test.yml` as its own `check-package-versions` job, and it is a
blocking gate: the job fails the workflow whenever a package's `__version__`
is not a valid single-step jump ahead of PyPI.

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

## SCJN pipeline: `fetch_scjn_legislacion.py` → `enlaza_scjn_legislacion.py` → `empaqueta_scjn_leyes.py`

Recovers, from the SCJN's own SCOW JSON API
([legislacion.scjn.gob.mx/consulta/buscador](https://legislacion.scjn.gob.mx/consulta/buscador),
issue #172), the reform-dated Markdown snapshots of a federal law that
`nota2md.legal_provisions` would otherwise have to OCR, and links each one to
the DOF `codNota` that published it — see
`packages/scjn/README.md` for why this is a legitimate source (each
snapshot is a consolidated-text-as-of-that-reform, not just a summary) and
why it is never mistaken for an official DOF/SIDOF Markdown (`fuente: scjn`
header on every file). The SCJN is the primary reform source, and since issue #186 nothing in this
pipeline touches the Cámara de Diputados at all: which laws exist and their
`nombre` are read back off the `scjn-leyes` release, and `actualizado` comes
from the SCJN's own reform table and the DOF's titles (issue #184).

Since issue #210 there is no single `catalogo.json` seeding the whole
pipeline any more: each law's own `estado.json`, inside its own
`<outdir>/leyes/<slug>/` directory, is the one per-law record
(`abrev`/`nombre`/`nombre_scjn`/`id_ordenamiento`/`url`/`actualizado_scjn`/
`actualizado_dof`/`actualizado`/`rastreado`/`enlazado`). A brand-new law is
seeded by hand-writing that directory's `estado.json` with at least
`abrev`/`nombre` before its first crawl — `scripts/discover_federal_laws.py`
finds the candidate and prints both to copy in; the existing corpus needs
nothing extra to keep going.

```bash
./scripts/fetch_scjn_legislacion.py --outdir scjn-legislacion                      # Fase 1: crawl

nota2md download gazette-metadata                                                  # DOF titles cache
./scripts/enlaza_scjn_legislacion.py --outdir scjn-legislacion                     # Fase 2: match

./scripts/empaqueta_scjn_leyes.py --outdir scjn-legislacion --destino leyes-release  # leyes only: package
```

**Keeping it up to date afterwards is two commands, not three** (issue
#148). The first refreshes every known law's own `actualizado_scjn`/
`actualizado_dof` (one reform-table request per law;
`--sin-refrescar-catalogo` skips that) and, by default, diffs each law's
own reform table row by row against its snapshots (issue #211 — another
request per law with an `id_ordenamiento`, `--solo-fecha` skips this and
falls back to a date comparison) to say what changed; the second runs the
whole chain above for exactly those laws, and exits without effects when
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

### `discover_federal_laws.py`

Reports federal laws the SCJN lists that the corpus does not have yet, then
stops without writing anything (issue #210 — the one part of the retired
`extract_scjn_titles.py --discover` with no replacement): a new federal law
is a handful a year, and an `abrev` is a release asset name, so adding one is
a human's decision.

```bash
./scripts/discover_federal_laws.py
```

Each candidate must be confirmed by a DOF title that names it and opens with
DECRETO/LEY — without that the SCJN's own `CODIGO` category alone contributes
~180 "CÓDIGO DE CONDUCTA DE ..." administrative documents. A suggested
`abrev` is printed with each candidate (`scjn.catalog.mint_abrev`: initials
of the name's meaningful words, `-2`/`-3` on collision); it is written down
once, by hand, into the new law's own `<outdir>/leyes/<slug>/estado.json`
(`abrev`/`nombre`) before its first crawl, and never recomputed, since
re-minting one renames that law's release asset and orphans it.

### `fetch_scjn_legislacion.py`

Fase 1: covers `leyes`, the only collection left (issue #189) — which is
why `leyes` is a literal path segment in these scripts and no longer
something to pass on the command line. Every subdirectory of
`<outdir>/leyes/` is a law (issue #210 retired the separate `catalogo.json`
that used to enumerate them); `nombre`/`abrev`/`nombre_scjn` come from that
law's own `estado.json`, falling back to `indice-global.json.gz` for
`nombre` when a directory predates issue #210 or was just seeded by hand.
`actualizado` is computed live, as the newer of `actualizado_scjn`/
`actualizado_dof` (issue #211's `_load_catalog`) — deliberately not read
verbatim off `estado.json`'s own `actualizado` key, which means something
else: `rastrea_coleccion` writes *that* one at crawl time, to record what a
law's snapshots were actually crawled against, and the date-only fallback
below compares the two. Resumable at two levels — a file already on disk is
left alone, and the **slug** of the last instrumento fully attempted is
checkpointed to `<outdir>/leyes/.progreso.json` and cleared once that
collection finishes, so a run killed partway (crash, network drop, Ctrl-C)
picks back up right after that law instead of re-walking every already-done
instrumento's reform table from the top (`--reiniciar` discards the
checkpoint and sweeps the collection from the beginning again) — and
rate-limited (`--espera`, default 1s) against this unofficial site's own
session-scoped URLs. `--reintenta SLUG` (repeatable) re-downloads only the
named instrumentos from scratch, for fixing a handful of wrong-document
matches (issue #115) without re-walking a whole collection.

Two more mechanisms (issue #124's follow-up) close the case a previous crawl
found nothing at all for — driven entirely by fields `refresca_catalogo()`
(below) writes into each law's own `estado.json`, nothing to pass on this
script's own command line:

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

`refresca_catalogo()` (`--plan`/`--actualiza`, unless
`--sin-refrescar-catalogo`) is what folds `extract_scjn_titles.py`'s old
refresh into this script (issue #210): it reads every law's own
`estado.json`, asks the SCJN's reform table (`--dof-only` skips this half
and makes the refresh offline, at the cost of the omnibus-decree laws only
the SCJN half dates) and the DOF titles cache for a fresh
`actualizado_scjn`/`actualizado_dof`, and writes only those two fields back
— never the bare `actualizado`, which stays `rastrea_coleccion`'s own (see
above); a law this run's sources did not answer for keeps whichever value
its own `estado.json` already carried, since neither source's date can move
backwards in practice.

### Completeness by row comparison (issue #211)

A date comparison alone cannot see a gap in the *middle* of a reform table:
`lfd` had 92 snapshots against 98 reforms, and the newest reform was always
on disk, so nothing about "the newest date on file" ever looked stale
(issue #178). `--plan`/`--actualiza` default to a **row comparison**
instead: for every law with an `id_ordenamiento` already on file, one
request fetches its whole reform table
(`scjn.api.ScjnApi.reformas_of_ordenamiento`, addressed by that stable id —
never a search, so it can never resolve to the wrong document, issue #115's
Hallazgo C) and `scjn.state.reformas_faltantes` diffs it against the
snapshots on disk, matching each reform by its own position among
same-date rows rather than by date alone (two reforms sharing a date, like
39 dates on the CPEUM, must not cancel each other out).

A full plan is therefore ~316 requests — the largest burst this project
makes outside a full crawl — rate-limited by `--espera` exactly like a
crawl, and resumable the same way
(`<outdir>/leyes/.progreso_plan.json`). `--solo-fecha` is the explicit,
offline fallback: the older date comparison above, for a quick local
refresh rather than a run whose plan anyone acts on. A law with no
`id_ordenamiento` yet (never crawled, or crawled before issue #172) is not
skipped even in row-comparison mode — it falls back to the date comparison
for that one law alone, and `--plan` reports it separately as having done
so.

`actualizado_dof`/`actualizado_scjn` are not replaced by any of this: row
comparison catches a *gap* in an indexed table, and the DOF half is still
the only thing that notices a reform the SCJN has not indexed at all yet
(issue #124's `lfca`) — a row comparison against a table that does not have
the reform cannot see it either.

    # el plan real, contra la SCJN (issue #211, ~316 requests, resumable):
    ./scripts/fetch_scjn_legislacion.py --outdir scripts/scjn --plan
    # el respaldo offline, mas barato y menos preciso:
    ./scripts/fetch_scjn_legislacion.py --outdir scripts/scjn --plan --solo-fecha

Issue #140 fixed two rough edges found running the above for real. The
incremental refresh does nothing, silently, if the corpus was never
refreshed under `refresca_catalogo()` (no law crawled yet, or hand-seeded) —
a run now warns on stderr, once per collection, when it sees zero
`actualizado` entries, naming the exact `--plan` command to fix it. And a
large instrumento's own crawl — confirmed live against the CPEUM, 301 rows
across 31 grid pages — used to print nothing at all while it ran,
indistinguishable from a hung process; it now narrates one line per grid
page and per row as it goes.

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
needing the catalogue at hand. `ratio_similitud`/`sospechoso` (issue #115)
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

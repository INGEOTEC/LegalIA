# scripts

Repo-level utilities. They add the package(s) they need (`packages/dofjson`,
`packages/nota2md`) to the import path themselves, so they run straight from
a clone without an editable install first — the SCJN scripts below also need
the "scjn" extra (`pip install "packages/nota2md[scjn]"`, for `python-docx`);
everything else needs only `requests`.

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

## SCJN pipeline: `extract_scjn_titles.py` → `fetch_scjn_legislacion.py` → `enlaza_scjn_legislacion.py` → `audita_scjn_legislacion.py` / `empaqueta_scjn_leyes.py`

Recovers, from [legislacion.scjn.gob.mx](https://legislacion.scjn.gob.mx/Buscador/),
the reform-dated Markdown snapshots of a law/reglamento/tratado that
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

python -c "from nota2md import download_legal_provisions_titles as d; d('titulos.jsonl.gz')"
./scripts/enlaza_scjn_legislacion.py --outdir scjn-legislacion --titulos titulos.jsonl.gz  # Fase 2: match

./scripts/audita_scjn_legislacion.py --outdir scjn-legislacion                     # offline audit
./scripts/empaqueta_scjn_leyes.py --outdir scjn-legislacion --destino leyes-release  # leyes only: package
```

### `extract_scjn_titles.py`

Every other script below reads `<outdir>/<coleccion>/catalogo.json` instead
of calling `download_legal_provisions_provenance_ids` itself — this is the
only script that does. It writes that file: each instrument's own `nombre`
and (when the collection has one) `abrev`, nothing else — Diputados'
`historial` never reaches it, and the `historial-legislativo` release is
downloaded once per collection instead of once per script. Re-run it to
refresh the catalogue (e.g. after Diputados adds a new instrument); every
downstream script fails fast, naming the exact command to run, if it hasn't
been run yet.

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
   DOF Markdown, fetched via `dofjson` and cached under each instrument's own
   `_cache_notas` subdirectory (`<outdir>/<coleccion>/<slug>/_cache_notas`,
   created automatically) — next to that instrument's own `indice.json`, not
   in a directory shared across instruments.

Needs a dofjson titles dataset (`codNota`+`titulo`+`fecha`, built once via
`nota2md.download_legal_provisions_titles`) to find each same-day candidate.

### `audita_scjn_legislacion.py`

Offline audit (issue #115): recomputes, from each instrument's already-saved
snapshot header, how well the SCJN document `elige_candidato` picked matches
the Diputados catalogue's own `nombre` for it — no re-crawl. Prints every
instrument sorted least- to most-confident (`acuerdo_interno`/
`grupo_incompatible`/`bajo_umbral` are near-certain wrong-document matches;
`sospechoso` is the zone worth a human look), and can also write the full
list as JSON (`--json`).

### `empaqueta_scjn_leyes.py`

`leyes` only (issue #128): packages every crawled+linked instrument into a
byte-reproducible `leyes.tgz`, plus a `MANIFEST.md` ranking every instrument
by confidence (issue #115's classification, `enlaza_scjn_legislacion.py`'s
link percentage and content-diff-confirmed count) and a `SHA256SUMS.txt` —
same pattern as `empaqueta_historial.py`. **Publishing is manual, always**:
the SCJN's search can return a wrong document entirely (issue #115), so this
script never calls `gh`, and no workflow should ever call it and then
publish on its own. Read `MANIFEST.md` in full before running the `gh
release create`/`upload` command the script prints.

## `repara_notas_editoriales_scjn.py`

A one-time re-process of what `fetch_scjn_legislacion.py` already
downloaded before `nota2md.scjn.docx_a_markdown` started stripping the
SCJN's own editorial commentary ("N. DE E." / "NOTA N", see issue #114) at
crawl time. Rewrites each snapshot's body in place, `quita_notas_editoriales`
applied paragraph by paragraph; its provenance header is left untouched, so
an already-built `indice.json` (Fase 2) stays valid — nothing here re-crawls
the SCJN or re-links a `codNota`.

```bash
./scripts/repara_notas_editoriales_scjn.py --outdir scjn-legislacion --dry-run   # report only
./scripts/repara_notas_editoriales_scjn.py --outdir scjn-legislacion
```

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

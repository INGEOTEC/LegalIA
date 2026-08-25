# scripts

Repo-level utilities. They add `packages/dofjson` to the import path
themselves, so they run straight from a clone — only `requests` has to be
available.

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

## `fetch_scjn_legislacion.py` / `enlaza_scjn_legislacion.py`

Fase 1 and Fase 2 of the SCJN crawl plan in issue #105: recover, from
[legislacion.scjn.gob.mx](https://legislacion.scjn.gob.mx/Buscador/), the
reform-dated Markdown snapshots of a law/reglamento/tratado that
`nota2md.legal_provisions` would otherwise have to OCR — see
`packages/nota2md/nota2md/scjn.py` for why this is a legitimate source (each
snapshot is a consolidated-text-as-of-that-reform, not just a summary) and
why it is never mistaken for an official DOF/SIDOF Markdown (`fuente: scjn`
header on every file).

```bash
./scripts/fetch_scjn_legislacion.py --outdir scjn-legislacion                     # Fase 1: crawl
python -c "from nota2md import download_legal_provisions_titles as d; d('titulos.jsonl.gz')"
./scripts/enlaza_scjn_legislacion.py --outdir scjn-legislacion --titulos titulos.jsonl.gz  # Fase 2: match
```

`fetch_scjn_legislacion.py` covers `leyes`, `reglamentos` and `tratados` only
— the SCJN does not catalogue NOM technical standards as ordenamientos of
their own (issue #105's Fase 0). It is resumable at two levels — a file
already on disk is left alone, and, per collection, the index of the last
instrumento fully attempted is checkpointed to
`<outdir>/<coleccion>/.progreso.json` and cleared once that collection
finishes, so a run killed partway (crash, network drop, Ctrl-C) picks back up
right after that index instead of re-walking every already-done
instrumento's reform table from the top (`--reiniciar` discards the
checkpoint and sweeps the collection from the beginning again) — and
rate-limited (`--espera`, default 1s) against this unofficial site's own
session-scoped URLs.

`enlaza_scjn_legislacion.py` then pairs each already-downloaded snapshot with
the `codNota` of the DOF note that published it, by date, against
`download_legal_provisions_provenance_ids`'s own `historial` for that
instrument — writing an `indice.json` per instrument directory. Needs a
dofjson titles dataset (`codNota`+`titulo`+`fecha`, built once via
`nota2md.download_legal_provisions_titles`) to look up each historial
`codNota`'s own date.

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

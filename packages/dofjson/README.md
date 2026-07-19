# dofjson

Prototype client for the JSON open data service exposed by
[sidof.segob.gob.mx](https://sidof.segob.gob.mx/datos_abiertos), the
Secretaría de Gobernación's system for Mexico's official gazette (DOF,
Diario Oficial de la Federación).

The service's public docs only show sample responses, but its real,
unauthenticated endpoints were found under `https://sidof.segob.gob.mx/dof/sidof/`:

| Endpoint | Description |
|---|---|
| `GET /diarios/porFecha/DD-MM-YYYY` | Edition metadata for a date (Matutina/Vespertina/Extraordinaria) |
| `GET /notas/DD-MM-YYYY` | Notes/documents published on a date |
| `GET /notas/nota/{codNota}` | Full detail of a single note, including its HTML content |
| `GET /indicadores/DD-MM-YYYY` | Economic indicators (exchange rate, TIIE, UDIS) |

This is an experimental package for evaluating whether this service is a
viable alternative (or complement) to `dof2md`'s PDF download + Markdown
conversion pipeline — notes already come with structured HTML content,
which may be easier to work with than OCR'd PDFs.

On top of the raw endpoints, the client offers note-scoped downloads that
resolve a note's page span (`infer_paginas`) and fetch it in whichever form
you want:

- `download_nota_imagenes(codNota)` — the note's scanned page image(s).
- `download_nota_pdf(codNota)` — the note as its own PDF: the whole edition
  PDF (there is no per-note PDF endpoint) sliced to just the note's pages,
  using `pypdf`.

## Usage

```bash
pip install -e "packages/dofjson[test]"
dofjson 2026-07-16 --endpoint notas --outdir output
```

## Building a local archive of daily indexes (`--archivo`)

`dofjson --archivo` downloads the **daily notes index** incrementally, day by
day, over a whole date range (by default from January 2, 1917 to today). For
each date it does exactly what `dofjson YYYY-MM-DD --endpoint notas` does —
`get_notas(date)` filtered with `quita_notas_sin_titulo` — and saves one JSON
per day. It does **not** download each note's content or scanned images: only
the index.

```bash
dofjson --archivo                                      # 1917-01-02 -> today
dofjson --archivo --desde 1980-01-01 --hasta 1980-12-31
dofjson --archivo --pausa 1.0                          # slower (kinder to the server)
```

Output goes to `notas-archivo/` (configurable with `--outdir`), a **local,
never-committed** directory (it is in the repo's `.gitignore`), with the same
per-day filenames the plain command produces:

```
notas-archivo/
  .completados                 # registry of finished days (for resuming)
  2026/
    15072026-notas.json        # index for 2026-07-15 (get_notas, filtered)
    16072026-notas.json
  1980/
    02011980-notas.json
```

The mode is resumable and idempotent: the `.completados` registry records the
finished days, so each run only fetches what is missing. Days that fail with
network errors are *not* marked and get retried on the next run; days the
service does not have (holidays, weekends, very old dates — a 404) *are*
marked so they are not retried forever. "Today" is never marked, so late
additions are picked up by a later run. You can interrupt with Ctrl-C and
resume at any time.

> The full range is ~40,000 days: a long download, meant to be run in parts.
> Start with a bounded range via `--desde/--hasta` if you only need an era.

## Building a compact titulo dataset from the release (`--titulos`)

`dofjson --titulos` builds a small `codNota` + `titulo` + `fecha` dataset out
of every note ever published, sourced from the [`notas-archivo` GitHub
release](https://github.com/INGEOTEC/LegalIA/releases/tag/notas-archivo)
(one `notas-YYYY.tgz` per year, 1917 to last year, plus one
`notas-YYYY-MM.tgz` per month of the current year). Each asset is downloaded
straight into memory, its daily JSON indexes are read without ever writing
them to disk, and only `codNota`/`titulo`/`fecha` are kept from every note
(`titulo` is Spanish for "title", `fecha` for "date") — `codNota` to fetch
that note's full content later, `titulo` for exploratory analysis of the
titles themselves, `fecha` to place each title in time. The
result is a single gzip-compressed JSONL file (~1.2 million notes fit in a
few tens of MB): small enough to move to a Colab GPU runtime for
experiments.

```bash
dofjson --titulos                    # -> titulos/titulos.jsonl.gz
dofjson --titulos --outdir /content  # e.g. from a Colab notebook
```

```python
import gzip, json
with gzip.open("titulos/titulos.jsonl.gz", "rt", encoding="utf-8") as f:
    notas = [json.loads(line) for line in f]
# notas[0] == {"codNota": 4434476, "titulo": "CIRCULAR nº. 164, ...", "fecha": "23-03-1917"}
```

Or use the function directly:

```python
from pathlib import Path
from dofjson.titulos import download_titulos

download_titulos(Path("titulos.jsonl.gz"))
```

## Development

```bash
pytest packages/dofjson
```

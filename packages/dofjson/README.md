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
| `GET /diarios/{YYYY}` | A whole year's `FechasSinPublicacion` — the dates it claims had no gazette |
| `GET /notas/DD-MM-YYYY` | Legal provisions/documents published on a date |
| `GET /notas/nota/{codNota}` | Full detail of a single legal provision, including its HTML content |
| `GET /indicadores/DD-MM-YYYY` | Economic indicators (exchange rate, TIIE, UDIS) |

Note that this service reports a missing day as `200 OK` with empty legal
provision lists, not as an error, and that some of the dates it lists as
unpublished were in fact published — see
[the days SIDOF loses](#the-days-sidof-loses-and-where-they-are-recovered-from---respaldo).

This is an experimental package for evaluating whether this service is a
viable alternative (or complement) to `dof2md`'s PDF download + Markdown
conversion pipeline — legal provisions already come with structured HTML
content, which may be easier to work with than OCR'd PDFs.

On top of the raw endpoints, the client offers legal-provision-scoped
downloads that resolve a legal provision's page span (`infer_paginas`) and
fetch it in whichever form you want:

- `download_nota_imagenes(codNota)` — the legal provision's scanned page image(s).
- `download_nota_pdf(codNota)` — the legal provision as its own PDF: the whole
  edition PDF (there is no per-legal-provision PDF endpoint) sliced to just
  the legal provision's pages, using `pypdf`. The edition PDF itself is
  cached in `outdir` as `edicion-{codDiario}.pdf`, so slicing out another
  legal provision from the same edition later does not re-fetch it, and a
  legal provision whose own `nota-{codNota}.pdf` is already in `outdir`
  is returned immediately, with no network call at all.
- `download_nota_imagen_o_pdf(codNota)` — tries the page image first, and
  falls back to the *whole, uncut* edition PDF (cached the same way) when
  SIDOF has no image for that page, or its image-listing endpoint 404s for
  that edition outright — deliberately not the sliced, per-legal-provision
  PDF `download_nota_pdf` produces, since working out a legal provision's
  page position is OCR/cutting work, not downloading. A legal provision
  whose images or fallback edition PDF are already in `outdir` is returned
  immediately, without repeating a previous, already-failed image attempt.
  Meant for bulk-downloading everything a large batch of legal provisions
  without HTML needs into one `outdir`, so that a later
  `nota2md.legal_provisions(codNota, outdir, source="image"|"pdf")` call
  against that same directory only has to OCR and cut — never re-download.

## Usage

```bash
pip install -e "packages/dofjson[test]"
dofjson 2026-07-16 --endpoint notas --outdir output
```

## Building a local archive of daily indexes (`--archivo`)

`dofjson --archivo` downloads the **daily legal provisions index**
incrementally, day by day, over a whole date range (by default from January
2, 1917 to today). For each date it does exactly what `dofjson YYYY-MM-DD
--endpoint notas` does — `get_notas(date)` filtered with
`quita_notas_sin_titulo` — and saves one JSON per day. It does **not**
download each legal provision's content or scanned images: only the index.

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
network errors are *not* marked and get retried on the next run; days with no
edition (holidays, weekends) *are* marked so they are not retried forever.
"Today" is never marked, so late additions are picked up by a later run. You
can interrupt with Ctrl-C and resume at any time.

> The full range is ~40,000 days: a long download, meant to be run in parts.
> Start with a bounded range via `--desde/--hasta` if you only need an era.

### The days SIDOF loses, and where they are recovered from (`--respaldo`)

SIDOF does not report a day it is missing as an error. It answers **200 OK
with every legal provision list empty** — which is also how it reports a
Sunday — and lists the date under `FechasSinPublicacion` in
`GET /diarios/{year}`. Most of those dates are genuine: weekends and
holidays. Some are not.

On 8 March 1999 the DOF published the decree amending articles 16, 19, 22 and
123 of the Constitution. SIDOF has no trace of it: the day is empty, the
legal provision's `codNota` returns `{"Nota": []}`, and its `codDiario` 404s.
Sampling four years (1999, 2006, 2010, 2020) turned up **eight** such days —
dates SIDOF calls unpublished that were published.

`www.dof.gob.mx`, the DOF's own website, is a separate system on a separate
database, and it has them. So an empty answer is no longer taken at face
value: on a weekday, the day is put to the website before being written off.

```bash
dofjson --archivo                        # habiles (default): re-check Mon-Fri
dofjson --archivo --respaldo todos       # also weekends (~10,000 more requests)
dofjson --archivo --respaldo nunca       # trust SIDOF alone
```

```
[1999-03-08] SIDOF no la tiene; recuperada de dof.gob.mx
```

The same applies to a single date, so a lost day is reachable directly:

```bash
dofjson 1999-03-08 --endpoint notas      # -> "fuente": "dof.gob.mx", 22 legal provisions
```

**The text of those legal provisions is recoverable too**, not just their
titles. `dofweb.get_nota(codNota)` reads a legal provision's page on the
website and returns it in the shape `client.get_nota()` uses, with the legal
provision's HTML in `cadenaContenido` — the same string SIDOF would have
served, so `nota2md` converts it to Markdown by the ordinary HTML path:

```python
from dofjson import dofweb

dofweb.get_nota(4997808)["Nota"]["cadenaContenido"]   # DOF 03-03-1999
```

On a legal provision both sources have, the recovered HTML differs from
SIDOF's only in escaping its accents as entities, and the Markdown built from
either is identical. The page carries no `codDiario`, `codEdicion` or
`pagina`, so the image and PDF paths stay SIDOF-only; a legal provision the
site has no HTML for comes back with `existeHtml` `"N"`, and an unknown
`codNota` with `"Nota": []`, as SIDOF answers.

**Which source a day came from is recorded, never inferred.** Every saved day
carries a `fuente` key (`"sidof"` or `"dof.gob.mx"`), and the registry stores
it next to the date, so provenance can be audited after the fact:

```
1999-03-06	sin-edicion
1999-03-08	dof.gob.mx
1999-03-09	sidof
```

In the compact `--titulos` dataset the marker rides along on the legal
provisions it applies to: a legal provision carries `fuente` only when its
day did *not* come from SIDOF, since repeating `"sidof"` on all ~1.2 million
rows would cost more than it says.

#### What the fallback carries, and what it does not

The website's daily index lists the substantive gazette — `PE`, `PJ`, `PL`,
`OA`, `OD` — and leaves out the three bulk-announcement groups, which are
reachable on the site only through its POST search form:

| | |
|---|---|
| `CV` | convocatorias for public-sector procurement |
| `VG` | convocatorias for civil-service vacancies |
| `AV` | avisos judiciales y generales |

On days both sources have, the recovered set of `codNota` matches SIDOF's
**exactly** once those three are excluded (checked on days sampled from 1999
through 2026). A recovered day is therefore complete with respect to what the
gazette *enacted* and short of what it *announced*, and says so in
`notasIncompletas` rather than passing for a whole day.

The website's per-legal-provision index **starts in January 1999**; before
that it holds only scanned images, so an older day returns an edition with
no index. Those come back in `edicionesSinIndice` as `{"codEdicion",
"codDiario"}`: no titles
to list, but proof the gazette was published, which is what keeps the day off
the empty pile. Every day confirmed lost from SIDOF so far is 1999 or later,
inside the range where titles can actually be recovered.

> **On a page served for the wrong date.** The index prints the date it is
> actually serving, and it has been seen answering with a *different* day's
> page. Since the parser stamps each legal provision with the date that was
> **asked for**, taking such a page at face value would file real legal
> provisions under the wrong day. Every page carrying content is therefore
> checked against what it claims to be, and a mismatch raises
> `dofweb.PaginaDeOtroDia`. `--archivo`
> treats that like a network error and leaves the day to retry — believing it
> would corrupt the day, and calling the day empty would bury it for good.
> Editions the gazette never ran carry no date and no content, which is not a
> mix-up and is not treated as one.

> **On TLS.** `www.dof.gob.mx` serves its leaf certificate without the
> intermediate that signs it, so verification fails with "unable to get local
> issuer certificate" on any client that does not chase the issuer itself. The
> missing GoDaddy intermediate — and its root — ship in
> `dofjson/certs/dof-gob-mx-chain.pem`. The system trust store is tried first
> and the bundled chain only on failure. Certificate verification is never
> disabled.

## Building a compact titulo dataset from the release (`--titulos`)

`dofjson --titulos` builds a small `codNota` + `titulo` + `fecha` dataset out
of every legal provision ever published, sourced from the [`notas-archivo`
GitHub release](https://github.com/INGEOTEC/LegalIA/releases/tag/notas-archivo)
(one `notas-YYYY.tgz` per year, 1917 to last year, plus one
`notas-YYYY-MM.tgz` per month of the current year). Each asset is downloaded
straight into memory, its daily JSON indexes are read without ever writing
them to disk, and only `codNota`/`titulo`/`fecha` are kept from every legal
provision (`titulo` is Spanish for "title", `fecha` for "date") — `codNota`
to fetch that legal provision's full content later, `titulo` for exploratory
analysis of the titles themselves, `fecha` to place each title in time. The
result is a single gzip-compressed JSONL file (~1.2 million legal provisions
fit in a few tens of MB): small enough to move to a Colab GPU runtime for
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
from dofjson.titulos import download_legal_provisions_titles

download_legal_provisions_titles(Path("titulos.jsonl.gz"))
```

Pass `cache_dir` to keep the downloaded `.tgz` assets on disk (e.g. a
Colab-persisted folder) so rebuilding the dataset later only fetches assets
not already there:

```python
download_legal_provisions_titles(Path("titulos.jsonl.gz"), cache_dir=Path("cache"))
```

`download_dof_assets` does just the download/cache part, independently of
building the titles dataset:

```python
from dofjson.titulos import download_dof_assets

download_dof_assets(Path("cache"))  # one notas-YYYY[-MM].tgz per asset
```

## Development

```bash
pytest packages/dofjson
```

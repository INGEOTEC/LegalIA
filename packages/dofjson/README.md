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

## One entry point: `dofjson` itself

`dofjson.sidof` talks to SIDOF (the preferred source — it exposes a REST
API); `dofjson.dofweb` talks to the DOF's own website, used to recover the
days and legal provisions SIDOF is missing outright (see below). A caller
should never need to import `sidof` or `dofweb` directly, or decide for
itself which of the two has what it needs — every function either module
offers is reachable straight off the `dofjson` package:

```python
import dofjson

dofjson.get_nota(4997808)               # SIDOF has it, or the website does
dofjson.get_notas(dt.date(1999, 3, 8))  # a day SIDOF lost, recovered from the website
dofjson.download_nota_pdf(4997808, outdir)
```

`get_nota(codNota)` and `get_notas(date)` are where that actually matters:
they try SIDOF first and fall back to the website automatically, so a
caller (in this package or another one, like `nota2md`) never risks only
ever calling `sidof` and missing the legal provisions/days that live on
the website instead. `download_nota(codNota)`, `download_nota_imagenes(codNota)`,
`download_nota_pdf(codNota)` and `download_nota_imagen_o_pdf(codNota)` (below)
still only ever fetch from SIDOF — the website carries no per-legal-provision
images, PDFs or page numbers — but they still resolve a bare `codNota`
through `get_nota()`'s own fallback, so a legal provision SIDOF has no
record of at all raises a clear error instead of crashing on a missing
field; `get_diario`, `get_indicadores`, `get_imagenes`, `download_pdf` and
`download_imagen` are the ones with genuinely nothing to resolve, and are
plain passthroughs to `sidof`. Either way, every one of them is reachable
straight off `dofjson`.

This is an experimental package for evaluating whether this service's
structured HTML is a viable alternative (or complement) to OCR'ing the PDF
`dof2md` converts to Markdown.

On top of the raw endpoints, `dofjson` offers legal-provision-scoped
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
  the edition outright — deliberately not the sliced, per-legal-provision
  PDF `download_nota_pdf` produces, since working out a legal provision's
  page position is OCR/cutting work, not downloading. A legal provision
  whose images or fallback edition PDF are already in `outdir` is returned
  immediately, without repeating a previous, already-failed image attempt.
  Meant for bulk-downloading everything a large batch of legal provisions
  without HTML needs into one `outdir`, so that a later
  `nota2md.legal_provisions(codNota, outdir, source="image"|"pdf")` call
  against that same directory only has to OCR and cut — never re-download.

`download_edicion_pdf(date, edicion, outdir)` downloads a *whole edition's*
PDF given only its date and edition (`MAT`/`VES`/`EXT`) — no `codNota`
needed. It resolves the edition's `codDiario` from `get_diario(date)` first,
then downloads and caches the PDF the same way `download_nota_pdf` caches
the edition it slices notes out of; a second call for the same edition
reuses the file already on disk. This is what `dof2md` used to do itself,
against `www.dof.gob.mx`, before its PDF download was retired in favor of
this function (issue #134) — `dof2md` today only ever converts a PDF or
image set already on disk.

```python
dofjson.download_edicion_pdf(dt.date(2026, 6, 16), "VES", outdir)
```

## Usage

```bash
pip install -e "packages/dofjson[test]"
dofjson 2026-07-16 --endpoint notas --outdir output
dofjson --pdf-edicion 16-06-2026 --edicion VES --outdir output
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

`--respaldo` is `dofjson.get_notas(date, respaldo=...)`'s own parameter,
just surfaced on the CLI — a single call to the library defaults to
`respaldo="todos"` (always double-check an empty day), unlike `--archivo`'s
`"habiles"`, since a one-off query has no ~40,000-day request budget to
mind:

```python
dofjson.get_notas(date)                       # respaldo="todos" by default
dofjson.get_notas(date, respaldo="nunca")      # trust SIDOF alone
```

The same applies to a single date on the CLI, so a lost day is reachable
directly:

```bash
dofjson 1999-03-08 --endpoint notas      # -> "fuente": "dof.gob.mx", 22 legal provisions
```

**The text of those legal provisions is recoverable too**, not just their
titles — `dofjson.get_nota(codNota)` is what recovers it, the same call as
any other legal provision (see above). Under the hood, `dofweb.get_nota()`
reads a legal provision's page on the website and returns it in the shape
`sidof.get_nota()` uses, with the legal provision's HTML in
`cadenaContenido` — the same string SIDOF would have served, so `nota2md`
converts it to Markdown by the ordinary HTML path:

```python
dofjson.get_nota(4997808)["cadenaContenido"]   # DOF 03-03-1999
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

In the compact `legal_provisions_titles` stream the marker rides along on the legal
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

## Every legal provision ever published, as titles (`legal_provisions_titles`)

`dofjson.legal_provisions_titles()` yields a small `codNota` + `titulo` +
`fecha` + `codOrgaUno` record for every legal provision ever published,
sourced from the [`notas-archivo` GitHub
release](https://github.com/INGEOTEC/LegalIA/releases/tag/notas-archivo)
(one `notas-YYYY.tgz` per year, 1917 to last year, plus one
`notas-YYYY-MM.tgz` per month of the current year). Each asset's daily JSON
indexes are read in turn and only those four fields kept from every titled
legal provision (`titulo` is Spanish for "title", `fecha` for "date") —
`codNota` to fetch that legal provision's full content later, `titulo` for
exploratory analysis of the titles themselves, `fecha` to place each title in
time, `codOrgaUno` to group by issuing branch.

Nothing is written: this used to be a `titulos.jsonl.gz` file plus an
`organigrama.json`, from before the asset cache and an iterator over it
existed. Both do now, and the dataset was a third copy of ~1.2 million
records that could quietly fall behind the release (issue #166). Populate the
cache once and every pass afterwards is local:

```bash
nota2md download gazette-metadata   # or: python -c "import dofjson; dofjson.download_dof_assets()"
```

```python
import dofjson

for titulo in dofjson.legal_provisions_titles():
    ...
# titulo == {"codNota": 4434476, "titulo": "CIRCULAR nº. 164, ...",
#            "fecha": "23-03-1917", "codOrgaUno": None}
```

`cache_dir` resolves as everywhere else in the package: left out it is the
package-wide `dofjson.titulos.CACHE_DIR`, a directory reads that one, and an
explicit `cache_dir=None` downloads every asset straight into memory without
touching disk:

```python
dofjson.legal_provisions_titles(Path("cache"))       # a cache of your own
dofjson.legal_provisions_titles(cache_dir=None)      # nothing on disk
```

The `codOrgaUno` -> `nombreCodOrgaUno` map comes out of the same pass —
`dofjson.organigrama()` makes that pass on its own, or pass your own dict as
`organigrama=` to get both at once. The stream is not re-iterable: a second
pass re-reads the assets, so call it again (or materialize what you need).

`download_dof_assets` does just the download/cache part, independently of
building the titles dataset. Its own `cache_dir` is optional too: leave it
out and the assets land in an OS-appropriate per-user cache directory
(`dofjson.titulos.directorio_cache_predeterminado()`, via `platformdirs` —
e.g. `~/.cache/dofjson` on Linux) instead of a path you have to pick
yourself:

```python
from dofjson.titulos import download_dof_assets

download_dof_assets()               # -> directorio_cache_predeterminado()
download_dof_assets(Path("cache"))  # -> one notas-YYYY[-MM].tgz per asset, here instead
```

`api.download_dof_assets` is the same function, reachable without importing
`dofjson.titulos` directly — `dofjson.api` is meant to be the one entry point
a caller needs (see above).

### Reading a whole asset, not just titles (`notas_de_tgz`)

`titulos._titulos_de_tgz` (what `legal_provisions_titles` yields)
only ever kept four fields per legal provision. `dofjson.notas_de_tgz` is the
general form: every field a note carries, in publication order (by day, then
by `codNota` within the day):

```python
import dofjson

for nota in dofjson.notas_de_tgz(Path("cache/notas-1980.tgz").read_bytes()):
    ...  # nota carries every field SIDOF/dofweb published it with
```

`dofjson.iterador_de_assets` is `notas_de_tgz` over the *whole* release
instead of one asset already in hand: it downloads (or reads from
`cache_dir`) every `notas-YYYY[-MM].tgz` in turn and yields every note across
all of them, one at a time, without ever holding more than one asset's notes
in memory. `legal_provisions_titles` itself is built on top of it (it just keeps the
titled notes and projects them down to four fields):

```python
import dofjson

for nota in dofjson.iterador_de_assets():           # every note ever published
    ...
for nota in dofjson.iterador_de_assets(Path("cache")):  # reuse a cache_dir on disk
    ...
```

### Reducing calls to SIDOF with an already-downloaded archive (`cache_dir`)

A day's notes index (`dofjson.get_notas(date)`/`dofjson.api.get_notas(date)`)
is exactly what a notas-archivo asset holds for an already-published date —
so when a `cache_dir` already populated by `download_dof_assets` has that
date, it is read straight off disk instead of asking SIDOF (or dofweb) at
all:

```python
from dofjson import api

api.get_notas(date, cache_dir=Path("cache"))   # a cache hit skips the network entirely
```

Leaving `cache_dir` out entirely still gets the cache: it defaults to the
package-wide `dofjson.titulos.CACHE_DIR` (itself
`directorio_cache_predeterminado()`, an OS-appropriate per-user cache
directory, unless you set it to something else). Pass `cache_dir=None`
explicitly to skip the cache for just that call.

The same default flows through `dofjson --archivo`/`download_archivo()` (so
rebuilding the archive elsewhere does not repeat the requests the release
itself already paid for) and the CLI's single-date query:

```bash
dofjson --archivo --cache-dir cache        # a specific directory
dofjson 1980-01-02 --endpoint notas        # no --cache-dir: uses CACHE_DIR
dofjson 1980-01-02 --endpoint notas --cache-dir none  # always fetch live
```

`dofjson.get_nota(codNota)` has no matching `cache_dir`: the archive only
ever holds the daily index, never a legal provision's own `cadenaContenido`
(its HTML text), which always requires the one-note SIDOF/dofweb request
`get_nota()` already makes.

## Development

```bash
pytest packages/dofjson
```

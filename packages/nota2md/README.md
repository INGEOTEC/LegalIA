# nota2md

Three entry points, all re-exported off the package itself
(`from nota2md import ...`), for Mexico's official gazette (DOF, Diario
Oficial de la Federación) and the federal laws it publishes:

| Entry point | Given | Returns/writes |
|---|---|---|
| [`legal_provisions`](#legal_provisions--a-single-dof-legal-provision-as-markdown) | a legal provision's `codNota` | its Markdown, written to `outdir/nota-{codNota}.md` |
| [`normative_reconstruction`](#normative_reconstruction--a-laws-current-text-from-its-dof-legal-provisions) | a law's reform history (`codNota` list) | its current text, written to `outdir/ley-{codNota}.md` |
| [`download_legal_provisions_provenance_ids`](#download_legal_provisions_provenance_ids--a-laws-reform-history) | a collection name (`"leyes"`, `"reglamentos"`, `"normas"`, `"tratados"`) | every instrument's reform history, in memory |

They compose: `download_legal_provisions_provenance_ids` gets you the `codNota` list
`normative_reconstruction` needs, and `normative_reconstruction` gets you a
law's current text the same way `legal_provisions` gets you a single legal
provision's — built from nothing but the DOF's own legal provisions, one
Markdown file at a time.

```python
from nota2md import download_legal_provisions_provenance_ids, legal_provisions, normative_reconstruction

leyes = download_legal_provisions_provenance_ids("leyes")
cpeum = next(l for l in leyes if l["abrev"] == "cpeum")

dest = normative_reconstruction(cpeum["historial"], "output", cpeum["nombre"])
print(f"{cpeum['nombre']} -> {dest}")
```

## `legal_provisions` — a single DOF legal provision as Markdown

Builds the Markdown of a **single DOF legal provision**, identified by its
`codNota`.

Where [`dof2md`](../dof2md) converts a whole edition PDF and
[`dofjson`](../dofjson) is a thin client for SIDOF's JSON service,
`legal_provisions` ties them together to produce the Markdown for one legal
provision, from any of three sources:

| Source | How | When |
|---|---|---|
| **HTML** | Converts the legal provision's `cadenaContenido` HTML directly (a DOF-tailored BeautifulSoup converter). | The legal provision has digital text. Preferred: clean, already scoped to the one legal provision, no OCR. |
| **Image** | Downloads the legal provision's scanned page image(s) via `dofjson`, OCRs them with `dof2md`/mineru, then slices out the one legal provision. | Image-only legal provisions — or any legal provision, when you want the certified scanned original. |
| **PDF** | Downloads the legal provision's own PDF (the edition PDF sliced to the legal provision's pages, via `dofjson.download_nota_pdf`), OCRs it with `dof2md`/mineru, then slices out the one legal provision. | When you'd rather OCR a PDF than page images. |

Both OCR paths (image and PDF) mirror the HTML path's output style (`#`/`##`
headings, `**bold**`, `*italic*`, GitHub tables — `dof2md` rewrites mineru's
HTML tables to Markdown), so a legal provision's Markdown looks much the same
whichever source it came from.

### Legal provisions SIDOF does not have

SIDOF is missing whole days of the gazette (see [`dofjson`](../dofjson)), and
the legal provisions published on them have no SIDOF record at all — no
`cadenaContenido`, and no `codDiario` or page numbers for the OCR paths to
start from. When SIDOF answers `{"Nota": []}` for a `codNota`,
`legal_provisions` looks the legal provision up on the DOF's own website
instead, which serves the same HTML:

```bash
nota2md 4997808 --outdir output   # DOF 03-03-1999, a day SIDOF lost
```

The HTML path is the only one that can build these legal provisions; asking
for `--source image` or `--source pdf` on one raises rather than fetching the
wrong pages.

### Cutting a legal provision out of its page

A scanned page (or a sliced PDF) usually holds more than one legal provision:
it can begin with the tail of the previous legal provision and end with the
start of the next. `legal_provisions` uses the per-day legal provision index —
which lists every legal provision's title in order — to locate two boundaries
in the OCR'd text (where **this** legal provision's title appears, and where
the **next** legal provision's title appears) and keeps only what lies
between. Matching is fuzzy (accent-folded, marker-stripped, `difflib`
alignment) to tolerate OCR differences, and it also drops the next legal
provision's organism header that the DOF prints above its title.

`fetch_day_notes(date)` (`nota2md.builder`) is the per-day index itself — a
day's browsable legal provisions (title, `codNota`, `codEdicion`...), from
SIDOF and, when SIDOF has nothing for that day, from the DOF's own website:

```python
from nota2md.builder import fetch_day_notes
import datetime as dt

for nota in fetch_day_notes(dt.date(2026, 7, 15))["NotasMatutinas"]:
    print(nota["codNota"], nota["titulo"])
```

### Usage

```bash
# HTML when available, otherwise OCR of the scanned page(s)
nota2md 5793655 --outdir output

# force the scanned-image + OCR path, sourcing the next legal provision's
# title from a saved notas index (avoids an extra request; works offline)
dofjson 2026-07-15 --outdir output          # writes 15072026-notas.json
nota2md 5793655 --source image --notas output/15072026-notas.json --outdir output

# force the PDF + OCR path (edition PDF sliced to the legal provision's pages)
nota2md 5793655 --source pdf --notas output/15072026-notas.json --outdir output
```

Programmatically:

```python
from nota2md import legal_provisions

legal_provisions(5793655, "output")                 # -> output/nota-5793655.md
```

The HTML path needs only `beautifulsoup4`; the image and PDF paths additionally
need `dof2md` (and mineru), imported lazily so the HTML path works without them.

## `normative_reconstruction` — a law's current text from its DOF legal provisions

Builds a law's current (vigente) text from nothing but its DOF legal
provisions: starts from the original publication and replays each reform
decree's own "se
reforma/adiciona/deroga el artículo N... para quedar como sigue" instruction
on top of it, article by article — filling back in, from the article's own
previous text, every fracción or inciso a reform elides with "..." instead of
repeating. It never reads a law's official consolidated ("texto vigente") text;
that exists separately (`nota2md.texto_vigente`) only as independent ground
truth to check reconstructions against, in `tests/test_leyes_44.py`, over 43
real federal laws.

```python
from nota2md import normative_reconstruction

# cpeum's own historial: [5592105, 5730586, ...] — oldest first, index 0 the
# original publication (see download_legal_provisions_provenance_ids below for where a
# law's own historial list comes from).
dest = normative_reconstruction(
    [5592105, 5730586], "output", "LEY de Amnistía",
)
print(dest.read_text(encoding="utf-8"))   # -> output/ley-5592105.md
```

Each legal provision it needs is fetched through `legal_provisions` into the
same `outdir`, as `nota-{codNota}.md` — a legal provision already there from
an earlier call (this law's own previous run, or another law's sharing the
same `outdir`) is read back from disk instead of fetched again.

The third argument, `nombre_ley` (as `download_legal_provisions_provenance_ids` names it,
e.g. `"LEY de Amnistía"`), scopes every legal provision to the one instrument
among the several a single decree may touch — pass it whenever a legal
provision is shared with another law's history, which `leyesmx`'s data does
not mark on its own. Left out, a legal provision is assumed to concern only
this law, which holds for most of them but silently mixes in another law's
articles for the rest.

## `download_legal_provisions_provenance_ids` — a law's reform history

Reads a Mexican legislative-history collection — laws, regulations, Normas
Oficiales Mexicanas, international treaties — back from the
[`historial-legislativo`](https://github.com/INGEOTEC/LegalIA/releases/tag/historial-legislativo)
release that [`leyesmx`](../leyesmx) publishes:

```python
from nota2md import download_legal_provisions_provenance_ids

leyes = download_legal_provisions_provenance_ids("leyes")   # or "reglamentos", "normas", "tratados"
cpeum = next(l for l in leyes if l["abrev"] == "cpeum")
print(cpeum["nombre"], cpeum["reformas"], len(cpeum["historial"]))
```

Downloads that collection's tarball straight into memory — nothing touches
disk — and returns one dict per instrument, merging its catalogue entry (name,
reform count, dates...) with its own `historial`: the `codNota` of its reforms
or decrees, oldest first, index 0 the original publication. That is exactly
what `normative_reconstruction` expects as its own first argument.

## Installation

```bash
pip install nota2md          # legal_provisions' HTML path, plus normative_reconstruction
                              # and download_legal_provisions_provenance_ids
pip install nota2md[ocr]     # also pulls in dof2md, for legal_provisions' image/PDF OCR paths
```

`dofjson` and `requests` are hard dependencies and install automatically. For
development in this monorepo, install the siblings editable instead so local
edits are picked up:

```bash
pip install -e "packages/dofjson"
pip install -e "packages/dof2md"          # only needed for the image/PDF OCR paths
pip install -e "packages/nota2md[test]"
```

## Development

```bash
pytest packages/nota2md
```

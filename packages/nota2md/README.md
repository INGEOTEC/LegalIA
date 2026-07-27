# nota2md

Builds the Markdown of a **single DOF note**, identified by its `codNota`, for
Mexico's official gazette (DOF, Diario Oficial de la Federación).

Where [`dof2md`](../dof2md) converts a whole edition PDF and
[`dofjson`](../dofjson) is a thin client for SIDOF's JSON service, `nota2md`
ties them together to produce the Markdown for one note, from any of three
sources:

| Source | How | When |
|---|---|---|
| **HTML** | Converts the note's `cadenaContenido` HTML directly (a DOF-tailored BeautifulSoup converter). | The note has digital text. Preferred: clean, already scoped to the one note, no OCR. |
| **Image** | Downloads the note's scanned page image(s) via `dofjson`, OCRs them with `dof2md`/mineru, then slices out the one note. | Image-only notes — or any note, when you want the certified scanned original. |
| **PDF** | Downloads the note's own PDF (the edition PDF sliced to the note's pages, via `dofjson.download_nota_pdf`), OCRs it with `dof2md`/mineru, then slices out the one note. | When you'd rather OCR a PDF than page images. |

Both OCR paths (image and PDF) mirror the HTML path's output style (`#`/`##`
headings, `**bold**`, `*italic*`, GitHub tables — `dof2md` rewrites mineru's
HTML tables to Markdown), so a note's Markdown looks much the same whichever
source it came from.

### Cutting a note out of its page

A scanned page (or a sliced PDF) usually holds more than one note: it can begin
with the tail of the previous note and end with the start of the next. `nota2md`
uses the per-day note index — which lists every note's title in order — to
locate two boundaries in the OCR'd text (where **this** note's title appears,
and where the **next** note's title appears) and keeps only what lies between.
Matching is fuzzy (accent-folded, marker-stripped, `difflib` alignment) to
tolerate OCR differences, and it also drops the next note's organism header that
the DOF prints above its title.

## Usage

```bash
# HTML when available, otherwise OCR of the scanned page(s)
nota2md 5793655 --outdir output

# force the scanned-image + OCR path, sourcing the next note's title from a
# saved notas index (avoids an extra request; works offline)
dofjson 2026-07-15 --outdir output          # writes 15072026-notas.json
nota2md 5793655 --source image --notas output/15072026-notas.json --outdir output

# force the PDF + OCR path (edition PDF sliced to the note's pages)
nota2md 5793655 --source pdf --notas output/15072026-notas.json --outdir output
```

Programmatically:

```python
from pathlib import Path
from nota2md.builder import build_nota_markdown

build_nota_markdown(5793655, Path("output"))                 # -> output/nota-5793655.md
```

The HTML path needs only `beautifulsoup4`; the image and PDF paths additionally
need `dof2md` (and mineru), imported lazily so the HTML path works without them.

## Installation

```bash
pip install nota2md          # HTML path only
pip install nota2md[ocr]     # also pulls in dof2md, for the image/PDF OCR paths
```

`dofjson` is a hard dependency and installs automatically. For development in
this monorepo, install the siblings editable instead so local edits are
picked up:

```bash
pip install -e "packages/dofjson"
pip install -e "packages/dof2md"          # only needed for the image/PDF OCR paths
pip install -e "packages/nota2md[test]"
```

## Development

```bash
pytest packages/nota2md
```

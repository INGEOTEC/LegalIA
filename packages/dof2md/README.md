# dof2md

Downloads editions of Mexico's official gazette (DOF, Diario Oficial de la
Federación) as PDF and converts them to Markdown.

Part of the [LegalIA](https://github.com/INGEOTEC/LegalIA) monorepo.

## Install

```bash
pip install -e ".[test]"
```

## Usage

```bash
dof2md 2010-01-05                     # morning edition (default)
dof2md 2010-01-05 --edition VES       # evening edition
dof2md 2010-01-05 --outdir my_folder  # output directory
```

Generates `<date>-<edition>.pdf` and `<date>-<edition>.md` in the output directory.

## Batch conversion (`BatchConverter`)

`dof2md` has no notion of what a "note" is or where a document came from —
`convert_to_markdown`/`convert_images_to_markdown` work on any PDF or set of
scanned page images, DOF or not. `BatchConverter` builds on top of them to
convert many documents in one run, keeping a single `mineru-api` server warm
across the whole batch instead of paying its startup (and model-loading)
cost once per document:

```python
from dof2md import BatchConverter

jobs = [
    ("a.pdf", "output", "a.md"),
    (["b-p1.jpg", "b-p2.jpg"], "output", "b.md"),
]

with BatchConverter() as convert:
    for path_or_paths, outdir, filename in jobs:
        convert(path_or_paths, outdir, filename)
```

Each call takes a single PDF path, or a list of image paths for a document
spanning several scanned pages, and writes the result to `outdir/filename`.
Two optional positional arguments, `titulo`/`titulo_siguiente`, slice the
OCR'd Markdown down to the text between them (see
[`dof2md.cutter.cut_markdown_by_titles`](dof2md/cutter.py)) — useful when a
page or PDF holds more than the one document of interest, e.g. a DOF page
shared with the notes right before and after it. Left out, the whole
conversion is kept as-is, on the assumption that the whole document is what
was asked for.

`nota2md.legal_provisions` accepts an already-`__enter__`'d `BatchConverter`
as its own `converter` parameter, so a batch of DOF legal provisions can
share the same warm server too.

## Tests

```bash
pytest -v
```

# LegalIA

A monorepo of Python packages for the analysis of legal texts in the Mexican
context, developed by [INGEOTEC](https://github.com/INGEOTEC). Its first
target is the *Diario Oficial de la Federación* (DOF), Mexico's official
gazette: more than 1.2 million notes published without interruption since
1917.

## Packages

| Package | Description |
|---|---|
| [dofjson](packages/dofjson) ([PyPI](https://pypi.org/project/dofjson/)) | Client for SIDOF's JSON open-data service: which notes were published on a given day, and the full detail — including HTML content, when it exists — of any one of them. |
| [nota2md](packages/nota2md) ([PyPI](https://pypi.org/project/nota2md/)) | Builds the Markdown of a single DOF note, identified by its `codNota`, from its official HTML or by OCR of its scanned pages. |
| [dof2md](packages/dof2md) ([PyPI](https://pypi.org/project/dof2md/)) | Downloads a complete edition of the DOF as PDF and converts it — OCR included — to Markdown; the heavy artillery `nota2md` borrows for notes that predate the HTML era. |

Each package lives under `packages/<name>/` with its own `pyproject.toml`,
dependencies, version, and tests — installed and released independently to
PyPI. For a guided walkthrough of the three working together, see
[From the gazette to Markdown](https://ingeotec.github.io/LegalIA/tools.html)
on the project's [website](https://ingeotec.github.io/LegalIA/).

## Quick start

For a modern note, only `dofjson` and `nota2md` are needed — `dof2md` stays
in the background, as `nota2md`'s OCR fallback for notes that only exist as
scanned page images:

```bash
pip install dofjson nota2md
```

```python
import datetime as dt
from pathlib import Path

from dofjson import client
from nota2md.builder import build_nota_markdown

# Every note published on a given day
notas = client.quita_notas_sin_titulo(client.get_notas(dt.date(2026, 7, 15)))
cod_nota = notas["NotasMatutinas"][0]["codNota"]

# The note's Markdown, from its official HTML
md_path = build_nota_markdown(cod_nota, Path("output"), source="html")
```

The same round trip is available from the command line:

```bash
dofjson 2026-07-15 --outdir output     # -> output/15072026-notas.json
nota2md 5793639 --outdir output        # -> output/nota-5793639.md
```

## Development

Install a package in editable mode with its test dependencies:

```bash
pip install -e "packages/dofjson[test]"
pytest packages/dofjson
```

## License

Apache License 2.0. See [LICENSE](LICENSE).

# LegalIA

A monorepo of Python packages for the analysis of legal texts in the Mexican
context, developed by [INGEOTEC](https://github.com/INGEOTEC). Its first
target is the *Diario Oficial de la Federación* (DOF), Mexico's official
gazette: more than 1.2 million legal provisions published without interruption
since 1917.

## Packages

| Package | Description |
|---|---|
| [dofjson](packages/dofjson) ([PyPI](https://pypi.org/project/dofjson/)) | Client for SIDOF's JSON open-data service: which legal provisions were published on a given day, and the full detail — including HTML content, when it exists — of any one of them. Also builds a compact `codNota` + `titulo` + `fecha` dataset of every legal provision ever published (`download_legal_provisions_titles`). |
| [nota2md](packages/nota2md) ([PyPI](https://pypi.org/project/nota2md/)) | Builds the Markdown of a single DOF legal provision (`legal_provisions`), reconstructs a law's current text from nothing but its legal provisions (`reconstruct_legal_provisions`), and reads back a law's reform history (`download_legal_provisions_provenance_ids`). |
| [dof2md](packages/dof2md) ([PyPI](https://pypi.org/project/dof2md/)) | Downloads a complete edition of the DOF as PDF and converts it — OCR included — to Markdown; the heavy artillery `nota2md` borrows for legal provisions that predate the HTML era. |

Each package lives under `packages/<name>/` with its own `pyproject.toml`,
dependencies, version, and tests — installed and released independently to
PyPI. For a guided walkthrough of the three working together, see
[From the gazette to Markdown](https://ingeotec.github.io/LegalIA/tools.html)
on the project's [website](https://ingeotec.github.io/LegalIA/).

## Quick start

`nota2md` has five entry points, all re-exported off the package itself.

For a modern legal provision, only `dofjson` and `nota2md` are needed —
`dof2md` stays in the background, as `nota2md`'s OCR fallback for legal
provisions that only exist as scanned page images:

```bash
pip install dofjson nota2md
```

### `legal_provisions` — a single DOF legal provision as Markdown

```python
import datetime as dt
from pathlib import Path

from nota2md import fetch_daily_legal_provisions, legal_provisions

# Every legal provision published on a given day
notas = fetch_daily_legal_provisions(dt.date(2026, 7, 15))
cod_nota = notas["NotasMatutinas"][0]["codNota"]

# The legal provision's Markdown, from its official HTML
md_path = legal_provisions(cod_nota, Path("output"), source="html")
```

The same round trip is available from the command line:

```bash
dofjson 2026-07-15 --outdir output     # -> output/15072026-notas.json
nota2md 5793639 --outdir output        # -> output/nota-5793639.md
```

### `download_legal_provisions_provenance_ids` and `reconstruct_legal_provisions` — a law's current text from its reform history

```python
from nota2md import download_legal_provisions_provenance_ids, reconstruct_legal_provisions

# Every federal law's reform history: a list of codNota per instrument
leyes = download_legal_provisions_provenance_ids("leyes")
cpeum = next(l for l in leyes if l["abrev"] == "cpeum")

# The law's current (vigente) text, reconstructed from nothing but its own
# DOF legal provisions
dest = reconstruct_legal_provisions(cpeum["historial"], "output", nombre_ley=cpeum["nombre"])
```

### `download_legal_provisions_titles` — every legal provision ever published, as titles

`download_legal_provisions_titles` builds a compact `codNota` + `titulo` +
`fecha` dataset covering every legal provision published since 1917 (~1.2
million rows, a few tens of MB compressed):

```python
from pathlib import Path
from nota2md import download_legal_provisions_titles

download_legal_provisions_titles(Path("titulos.jsonl.gz"))
```

```bash
dofjson --titulos --outdir output    # -> output/titulos.jsonl.gz
```

## Development

Install a package in editable mode with its test dependencies:

```bash
pip install -e "packages/dofjson[test]"
pytest packages/dofjson
```

## License

Apache License 2.0. See [LICENSE](LICENSE).

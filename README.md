# LegalIA

[![Documentation Status](https://readthedocs.org/projects/legalia/badge/?version=latest)](https://legalia.readthedocs.io/en/latest/?badge=latest)

A monorepo of Python packages for the analysis of legal texts in the Mexican
context, developed by [INGEOTEC](https://github.com/INGEOTEC). Its first
target is the *Diario Oficial de la Federación* (DOF), Mexico's official
gazette: more than 1.2 million legal provisions published without interruption
since 1917.

**Read the Docs is the developer reference** — the full API of every
package, public and private, with a worked example for every public symbol.
This README stays focused on a quick start; for datasets, findings and the
analysis of the gazette, see the project's
[website](https://ingeotec.github.io/LegalIA/) instead.

## Packages

| Package | Description |
|---|---|
| [dofjson](packages/dofjson) ([PyPI](https://pypi.org/project/dofjson/)) | Client for SIDOF's JSON open-data service: which legal provisions were published on a given day, and the full detail — including HTML content, when it exists — of any one of them. Also streams a compact `codNota` + `titulo` + `fecha` record of every legal provision ever published (`legal_provisions_titles`), off the same on-disk cache. |
| [nota2md](packages/nota2md) ([PyPI](https://pypi.org/project/nota2md/)) | Builds the Markdown of a single DOF legal provision (`legal_provisions`), reconstructs a law's current text from nothing but its legal provisions (`reconstruct_legal_provisions`), and reads the SCJN corpus of consolidated law texts back from the `scjn-leyes` release (`download_scjn_leyes_corpus`/`_index`/`_catalog`). |
| [dof2md](packages/dof2md) ([PyPI](https://pypi.org/project/dof2md/)) | Downloads a complete edition of the DOF as PDF and converts it — OCR included — to Markdown; the heavy artillery `nota2md` borrows for legal provisions that predate the HTML era. |
| [md2akn](packages/md2akn) ([PyPI](https://pypi.org/project/md2akn/)) | Segments a Mexican federal law's Markdown — the output of `nota2md` — into a navigable hierarchy labelled with Akoma Ntoso's vocabulary. Depends on none of the other three packages. |

Each package lives under `packages/<name>/` with its own `pyproject.toml`,
dependencies, version, and tests — installed and released independently to
PyPI. For the full API — every public and private symbol, with a worked
example for every public one — see
[Read the Docs](https://legalia.readthedocs.io/).

## Quick start

`nota2md` has eight entry points, all re-exported off the package itself.
Note that `legal_provisions` answers from the SCJN's consolidated law texts
by default when the `scjn-leyes` release covers the `codNota`, and only goes
to the DOF otherwise — `source="dof"` forces the original source. See
[`packages/nota2md`](packages/nota2md#the-scjn-path--a-laws-consolidated-text-at-each-reform).

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

# Every legal provision published on a given day, in publication order
notas = fetch_daily_legal_provisions(dt.date(2026, 7, 15))
cod_nota = notas[0]["codNota"]

# The legal provision's Markdown, from its official HTML
md_path = legal_provisions(cod_nota, Path("output"), source="html")

# With no outdir at all: written into nota2md's cache, path returned
md_path = legal_provisions(cod_nota)
```

The same round trip is available from the command line:

```bash
dofjson 2026-07-15 --outdir output     # -> output/15072026-notas.json
nota2md 5793639 --source dof --outdir output   # -> output/nota-5793639.md
```

### `download_scjn_leyes_corpus` and `reconstruct_legal_provisions` — a law's current text from its reform history

```python
from pathlib import Path

from nota2md import download_scjn_leyes_corpus, reconstruct_legal_provisions

# One law's reform history: one entry per reform, oldest first, each with the
# codNota of the DOF decree that published it (None where it is not linked)
cpeum = download_scjn_leyes_corpus("cpeum")
historial = [s["codNota"] for s in cpeum["snapshots"] if s["codNota"]]

# The law's current (vigente) text, reconstructed from nothing but its own
# DOF legal provisions
dest = reconstruct_legal_provisions(
    historial, Path("output"),
    nombre_ley="CONSTITUCIÓN Política de los Estados Unidos Mexicanos",
)
```

### `legal_provisions_titles` — every legal provision ever published, as titles

`legal_provisions_titles` streams a compact `codNota` + `titulo` + `fecha` +
`codOrgaUno` record for every legal provision published since 1917 (~1.2
million of them), read straight off the `notas-archivo` cache — nothing is
written, and a populated cache means no network at all:

```bash
nota2md download gazette-metadata    # populate the cache, once
```

```python
from nota2md import legal_provisions_titles

for titulo in legal_provisions_titles():
    ...
```

## Development

Install a package in editable mode with its test dependencies, then run its
tests. The same two commands work for any of the four packages —
`dofjson`, `nota2md`, `dof2md` and `md2akn`:

```bash
pip install -e "packages/dofjson[test]"
pytest packages/dofjson
```

## License

Apache License 2.0. See [LICENSE](LICENSE).

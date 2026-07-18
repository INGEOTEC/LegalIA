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

## Tests

```bash
pytest -v
```

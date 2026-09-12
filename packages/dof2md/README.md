# dof2md — renamed to `document2md`

**This package has been renamed. `dof2md` 0.3.0 contains no code: importing it
raises `ImportError`.**

```bash
pip install document2md
```

```python
from document2md import BatchConverter
```

The public API, the CLI flags and the output are unchanged — only the name is
different. The old one was wrong: the package has had no notion of the *Diario
Oficial de la Federación* since the edition download moved out into
[`dofjson`](https://github.com/INGEOTEC/LegalIA/tree/master/packages/dofjson).
What it does is convert a PDF, or an ordered set of scanned page images, to
Markdown — whatever document they come from.

- New package: [`document2md` on
  PyPI](https://pypi.org/project/document2md/) ·
  [source](https://github.com/INGEOTEC/LegalIA/tree/master/packages/document2md)
  · [docs](https://legalia.readthedocs.io/en/latest/document2md_api.html)

Part of the [LegalIA](https://github.com/INGEOTEC/LegalIA) monorepo.

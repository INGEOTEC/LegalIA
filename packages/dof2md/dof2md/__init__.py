"""Tombstone for the renamed `dof2md` distribution: importing it raises.

The package was renamed to `document2md` (issue #228) because it has had no
notion of the *Diario Oficial de la Federación* since issue #134 moved the
edition download out into `dofjson.download_edicion_pdf`: what it converts is
a PDF or an ordered set of page images, whatever document they come from.

This release exists only so that `pip install dof2md` fails loudly, at import,
with a message saying where the code went — rather than PyPI serving 0.2.0
forever with nothing to say it was renamed. It deliberately does **not**
depend on `document2md`: a working shim would keep this package alive as real,
maintained code.

`__version__` is assigned *above* the `raise` and as a plain literal on
purpose: setuptools' `attr:` resolution and `scripts/check_package_versions.py`
both read it statically (AST, no import), which is what makes a module that
raises on import buildable and publishable at all. Moving the `raise` above it
breaks the publish workflow silently — `tests/test_tombstone.py` guards the
ordering.
"""

__version__ = "0.3.0"

raise ImportError(
    "dof2md has been renamed to document2md; this release is a tombstone and "
    "contains no code. Install the new package instead:\n\n"
    "    pip install document2md\n\n"
    "and import it as `document2md` (the public API is unchanged: "
    "`from document2md import BatchConverter`). "
    "See https://github.com/INGEOTEC/LegalIA/tree/master/packages/document2md"
)

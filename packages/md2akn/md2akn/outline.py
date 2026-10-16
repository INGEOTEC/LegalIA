"""The outline markers `scjn.api` writes before every block of a snapshot
(rules 19-23, review fix-2 of #291).

The SCJN's API states the structure of an instrument -- the label of each
block it serves (`ÍNDICE`, `TÍTULO PRIMERO`, `ARTÍCULO 6 TER`, `ANEXO 1`,
`PARTE 3`, `D.O.F. 10 DE NOVIEMBRE DE 2023`) -- and the Markdown writer keeps
it as one HTML comment line before the block::

    <!-- scjn:block orden=6 numero=0 kind=toc label="ÍNDICE" -->

This module reads that line back. The grammar is the one documented in
`scjn.outline`; it is a copy on purpose (`md2akn` imports nothing else of this
monorepo) and `tests/test_outline_markers.py` parses lines a real fixture of
that package wrote, so the two cannot drift. A marker is **metadata, not
text**: the builder gives it no node, `text_units` strips it exactly as it
strips a reform annotation, and `coverage()` counts its characters as
annotation.
"""

from __future__ import annotations

import json
import re

#: A marker line, whole. Matched against a block's text (a marker is a block of
#: its own: the writer puts a blank line on each side) and, with `MULTILINE`,
#: against the whole document to find every range to strip.
MARCADOR_ESQUEMA = re.compile(r"^<!-- scjn:block (?P<attrs>.*?) -->[ \t]*$")
MARCADORES_EN_TEXTO = re.compile(r"^<!-- scjn:block .*? -->[ \t]*$", re.MULTILINE)

_ATRIBUTO = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|\S+)')


def lee_marcador(texto: str) -> dict[str, str] | None:
    """The attributes of a marker line (`label`/`num` decoded), or `None` when
    `texto` is not one.

    >>> lee_marcador('<!-- scjn:block orden=7 numero=1 kind=article num="6 TER" label="ARTÍCULO 6 TER" -->')["num"]
    '6 TER'
    >>> lee_marcador("**Artículo 1.**") is None
    True
    """
    m = MARCADOR_ESQUEMA.match(texto.strip())
    if m is None:
        return None
    attrs: dict[str, str] = {}
    for clave, valor in _ATRIBUTO.findall(m.group("attrs")):
        attrs[clave] = json.loads(valor) if valor.startswith('"') else valor
    return attrs

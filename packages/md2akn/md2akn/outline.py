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


# -- the blocks the API calls `other` (rules 29 and 30, review fix-4 of #291) --

_ACENTOS = str.maketrans("ÁÉÍÓÚÜÑáéíóúüñ", "AEIOUUNaeiouun")
_ESPACIOS = re.compile(r"\s+")


def pliega_etiqueta(etiqueta: str) -> str:
    """A block's label as it is compared: upper case, no accents, whitespace
    collapsed, no surrounding punctuation.

    >>> pliega_etiqueta("Ratificación  y depósito")
    'RATIFICACION Y DEPOSITO'
    """
    plano = etiqueta.translate(_ACENTOS).upper()
    return _ESPACIOS.sub(" ", plano).strip(" .:;,-")


#: The roles a label can carry (rule 29), as ``(role, pattern)`` in the order they
#: are tried, each pattern matched against the *whole* folded label. An explicit
#: table, not a guess per instrument: the vocabulary of the blocks the API calls
#: `other` is open (2,620 distinct shapes over the four corpora once digits are
#: folded), but a few dozen words account for most of the blocks.
#:
#: - `conclusions`: `PARTE FINAL DEL ACUERDO`, `PARTE FINAL DE LAS ACTAS` -- the
#:   closing signatures and formulas, wherever the API puts them.
#: - `promulgation`: the blocks of the Mexican promulgation decree that carries a
#:   treaty into the national legal order (`FIRMA`, `APROBACIÓN`, `RESERVA(S)`,
#:   `RATIFICACIÓN Y DEPÓSITO`, `PROMULGACIÓN`, `CERTIFICACIÓN`, `DECLARACIÓN ...`,
#:   and the steps of the same procedure: `ADOPCIÓN`, `ACEPTACIÓN`, `ADHESIÓN`,
#:   `DEPÓSITO`, `NOTIFICACIÓN`). Consecutive ones are kept together, outside the
#:   treaty's own text. A label that merely *starts* with one of the words
#:   (`RESERVAS AL REGLAMENTO DE RADIOCOMUNICACIONES`, the reservations of a
#:   conference's own text) is **not** one: the tails admitted are the listed
#:   connectives only.
#: - `note`: `NOTA EXPLICATIVA`, `NOTAS`.
#: - `resolution`: `RESOLUCIÓN MSC.404(96)` -- an amending resolution is a text of
#:   its own, always a `section` (rule 33: the container rule 23 gave one that
#:   holds articles, now every one, so sibling resolutions are of one type).
#: - everything else has no role: a generic labelled container.
_PASO = (
    r"(?:FIRMA|APROBACION|RESERVAS?|RATIFICACION|ACEPTACION|ADHESION|ADOPCION|DEPOSITO|"
    r"NOTIFICACIONES?|PROMULGACION|CERTIFICACION)"
)
ROLES_DE_ETIQUETA: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    ("conclusions", re.compile(r"PARTE FINAL(?: .*)?")),
    ("promulgation", re.compile(
        rf"{_PASO}(?:(?:,| Y) {_PASO})*|DECLARACIONES?(?: .*)?|"
        r"INSTRUMENTO DE (?:RATIFICACION|ACEPTACION|ADHESION|APROBACION)"
    )),
    ("note", re.compile(r"NOTAS?(?: EXPLICATIVAS?)?")),
    ("resolution", re.compile(r"RESOLUCION(?: .*)?")),
)


def rol_de_etiqueta(etiqueta: str) -> str | None:
    """The role of an `other` block's label (rule 29), or `None` for a generic one.

    >>> rol_de_etiqueta("PARTE FINAL DEL DECRETO PROMULGATORIO")
    'conclusions'
    >>> rol_de_etiqueta("RATIFICACIÓN Y DEPÓSITO")
    'promulgation'
    >>> rol_de_etiqueta("RESERVAS AL REGLAMENTO DE RADIOCOMUNICACIONES") is None
    True
    >>> rol_de_etiqueta("NOTA EXPLICATIVA")
    'note'
    >>> rol_de_etiqueta("RESOLUCIÓN MSC.404(96)")
    'resolution'
    """
    plano = pliega_etiqueta(etiqueta)
    for rol, patron in ROLES_DE_ETIQUETA:
        if patron.fullmatch(plano):
            return rol
    return None


_CONTENIDO = re.compile(r"CONTENIDO (?:(?:DE LAS|DE LOS|DE LA|DEL|DE) )?(?P<resto>.+)")


def es_continuacion(etiqueta: str, anterior: str) -> bool:
    """Rule 30: whether a block labelled `etiqueta` is the content of the block
    labelled `anterior`, which the API split in two -- a block with only the
    title (`ARTÍCULOS`) and a second with its text (`CONTENIDO ARTICULOS`).

    >>> es_continuacion("CONTENIDO ARTICULOS", "ARTÍCULOS")
    True
    >>> es_continuacion("CONTENIDO DE LAS ENMIENDAS DE 1977", "ENMIENDAS DE 1977")
    True
    >>> es_continuacion("CONTENIDO APÉNDICES", "ARTÍCULOS")
    False
    """
    m = _CONTENIDO.fullmatch(pliega_etiqueta(etiqueta))
    return m is not None and m.group("resto") == pliega_etiqueta(anterior)

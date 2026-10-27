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
#: Review fix-1 of #301 widened this only where the measurement showed a decree step
#: missed: `DECLARACIÓN`/`DECLARACIONES` as the second step of `APROBACIÓN Y DECLARACIÓN`
#: (and the labels listed in the `promulgation` pattern below). `APROBACION` followed by
#: anything else is *not* a step (`SECCIÓN 5 APROBACIÓN`, an annex title naming an
#: approval): only the whole labels match. The blocks of the decree that have no such
#: label (`CANJE DE NOTAS`) are read by position, not by label (`bloques_de_decreto`).
_PASO = (
    r"(?:FIRMA|APROBACION|RESERVAS?|RATIFICACION|ACEPTACION|ADHESION|ADOPCION|DEPOSITO|"
    r"NOTIFICACIONES?|PROMULGACION|CERTIFICACION|DECLARACION(?:ES)?)"
)
ROLES_DE_ETIQUETA: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    ("conclusions", re.compile(r"PARTE FINAL(?: .*)?")),
    ("promulgation", re.compile(
        rf"{_PASO}(?:(?:,| Y) {_PASO})*|DECLARACIONES?(?: .*)?|"
        r"INSTRUMENTO DE (?:RATIFICACION|ACEPTACION|ADHESION|APROBACION)|"
        r"APROBACION (?:DEL PROYECTO|DE INSTRUMENTOS|DE LA ASAMBLEA GENERAL DE LAS NACIONES UNIDAS)|"
        r"APROBACION, RATIFICACION Y CANJE DE RATIFICACIONES"
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


#: The longest single line a block can be and still be only a title (rule 40),
#: the same bound rule 38 uses for a heading block's lines.
MAX_LINEA_DE_TITULO = 160


def es_solo_titulo(texto: str) -> bool:
    """Rule 40: whether the text of an API block (its marker line left out) is
    only a title -- one short line, bold or not, with no body. Reform
    annotations (`(REFORMADO, D.O.F. ...)`) do not count as text.

    >>> es_solo_titulo("**ANEXO**")
    True
    >>> es_solo_titulo("ANEXO A LA RESOLUCION No. 46 (CAMR-92)")
    True
    >>> es_solo_titulo("**TÍTULO**\\n\\nArtículo 1.- Texto.")
    False
    >>> es_solo_titulo("")
    False
    """
    lineas = [
        linea.replace("**", "").strip()
        for linea in texto.splitlines()
        if linea.strip()
    ]
    lineas = [linea for linea in lineas if not linea.startswith("(")]
    return len(lineas) == 1 and len(lineas[0]) <= MAX_LINEA_DE_TITULO


def es_contenido_suelto(
    etiqueta: str, kind: str, texto_anterior: str, kind_anterior: str = "other"
) -> bool:
    """Rule 40 (issue #300): whether an API block is the bare `CONTENIDO` the
    SCJN serves as the body of the block before it, when that block holds only
    its own title (`ANEXO`, `ANEXO 4`, `PROTOCOLO`). The second half of a split
    block like rule 30's, but with no label after `CONTENIDO`, so rule 30 does
    not recognise it. A `CONTENIDO` after a block that has text of its own (a
    `CONSIDERANDO` followed by an index), or after a header, recitals, preamble or
    article block, is not this one.

    >>> es_contenido_suelto("CONTENIDO", "other", "**ANEXO**")
    True
    >>> es_contenido_suelto("CONTENIDO", "other", "**ANEXO**", "container")
    True
    >>> es_contenido_suelto("CONTENIDO", "other", "**CONSIDERANDO**", "recitals")
    False
    >>> es_contenido_suelto("CONTENIDO", "other", "CONSIDERANDO\\n\\nQue el decreto.")
    False
    >>> es_contenido_suelto("CONTENIDO", "container", "**ANEXO**")
    False
    >>> es_contenido_suelto("CONTENIDO ARTICULOS", "other", "**ANEXO**")
    False
    """
    return (
        kind == "other"
        and kind_anterior in ("other", "container")
        and pliega_etiqueta(etiqueta) == "CONTENIDO"
        and es_solo_titulo(texto_anterior)
    )


#: The folded label of the block that closes a Mexican promulgation decree (rule 29's
#: `conclusions` role, narrowed): a treaty's *own* closing clause (`PARTE FINAL DEL
#: ESTATUTO`, `PARTE FINAL DE LAS ACTAS`) is part of the instrument and is not it, and
#: neither is the closing of a decree that is itself the instrument (`PARTE FINAL DEL
#: DECRETO`, a treaty entry that is only a decree). Review fix-1 of #301 admits the
#: spellings of the same closing that the measurement found (`PARTE FINAL DE DECRETO
#: PROMULGATORIO`, `... DEL DECRETO DE PROMULGACIÓN`, `... DE LA CERTIFICACIÓN`).
ETIQUETA_PARTE_FINAL_DEL_DECRETO = "PARTE FINAL DEL DECRETO PROMULGATORIO"
PARTE_FINAL_DE_DECRETO = re.compile(
    r"PARTE FINAL DE(?:L| LA)? (?:DECRETO PROMULGATORIO|DECRETO DE PROMULGACION|"
    r"PROMULGACION|CERTIFICACION)"
)


def es_etiqueta_de_decreto(etiqueta: str) -> bool:
    """Whether a label names a step of the Mexican promulgation decree or its closing
    certification, *wherever the block stands* -- position decides whether it is
    envelope (`bloques_de_decreto`).

    >>> es_etiqueta_de_decreto("PROMULGACIÓN")
    True
    >>> es_etiqueta_de_decreto("PARTE FINAL DEL DECRETO PROMULGATORIO")
    True
    >>> es_etiqueta_de_decreto("APROBACIÓN DE INSTRUMENTOS")
    True
    >>> es_etiqueta_de_decreto("SECCIÓN 5 APROBACIÓN")
    False
    >>> es_etiqueta_de_decreto("PARTE FINAL DEL ACTA")
    False
    >>> es_etiqueta_de_decreto("PARTE FINAL DEL DECRETO")
    False
    """
    plano = pliega_etiqueta(etiqueta)
    return rol_de_etiqueta(etiqueta) == "promulgation" or PARTE_FINAL_DE_DECRETO.fullmatch(plano) is not None


def bloques_de_decreto(marcas: list[dict[str, str]]) -> dict[int, str]:
    """Review fix-1 of #301: which API blocks, by position in `marcas` (the markers of
    one document in order), are the Mexican promulgation decree wrapped around the
    instrument, as ``{index: "leading" | "trailing"}``.

    A label alone does not decide it (a treaty's own testimonium is labelled `FIRMA`;
    the decree's opening may be labelled anything). The decree is a *run* of blocks at
    one end of the document:

    * the **leading run** starts right after the title block(s) and goes on while the
      blocks are `kind=other`; it must open with a block whose label names a decree
      step (`es_etiqueta_de_decreto`), and a block with no role at all (`CANJE DE
      NOTAS`, `NOTIFICACIÓN`) stays in it when a decree step comes after it, because
      the decree interleaves them. A block after the last decree step is the
      instrument's own; so is a block with a role of its own (`PARTE FINAL DEL ACTA`,
      `NOTA`, `RESOLUCIÓN ...`) or any other kind (a preamble, an article, a container).
    * the **trailing run** is the same read backwards from the last block, down to
      where the leading run ended.

    A decree step *between* the instrument's own blocks is its own text and is in
    neither run.

    >>> blocks = [{"kind": "header"}, {"kind": "other", "label": "FIRMA"},
    ...           {"kind": "other", "label": "CANJE DE NOTAS"},
    ...           {"kind": "other", "label": "PROMULGACIÓN"}, {"kind": "preamble"}]
    >>> bloques_de_decreto(blocks)
    {1: 'leading', 2: 'leading', 3: 'leading'}
    >>> blocks = [{"kind": "header"}, {"kind": "preamble"}, {"kind": "article"},
    ...           {"kind": "other", "label": "FIRMA"}, {"kind": "other", "label": "PARTE FINAL DEL ACTA"}]
    >>> bloques_de_decreto(blocks)
    {}
    """
    n = len(marcas)

    def tipo(k):
        return marcas[k].get("kind")

    def decreto(k):
        return tipo(k) == "other" and es_etiqueta_de_decreto(marcas[k].get("label", ""))

    def generico(k):
        return tipo(k) == "other" and rol_de_etiqueta(marcas[k].get("label", "")) is None

    resultado: dict[int, str] = {}

    def corre(indices, nombre):
        pendientes: list[int] = []
        encontrados: dict[int, str] = {}
        for k in indices:
            if decreto(k):
                encontrados.update({p: nombre for p in pendientes})
                encontrados[k] = nombre
                pendientes = []
            elif generico(k) and encontrados:
                pendientes.append(k)
            else:
                break
        return encontrados

    inicio = 0
    while inicio < n and tipo(inicio) == "header":
        inicio += 1
    delante = corre(range(inicio, n), "leading")
    resultado.update(delante)
    limite = max(delante, default=inicio - 1) + 1
    resultado.update(corre(range(n - 1, limite - 1, -1), "trailing"))
    return resultado


def es_bloque_de_envoltura(attrs: dict[str, str]) -> bool:
    """Issue #301, kept for a block read alone: whether it is a title block. Whether a
    decree block is envelope depends on its position (`bloques_de_decreto`, review fix-1
    of #301), so `rangos_de_envoltura` reads the whole document.

    >>> es_bloque_de_envoltura({"kind": "header", "label": "ENCABEZADO"})
    True
    >>> es_bloque_de_envoltura({"kind": "other", "label": "RATIFICACIÓN Y DEPÓSITO"})
    False
    """
    return attrs.get("kind") == "header"


def rangos_de_envoltura(texto: str) -> list[tuple[int, int]]:
    """The character ranges of `texto` that are envelope blocks, as `(start, end)`.

    The envelope (issue #301, by position since review fix-1) is the title block, the
    decree's leading run and its trailing run (`bloques_de_decreto`), read from the
    markers alone: no eId, no `path`, no instrument. A block runs from where the text
    before its marker ends (the blank lines between two blocks belong to the block that
    follows, the way `validate` reads them) to the next block's start. A document with no
    markers has none.
    """
    encontradas = list(MARCADORES_EN_TEXTO.finditer(texto))
    inicios = [len(texto[: m.start()].rstrip()) if m.start() else 0 for m in encontradas]
    fines = [*inicios[1:], len(texto) + 1]
    marcas = [lee_marcador(m.group(0)) or {} for m in encontradas]
    decreto = bloques_de_decreto(marcas)
    return [
        (inicio, fin)
        for k, (inicio, fin) in enumerate(zip(inicios, fines))
        if k in decreto or es_bloque_de_envoltura(marcas[k])
    ]

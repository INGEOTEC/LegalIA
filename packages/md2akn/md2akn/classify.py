"""What one block of Markdown says it is, from its own text (issue #159; split out of
`md2akn.structure` by issue #304).

The tools every other layer reads blocks with, none of which looks at the outline
markers: `clasifica` (a block's class from its first line), `es_fila_tabla`, and
`modo_sin_articulos`, rule B3's per-document decision of how an instrument that never
writes `Artículo N` numbers its provisions. `md2akn.skeleton` reads the SCJN's markers
on top of these, and `md2akn.structure` places the blocks in a tree below them.
"""

from __future__ import annotations

import re

from md2akn.annotations import es_anotacion
from md2akn.model import REFERS_TO_ANEXO, REFERS_TO_APENDICE
from md2akn.patterns import (
    ANOTACION,
    ARTICULO,
    ARTICULO_ORDINAL,
    CONTENEDOR,
    CONTENEDOR_ANEXO,
    CONTENEDOR_PARTE,
    CONTENEDORES,
    DOF_TRANSITORIOS,
    ETIQUETA_TABLA,
    FILA_TABLA,
    NOTA_EDITORIAL,
    NUMERAL_DECIMAL,
    TRANSITORIOS,
)

_ACENTOS = str.maketrans("ÁÉÍÓÚÜáéíóúü", "AEIOUUaeiouu")
_ESPACIOS = re.compile(r"\s+")


def _sin_acentos_mayusculas(texto: str) -> str:
    return texto.translate(_ACENTOS).upper()


def _limpia(texto: str) -> str:
    """A heading's text without its Markdown bold markers or its trailing
    punctuation — what a reader would call the epigraph."""
    return _ESPACIOS.sub(" ", texto.replace("**", "").strip()).strip(" .:")


def _titulo(resto) -> str | None:
    """The short title a container heading carries after its number
    (`PARTE I. DISPOSICIONES GENERALES`), or `None` when it has none."""
    return _limpia(resto or "").lstrip("-–— ").strip() or None


def es_fila_tabla(bloque) -> bool:
    """Whether `bloque` is a table row (rule B7, issue #291): a block that
    opens with `|` and closes with `|`, the shape `scjn.api` writes one
    recovered row in since issue #253."""
    return bool(FILA_TABLA.match(bloque.text.strip()))


def es_etiqueta_de_tabla(bloque) -> bool:
    """Whether `bloque` is the one non-row block a table tolerates inside
    itself (`**(ARANCEL)**`, see `patterns.ETIQUETA_TABLA`)."""
    return bool(ETIQUETA_TABLA.match(bloque.text.strip()))


def clasifica(bloque) -> tuple[str, dict]:
    """What a block is, from its first line alone, as ``(kind, fields)``.

    The order of the tests is the rule, not an implementation detail:

    - Annotations and editorial notes are checked **first**. `(DEROGADO, ...)`
      opens with a capitalized word and would otherwise be examined as prose;
      more importantly, `**(ADICIONADO CON LOS ARTÍCULOS QUE LO INTEGRAN, ...)`
      contains the word "ARTÍCULOS" and must never be read as an article.
    - Transitorios before containers, because `**ARTICULOS TRANSITORIOS**`
      would otherwise be examined as an article heading.
    - Containers before articles: no container word can open an article
      heading, so the order between those two is free, but keeping the
      broadest patterns first makes the sequence readable.
    """
    linea = bloque.text.split("\n", 1)[0]

    if NOTA_EDITORIAL.match(linea):
        return "nota_editorial", {}
    m = ANOTACION.match(linea)
    if m and es_anotacion(m.group("cuerpo")):
        return "anotacion", {"cuerpo": m.group("cuerpo")}
    if TRANSITORIOS.match(linea):
        return "transitorios", {}
    if DOF_TRANSITORIOS.match(linea):
        m = DOF_TRANSITORIOS.match(linea)
        return "transitorios_dof", {"num": _limpia(m.group("fecha"))}

    m = CONTENEDOR.match(linea)
    if m:
        clase = _sin_acentos_mayusculas(m.group("clase"))
        num = _limpia(m.group("num"))
        if clase in CONTENEDORES and num:
            return "contenedor", {"akn_type": CONTENEDORES[clase], "num": num}

    # Rule B5 (review fix-1 of #291): `PARTE`, `ANEXO`, `APÉNDICE`.
    m = CONTENEDOR_PARTE.match(linea)
    if m:
        num = _limpia(m.group("ord") or m.group("num") or m.group("num2"))
        return "contenedor", {
            "akn_type": "part", "num": num, "heading": _titulo(m.group("resto")),
        }
    m = CONTENEDOR_ANEXO.match(linea)
    if m:
        es_anexo = _sin_acentos_mayusculas(m.group("clase")) == "ANEXO"
        return "contenedor", {
            "akn_type": "attachment",
            "num": _limpia(m.group("num") or "") or None,
            "refers_to": REFERS_TO_ANEXO if es_anexo else REFERS_TO_APENDICE,
            "heading": _titulo(m.group("resto")),
        }

    # Matched against the line with its bold markers removed. The CCF puts a
    # `**` in the middle of a number -- `**ARTICULO 410 **A.-`,
    # `**ARTICULO 103-**Bis.-` -- and reading those as plain "410" and "103"
    # collapsed six articles into two. Bold carries no information for an
    # article heading, so dropping it costs nothing; the patterns that do
    # rely on bold (the annotation and the bare-ordinal transitorio) are
    # matched against the raw line instead.
    m = ARTICULO.match(linea.replace("**", ""))
    if m:
        return "articulo", {"num": _limpia(m.group("num"))}

    m = ARTICULO_ORDINAL.match(linea)
    if m:
        # Only an article inside transitorios -- see `ARTICULO_ORDINAL` and
        # `_bloque_articulo_ordinal`. Reported as its own kind rather than as
        # an article so the builder can make that call with the context it
        # has and the classifier does not.
        return "articulo_ordinal", {"num": _limpia(m.group("num"))}

    return "contenido", {}


#: How an instrument that never writes `Artículo N` numbers its provisions
#: (issue #227's rule B3) — `modo_sin_articulos`' two answers, plus `None` for
#: "it numbers them the ordinary way, or not recognizably at all".
MODO_ORDINAL = "ordinal"
MODO_NUMERAL = "numeral"

#: How many bold ordinals (`**PRIMERO.-**`) an articleless document needs
#: before they are read as its article numbering. Measured over the 89
#: articleless lineamientos of the `scjn-lineamientos` release (issue #227):
#: 83 of them carry three or more. A document with one or two is not numbering
#: provisions with them — it is a decree quoting an ordinal in passing.
MIN_ORDINALES = 3

#: The same threshold for decimal numerals, higher because the signal is
#: weaker: 37 of the 89 carry five or more (`1.`, `2.1.`), and a handful of
#: numbered items in an otherwise unnumbered acuerdo is a list, not a
#: provision numbering scheme.
MIN_NUMERALES = 5


def modo_sin_articulos(bloques, clases=None) -> str | None:
    """Rule B3 (issue #227), decided **once per document**: how this
    instrument numbers its provisions, when it never writes `Artículo N`.

    A *lineamiento* is usually an **acuerdo**, and an acuerdo does not number
    its provisions `Artículo N`: it writes `**PRIMERO.-**`, or `1.` / `2.1`.
    70.6 % of the `scjn-lineamientos` corpus (89 of 126 instruments with text)
    has no article heading anywhere, and every character of such a document
    used to fall into the one unit that swallows whatever precedes the first
    recognized structure — the `preamble`, 51,246 characters long at its
    worst (issue #227's own measurement).

    Returns `None` when the document has any `ARTICULO` match outside its
    transitorios — the overwhelmingly common case, and what makes this rule
    safe by construction: no law, and none of the 1,073 reglamentos that do
    number articles, can change, because the rule never switches on for them.
    That is a property of the *gate*, not of a loosened pattern:
    `ARTICULO_ORDINAL` and `NUMERAL_DECIMAL` are read exactly as before
    everywhere else.

    Otherwise `MODO_ORDINAL` when the document carries at least
    `MIN_ORDINALES` bold ordinals, `MODO_NUMERAL` when it carries at least
    `MIN_NUMERALES` decimal numerals, and `None` when it carries neither —
    3 of the 89 number their provisions in some way this does not recognize
    (a roman-numeral outline, mostly), and they keep the behaviour they
    already had rather than being guessed at.

    A roman-numbered article (`ARTÍCULO I`, issue #279's rule B1) is an
    `ARTICULO` match, so a roman-numbered treaty whose paragraphs read `1.`,
    `2.` returns `None` here instead of `MODO_NUMERAL`. The 3 lineamientos
    above are unchanged by that rule (0 roman article openers measured in
    the 126 lineamientos), so the sentence stays true.

    >>> from md2akn.segmenter import iter_blocks
    >>> from md2akn.classify import modo_sin_articulos
    >>> texto = "**PRIMERO.-** Uno.\\n\\n**SEGUNDO.-** Dos.\\n\\n**TERCERO.-** Tres."
    >>> modo_sin_articulos(list(iter_blocks(texto)))
    'ordinal'
    >>> modo_sin_articulos(list(iter_blocks("**Artículo 1o.** Uno."))) is None
    True

    `clases`, when given, is `clasifica`'s answer for each of `bloques` -- what
    the builder passes for a snapshot that carries outline markers (rule S1),
    whose classes the markers have already settled.
    """
    en_transitorios = False
    ordinales = 0
    numerales = 0
    for k, bloque in enumerate(bloques):
        kind, _campos = clasifica(bloque) if clases is None else clases[k]
        if kind == "articulo":
            if not en_transitorios:
                return None
        elif kind == "transitorios":
            en_transitorios = True
        elif kind == "contenedor":
            # A container after transitorios belongs to a further decree's own
            # text, exactly as `_Constructor._bloque_contenedor` reads it.
            en_transitorios = False
        elif kind == "articulo_ordinal":
            if not en_transitorios:
                ordinales += 1
        elif kind == "contenido" and not en_transitorios:
            if NUMERAL_DECIMAL.match(bloque.text.split("\n", 1)[0]):
                numerales += 1
    if ordinales >= MIN_ORDINALES:
        return MODO_ORDINAL
    if numerales >= MIN_NUMERALES:
        return MODO_NUMERAL
    return None


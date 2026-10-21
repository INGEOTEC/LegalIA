"""Building the container/article hierarchy out of the blocks (issue #159).

The shape of a Mexican federal law, and the Akoma Ntoso element each part
becomes:

    act
    ├── preamble            "Al margen un sello...", the enacting formula
    ├── body
    │   ├── book / title / chapter / section     the containers, nesting by rank
    │   │   └── article
    │   ├── level  (refers_to="#apartado")       APARTADO A/B of article 123
    │   └── section (refers_to="#transitorios")  one per decree that added some
    └── conclusions         the closing signatures, where a law has them

Two of those are `refers_to` rather than elements of their own, because the
standard has neither: "transitorios" and "apartado".

`preamble` is a **sibling** of `body`, not a child: Akoma Ntoso's `body`
admits only hierarchical elements, and the enacting formula is not one.

Containers nest by **precedence rank**, not by any depth marker in the text
(the text has none). On meeting a container, every open container of equal or
lower rank is closed. Three consequences worth stating, since each is a real
shape in the corpus rather than a hypothetical:

- A law that opens with `CAPÍTULO I` and only later reaches `TÍTULO PRIMERO`
  comes out with the chapter closed and the title starting fresh, rather than
  with a title nested inside a chapter. The chapter is left "hanging" at the
  top of the body, which is what the document actually says.
- Numbering that restarts — two `CAPÍTULO I` under different titles — needs
  no special handling at all: the eId is a path, so the two are
  `tit_PRIMERO__cap_I` and `tit_SEGUNDO__cap_I` and never collide.
- A container whose articles were all repealed keeps its node, with its span,
  as a leaf. An empty chapter is a fact about the law.

Fracciones and incisos inside an article are #160; the `(REFORMADO, ...)`
annotations are #161. Both are tolerated here — an annotation block is
recognized only so that it is never mistaken for an article's closing
paragraph — and neither is interpreted.
"""

from __future__ import annotations

import re

from md2akn.annotations import es_anotacion, parse_annotation
from md2akn.lists import ConstructorDeArticulo, Pendientes
from md2akn.divisions import (
    ARTICULO_ORDINAL_MIXTO,
    ENCABEZADO_APENDICE_RR,
    ENCABEZADO_ARTICULO_RR,
    PROVISION_RR,
    SE_CERTIFICA,
    VEASE_ARCHIVO_ANEXO,
    encabezado_debil,
    encabezado_fuerte,
    es_encabezado_de_formulario,
    inicio_de_la_serie,
)
from md2akn.outline import es_continuacion, lee_marcador, rol_de_etiqueta
from md2akn.model import (
    REFERS_TO_ANEXO,
    REFERS_TO_APARTADO,
    REFERS_TO_APENDICE,
    REFERS_TO_CONCLUSIONES,
    REFERS_TO_DIVISION,
    REFERS_TO_FORMULARIO,
    REFERS_TO_NOTA,
    REFERS_TO_PROMULGACION,
    REFERS_TO_TRANSITORIOS,
    AknNode,
    EIdAllocator,
)
from md2akn.patterns import (
    ANOTACION,
    ANOTACION_EN_LINEA,
    ARTICULO,
    ARTICULO_ORDINAL,
    CONCLUSIONES,
    CONCLUSIONES_ULTIMOS_BLOQUES,
    CONSIDERANDO,
    CONTENEDOR,
    CONTENEDOR_ANEXO,
    CONTENEDOR_PARTE,
    CONTENEDORES,
    DOF_TRANSITORIOS,
    ETIQUETA_TABLA,
    LEYENDA_TABLA,
    FILA_TABLA,
    MARCADOR_DISPOSITIVO,
    MARCADOR_INDICE,
    MAX_EPIGRAFE,
    NOTA_EDITORIAL,
    NUMERAL_DECIMAL,
    ORDINAL_PALABRA,
    PRECEDENCIA,
    PRECEDENCIA_PARTE_EN_CAPITULO,
    SOLO_NEGRITAS,
    TRANSITORIOS,
)

#: eId prefixes, by `akn_type` — Akoma Ntoso's own abbreviations.
PREFIJO_EID = {
    "book": "book",
    "title": "tit",
    "chapter": "cap",
    "section": "sec",
    "level": "lvl",
    "article": "art",
    "paragraph": "para",
    "point": "point",
    "subpoint": "subpoint",
    # A paragraph is a citable unit of its own ("el párrafo segundo del
    # artículo 1o.", and reforms published against it) -- issue #181.
    "content": "p",
    "table": "table",
    # Review fix-1 of #291 (rules 15 and 16).
    "part": "part",
    "attachment": "att",
    "toc": "toc",
    # Review fix-4 of #291 (rules 29 to 31): a labelled block or a division.
    "hcontainer": "hc",
}

#: The eId prefix of an appendix (rule 26, review fix-3 of #291): AKN types it as
#: an `attachment`, like an annex, and `refersTo` tells them apart.
PREFIJO_EID_APENDICE = "app"

_ORDINAL_PALABRA = re.compile(ORDINAL_PALABRA, re.IGNORECASE | re.UNICODE)

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
    """Whether `bloque` is a table row (rule 13, issue #291): a block that
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

    # Rule 16 (review fix-1 of #291): `PARTE`, `ANEXO`, `APÉNDICE`.
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
#: (issue #227's rule 8) — `modo_sin_articulos`' two answers, plus `None` for
#: "it numbers them the ordinary way, or not recognizably at all".
MODO_ORDINAL = "ordinal"
MODO_NUMERAL = "numeral"

#: Rule 18 (review fix-1 of #291) switch: a whole numeral that starts over is a
#: list item. `True` is the rule; the one script that measures what each rule of
#: the fix contributed (`scripts/embeddings/measure_repeated_numbers.py`) turns it
#: off to see the number repeat again. Not a parameter of the public API.
REINICIO_ES_ITEM = True

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
    """Rule 8 (issue #227), decided **once per document**: how this
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

    A roman-numbered article (`ARTÍCULO I`, issue #279's rule 10) is an
    `ARTICULO` match, so a roman-numbered treaty whose paragraphs read `1.`,
    `2.` returns `None` here instead of `MODO_NUMERAL`. The 3 lineamientos
    above are unchanged by that rule (0 roman article openers measured in
    the 126 lineamientos), so the sentence stays true.

    >>> from md2akn.segmenter import iter_blocks
    >>> from md2akn.structure import modo_sin_articulos
    >>> texto = "**PRIMERO.-** Uno.\\n\\n**SEGUNDO.-** Dos.\\n\\n**TERCERO.-** Tres."
    >>> modo_sin_articulos(list(iter_blocks(texto)))
    'ordinal'
    >>> modo_sin_articulos(list(iter_blocks("**Artículo 1o.** Uno."))) is None
    True

    `clases`, when given, is `clasifica`'s answer for each of `bloques` -- what
    the builder passes for a snapshot that carries outline markers (rule 19),
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


def _es_epigrafe(bloque, kind: str) -> bool:
    """Whether `bloque` is the epigraph of the container just opened.

    The corpus writes a container's title in the block *after* the container
    line, never on the same line::

        **CAPITULO I.**

        **DE LOS DERECHOS HUMANOS Y SUS GARANTIAS.**

    so the rule has to reach forward one block — and it has to be
    conservative, because reaching forward wrongly swallows the first
    paragraph of the law's text into a heading. It is taken only when the
    block is short, matched no other pattern, and either is entirely bold or
    is a single line that does not read as a sentence (no closing
    punctuation). That covers both the bold form above and the unadorned one
    the newer laws use (`Capítulo I` followed by `Del Código Nacional de
    Procedimientos Civiles y Familiares`).

    A bold epigraph may run to several lines — the corpus hard-wraps long
    ones, each line bolded separately — so "entirely bold" is checked line by
    line rather than over the block as a whole.
    """
    if kind != "contenido":
        return False
    # A table row is the container's own first table, never its title: a
    # single line that does not end in a full stop would otherwise pass the
    # test below (issue #291).
    if es_fila_tabla(bloque):
        return False
    texto = bloque.text
    if len(texto) > MAX_EPIGRAFE:
        return False
    lineas = [linea for linea in texto.splitlines() if linea.strip()]
    if lineas and all(SOLO_NEGRITAS.match(linea) for linea in lineas):
        return True
    if len(lineas) != 1:
        return False
    return not lineas[0].rstrip().endswith((".", ";", ":"))


#: Longest a block of a table of contents may be (rule 15). Some indexes carry
#: long titles (`5.5.3 Disposiciones especiales aplicables a los bultos...`),
#: so the bound is generous; `MAX_ENCABEZADO_INDICE` is what a heading that
#: *repeats* an entry may be.
MAX_ENTRADA_INDICE = 600
MAX_ENCABEZADO_INDICE = 250

#: How many of its first entries the body is expected to repeat (rule 15).
VENTANA_INDICE = 6

#: How many entries a table of contents needs. A marker (`Contenido`) followed
#: by two short lines is a table column or a caption, not an index.
MIN_ENTRADAS_INDICE = 3

_PALABRAS_DE_CLASE = {
    "libro": "libro", "titulo": "titulo", "capitulo": "capitulo", "seccion": "seccion",
    "apartado": "apartado", "parte": "parte", "anexo": "anexo", "apendice": "apendice",
    "articulo": "articulo", "art": "articulo",
}
_CLASE_Y_NUMERO = re.compile(
    r"^(libro|t[ií]tulo|cap[ií]tulo|secci[oó]n|apartado|parte|anexo|ap[eé]ndice|"
    r"art[ií]culo|art)\.?[ \t]+(\S+)",
    re.IGNORECASE | re.UNICODE,
)
_ETIQUETA_DE_ENTRADA = re.compile(r"^(?:\d+(?:\.\d+)*|[IVXLCDM]+|[A-Za-z])[.)\-][ \t]+")
_PAGINA_AL_FINAL = re.compile(r"[ \t]+\d{1,4}$")
_NO_ALFANUMERICO = re.compile(r"[^0-9a-z ]")


def _plegar(texto: str) -> str:
    """`texto` without accents, case, punctuation or runs of whitespace."""
    plano = _sin_acentos_mayusculas(texto).lower()
    return _ESPACIOS.sub(" ", _NO_ALFANUMERICO.sub(" ", plano)).strip()


def _clave_de_entrada(bloque) -> tuple[str, str]:
    """What identifies a heading whether it is written as a table-of-contents
    entry or as the heading it lists (rule 15): a container or article word and
    its number (`CAPÍTULO I. DISPOSICIONES GENERALES.` and `CAPÍTULO I
    Disposiciones Generales.` are both ``("capitulo", "i")``), else the title
    with its leading label and its trailing page number dropped (`I. OBJETIVO`
    and `OBJETIVO` are both ``("titulo", "objetivo")``). An entry written as a
    table row (`| CAPÍTULO I Disposiciones Generales | 9 |`) is read by its
    first cell."""
    linea = bloque.text.split("\n", 1)[0].strip()
    if linea.startswith("|"):
        celdas = [c.strip() for c in linea.strip("|").split("|")]
        linea = next((c for c in celdas if c), "")
    texto = _ESPACIOS.sub(" ", linea.replace("**", "").replace("#", " ")).strip(" .:")
    m = _CLASE_Y_NUMERO.match(texto)
    if m:
        clase = _PALABRAS_DE_CLASE[_plegar(m.group(1))]
        return clase, _plegar(m.group(2).strip(".,:;-–"))
    texto = _PAGINA_AL_FINAL.sub("", _ETIQUETA_DE_ENTRADA.sub("", texto))
    return "titulo", _plegar(texto)


def _es_entrada_de_indice(bloque, clase: str) -> bool:
    texto = bloque.text.strip()
    return (
        len(texto) <= MAX_ENTRADA_INDICE
        and texto.count("\n") <= 1
        and clase not in ("anotacion", "nota_editorial")
    )


def rangos_de_indice(bloques, clases, fin) -> dict[int, int]:
    """Rule 15 (review fix-1 of #291): where each table of contents is, as
    ``{index of its marker block: index of the first block after it}``.

    A table of contents is the run of short blocks (one or two lines, table
    rows included) that follows a marker (`MARCADOR_INDICE`: a block that is
    only `Índice`, `Contenido`...) -- at least `MIN_ENTRADAS_INDICE` of them --
    **and that the document then repeats**: one of its first `VENTANA_INDICE`
    entries reappears, as `_clave_de_entrada` reads it, as a heading inside
    that same run of short blocks, which carries on into the body's own first
    headings (the first one, normally; the IMDG Code's second index lists two
    parts that live in another volume and is followed by the third). Only that
    repetition makes it an index; a `Contenido` with no repeat is a column
    header or a caption and is left alone, which is why no marker alone ever
    swallows text. The index ends at the first repeat, minus any paragraph
    that sits just before it, so the preamble some documents put between their
    index and their first heading is never part of it.

    `clases` is `clasifica` of every block, computed once by the caller.
    Only the first `fin` blocks are looked at (the closing signatures are not).
    """
    rangos: dict[int, int] = {}
    i = 0
    while i < fin:
        marcador = bloques[i].text.strip()
        if "\n" in marcador or not MARCADOR_INDICE.match(marcador):
            i += 1
            continue
        j = i + 1
        marcas_cruzadas: list[int] = []
        while j < fin and _es_entrada_de_indice(bloques[j], clases[j][0]):
            if lee_marcador(bloques[j].text) is not None:
                # Rule 34 (review fix-6 of #291): a marker is a boundary. An index
                # may reach into the next API block only up to the heading that
                # block opens with (the `PRESENTACIÓN` below), never past two
                # markers: a short decree header and a list of short lines in the
                # next block are not entries of this block's index.
                if len(marcas_cruzadas) == 1:
                    break
                marcas_cruzadas.append(j)
            j += 1
        # The first entries are what the body is expected to replay; an entry
        # written twice among them (`Véase el volumen 1`) identifies nothing.
        ventana = min(VENTANA_INDICE, (j - i - 1) // 2)
        claves = [_clave_de_entrada(bloques[k]) for k in range(i + 1, i + 1 + ventana)]
        candidatas = {c for c in claves if claves.count(c) == 1}
        repite = next(
            (
                k for k in range(i + 1 + ventana, j)
                if len(bloques[k].text) <= MAX_ENCABEZADO_INDICE
                and _clave_de_entrada(bloques[k]) in candidatas
                # Rule 34: across a marker, only the heading the next block opens
                # with (the marker line, then at most an epigraph) repeats an entry.
                and all(k <= m + 2 for m in marcas_cruzadas if m < k)
            ),
            None,
        ) if ventana >= MIN_ENTRADAS_INDICE else None
        if repite is None:
            i += 1
            continue
        fin_indice = repite
        if marcas_cruzadas:
            # Rule 34 (review fix-6 of #291): an index belongs to the API block it
            # starts in, so it ends at that block's end, the next marker, when
            # the repeat lies beyond it (the heading line after the marker --
            # `CAPITULO I`, then the epigraph -- belongs to the block that opens
            # with it) or when nothing but heading-sized lines separates the
            # repeat from the marker (an entry listed twice, a `Sección Primera`
            # under two chapters, is not the body). A longer paragraph after the
            # repeat is the body, in the same block, and the index ends at the
            # repeat as before.
            marca = marcas_cruzadas[0]
            if repite > marca or all(
                len(bloques[k].text) <= MAX_ENCABEZADO_INDICE for k in range(repite, marca)
            ):
                fin_indice = marca
                if fin_indice - i - 1 < MIN_ENTRADAS_INDICE:
                    i += 1
                    continue
        while (
            fin_indice - 1 > i + MIN_ENTRADAS_INDICE
            and len(bloques[fin_indice - 1].text) > MAX_ENCABEZADO_INDICE
        ):
            fin_indice -= 1
        # Rule 29 (review fix-4 of #291): the marker line of the API block that
        # holds the first repeated heading (`PRESENTACIÓN`) is that block's, not
        # an entry of the index it follows.
        while fin_indice - 1 > i and lee_marcador(bloques[fin_indice - 1].text) is not None:
            fin_indice -= 1
        rangos[i] = fin_indice
        i = fin_indice
    return rangos


def _rango_conclusiones(bloques) -> int:
    """Index of the first block of the closing signatures, or `len(bloques)`
    when the law has none — which is the common case: only 85 of the corpus'
    315 laws end with a rubric, the rest end at their last transitorio.

    Looked for only in the last few blocks (`CONCLUSIONES_ULTIMOS_BLOQUES`):
    the same words in the middle of a law are a decree being quoted, not this
    text being signed.
    """
    inicio = max(0, len(bloques) - CONCLUSIONES_ULTIMOS_BLOQUES)
    for i in range(inicio, len(bloques)):
        if CONCLUSIONES.search(bloques[i].text):
            return i
    return len(bloques)


#: Rule 39 (review fix-6 of #291): the label of an `other` block that is a numbered
#: provision the SCJN could not classify because the source misprints its number,
#: `98 (SIC)`.
_ETIQUETA_PROVISION_SIC = re.compile(r"^(?P<num>\d+[A-Za-z]?)[ \t]*\((?:sic|SIC)\)\.?$")

_MARCA_ORDINAL = re.compile(r"(?<=\d)[oº°]")
_SOLO_ALFANUMERICO = re.compile(r"[^0-9a-z]")


def _forma_comparable(numero: str) -> str:
    """`numero` as `_numero_de_articulo` compares it: no accents, case,
    spaces, punctuation or ordinal indicator (`1o.-A BIS` is `1abis`)."""
    plano = _sin_acentos_mayusculas(numero).lower()
    return _SOLO_ALFANUMERICO.sub("", _MARCA_ORDINAL.sub("", plano))


def _extiende(largo: str, corto: str) -> bool:
    """Whether `largo` is `corto` followed by more -- not by more digits, which
    would make `10` an extension of `1`."""
    return (
        len(largo) > len(corto)
        and largo.startswith(corto)
        and not (corto[-1:].isdigit() and largo[len(corto)].isdigit())
    )


_ROMANO_VALOR = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}


def _valor_entero(numero: str) -> int | None:
    """The integer an article number starts with, arabic or roman (`82 BIS`
    is 82, `XXXVII` is 37), or `None`."""
    m = re.match(r"\s*(\d+)", numero)
    if m:
        return int(m.group(1))
    m = re.match(r"\s*([IVXLCDMivxlcdm]+)\b", numero)
    if not m:
        return None
    total = 0
    simbolos = [_ROMANO_VALOR[c.lower()] for c in m.group(1)]
    for k, valor in enumerate(simbolos):
        total += -valor if k + 1 < len(simbolos) and valor < simbolos[k + 1] else valor
    return total


def _es_el_mismo_numero_en_otra_cifra(texto: str, etiqueta: str) -> bool:
    """Whether the two print one number in two numerals, roman and arabic
    (`Artículo I` under the label `ARTÍCULO 1`): the same value and the same
    rest (`I BIS` and `1 BIS`). A treaty's text spells its articles the way the
    treaty does and the SCJN's label counts in arabic; the text's spelling is
    the one kept, as everywhere else."""
    valores = _valor_entero(texto), _valor_entero(etiqueta)
    if valores[0] is None or valores[0] != valores[1]:
        return False
    resto = [
        _forma_comparable(re.sub(r"^\s*(?:\d+|[IVXLCDMivxlcdm]+\b)", "", s)) for s in (texto, etiqueta)
    ]
    return resto[0] == resto[1]


def _numero_de_articulo(texto: str | None, etiqueta: str, numero: str | None = None) -> str:
    """The number an article block is identified by (rule 19): what its text
    prints, unless the SCJN's label says something more specific or something
    else.

    The two agree almost always (`1o.-` under the label `ARTÍCULO 1`), and then
    the text's spelling is kept. The label can be the longer one -- `ARTÍCULO 6
    (SIC)` over a text that prints `6o` -- and then it is the label's, and the
    text can be -- `ARTICULO 82 BIS` under a block the SCJN labels `ARTÍCULO 82`
    -- and then it is the text's. When they disagree the one that agrees with
    the API's own `numero` for the block wins (the label `ARTICULO XXVII` on the
    block numbered 37, whose text prints `XXXVII`; the text `916` under the
    labels 915 and 916), and failing that the label, which is the one that is
    not repeated.

    >>> _numero_de_articulo("1o", "1")
    '1o'
    >>> _numero_de_articulo("82 BIS", "82")
    '82 BIS'
    >>> _numero_de_articulo("6o", "6 (SIC)")
    '6 (SIC)'
    >>> _numero_de_articulo("916", "915", "915")
    '915'
    >>> _numero_de_articulo("XXXVII", "XXVII", "37")
    'XXXVII'
    >>> _numero_de_articulo("I", "1")
    'I'
    >>> _numero_de_articulo("IV BIS", "4 BIS")
    'IV BIS'
    >>> _numero_de_articulo(None, "7")
    '7'
    """
    if texto is None:
        return etiqueta
    t, e = _forma_comparable(texto), _forma_comparable(etiqueta)
    if t == e or _extiende(t, e):
        return texto
    if _es_el_mismo_numero_en_otra_cifra(texto, etiqueta):
        return texto
    if _extiende(e, t):
        return etiqueta
    if numero is not None and numero.strip().isdigit():
        if _valor_entero(texto) == int(numero) and _valor_entero(etiqueta) != int(numero):
            return texto
    return etiqueta


def _quita_etiqueta(linea: str, etiqueta: str) -> tuple[bool, str | None]:
    """Whether `linea` starts with the label `etiqueta`, and what follows it as
    a heading (`None` when nothing does): rule 19 reads `PARTE 1- ENSAYO DE
    INCOMBUSTIBILIDAD` under the label `PARTE 1` as the part titled `ENSAYO DE
    INCOMBUSTIBILIDAD`. Compared without accents, case, spaces or punctuation."""
    texto = linea.replace("**", "")
    objetivo = [c for c in _sin_acentos_mayusculas(etiqueta).lower() if c.isalnum()]
    if not objetivo:
        return False, None
    consumidos = 0
    for i, char in enumerate(texto):
        plano = _sin_acentos_mayusculas(char).lower()
        if not plano.isalnum():
            continue
        if plano != objetivo[consumidos]:
            return False, None
        consumidos += 1
        if consumidos == len(objetivo):
            # `PARTE 10` is not the label `PARTE 1` followed by a `0`.
            resto = texto[i + 1:]
            if resto[:1].isalnum():
                return False, None
            # A label the API wrote as a table row (`| CAPÍTULO II |  |`, the
            # SCJN's own text carries a tab) has no heading in its cells.
            resto = resto.replace("|", " ") if linea.lstrip().startswith("|") else resto
            return True, _limpia(resto).lstrip("-–— ").strip() or None
    return False, None


_LETRA_DE_LISTA = re.compile(r"\(?[a-z]{1,2}[).]")


def _es_valor_en_minuscula(resto: str) -> bool:
    """Whether the text after a numeral starts with a lower-case word (`1.1.
    cuando ...`) rather than with a capital, a list letter (`a)`) or nothing
    (rule 31, review fix-4 of #291): ``" cuando las propiedades"`` is, ``" a) Por
    el Grupo"`` and ``" Queda constituido"`` are not.
    """
    resto = resto.lstrip()
    return resto[:1].islower() and _LETRA_DE_LISTA.match(resto) is None


class _Constructor:
    """The single pass over the blocks that builds the tree.

    Kept as a class only because the walk carries state — the stack of open
    containers, the article being filled, whether transitorios have started.
    One instance per document; nothing survives between documents.
    """

    def __init__(self, doc, meta, bloques, divisiones=None):
        from md2akn.segmenter import node_span

        self._doc = doc
        self._node_span = node_span
        self._bloques = bloques
        #: Rule 31 (review fix-4 of #291): the blocks that open a division of
        #: their own, ``{block index: (label, role)}``, as the passes before this
        #: one asked for them (`pide_divisiones`); the model-certificate headings
        #: are added below, once the markers are known.
        self._divisiones: dict[int, tuple[str | None, str | None]] = dict(divisiones or {})
        self._nodos_division: set[AknNode] = set()
        self._formularios: set[AknNode] = set()
        #: The block each article was opened at, ``{eId: block index}`` -- what a
        #: later pass needs to place a division in front of a restarted series.
        self._bloque_de_articulo: dict[str, int] = {}
        self._eids = EIdAllocator()
        self._fin_cuerpo = _rango_conclusiones(bloques)

        inicio = bloques[0].start if bloques else 0
        fin = bloques[-1].end if bloques else 0
        self.act = AknNode(
            "act", self._span(inicio, fin, "act"), eId=self._eids.allocate("act"),
            meta=dict(meta),
        )
        self.body: AknNode | None = None
        self.preambulo: AknNode | None = None
        self.conclusiones: AknNode | None = None
        #: Open containers, outermost first, as (rank, node).
        self._pila: list[tuple[int, AknNode]] = []
        self._articulo: AknNode | None = None
        self._interior: ConstructorDeArticulo | None = None
        #: Annotations read but not yet attached: an annotation precedes the
        #: node it describes, so it waits for that node to exist.
        self._pendientes = Pendientes()
        self._transitorios: AknNode | None = None
        self._pendiente_epigrafe: AknNode | None = None
        #: The table (rule 13, issue #291) the previous block belonged to, as
        #: long as the blocks that follow are still its rows; any other block
        #: ends it.
        self._tabla: AknNode | None = None
        #: Rule 36 (review fix-6 of #291): the table opened at its caption, if
        #: that is the one `_tabla` is.
        self._tabla_con_leyenda: AknNode | None = None
        #: `clasifica` of every block, computed once: the table-of-contents
        #: scan, rule 8's gate, the rank of a `PARTE` and the pass itself all
        #: read it.
        self._clases = [clasifica(bloque) for bloque in bloques]
        #: Rule 19 (review fix-2 of #291): the outline markers a snapshot
        #: written by `scjn.api` carries, ``{block index: attributes}``, and
        #: for every block the marker of the API block it belongs to.
        self._marcas: dict[int, dict[str, str]] = {}
        self._de_marca: list[dict[str, str] | None] = [None] * len(bloques)
        #: The first block of each API block that is not an annotation: the
        #: one the marker's `kind` is about, ``{block index: attributes}``.
        self._primeros: dict[int, dict[str, str]] = {}
        #: The block each marker's `_primeros` entry is at, ``{marker index: block}``.
        self._destino_de_marca: dict[int, int] = {}
        #: Rule 39: the markers (by identity) of the `other` blocks read as a provision.
        self._provisiones_sic: set[int] = set()
        #: Rule 29: for an article whose caption comes before its heading line,
        #: ``{heading block index: first block index}``.
        self._encabezados_de_leyenda: dict[int, int] = {}
        #: Where the marker line not yet claimed by a node starts, so that
        #: node's span is pulled back over it (the way an annotation's is).
        self._marca_inicio: int | None = None
        self._lee_marcas()
        #: Rule 15 (review fix-1 of #291): the tables of contents, as
        #: ``{marker block index: first block index after it}``. Rule 19: a
        #: block the SCJN labels `ÍNDICE` is one, whatever its text says.
        self._indices = rangos_de_indice(bloques, self._clases, self._fin_cuerpo)
        for inicio, fin in self._indices_de_marcas().items():
            self._indices = {
                a: b for a, b in self._indices.items() if b <= inicio or a >= fin
            }
            self._indices[inicio] = fin
        en_indice = {
            k for inicio, fin in self._indices.items() for k in range(inicio, fin)
        }
        #: Rule 8 (issue #227), decided once for the whole document before
        #: the pass starts -- `None` for every law and every instrument that
        #: numbers articles the ordinary way. A table of contents is not the
        #: document's own numbering, so its blocks are not read. Rule 19: nor
        #: are a reform decree's, or the recitals', which are not the
        #: instrument's own provisions either.
        propios = [
            k for k in range(len(bloques))
            if k not in en_indice and not self._es_ajeno_a_la_numeracion(k)
        ]
        self._modo_sin_articulos = modo_sin_articulos(
            [bloques[k] for k in propios], [self._clases[k] for k in propios]
        )
        self._busca_secciones(self._indices_de_marca, self._siguiente_de_marca)
        self._busca_formularios(en_indice)
        #: Rule 16 (review fix-1 of #291): a `PARTE` is above the books, titles
        #: and chapters unless the document opens one of those first.
        #: Rule 17 (review fix-1 of #291): inside an instrument's recitals,
        #: whose numbered `PRIMERO.`/`1.` paragraphs are not provisions.
        self._en_considerandos = False
        self._recien_abiertos = False
        # Rule 18 (review fix-1 of #291): the highest whole numeral that opened
        # an article, and the last item of the list being read, if any.
        self._ultimo_entero = 0
        self._item_de_lista: int | None = None
        self._numeros_de_considerandos: set[str] = set()
        #: Rule 20 (review fix-2 of #291): inside a block the SCJN labels
        #: `D.O.F. <fecha>`, a reform decree's own text, which nests under
        #: the decree whatever it reprints.
        self._decreto = False
        self._decreto_nodo: AknNode | None = None
        #: Rule 29 (review fix-4 of #291): the labelled container the current API
        #: block opened, the group of the Mexican promulgation decree's blocks, and
        #: the role of the current block's label.
        self._bloque_abierto: AknNode | None = None
        self._piso_del_bloque: float | None = None
        self._grupo: AknNode | None = None
        self._rol_actual: str | None = None
        self._indice_marca = -1
        #: Rule 23: the implicit section open now, as (node, index of the block
        #: that ends it), or `None`.
        self._implicita: tuple[AknNode, int] | None = None
        #: The node that holds each eId, for `AknNode.repeated`.
        self._por_eid: dict[str, AknNode] = {}
        #: Rule 21 (review fix-2 of #291): the rank of the container the
        #: current API block opened, or `None`. A heading the *text* of that
        #: block contains (an `APÉNDICE` inside a `PARTE`) belongs to it.
        self._piso: float | None = None
        self._parte_en_capitulo = False
        for k, (kind, campos) in enumerate(self._clases):
            if k in en_indice or kind != "contenedor":
                continue
            if campos["akn_type"] == "part":
                break
            if campos["akn_type"] in ("book", "title", "chapter"):
                self._parte_en_capitulo = True
                break

    # -- outline markers (rule 19, review fix-2 of #291) ------------------

    #: A marker's container `type` as the tree's `akn_type` and `refers_to`.
    TIPOS_DE_CONTENEDOR = {
        "book": ("book", None),
        "title": ("title", None),
        "chapter": ("chapter", None),
        "section": ("section", None),
        "apartado": ("level", None),
        "part": ("part", None),
        "annex": ("attachment", REFERS_TO_ANEXO),
        "appendix": ("attachment", REFERS_TO_APENDICE),
    }

    def _lee_marcas(self) -> None:
        """Find the markers, and settle with them what the block each one
        heads *is*: the first block of an API block that is not an annotation
        carries its label's meaning (`_clase_de_marca`); for a container, the
        block that is the heading does (`_encabezado_de`)."""
        indices = [k for k, bloque in enumerate(self._bloques)
                   if bloque.text.startswith("<!-- scjn:block ")
                   and lee_marcador(bloque.text) is not None]
        siguiente = {k: (indices[n + 1] if n + 1 < len(indices) else len(self._bloques))
                     for n, k in enumerate(indices)}
        actual = None
        esperando_primero = False
        for k, bloque in enumerate(self._bloques):
            if k in siguiente:
                actual = lee_marcador(bloque.text)
                self._marcas[k] = actual
                esperando_primero = True
                self._clases[k] = ("marcador", {})
                inicio_de_marca = k
            elif esperando_primero and self._clases[k][0] not in ("anotacion", "nota_editorial"):
                esperando_primero = False
                destino = k
                if actual.get("kind") in ("container", "article", "transitorios", "decree"):
                    destino = self._encabezado_de(k, siguiente[inicio_de_marca], actual)
                    # What stands between the marker and its heading is the
                    # heading's own annotation (`(REFORMADA SU DENOMINACIÓN,
                    # ...) (REPUBLICADA, ...)`), whatever it looks like.
                    # Rule 29: that includes the court's `(NOTA: EL PLENO ...)` the
                    # SCJN prints before a chapter's own heading, which used to
                    # hang from the container before it.
                    for i in range(inicio_de_marca + 1, destino):
                        texto = self._bloques[i].text.strip().replace("**", "")
                        if texto.startswith("("):
                            self._clases[i] = ("anotacion", {"cuerpo": texto.strip("() ")})
                        elif (actual.get("kind") != "article"
                              and self._clases[destino][0] in (
                                  "contenedor", "transitorios", "transitorios_dof")
                              and self._clases[i][0] not in ("anotacion", "nota_editorial")):
                            # Rule 34 (review fix-6 of #291): any other line before
                            # the heading -- the court's elision `[...]` ahead of
                            # `Transitorios`, the institution's name above `SEGUNDA
                            # PARTE` -- is part of the heading of the container this
                            # API block opens. It used to be placed as text of the
                            # container *before* it, the one the block closes.
                            self._clases[i] = ("encabezado_previo", {})
                self._primeros[destino] = actual
                self._destino_de_marca[inicio_de_marca] = destino
                self._clases[destino] = self._clase_de_marca(
                    self._clases[destino], actual, self._bloques[destino]
                )
                if actual.get("kind") == "article" and self._clases[destino][0] == "articulo":
                    # Rule 29: what the API block prints before the article's own
                    # heading line (the caption `Objeto`, a `(NOTA: ...)`) is the
                    # article's, not text hanging from the container before it: the
                    # article opens at the first of those blocks and its heading
                    # line goes on in it.
                    primero = next(
                        (i for i in range(k, destino)
                         if self._clases[i][0] not in ("anotacion", "nota_editorial")),
                        None,
                    )  # (a note before `k` is an annotation now: see above)
                    if primero is not None:
                        self._clases[primero] = ("articulo", dict(self._clases[destino][1]))
                        self._encabezados_de_leyenda[destino] = primero
                if actual.get("kind") in self.BLOQUES_DE_UN_SOLO_TEXTO:
                    # Rule 24: a block the SCJN labels as an article is one
                    # article, and one labelled as a title block, a preamble or
                    # recitals opens none: an article, container or
                    # transitorios heading inside its text is quoted text (an
                    # amendment quoting the text it amends, an alternative
                    # wording, a list of the articles that follow), not a
                    # further one.
                    desde = destino + 1 if actual["kind"] == "article" else k
                    for i in range(desde, siguiente[inicio_de_marca]):
                        if self._clases[i][0] in self.APERTURAS_DE_ESTRUCTURA:
                            self._clases[i] = ("contenido", {})
            self._de_marca[k] = actual
        self._indices_de_marca = indices
        self._siguiente_de_marca = siguiente

    def _abre_articulos(self, i: int) -> bool:
        """Whether block `i` opens an article, as the pass will read it: by its
        class, or, in a document that numbers its provisions some other way
        (rule 8), by the shape that document numbers them with."""
        kind, _campos = self._clases[i]
        if kind == "articulo":
            return True
        if self._marcas and kind == "contenido":
            # Rule 32: a radio-regulations provision (`S5.43`) is an article.
            linea = self._bloques[i].text.split("\n", 1)[0]
            if PROVISION_RR.match(linea) or ENCABEZADO_ARTICULO_RR.match(linea):
                return True
        if self._modo_sin_articulos == MODO_ORDINAL:
            return kind == "articulo_ordinal"
        if self._modo_sin_articulos == MODO_NUMERAL and kind == "contenido":
            return bool(NUMERAL_DECIMAL.match(self._bloques[i].text.split("\n", 1)[0]))
        return False

    #: The roles whose blocks are never read as a section of numbered articles
    #: (rule 23): the Mexican decree's own blocks, a closing formula, a note.
    ROLES_SIN_SECCION = ("promulgation", "conclusions", "note")

    def _busca_secciones(self, indices, siguiente) -> None:
        """Rules 23 and 30: which markers open an implicit section, where it
        ends, and which markers are only the second half of a block the SCJN
        split in two.

        The SCJN gives a heading it has no word for as a block of its own --
        `ESTATUTO`, `MANUAL OPERATIVO`, `ESTATUS DE LA LISTA DE RESERVAS`, a
        second `PREÁMBULO` -- and what is under it is a text of its own: its
        articles start over at 1. A block of `kind` `other`, or a preamble that
        is not the document's first, opens a section when it holds articles in
        its own text or is followed by article blocks. The section lasts until
        the first later marker that is not an article. One whose label names a
        role of the Mexican decree (`FIRMA`, `PARTE FINAL ...`, `NOTA EXPLICATIVA`)
        never opens one (rule 29): it is an `hcontainer`.

        Rule 30: a block labelled `CONTENIDO <label of the block before>` (`ARTÍCULOS`
        and `CONTENIDO ARTICULOS`) is the content of that block, not a new one:
        it opens nothing, and the container the first opened goes on through it.
        """
        self._aperturas: dict[int, int] = {}
        self._fusion: set[int] = set()
        for n in range(1, len(indices)):
            marca, anterior = self._marcas[indices[n]], self._marcas[indices[n - 1]]
            if marca.get("kind") == "other" and es_continuacion(
                marca.get("label", ""), anterior.get("label", "")
            ):
                self._fusion.add(indices[n])
        #: The role of each `other` block, by marker index.
        self._rol_de: dict[int, str | None] = {
            k: rol_de_etiqueta(self._marcas[k].get("label", ""))
            for k in indices if self._marcas[k].get("kind") == "other"
        }
        preambulos = 0
        for n, k in enumerate(indices):
            kind = self._marcas[k].get("kind")
            if kind == "preamble":
                preambulos += 1
            if k in self._fusion or not (
                kind == "other" or (kind == "preamble" and preambulos > 1)
            ):
                continue
            rol = self._rol_de.get(k)
            if rol in self.ROLES_SIN_SECCION:
                continue
            if id(self._marcas[k]) in self._provisiones_sic:
                continue  # rule 39: a provision printed `N (SIC)`, an article
            ultimo = n
            while ultimo + 1 < len(indices) and indices[ultimo + 1] in self._fusion:
                ultimo += 1
            propio = any(
                self._abre_articulos(i) for i in range(k + 1, siguiente[indices[ultimo]])
            )
            fin = ultimo + 1
            while fin < len(indices) and self._marcas[indices[fin]].get("kind") == "article":
                fin += 1
            siguientes = fin > ultimo + 1
            if propio or siguientes or rol == "resolution":
                self._aperturas[k] = indices[fin] if fin < len(indices) else len(self._bloques)
        self._busca_articulos_de_encabezado(indices, siguiente)

    #: Rule 38: how many heading-only article blocks a document needs, each followed
    #: by provision blocks, before they are read as headings, and the longest a
    #: heading block's lines are.
    MIN_ENCABEZADOS_DE_ARTICULO = 3
    MAX_LINEA_DE_ENCABEZADO = 160

    def _busca_articulos_de_encabezado(self, indices, siguiente) -> None:
        """Rule 38 (review fix-6 of #291): the Reglamento General de Infantería is
        written `ARTICULO 1°. Generalidades` and, under it, `1.- La preparación ...`
        to `5.- ...`, then `ARTICULO 2°` and `6.- ...`: the numbered provisions run
        on across the articles, which are headings that group them. Read as
        articles, the two numberings interleave (1, 5 then 2°, 6, 9 then 3°, 10 ...)
        and 70 numbers look missing. An API `article` block whose text is only its
        heading line and a caption, followed by `provision` blocks, opens a section
        of those provisions (rule 23's machinery: it lasts to the next marker that is
        neither a provision nor an `other` block) -- when the document does it three
        times or more; one such block in a reglamento is an article whose numbered
        paragraphs the API labelled provisions, and stays an article."""
        candidatos = []
        for n in range(len(indices) - 1):
            k = indices[n]
            if (self._marcas[k].get("kind") != "article"
                    or self._marcas[indices[n + 1]].get("kind") != "provision"):
                continue
            texto = [i for i in range(k + 1, siguiente[k])
                     if self._clases[i][0] not in ("anotacion", "nota_editorial")]
            if 0 < len(texto) <= 2 and all(
                len(self._bloques[i].text.strip()) <= self.MAX_LINEA_DE_ENCABEZADO
                and "\n" not in self._bloques[i].text.strip() for i in texto
            ):
                candidatos.append((n, texto))
        if len(candidatos) < self.MIN_ENCABEZADOS_DE_ARTICULO:
            return
        for n, texto in candidatos:
            fin = n + 1
            while fin < len(indices) and self._marcas[indices[fin]].get("kind") in ("provision", "other"):
                fin += 1
            self._aperturas[indices[n]] = indices[fin] if fin < len(indices) else len(self._bloques)
            for i in texto:
                self._clases[i] = ("encabezado_de_seccion", {})

    def _busca_formularios(self, en_indice) -> None:
        """Rule 31: the heading of a model certificate or form opens a division of
        its own -- the `MODELO DE CERTIFICADO ...` an appendix reprints, one per
        certificate, each numbering its boxes from 2.1. A second heading in the
        very next block (`CERTIFICADO DE SEGURIDAD ...` under `MODELO DE
        CERTIFICADO DE SEGURIDAD ...`) is the same certificate's title."""
        if not self._marcas:
            return
        anterior = None
        #: A heading's own `SE CERTIFICA` has not been read yet.
        esperando_cuerpo = False
        for k, bloque in enumerate(self._bloques):
            if k in en_indice or self._clases[k][0] != "contenido" or k >= self._fin_cuerpo:
                continue
            if self._de_marca[k] is not None and self._de_marca[k].get("kind") in (
                self.BLOQUES_DE_UN_SOLO_TEXTO + ("toc", "transitorios", "decree")
            ):
                continue
            titulo = es_encabezado_de_formulario(bloque.text)
            if titulo is not None:
                if anterior != k - 1 and k not in self._divisiones:
                    self._divisiones[k] = (titulo, "form")
                anterior = k
                esperando_cuerpo = True
            elif SE_CERTIFICA.match(bloque.text.strip()):
                if esperando_cuerpo:
                    esperando_cuerpo = False
                    continue
                # A certificate whose title is a picture: it begins at the
                # placeholder the SCJN prints for it, when there is one.
                inicio = k
                if k and VEASE_ARCHIVO_ANEXO.match(self._bloques[k - 1].text.strip()) and (
                    self._clases[k - 1][0] == "contenido"
                ):
                    inicio = k - 1
                self._divisiones.setdefault(inicio, ("SE CERTIFICA", "form"))

    #: Rule 24: the `kind`s of API block inside which no heading opens a
    #: structure, and the classes of block that would.
    BLOQUES_DE_UN_SOLO_TEXTO = ("article", "header", "preamble", "recitals")
    APERTURAS_DE_ESTRUCTURA = (
        "articulo", "articulo_ordinal", "contenedor", "transitorios", "transitorios_dof",
    )

    #: How many blocks into an API block a container's heading is looked for.
    VENTANA_ENCABEZADO = 6

    def _encabezado_de(self, primero: int, fin: int, marca) -> int:
        """Index of the block that is an API block's heading -- the one its
        marker's `kind` is about: the first block the text itself reads as that
        (a container of that type, an article, transitorios, a `D.O.F.` header),
        else, for a container, the first that starts with the label (`PARTE 1-
        ENSAYO...` under `PARTE 1`), else `primero`. Looked for only before the
        block's first article, and not past `VENTANA_ENCABEZADO` blocks."""
        kind = marca.get("kind")
        akn_type, _ = self.TIPOS_DE_CONTENEDOR.get(marca.get("type"), (None, None))
        limite = min(fin, primero + self.VENTANA_ENCABEZADO)
        rango = []
        for i in range(primero, limite):
            rango.append(i)
            if self._clases[i][0] in ("articulo", "transitorios", "transitorios_dof"):
                break
        for i in rango:
            clase = self._clases[i]
            if kind == "container":
                if clase[0] == "contenedor" and clase[1].get("akn_type") == akn_type:
                    return i
            elif kind == "article":
                if clase[0] == "articulo":
                    return i
            elif kind == "transitorios":
                if clase[0] == "transitorios":
                    return i
            elif kind == "decree":
                if clase[0] == "transitorios_dof":
                    return i
        if kind == "container":
            for i in rango:
                if _quita_etiqueta(self._bloques[i].text.split("\n", 1)[0], marca.get("label", ""))[0]:
                    return i
        return primero

    def _clase_de_marca(self, clase, marca, bloque):
        """`clasifica`'s answer for the first block of an API block, as its
        marker says it should read: a container of the type and number the
        label names, a reform decree, transitorios, an article of the number
        the label names. Anything else keeps the text's own reading."""
        kind = marca.get("kind")
        linea = bloque.text.split("\n", 1)[0]
        if kind == "container" and marca.get("type") in self.TIPOS_DE_CONTENEDOR:
            akn_type, refers_to = self.TIPOS_DE_CONTENEDOR[marca["type"]]
            coincide, heading = _quita_etiqueta(linea, marca.get("label", ""))
            if clase[0] == "contenedor" and clase[1].get("akn_type") == akn_type:
                # Review fix-3 of #291: unless the label's number extends the
                # text's -- `ANEXO 2-A:` read by the text alone is annex `2`
                # titled `A` -- and then what follows the label is the title.
                extiende = coincide and _extiende(
                    _forma_comparable(marca.get("num", "")),
                    _forma_comparable(clase[1].get("num") or ""),
                )
                if not extiende:
                    heading = clase[1].get("heading") or heading
            return "contenedor", {
                "akn_type": akn_type,
                "num": _limpia(marca.get("num", "")) or None,
                "refers_to": refers_to,
                "heading": heading,
                "forzado": True,
                "numero": marca.get("numero"),
                "tipo": marca["type"],
            }
        if kind == "other" and self._es_provision_sic(marca, linea):
            # Rule 39: `98 (SIC).- El pelotón es ...` is provision `98 (SIC)`.
            self._provisiones_sic.add(id(marca))
            return "articulo", {"num": _limpia(marca["label"])}
        if kind == "decree":
            return "transitorios_dof", {"num": _limpia(marca.get("num", "")), "forzado": True}
        if kind == "transitorios":
            return "transitorios", {}
        if (kind == "article" and clase[0] == "contenedor"
                and _forma_comparable(clase[1].get("num") or "") == _forma_comparable(marca.get("num", ""))):
            # The SCJN's own label is wrong: its LIGIE calls the tariff's
            # `Capítulo 84` block `ARTÍCULO 84`. The text says container, with the
            # very number the label gives, so it is read as the container it is
            # (a container with another number, a section heading printed above
            # an article, stays inside the article, rule 24).
            return clase
        if kind == "article" and marca.get("num"):
            texto = clase[1]["num"] if clase[0] == "articulo" else None
            etiqueta = _limpia(marca["num"])
            elegido = _numero_de_articulo(texto, etiqueta, marca.get("numero"))
            otro = texto if elegido == etiqueta else etiqueta
            campos = {"num": elegido}
            if texto is not None and _forma_comparable(otro) != _forma_comparable(elegido):
                campos["alternativa"] = otro
            return "articulo", campos
        return clase

    def _indices_de_marcas(self) -> dict[int, int]:
        """The blocks a marker labels `ÍNDICE`, as ``{marker index: first
        index after the API block}`` -- rule 15's table of contents, decided
        by the SCJN rather than by the text."""
        rangos = {}
        siguientes = sorted(self._marcas)
        for n, k in enumerate(siguientes):
            if self._marcas[k].get("kind") != "toc" or k >= self._fin_cuerpo:
                continue
            fin = siguientes[n + 1] if n + 1 < len(siguientes) else len(self._bloques)
            rangos[k] = min(fin, self._fin_cuerpo)
        return rangos

    def _es_ajeno_a_la_numeracion(self, k: int) -> bool:
        """Whether block `k` is not one of the instrument's own provisions, so
        rule 8 must not read its numbering: a reform decree's text, or the
        recitals."""
        marca = self._de_marca[k]
        return marca is not None and marca.get("kind") in (
            "decree", "recitals", "preamble", "header", "transitorios",
        )

    @staticmethod
    def _es_provision_sic(marca, linea: str) -> bool:
        """Rule 39: whether an `other` block is a numbered provision whose number
        the source prints with a `(SIC)` -- its label is `<number> (SIC)` and its
        text opens with that number and goes on."""
        m = _ETIQUETA_PROVISION_SIC.match(marca.get("label", "").strip())
        if m is None:
            return False
        resto = linea.replace("**", "").strip()
        return (resto.upper().startswith(marca["label"].strip().upper().rstrip(".")[:len(m.group("num"))])
                and re.match(rf"{re.escape(m.group('num'))}[ \t]*\((?:sic|SIC)\)[.\-– \t]*\S", resto) is not None)

    def _toma_marca(self, inicio: int) -> int:
        """`inicio`, pulled back over the marker line still waiting for a node
        -- the way a node's span is pulled back over its annotation."""
        if self._marca_inicio is None:
            return inicio
        marca, self._marca_inicio = self._marca_inicio, None
        return min(inicio, marca)

    def _bloque_marcador(self, bloque, campos):
        """A marker is metadata: it ends whatever the previous API block had
        open (an article never reaches into the next block the SCJN serves,
        which is what kept the text after a treaty's last article inside it),
        and its characters belong to the node the block it heads opens."""
        marca = self._marcas[self._i]
        self._indice_marca = self._i
        if self._marca_inicio is None:
            self._marca_inicio = bloque.start
        self._decreto = False
        self._piso = None
        # Recitals, a preamble and a title block are not provisions: no
        # numbered paragraph of theirs opens an article (rule 8, rule 17).
        self._en_considerandos = marca.get("kind") in ("recitals", "preamble", "header")
        self._recien_abiertos = False
        self._numeros_de_considerandos = set()
        self._cierra_articulo()
        self._pendiente_epigrafe = None
        self._tabla = None
        self._cierra_divisiones()
        continuacion = self._i in self._fusion
        if not continuacion:
            self._cierra_bloque_otro(marca)
        elif self._piso_del_bloque is not None:
            # The second half of a block the API split in two (rule 30): what its
            # text opens still nests under the container the first half opened.
            self._piso = self._piso_del_bloque
        self._rol_actual = self._rol_de.get(self._i) if marca.get("kind") == "other" else None
        if self._implicita is not None and self._i >= self._implicita[1]:
            # Rule 23: the implicit section ends where the first marker that is
            # not an article begins.
            nodo, _ = self._implicita
            self._implicita = None
            self._piso_del_bloque = None
            while self._pila and self._pila[-1][1] is not nodo and nodo in [n for _, n in self._pila]:
                self._pila.pop()
            if self._pila and self._pila[-1][1] is nodo:
                self._pila.pop()
        if marca.get("kind") == "article":
            # An article the SCJN serves as a block of its own is not a
            # transitory provision of the block before it.
            self._transitorios = None
        if marca.get("kind") in ("header", "preamble", "recitals") and (
            self.body is not None or self._pila
        ):
            # An instrument's own opening text comes first; one of these
            # later on (the preamble of a resolution annexed to an acuerdo) is
            # text of its own, not the tail of the transitorios before it.
            self._transitorios = None
        if self._i in self._aperturas:
            # Rule 28 (review fix-3 of #291): a text of its own -- the
            # `RESOLUCIÓN MSC.411(97)` a treaty prints after the code it amends --
            # is not part of the annex or appendix that came before it: when one
            # is open it hangs from the body, a sibling of the instrument's own
            # parts. (Without an attachment open the section stays where rule 23
            # put it: `DISPOSICIONES GENERALES` inside the chapter that holds it.)
            if any(n.akn_type == "attachment" for _, n in self._pila):
                self._pila.clear()
                self._transitorios = None
            if self._rol_actual == "resolution":
                # An amending resolution is a text of its own, not the tail of the
                # transitorios before it (rule 29, whichever container it opens).
                self._transitorios = None
            etiqueta = _limpia(marca.get("label", "")) or "section"
            nodo = self._nuevo_nodo("section", bloque.start, bloque.end, num=etiqueta)
            nodo.heading = None
            self._marca_inicio = None
            self._pila.append((5.0, nodo))
            self._piso = self._piso_del_bloque = 5.0
            self._implicita = (nodo, self._aperturas[self._i])
        elif not continuacion and self._i + 1 < self._fin_cuerpo and (
            (marca.get("kind") == "other" and id(marca) not in self._provisiones_sic)
            or (marca.get("kind") in ("header", "preamble", "recitals")
                and (self.body is not None or self._pila))
        ):
            # Rule 29. A title block, a preamble or recitals that come after the
            # instrument's own text began (a treaty's preamble after the
            # promulgation decree that precedes it) are a text of their own: a
            # labelled container, not loose text of the body.
            self._abre_bloque_otro(bloque, marca)

    # -- blocks the API labels `other` (rules 29-31, review fix-4 of #291) --

    #: Where a labelled block, a promulgation decree's group and a division sit
    #: in the stack of open containers: below every container a text can open
    #: (`PRECEDENCIA` ends at 6), so any heading of the text closes them.
    RANGO_BLOQUE = 7.0
    RANGO_BLOQUE_EN_GRUPO = 7.25

    def _cierra_a_traves_de(self, nodo) -> None:
        """Pop the stack through `nodo`, if it is on it."""
        if nodo is not None and any(n is nodo for _, n in self._pila):
            while self._pila and self._pila.pop()[1] is not nodo:
                pass

    def _cierra_divisiones(self) -> None:
        """Every division ends where the API block it was opened in does."""
        for k, (_, nodo) in enumerate(self._pila):
            if nodo in self._nodos_division:
                del self._pila[k:]
                return

    def _cierra_bloque_otro(self, marca) -> None:
        """The previous API block's labelled container ends at this marker, and
        so does the group of promulgation blocks, unless this block is one too."""
        self._cierra_a_traves_de(self._bloque_abierto)
        self._bloque_abierto = None
        self._piso_del_bloque = None
        rol = self._rol_de.get(self._i) if marca.get("kind") == "other" else None
        if self._grupo is not None and rol != "promulgation":
            self._cierra_a_traves_de(self._grupo)
            self._grupo = None

    def _eid_de_bloque(self, padre: AknNode, etiqueta: str) -> str:
        """An eId for a labelled block or a division: the parent's path and
        `hc_<label>`, with a counter when the same label comes up again under the
        same parent -- never a repeat (rule 22 is about articles): two blocks both
        labelled `PARTE FINAL DE LAS ACTAS` are two blocks."""
        propuesto = self._eids.propuesto(
            "" if padre.akn_type in ("act", "body") else padre.eId, "hc", etiqueta[:60]
        )
        eid, i = propuesto, 2
        while eid in self._eids.used:
            eid = f"{propuesto}_{i}"
            i += 1
        return eid

    def _abre_bloque_otro(self, bloque, marca) -> None:
        """Rule 29: a block the API labels `other` is a container of its own, an
        `hcontainer` named by its label. One whose label has a role in the Mexican
        decree (`rol_de_etiqueta`) hangs from the body, whatever was open; the
        blocks of the promulgation decree are kept together under one container
        for the decree, and `PARTE FINAL ...` is its `#conclusions`, `NOTA
        EXPLICATIVA` its `#note`. Any other label opens a plain labelled container
        where the text stands, as rule 23's sections do."""
        etiqueta = _limpia(marca.get("label", "")) or "block"
        rol = self._rol_actual
        refers_to = {
            "promulgation": REFERS_TO_PROMULGACION,
            "conclusions": REFERS_TO_CONCLUSIONES,
            "note": REFERS_TO_NOTA,
        }.get(rol)
        rango, piso = self.RANGO_BLOQUE, self.RANGO_BLOQUE
        padre = None
        if rol == "resolution":
            # An amending resolution is a text of its own, not the tail of the
            # transitorios before it.
            self._transitorios = None
        if rol in self.ROLES_SIN_SECCION:
            # Whatever transitorios were open end: this is a text of its own.
            self._transitorios = None
            self._decreto = False
            # Nor is it part of the chapter or annex before it (unless it is the
            # next block of the promulgation decree already open).
            if not (rol == "promulgation" and self._grupo is not None):
                self._pila.clear()
                self._grupo = None
            if rol == "promulgation":
                if self._grupo is None:
                    cuerpo = self._asegura_body(bloque.start)
                    self._grupo = self._nuevo_nodo(
                        "hcontainer", bloque.start, bloque.start, num="DECRETO PROMULGATORIO",
                        refers_to=REFERS_TO_PROMULGACION, padre=cuerpo,
                        eid=self._eid_de_bloque(cuerpo, "DECRETO PROMULGATORIO"),
                    )
                    self._pila.append((self.RANGO_BLOQUE, self._grupo))
                padre = self._grupo
                rango, piso = self.RANGO_BLOQUE_EN_GRUPO, self.RANGO_BLOQUE_EN_GRUPO
                refers_to = None
        padre_real = padre if padre is not None else self._padre_contenedor(bloque.start)
        nodo = self._nuevo_nodo(
            "hcontainer", bloque.start, bloque.end, num=etiqueta, refers_to=refers_to,
            padre=padre_real, eid=self._eid_de_bloque(padre_real, etiqueta),
        )
        self._marca_inicio = None
        self._pila.append((rango, nodo))
        self._piso = self._piso_del_bloque = piso
        self._bloque_abierto = nodo

    # -- helpers ---------------------------------------------------------

    def _span(self, inicio, fin, label):
        return self._node_span(self._doc, inicio, fin, label)

    def _asegura_body(self, inicio):
        if self.body is None:
            self.body = self.act.add(
                AknNode("body", self._span(inicio, inicio, "body"),
                        eId=self._eids.allocate("body"))
            )
        return self.body

    def _padre_contenedor(self, inicio) -> AknNode:
        """Where a new article or container hangs: the deepest open
        container, or the body itself — many short laws have no containers at
        all, and an article with no chapter is not an error."""
        if self._transitorios is not None:
            # Rule 20: a reform decree's own containers nest under it.
            if self._decreto and self._pila:
                return self._pila[-1][1]
            # Rule 34 (review fix-6 of #291): so does everything the text of an
            # API block that opened a container of its own holds. The block's
            # container is the root of its text's hierarchy, whatever
            # transitorios section is open around it.
            if self._pila and self._raiz_de_bloque_en_pila():
                return self._pila[-1][1]
            return self._transitorios
        if self._pila:
            return self._pila[-1][1]
        return self._asegura_body(inicio)

    def _raiz_de_bloque_en_pila(self) -> bool:
        """Whether the container the current API block opened (rules 23, 28,
        29 and 31) is still open: while it is, it bounds where the block's own
        text can hang."""
        raices = [self._bloque_abierto, *self._nodos_division]
        if self._implicita is not None:
            raices.append(self._implicita[0])
        return any(n is r for _, n in self._pila for r in raices if r is not None)

    def _cierra_hasta(self, rango: int):
        while self._pila and self._pila[-1][0] >= rango:
            self._pila.pop()

    def _nuevo_nodo(self, akn_type, inicio, fin, num=None, refers_to=None, padre=None,
                    eid=None):
        padre = padre if padre is not None else self._padre_contenedor(inicio)
        if eid is None:
            prefijo = PREFIJO_EID.get(akn_type, akn_type)
            if self._marcas and akn_type == "attachment" and refers_to == REFERS_TO_APENDICE:
                # Rule 26 (review fix-3 of #291): an appendix is not an annex, and
                # its identifier says so. Only where the document has markers,
                # so one without them keeps the eIds it always had.
                prefijo = PREFIJO_EID_APENDICE
            propuesto = self._eids.propuesto(
                "" if padre.akn_type in ("act", "body") else padre.eId,
                prefijo,
                num if num is not None else akn_type,
            )
        else:
            propuesto = eid
        eid = self._eids.allocate(propuesto)
        inicio, notas = self._pendientes.toma(inicio)
        inicio = self._toma_marca(inicio)
        # The pull-back may not escape the parent, so the parent comes back
        # with it as far as it can. It cannot always: a law whose *title*
        # carries an annotation (`(REFORMADA SU DENOMINACIÓN, …)`, written
        # above the heading and so above the body) would drag the body back
        # over the preamble. Where the parent stops, so does the pull-back —
        # the annotation is still attached, only its characters stay with
        # whoever already held them.
        inicio = max(inicio, self._retrocede(padre, inicio))
        nodo = AknNode(
            akn_type, self._span(inicio, fin, eid), eId=eid, num=num, refers_to=refers_to,
            notes=notas, repeated=eid != propuesto,
        )
        # A repeat (review fix-2 of #291) involves both claimants of the
        # identifier: the one that had to take a suffix and the one that held it.
        if eid != propuesto and propuesto in self._por_eid:
            self._por_eid[propuesto].repeated = True
        self._por_eid.setdefault(eid, nodo)
        return padre.add(nodo)

    # -- the pass --------------------------------------------------------

    def run(self) -> AknNode:
        i = 0
        while i < len(self._bloques):
            bloque = self._bloques[i]
            if i >= self._fin_cuerpo:
                self._agrega_conclusiones(bloque)
                i += 1
                continue
            if i in self._indices:
                fin = self._indices[i]
                self._bloque_indice(self._bloques[i], self._bloques[fin - 1])
                i = fin
                continue
            kind, campos = self._clases[i]
            self._i = i
            if self._marcas:
                kind, campos = self._refina_con_marcas(bloque, kind, campos)
            if self._modo_sin_articulos is not None:
                kind, campos = self._promueve_a_articulo(bloque, kind, campos)

            # Rule 13 (issue #291): a table lasts exactly as long as its rows
            # do, plus one tolerated label block between two of them.
            if (self._tabla is not None and not es_fila_tabla(bloque)
                    and not (self._marcas and kind == "contenido"
                             and self._tabla_con_leyenda is self._tabla
                             and self._es_nota_de_tabla(i))):
                if (
                    kind == "contenido"
                    and es_etiqueta_de_tabla(bloque)
                    and i + 1 < self._fin_cuerpo
                    and es_fila_tabla(self._bloques[i + 1])
                ):
                    self._extiende(self._tabla, bloque.end)
                    i += 1
                    continue
                self._tabla = None

            if self._pendiente_epigrafe is not None:
                contenedor = self._pendiente_epigrafe
                self._pendiente_epigrafe = None
                if _es_epigrafe(bloque, kind):
                    contenedor.heading = _limpia(bloque.text)
                    self._extiende(contenedor, bloque.end)
                    i += 1
                    continue

            if self._marcas and self._articulo is None and kind == "contenido":
                # Rule 36 (review fix-6 of #291): the caption of a table.
                if self._tabla is None:
                    fin = self._fin_de_leyenda(i)
                    if fin is not None:
                        self._abre_tabla_con_leyenda(i, fin)
                        i = fin
                        continue
                elif self._tabla_con_leyenda is self._tabla and self._es_nota_de_tabla(i):
                    self._extiende(self._tabla, bloque.end)
                    i += 1
                    continue

            if self._marcas and i in self._divisiones:
                # Rule 31. After the epigraph check: the title of the container
                # just opened is its own, whatever it is called.
                self._abre_division(bloque, *self._divisiones[i])
                i += 1
                continue
            self._i = i
            manejador = getattr(self, f"_bloque_{kind}", self._bloque_contenido)
            manejador(bloque, campos)
            i += 1

        self._cierra_articulo()
        # An annotation with no node after it — the last thing in the file —
        # hangs off the act rather than being lost.
        _, notas = self._pendientes.toma(self.act.start_char)
        self.act.notes.extend(notas)
        # An `act` always has a `body`, even an empty document's, so that
        # consumers never have to special-case its absence. It is created
        # last when nothing needed it earlier, which also keeps it after the
        # `preamble` in document order.
        self._asegura_body(self.act.start_char)
        self._cierra_spans()
        self.act.modo = self._modo_sin_articulos
        return self.act

    def _refina_con_marcas(self, bloque, kind, campos):
        """Rule 32 (review fix-4 of #291): numbering the ordinary grammar does not
        read, on a snapshot that carries markers. Inside transitorios, `Artículo
        Primero.-` with the ordinal in mixed case is a transitory article; outside
        them, `ARTÍCULO S5` of a radio-regulations text is the container of its
        provisions and `S5.43` is a provision (an article). Never inside the
        blocks rule 24 reads as one text, the recitals or a Mexican decree's own
        blocks."""
        primera = self._primeros.get(self._i)
        if kind == "articulo" and primera is None and not self._en_considerandos:
            # Rule 37 (review fix-6 of #291): `**Artículo 8o. **fracción II primer
            # párrafo: $179,017.26`, a line of a tariff that names the article it
            # is a quantity of, is a reference and not an opener: no provision
            # begins with a lower-case word after its number, and the API did not
            # say this block is an article (an API article stays one).
            linea = bloque.text.split("\n", 1)[0].replace("**", "")
            m = ARTICULO.match(linea)
            if m and linea[m.end():].lstrip()[:1].islower():
                return "contenido", {}
        if (
            kind in ("contenido", "articulo_ordinal") and primera is not None
            and primera.get("kind") == "provision" and primera.get("num")
            and not self._en_considerandos
        ):
            # Rule 29: the API says this block is a numbered provision of an
            # acuerdo (`PRIMERO`, `1.1.0`): it is an article, whatever case its
            # ordinal is written in and whether or not the document as a whole is
            # read as numbering that way (rule 8).
            # The number the text writes wins over the label's (an upper-case
            # `PRIMERO` and the instrument's own `Primero` are two provisions).
            numero = campos["num"] if kind == "articulo_ordinal" else _limpia(primera["num"])
            if self._modo_sin_articulos == MODO_NUMERAL and numero.isdigit():
                # The numbered list inside this provision (`1.`, `2.`) starts
                # over after it, exactly as after a provision numeral mode read
                # itself (rule 18).
                self._ultimo_entero = int(numero)
                self._item_de_lista = None
            return "articulo", {"num": numero}
        if kind != "contenido" or self._en_considerandos:
            return kind, campos
        marca = self._de_marca[self._i]
        if marca is not None and marca.get("kind") in self.BLOQUES_DE_UN_SOLO_TEXTO:
            return kind, campos
        linea = bloque.text.split("\n", 1)[0]
        if self._transitorios is not None:
            m = ARTICULO_ORDINAL_MIXTO.match(linea.replace("**", ""))
            if m:
                return "articulo", {"num": _limpia(m.group("num"))}
            return kind, campos
        if self._rol_actual in self.ROLES_SIN_SECCION:
            return kind, campos
        m = ENCABEZADO_APENDICE_RR.match(linea)
        if m:
            return "contenedor", {
                "akn_type": "attachment", "num": m.group("num"),
                "refers_to": REFERS_TO_APENDICE, "heading": None,
            }
        m = ENCABEZADO_ARTICULO_RR.match(linea)
        if m:
            return "contenedor", {"akn_type": "section", "num": m.group("num")}
        m = PROVISION_RR.match(linea)
        if m:
            return "articulo", {"num": m.group("num")}
        return kind, campos

    def _en_formulario(self) -> bool:
        return any(nodo in self._formularios for _, nodo in self._pila)

    def _abre_division(self, bloque, etiqueta, rol) -> None:
        """Rule 31: the heading at `bloque` opens a division, an `hcontainer`
        named by it, that lasts until the next division, the next container the
        text opens or the end of the API block. What it holds is segmented as
        anywhere else; its articles are its own."""
        self._cierra_articulo()
        self._pendiente_epigrafe = None
        self._tabla = None
        self._cierra_divisiones()
        padre = self._padre_contenedor(bloque.start)
        nodo = self._nuevo_nodo(
            "hcontainer", bloque.start, bloque.end, num=etiqueta,
            refers_to=REFERS_TO_FORMULARIO if rol == "form" else REFERS_TO_DIVISION,
            padre=padre, eid=self._eid_de_bloque(padre, etiqueta or "division"),
        )
        self._extiende(nodo, bloque.end)
        self._pila.append((self.RANGO_BLOQUE, nodo))
        self._nodos_division.add(nodo)
        if rol == "form":
            self._formularios.add(nodo)

    def pide_divisiones(self) -> dict[int, tuple[str | None, str | None]]:
        """The divisions that would keep apart the articles this pass could only
        tell apart with a `_2` suffix (rule 31): for each article that took one
        (outside transitorios), the series of article numbers it belongs to -- the
        run that strictly increases up to it, in its container -- begins where the
        numbering started over, and the heading just before that point opens a
        division. The nearest heading in the gap since the previous article is the
        one (an upper-case line, a document title, else a short plain paragraph);
        with none there is no division and the repeat stays what the text says: the
        same number twice with nothing between to say it is another text."""
        nuevas: dict[int, tuple[str | None, str | None]] = {}
        for nodo in self.act.walk():
            if nodo.akn_type != "article" or nodo.eId not in self._eids.suffixed:
                continue
            if any(a.refers_to == REFERS_TO_TRANSITORIOS for a in self._ancestros(nodo)):
                continue
            hermanos = [h for h in nodo.parent.children if h.akn_type == "article"]
            posicion = hermanos.index(nodo)
            inicio = inicio_de_la_serie([h.num for h in hermanos], posicion)
            if inicio == 0:
                inicio = posicion
            primero = self._bloque_de_articulo.get(hermanos[inicio].eId)
            if primero is None:
                continue
            anterior = self._bloque_de_articulo.get(hermanos[inicio - 1].eId) if inicio else None
            encontrado = self._encabezado_de_serie(primero, anterior)
            if encontrado is not None and encontrado[0] not in self._divisiones:
                nuevas[encontrado[0]] = (encontrado[1], None)
        return nuevas

    def _ancestros(self, nodo):
        while nodo.parent is not None:
            nodo = nodo.parent
            yield nodo

    def _encabezado_de_serie(self, primero: int, anterior: int | None) -> tuple[int, str] | None:
        """The block a division in front of the series that starts at block
        `primero` opens at, and its label: the nearest strong heading between the
        previous article's block and `primero`, else the nearest weak one, else
        `primero` itself and no label. The scan stops at a marker (another API
        block) and never reads an annotation, a table row or a block the text
        classes as structure."""
        fuerte = debil = None
        for k in range(primero - 1, (anterior if anterior is not None else -1), -1):
            if k in self._marcas or self._clases[k][0] != "contenido" or es_fila_tabla(self._bloques[k]):
                if k in self._marcas:
                    break
                continue
            texto = self._bloques[k].text
            if fuerte is None:
                titulo = encabezado_fuerte(texto)
                if titulo is not None:
                    fuerte = (k, titulo)
                    break
            if debil is None:
                titulo = encabezado_debil(texto)
                if titulo is not None:
                    debil = (k, titulo)
        elegido = fuerte or debil
        if elegido is None:
            return None
        return elegido[0], elegido[1][:80]

    def _bloque_indice(self, primero, ultimo):
        """Rule 15 (review fix-1 of #291): a table of contents is one `toc`
        node, a leaf spanning its marker and every entry, hung where any other
        block of text would be. Nothing in it is classified: its entries are
        the headings the document repeats as its body, and reading them twice
        is how a `PARTE` of the IMDG Code came to have 241 articles that were
        index lines."""
        self._cierra_articulo()
        self._pendiente_epigrafe = None
        self._tabla = None
        destino = self._padre_contenedor(primero.start)
        numero = 1 + sum(1 for h in destino.children if h.akn_type == "toc")
        nodo = self._nuevo_nodo(
            "toc", primero.start, ultimo.end, padre=destino, num=str(numero),
        )
        self._extiende(nodo, ultimo.end)

    def _rango(self, akn_type: str, forzado: bool = False, refers_to: str | None = None,
               num: str | None = None):
        rango = (
            PRECEDENCIA_PARTE_EN_CAPITULO
            if akn_type == "part" and self._parte_en_capitulo
            else PRECEDENCIA[akn_type]
        )
        if (
            self._marcas and akn_type == "attachment" and not forzado
            and refers_to == REFERS_TO_ANEXO and self._es_codigo_de_resolucion()
        ):
            # Rule 29: an `ANEXO` a labelled block prints and whose structure the
            # API serves as blocks of their own *after* it (the code a resolution
            # amends, the reglamento an `ANEXO ÚNICO` approves) is the instrument's,
            # not the block's: it ends the block's container and takes the blocks
            # that follow. An annex whose structure is in the block's own text is
            # the block's (rule 33).
            self._piso = self._piso_del_bloque = None
            self._bloque_abierto = None
            self._implicita = None
            return rango
        if forzado:
            self._piso = rango
        elif self._piso is not None and rango <= self._piso:
            # Rules 21 and 33: a heading the text of this API block contains nests
            # under the container the API block itself opened, and keeps its place
            # among the other headings of the same text -- an annex holds the
            # chapters printed after it, a chapter its parts (review fix-5 of #291;
            # before it every such heading took the same rank and they were
            # siblings).
            return self._piso + self.RANGO_EN_BLOQUE + self._orden_en_bloque(akn_type, rango, refers_to, num)
        return rango

    def _es_codigo_de_resolucion(self) -> bool:
        """Whether the annex heading being read is the instrument's own and not
        the labelled block's: the block's container is open, and nothing after the
        heading in the block's text opens a container or an article -- the annex
        is a title and the text it amends or introduces, and its structure (the
        chapters of a code, the instrument an `ANEXO ÚNICO` approves) is what the
        API serves in the blocks that follow."""
        abierto = self._bloque_abierto
        if abierto is None and self._implicita is not None:
            abierto = self._implicita[0]
        if abierto is None or self._piso != self._piso_del_bloque:
            return False
        if not any(n is abierto for _, n in self._pila):
            return False
        fin = self._siguiente_de_marca.get(self._indice_marca)
        if fin is None or fin >= len(self._bloques) or self._rol_de.get(fin) in ("resolution", *self.ROLES_SIN_SECCION):
            # Nothing follows, or what follows is a text of its own (the next
            # resolution, the closing formulas): there is no structure to take.
            return False
        return not any(
            self._clases[j][0] in ("contenedor", "articulo") for j in range(self._i + 1, fin)
        )

    #: Rule 33 (review fix-5 of #291): the first rank below the container an API
    #: block opened, and how far the precedence of a heading the block's text
    #: prints moves it down from there (`precedence / 100` stays below every
    #: step of 0.25 and 0.5 the other rules use).
    RANGO_EN_BLOQUE = 0.5

    def _orden_en_bloque(self, akn_type: str, rango: float, refers_to: str | None,
                         num: str | None) -> float:
        """How far below `RANGO_EN_BLOQUE` a heading printed inside an API block
        sits: its own precedence, scaled so the order among headings is kept. A bare
        `APÉNDICE` printed after an annex of the same block is below the annex and
        above every chapter; one that carries a number (`APÉNDICE III-A`, which
        names the annex it belongs to) keeps the rank of any other attachment."""
        if akn_type == "attachment" and refers_to == REFERS_TO_APENDICE and num is None and any(
            n.akn_type == "attachment" and n.refers_to == REFERS_TO_ANEXO
            for _, n in self._pila
        ):
            return 0.005
        if (
            akn_type == "attachment" and refers_to == REFERS_TO_ANEXO and num is None
            and self._pila and self._pila[-1][1].akn_type == "attachment"
            and self._pila[-1][1].refers_to == REFERS_TO_ANEXO
            and not self._pila[-1][1].children
        ):
            # A bare `ANEXO` that follows another with nothing between them but its
            # title (`ANEXO` / `ENMIENDAS AL PROTOCOLO` / `ANEXO` / `MODIFICACIONES
            # Y ADICIONES AL ANEXO DEL CONVENIO`) is that annex's own text, the
            # annex of the annex: the first is not left an empty node.
            return 0.001
        return rango / 100

    def _dueno_numerado(self, campos) -> tuple[float, AknNode] | None:
        """Rule 25 (review fix-3 of #291): the open container an annex or an
        appendix the SCJN numbers belongs to, as ``(rank, node)``.

        A treaty prints `ANEXO 2-A` after the chapter it annexes, and the API
        says so: the block's `numero` is the chapter's number (2), and so is the
        `N-` prefix of its `num`. The innermost open container (a chapter, a
        title, a part...; never another annex or appendix) whose number is that
        one is the owner. `numero` 0 -- the API's "no owner" -- falls back to the
        prefix, and a prefix that names no open container, or none at all
        (`ANEXO I`, `APÉNDICE 1`), means no owner: the annex stays where its
        own rank puts it."""
        numero = (campos.get("numero") or "").strip()
        tipos = None
        if not numero.isdigit() or int(numero) == 0:
            # The prefix is a weaker signal than the API's own number (the
            # letter of a section is not the number of a chapter), so it is
            # only read against the containers that carry numbers.
            prefijo = re.match(r"\s*(\w+?)[-.]", campos.get("num") or "")
            numero = prefijo.group(1) if prefijo else ""
            tipos = ("book", "title", "chapter", "part")
        if not numero:
            return None
        buscado = _forma_comparable(numero)
        for rango, nodo in reversed(self._pila):
            if nodo.akn_type == "attachment" or nodo.num is None:
                continue
            if tipos is not None and nodo.akn_type not in tipos:
                continue
            if _forma_comparable(nodo.num) == buscado:
                return rango, nodo
        return None

    #: Rule 25: how far below its owner an annex sits -- under the chapter, above
    #: everything the chapter's own sections would hold.
    RANGO_BAJO_DUENO = 0.25
    #: Rule 26: where an appendix with no owner sits inside the annex that holds
    #: it -- under the annex, above every chapter or part of the annex's text.
    RANGO_APENDICE_EN_ANEXO = 0.5

    def _rango_de_anexo(self, campos) -> float | None:
        """Rules 25 and 26: the rank an annex or appendix the markers name takes
        when it belongs to something open, or `None` when it keeps its own."""
        if campos.get("tipo") not in ("annex", "appendix"):
            return None
        dueno = self._dueno_numerado(campos)
        if dueno is not None:
            return dueno[0] + self.RANGO_BAJO_DUENO
        if campos["tipo"] == "appendix" and any(
            n.akn_type == "attachment" and n.refers_to == REFERS_TO_ANEXO
            for _, n in self._pila
        ):
            return self.RANGO_APENDICE_EN_ANEXO
        return None

    def _es_cabecera_repetida(self, campos) -> bool:
        """Rule 27 (review fix-3 of #291): a bare `**APÉNDICE**` heading, with no
        number, directly under the unnumbered appendix it repeats.

        The T-MEC prints the tariff appendix of its annex 2-C over seven pages,
        and the SCJN's text carries each page's running header (`**APÉNDICE**`,
        `**AUTOPARTES**`, `Continúa`) in the middle of the table. Read as
        headings they were seven appendices (`att_attachment` ... `_7`) holding
        one table each; they are the one appendix's own text. Only where the
        document has markers, so a document without them parses as before."""
        if not self._marcas or campos.get("forzado") or campos.get("num") is not None:
            return False
        if campos["akn_type"] != "attachment" or campos.get("refers_to") != REFERS_TO_APENDICE:
            return False
        if not self._pila:
            return False
        arriba = self._pila[-1][1]
        return (
            arriba.akn_type == "attachment"
            and arriba.refers_to == REFERS_TO_APENDICE
            and arriba.num is None
        )

    def _bloque_contenedor(self, bloque, campos):
        akn_type = campos["akn_type"]
        if self._es_cabecera_repetida(campos):
            self._bloque_contenido(bloque, campos)
            return
        # A container ends whatever transitorios were open: transitional
        # provisions are the tail of the law, and a container after them
        # belongs to a further decree's own text.
        if not self._decreto:
            self._transitorios = None
        self._cierra_articulo()
        rango_propio = self._rango_de_anexo(campos) if campos.get("forzado") else None
        if rango_propio is not None:
            self._piso = rango = rango_propio
        else:
            rango = self._rango(akn_type, campos.get("forzado", False), campos.get("refers_to"),
                               campos.get("num"))
            if self._marcas and not campos.get("forzado") and campos.get("refers_to") == REFERS_TO_ANEXO:
                # Rule 31 (review fix-4 of #291): an `ANEXO 1` the text of one block
                # prints under the appendix before it (`APÉNDICE S30` and its annexes
                # 1 to 7) is that appendix's, not a sibling whose number repeats
                # the annexes of every other appendix.
                abierto = next((e for e in reversed(self._pila) if e[1].akn_type == "attachment"), None)
                if abierto is not None and abierto[1].refers_to == REFERS_TO_APENDICE:
                    rango = max(rango, abierto[0] + 0.25)
        self._cierra_hasta(rango)
        refers_to = REFERS_TO_APARTADO if akn_type == "level" else campos.get("refers_to")
        nodo = self._nuevo_nodo(
            akn_type, bloque.start, bloque.end, num=campos["num"], refers_to=refers_to,
        )
        if campos.get("heading"):
            nodo.heading = campos["heading"]
        self._pila.append((rango, nodo))
        # A heading the label's own line already carried (`PARTE 1- ENSAYO`)
        # is not looked for again in the block after it (rule 19).
        self._pendiente_epigrafe = None if campos.get("forzado") and nodo.heading else nodo

    def _bloque_articulo(self, bloque, campos):
        if (
            self._i in self._encabezados_de_leyenda and self._articulo is not None
            and self._bloque_de_articulo.get(self._articulo.eId)
            == self._encabezados_de_leyenda[self._i]
        ):
            # Rule 29: the heading line of an article already open at its caption.
            self._extiende(self._articulo, bloque.end)
            self._interior.agrega(bloque, es_marcador_estructural=True)
            for m in ANOTACION_EN_LINEA.finditer(bloque.text.split("\n", 1)[0]):
                if es_anotacion(m.group("cuerpo")):
                    self._articulo.notes.append(parse_annotation(m.group(0), m.group("cuerpo")))
            return
        self._cierra_articulo()
        num = campos["num"]
        if campos.get("alternativa") is not None:
            # The label and the text disagree and neither explains the other
            # (`ARTÍCULO 6` on a block whose text prints `ARTICULO 7.-`): the
            # one whose identifier is not already taken is the article's.
            padre = self._padre_contenedor(bloque.start)
            tomado = self._eids.propuesto(
                "" if padre.akn_type in ("act", "body") else padre.eId, "art", num
            ) in self._eids.used
            if tomado:
                num = campos["alternativa"]
        self._articulo = self._nuevo_nodo(
            "article", bloque.start, bloque.end, num=num,
        )
        self._bloque_de_articulo[self._articulo.eId] = self._i
        self._interior = ConstructorDeArticulo(
            self._articulo, self._eids, self._node_span, self._doc, PREFIJO_EID,
            self._pendientes,
        )
        # An annotation written inside the heading itself belongs to the
        # article, not to whatever comes next.
        for m in ANOTACION_EN_LINEA.finditer(bloque.text.split("\n", 1)[0]):
            if es_anotacion(m.group("cuerpo")):
                self._articulo.notes.append(
                    parse_annotation(m.group(0), m.group("cuerpo"))
                )
        # The heading block is the article's own opening text -- "Artículo 4.
        # Son obligaciones:" -- so it is placed as text, never examined for a
        # list marker it cannot carry.
        self._interior.agrega(bloque, es_marcador_estructural=True)

    def _cierra_articulo(self):
        """Finish the article that was open, if any: the list machine only
        knows where the article's closing paragraphs are once something else
        has started."""
        if self._interior is not None:
            self._interior.cierra()
        self._articulo = None
        self._interior = None

    def _promueve_a_articulo(self, bloque, kind, campos):
        """Rule 8 (issue #227) applied to one block: in a document that
        numbers nothing with `Artículo N` (`modo_sin_articulos`), the shape
        that document *does* number with opens an article in the body too.

        Called before the epigraph check rather than inside the block
        handlers, so that a promoted provision can never be swallowed as the
        epigraph of the container above it.

        Except inside the recitals (rule 17, review fix-1 of #291): the
        paragraphs numbered `PRIMERO.` .. `NOVENO.` (or `1.` ..) under a
        `CONSIDERANDO` heading are the instrument's preamble, and numbering
        them as articles gave every such lineamiento two articles `PRIMERO`.
        The recitals end at a container, at the heading that opens what the
        instrument orders (`MARCADOR_DISPOSITIVO`), at transitorios, or when
        their own numbering starts over -- a number already used inside them
        is the first provision of the next series.
        """
        linea = bloque.text.split("\n", 1)[0]
        if self._marcas:
            # Rule 19: the recitals are the API block labelled `CONSIDERANDO`,
            # and end where it does -- no text heuristic needed.
            if self._en_considerandos:
                return kind, campos
            # Rule 31: the numbered boxes of a model certificate are its form
            # fields, and the numbered paragraphs of the Mexican decree's own
            # blocks are not provisions of the instrument either.
            if self._en_formulario() or self._rol_actual in self.ROLES_SIN_SECCION:
                return kind, campos
        elif kind == "contenido" and self._transitorios is None and CONSIDERANDO.match(linea):
            self._en_considerandos = True
            self._recien_abiertos = True
            self._numeros_de_considerandos = set()
            return kind, campos
        numero = None
        if self._modo_sin_articulos == MODO_ORDINAL:
            if kind == "articulo_ordinal":
                numero = campos["num"]
        elif kind == "contenido" and self._transitorios is None:
            m = NUMERAL_DECIMAL.match(linea)
            if m and not (self._marcas and _es_valor_en_minuscula(linea[m.end():])):
                # A numeral followed by a lower-case word is a list item or a
                # value (`1.1. cuando las propiedades ...`), never the opener of
                # a provision (rule 31, review fix-4 of #291); `3. a) Por ...`
                # is still a provision whose first paragraph is lettered.
                numero = _limpia(m.group("num"))

        if self._en_considerandos and self._recien_abiertos:
            # Numbered recitals are the ones that start with a number right
            # after the heading; `Que ...` recitals leave the numbered
            # paragraphs below them to be the provisions.
            self._recien_abiertos = False
            if numero is None:
                self._en_considerandos = False
        if self._en_considerandos:
            if kind in ("contenedor", "transitorios", "transitorios_dof") or (
                kind == "contenido" and MARCADOR_DISPOSITIVO.match(linea)
            ):
                self._en_considerandos = False
            elif numero is not None:
                if numero not in self._numeros_de_considerandos:
                    self._numeros_de_considerandos.add(numero)
                    return kind, campos
                self._en_considerandos = False
        if numero is None:
            return kind, campos
        if (
            self._marcas and self._modo_sin_articulos == MODO_NUMERAL
            and self._articulo is not None and self._articulo.num == numero
        ):
            # Rule 31 (review fix-4 of #291): a numeral that repeats the number of
            # the provision directly above it is a paragraph of that provision,
            # not a second one with its number: `1.1 De conformidad con ...`
            # followed by `1.1 Las divisiones ...` (the SCJN prints `1.1` for what
            # reads `1.1.1`). The text stays in the provision it continues.
            return kind, campos
        if REINICIO_ES_ITEM and self._modo_sin_articulos == MODO_NUMERAL and numero.isdigit():
            # Rule 18 (review fix-1 of #291): a whole numeral that starts over
            # (`1.` after `3.`) or carries on a list that did is a list item
            # of the provision above, not a further article. Dotted numerals
            # (`2.1`) are provisions in their own right and never come here.
            valor = int(numero)
            if valor <= self._ultimo_entero or (
                self._item_de_lista is not None and valor == self._item_de_lista + 1
            ):
                self._item_de_lista = valor
                return kind, campos
            self._ultimo_entero = valor
            self._item_de_lista = None
        return "articulo", ({"num": numero} if self._modo_sin_articulos != MODO_ORDINAL else campos)

    def _bloque_articulo_ordinal(self, bloque, campos):
        # `**Primero.**` numbers a transitorio provision; outside
        # transitorios the same shape is an ordinary bolded paragraph opener
        # and must stay content -- unless the whole document numbers its
        # provisions that way, which `_promueve_a_articulo` decides before
        # this handler is ever reached (rule 8, issue #227).
        if self._transitorios is None:
            self._bloque_contenido(bloque, campos)
            return
        self._bloque_articulo(bloque, campos)

    def _bloque_transitorios(self, bloque, campos):
        self._abre_transitorios(bloque, campos.get("num"))

    def _bloque_transitorios_dof(self, bloque, campos):
        # A `**D.O.F. <fecha>.**` header only opens a new block of transitional
        # provisions once inside transitorios; before them, in the body, the
        # same shape is an ordinary paragraph -- unless what follows it is a
        # decree's own provisions (rule 17, review fix-1 of #291).
        if campos.get("forzado"):
            # Rule 20: the SCJN labels this block a decree, whatever came before.
            self._abre_transitorios(bloque, campos.get("num"), decreto=True)
            self._decreto = True
            return
        if self._transitorios is None and not self._abre_decreto(self._i):
            self._bloque_contenido(bloque, campos)
            return
        self._abre_transitorios(bloque, campos.get("num"))

    #: How many blocks after a `D.O.F.` header an ordinal provision is looked
    #: for (rule 17): room for the explanatory line the SCJN sometimes puts
    #: between the header and the decree's text (`SE TRANSCRIBE UNICAMENTE EL
    #: TRANSITORIO DEL DECRETO...`) and for a decree that has none.
    VENTANA_DECRETO = 4

    def _abre_decreto(self, indice: int) -> bool:
        """Whether the `D.O.F.` header at `indice` heads a decree's
        transitional provisions even though no `TRANSITORIOS` heading came
        before it (rule 17, review fix-1 of #291): an ordinal provision --
        `**PRIMERO.-**`, `**ARTICULO UNICO.-**` -- follows within
        `VENTANA_DECRETO` blocks, with nothing but explanatory lines, annotations
        and further `D.O.F.` headers between. The Ley sobre el Contrato de
        Seguro ends with five such decrees and no `TRANSITORIOS` heading at all,
        and every one of its `ARTICULO PRIMERO` was an article of the law."""
        for k in range(indice + 1, min(indice + 1 + self.VENTANA_DECRETO, self._fin_cuerpo)):
            kind, campos = self._clases[k]
            if kind == "articulo_ordinal":
                return True
            if kind == "articulo":
                return bool(_ORDINAL_PALABRA.fullmatch(campos["num"]))
            if kind not in ("contenido", "anotacion", "nota_editorial", "transitorios_dof"):
                return False
        return False

    def _abre_transitorios(self, bloque, num, decreto=False):
        # Several sibling sections, one per decree that added transitional
        # provisions -- never merged, since which decree a provision belongs
        # to is exactly what the separation records.
        #
        # Inside a decree's own text (rule 20) a `Transitorios` heading is that
        # decree's own transitory provisions, nested under it: it must not
        # end the decree and start a sibling at the body.
        padre = self._decreto_nodo if self._decreto and not decreto else None
        self._pila.clear()
        self._cierra_articulo()
        self._transitorios = None
        self._transitorios = self._nuevo_nodo(
            "section", bloque.start, bloque.end,
            num=num or "transitorios",
            refers_to=REFERS_TO_TRANSITORIOS,
            padre=padre if padre is not None else self._asegura_body(bloque.start),
        )
        if decreto:
            self._decreto_nodo = self._transitorios

    def _bloque_contenido(self, bloque, campos, es_marcador_estructural=False):
        if self._articulo is not None:
            self._extiende(self._articulo, bloque.end)
            self._interior.agrega(bloque, es_marcador_estructural)
            return
        if self._pila or self._transitorios is not None or self.body is not None:
            destino = self._destino_de_contenido(bloque)
            if es_fila_tabla(bloque):
                self._agrega_fila_de_tabla(destino, bloque)
            elif destino.akn_type == "intro":
                self._nuevo_nodo(
                    "content", bloque.start, bloque.end, padre=destino,
                    num=str(1 + sum(1 for h in destino.children if h.akn_type == "content")),
                )
                self._extiende(destino, bloque.end)
            else:
                self._nuevo_nodo("content", bloque.start, bloque.end, padre=destino)
            return
        # Nothing structural has opened yet, so this is the preamble.
        if self.preambulo is None:
            self.preambulo = self.act.add(
                AknNode("preamble", self._span(self._toma_marca(bloque.start), bloque.end, "preamble"),
                        eId=self._eids.allocate("preamble"))
            )
        else:
            self._extiende(self.preambulo, bloque.end)

    def _destino_de_contenido(self, bloque) -> AknNode:
        """The node a block of plain text (or a table row) hangs from when no
        article is open."""
        destino = self._padre_contenedor(bloque.start)
        if self._transitorios is None and self._pila:
            return self._intro_de(destino, bloque)
        if self._marcas and destino.akn_type != "act":
            # Rule 29: the text a decree's transitorios section (or a division in
            # one) opens with -- `SE TRANSCRIBEN ÚNICAMENTE LOS TRANSITORIOS DEL
            # DECRETO...` -- is its introduction, like any container's, not text
            # hanging loose from it. Rule 34 (review fix-6 of #291): so is the
            # text of the body itself that follows a table of contents (the rest
            # of a header block, after its index).
            return self._intro_de(destino, bloque)
        return destino

    def _intro_de(self, contenedor: AknNode, bloque) -> AknNode:
        """Where a block that is neither an article nor a container goes when
        it falls straight under `contenedor` (rule 12, issue #291): the
        container's `intro`, opened by the first such block.

        Everything between a container's heading (epigraph included) and its
        first child container or article is its introduction -- the *Notas.*
        of a LIGIE section or chapter, an acuerdo's opening paragraphs -- and
        so is a table that sits there. The builder only ever reaches this
        with a container that has no child yet or whose last child is its
        `intro`: any block after an article belongs to that article until the
        next container opens, which is why this text used to come out as a
        container's own loose `content` leaves. The guard on `children` keeps
        an `intro` from opening after an article all the same.
        """
        if contenedor.children and contenedor.children[-1].akn_type == "intro":
            return contenedor.children[-1]
        if (self._marcas and contenedor.children
                and all(h.akn_type in ("intro", "toc", "table") for h in contenedor.children)):
            # Rule 34 (review fix-6 of #291): what a container's own text holds
            # after its table of contents, or after a captioned table (rule 36),
            # is its introduction too -- neither is a child container or an
            # article, which is what ends one.
            n = 1 + sum(1 for h in contenedor.children if h.akn_type == "intro")
            return self._nuevo_nodo(
                "intro", bloque.start, bloque.end, padre=contenedor,
                eid=f"{contenedor.eId}__intro_{n}",
            )
        if contenedor.children:
            return contenedor
        return self._nuevo_nodo(
            "intro", bloque.start, bloque.end, padre=contenedor,
            eid=f"{contenedor.eId}__intro",
        )

    #: Rule 36: the longest caption and note, in characters, a captioned table
    #: takes with it.
    MAX_LEYENDA = 400
    MAX_NOTA_DE_TABLA = 1000

    def _es_leyenda(self, k: int) -> bool:
        bloque = self._bloques[k]
        texto = bloque.text.strip()
        return (self._clases[k][0] == "contenido" and k not in self._divisiones
                and len(texto) <= self.MAX_LEYENDA and LEYENDA_TABLA.match(texto) is not None)

    def _fin_de_leyenda(self, i: int) -> int | None:
        """Rule 36 (review fix-6 of #291): if blocks `i` to `j - 1` (one or two,
        each a bold-led line, `**A.** Tarifa para determinar ...` and `**TARIFA**`)
        are the caption of the table whose first row is block `j`, `j`."""
        j = i
        while j < self._fin_cuerpo and j - i < 2 and self._es_leyenda(j):
            j += 1
        if j == i or j >= self._fin_cuerpo or j in self._marcas:
            return None
        return j if es_fila_tabla(self._bloques[j]) else None

    def _es_nota_de_tabla(self, i: int) -> bool:
        """A plain paragraph straight after the rows of a captioned table -- `Si el
        precio del automóvil es superior a ... se reducirá ...` -- is that table's
        note, unless it opens the next caption."""
        bloque = self._bloques[i]
        return (self._clases[i][0] == "contenido" and i not in self._marcas
                and i not in self._divisiones
                and len(bloque.text) <= self.MAX_NOTA_DE_TABLA
                and not es_fila_tabla(bloque) and self._fin_de_leyenda(i) is None)

    def _abre_tabla_con_leyenda(self, i: int, fin: int) -> None:
        """Open a `table` at the caption blocks `i .. fin - 1`; the rows that
        follow extend it (rule 13) and the caption is its heading."""
        destino = self._padre_contenedor(self._bloques[i].start)
        if self._transitorios is None and self._pila and not destino.children:
            # A container whose first child this is has no introduction of its
            # own to speak of: the table is the first thing it holds.
            pass
        self._agrega_fila_de_tabla(destino, self._bloques[i])
        for k in range(i + 1, fin):
            self._extiende(self._tabla, self._bloques[k].end)
        self._tabla.heading = _limpia(self._bloques[i].text)
        self._tabla_con_leyenda = self._tabla

    def _agrega_fila_de_tabla(self, destino: AknNode, bloque) -> None:
        """One table row (rule 13): the open table grows over it, or a new
        `table` opens under `destino` -- the intro it sits in, the container,
        a transitorios section or the body."""
        if self._tabla is not None and self._tabla.parent is destino:
            self._extiende(self._tabla, bloque.end)
            return
        numero = 1 + sum(1 for h in destino.children if h.akn_type == "table")
        self._tabla = self._nuevo_nodo(
            "table", bloque.start, bloque.end, padre=destino, num=str(numero),
        )
        self._extiende(self._tabla, bloque.end)

    # Annotations and editorial notes are not nodes: their text belongs to
    # whatever node they precede, which is what keeps #162's coverage
    # invariant true. #161 turns the first kind into `AknNode.notes`.
    #
    # They are handed on as structural markers so that the list machine never
    # examines one for a fracción label. `**(REFORMADO...` cannot match the
    # marker pattern today, but a list broken by an annotation would be a
    # silent, hard-to-find failure, and saying so here costs one argument.
    def _bloque_anotacion(self, bloque, campos):
        # Annotations are not nodes and never appear in `walk()`. The block is
        # held instead, and attaches to the next node created — which also
        # pulls that node's span back to cover the annotation's own text, so
        # the document stays fully covered.
        self._pendientes.agrega(
            parse_annotation(bloque.text, campos.get("cuerpo")), bloque.start
        )
        # The open article is deliberately *not* grown over the annotation
        # here. Whatever claims it — a fracción of this article, or the next
        # article — pulls its own span back over it, and `_cierra_spans`
        # grows the ancestors afterwards; growing the article eagerly as well
        # made it overlap the sibling that then claimed the same characters
        # (201 laws' worth of `overlapping-siblings` in #162's first sweep).
        if self._articulo is not None:
            self._interior.marca_anotacion()

    def _bloque_encabezado_de_seccion(self, bloque, campos):
        # Rule 38: a line of the heading of the section the API block opened;
        # the section's span grows over it and its heading is the lines' text.
        nodo = self._implicita[0]
        self._extiende(nodo, bloque.end)
        linea = _limpia(bloque.text)
        nodo.heading = f"{nodo.heading} {linea}" if nodo.heading else linea
        self._tabla = None

    def _bloque_encabezado_previo(self, bloque, campos):
        # Rule 34: the lines that stand between an API block's marker and the
        # heading it opens a container with. They create no node: the node the
        # heading opens is pulled back over the marker (`_toma_marca`), and so
        # over them, so they are that container's heading and nothing is lost.
        self._tabla = None

    def _bloque_nota_editorial(self, bloque, campos):
        self._bloque_contenido(bloque, campos, es_marcador_estructural=True)

    def _agrega_conclusiones(self, bloque):
        if self.conclusiones is None:
            self.conclusiones = self.act.add(
                AknNode("conclusions",
                        self._span(self._toma_marca(bloque.start), bloque.end, "conclusions"),
                        eId=self._eids.allocate("conclusions"))
            )
        else:
            self._extiende(self.conclusiones, bloque.end)

    # -- spans -----------------------------------------------------------

    def _retrocede(self, nodo: AknNode, inicio: int) -> int:
        """Grow `nodo` and its ancestors backwards to `inicio` where they can,
        and return the earliest offset actually reached.

        A node may only move back as far as its own previous sibling's end:
        past that, growing it would make two siblings claim the same
        characters. The chain stops at the first ancestor that cannot move,
        since an ancestor that stays put pins everything under it.
        """
        cadena = []
        actual = nodo
        while actual is not None and actual.start_char > inicio:
            cadena.append(actual)
            actual = actual.parent

        piso = inicio
        for hijo in cadena[::-1]:
            padre = hijo.parent
            if padre is not None:
                # `h is not hijo` matters: a container opens with an empty
                # span, so without it a node counts as its own previous
                # sibling and can never move.
                previos = [
                    h for h in padre.children
                    if h is not hijo and h.end_char <= hijo.start_char
                ]
                if previos:
                    piso = max(piso, max(h.end_char for h in previos))
                piso = max(piso, padre.start_char)
            if hijo.start_char <= piso:
                return hijo.start_char
            hijo.span = self._span(piso, hijo.end_char, hijo.eId)
        return piso

    def _extiende(self, nodo: AknNode, fin: int):
        """Grow `nodo`'s span to `fin`, and every ancestor's with it — a
        parent always covers its children."""
        actual = nodo
        while actual is not None:
            if actual.end_char < fin:
                actual.span = self._span(actual.start_char, fin, actual.eId)
            actual = actual.parent

    def _cierra_spans(self):
        """Grow every node to cover its children, bottom up.

        A container's span opens at its own heading and has to reach the end
        of its last article — which is not known when the container opens,
        since it is a fact about blocks not yet read. Doing it here, in one
        post-order pass, is what makes "a parent covers its children" true of
        every node rather than only of the ones content was appended to.
        """
        def cierra(nodo: AknNode) -> int:
            fin = nodo.end_char
            for hijo in nodo.children:
                fin = max(fin, cierra(hijo))
            if fin > nodo.end_char:
                nodo.span = self._span(nodo.start_char, fin, nodo.eId)
            return fin

        cierra(self.act)


#: How many times a document with outline markers is built again to place the
#: divisions its repeated article numbers asked for (rule 31). Each pass can only
#: add divisions, and the worst document measured needs three.
MAX_PASADAS = 6


def build(doc, meta, bloques) -> AknNode:
    """`doc`'s law as a tree — the entry point `md2akn.segmenter` calls.

    A document that carries outline markers is built again, with the divisions
    that separate its restarted numberings (rule 31), until no article has to
    take a `_2` suffix or no division can still be added: the builder reads the
    text in one pass, and where a series of numbers *restarts* is only known
    once it has been read."""
    divisiones: dict[int, tuple[str | None, str | None]] = {}
    for _ in range(MAX_PASADAS):
        constructor = _Constructor(doc, meta, bloques, divisiones)
        act = constructor.run()
        if not constructor._marcas or not constructor._eids.suffixed:
            return act
        nuevas = {
            k: v for k, v in constructor.pide_divisiones().items()
            if k not in constructor._divisiones
        }
        if not nuevas:
            return act
        divisiones.update(nuevas)
    return act

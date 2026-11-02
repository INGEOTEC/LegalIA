"""The skeleton: what each API block of an instrument is, read from the SCJN's outline
markers *before* any text is placed in a tree (issue #304; the marker-reading half of
`md2akn.structure`'s old single pass).

`GET /api/SCOW/Articulos` serves an instrument as ordered blocks and `scjn.api` writes one
marker line before each (`scjn.outline`; the contract is verified by
`scripts/verify_scjn_outline.py`). That outline is the structure; the text rules come after,
*inside* a block (`md2akn.structure`). This module is the first phase: it classifies every
Markdown block of the document once, settles with the markers what the block each API block
opens *is* (a container of a type and number, an article of a number, a decree, transitorios,
an index, a labelled `hcontainer`), which API blocks open an implicit section, which are the
second half of one the API split in two, which are the steps of the promulgation decree, and
how the document numbers provisions when it writes no `Artículo N` (rule B3). A document with
no markers is a single implicit block: the skeleton is then just `clasifica` of every block
and the text rules of the next phase run unbounded, exactly as they always did.

Everything here is a pure function of the document's blocks: the `Skeleton` holds the result
and `md2akn.structure._Constructor` reads it, never the other way round.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from md2akn.classify import (
    MODO_NUMERAL,
    MODO_ORDINAL,
    _limpia,
    _sin_acentos_mayusculas,
    clasifica,
    es_fila_tabla,
    modo_sin_articulos,
)
from md2akn.hits import hit
from md2akn.divisions import (
    ENCABEZADO_ARTICULO_RR,
    PROVISION_RR,
    SE_CERTIFICA,
    VEASE_ARCHIVO_ANEXO,
    es_encabezado_de_formulario,
)
from md2akn.model import REFERS_TO_ANEXO, REFERS_TO_APENDICE
from md2akn.outline import (
    bloques_de_decreto,
    es_continuacion,
    es_contenido_suelto,
    es_linea_de_etiqueta,
    lee_marcador,
    rol_de_etiqueta,
)
from md2akn.patterns import (
    ARTICULO,
    CONCLUSIONES,
    CONCLUSIONES_ULTIMOS_BLOQUES,
    MARCADOR_INDICE,
    NUMERAL_DECIMAL,
)

_ESPACIOS = re.compile(r"\s+")



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

#: The roles whose blocks are never read as a section of numbered articles
#: (rule S6): the Mexican decree's own blocks, a closing formula, a note.
ROLES_SIN_SECCION = ("promulgation", "conclusions", "note")

#: Rule S6: how many heading-only article blocks a document needs, each followed
#: by provision blocks, before they are read as headings, and the longest a
#: heading block's lines are.
MIN_ENCABEZADOS_DE_ARTICULO = 3
MAX_LINEA_DE_ENCABEZADO = 160

#: Rule S2: the `kind`s of API block inside which no heading opens a
#: structure, and the classes of block that would.
BLOQUES_DE_UN_SOLO_TEXTO = ("article", "provision", "header", "preamble", "recitals")
APERTURAS_DE_ESTRUCTURA = (
    "articulo", "articulo_ordinal", "contenedor", "transitorios", "transitorios_dof",
)

#: How many blocks into an API block a container's heading is looked for.
VENTANA_ENCABEZADO = 6

#: The kinds of API block whose node is an `hcontainer` named by the block's label
#: (rule S8): the label line the block may open with is that node's heading.
BLOQUES_CON_ROTULO = ("other", "preamble", "recitals", "header")


#: Longest a block of a table of contents may be (rule S14). Some indexes carry
#: long titles (`5.5.3 Disposiciones especiales aplicables a los bultos...`),
#: so the bound is generous; `MAX_ENCABEZADO_INDICE` is what a heading that
#: *repeats* an entry may be.
MAX_ENTRADA_INDICE = 600
MAX_ENCABEZADO_INDICE = 250

#: How many of its first entries the body is expected to repeat (rule S14).
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
    entry or as the heading it lists (rule S14): a container or article word and
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
    """Rule S14 (review fix-1 of #291): where each table of contents is, as
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
                # Rule S4 (review fix-6 of #291): a marker is a boundary. An index
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
                # Rule S4: across a marker, only the heading the next block opens
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
            # Rule S4 (review fix-6 of #291): an index belongs to the API block it
            # starts in, so it ends at that block's end, the next marker, when
            # the repeat lies beyond it (the heading line after the marker --
            # `CAPITULO I`, then the epigraph -- belongs to the block that opens
            # with it) or when nothing but heading-sized lines separates the
            # repeat from the marker (an entry listed twice, a `Sección Primera`
            # under two chapters, is not the body). A longer paragraph after the
            # repeat is the body, in the same block, and the index ends at the
            # repeat as before.
            marca = marcas_cruzadas[0]
            # (A table row after the repeat is body text unless the index itself is
            # written as rows.)
            entradas_en_filas = any(es_fila_tabla(bloques[k]) for k in range(i + 1, repite))
            hit("S14", "crossed-marker", True)
            if repite > marca or all(
                len(bloques[k].text) <= MAX_ENCABEZADO_INDICE
                and (entradas_en_filas or not es_fila_tabla(bloques[k]))
                for k in range(repite, marca)
            ):
                fin_indice = marca
                hit("S14", "ends-at-marker", True)
                if fin_indice - i - 1 < MIN_ENTRADAS_INDICE:
                    i += 1
                    continue
        while (
            fin_indice - 1 > i + MIN_ENTRADAS_INDICE
            and len(bloques[fin_indice - 1].text) > MAX_ENCABEZADO_INDICE
        ):
            fin_indice -= 1
        # Rule S8 (review fix-4 of #291): the marker line of the API block that
        # holds the first repeated heading (`PRESENTACIÓN`) is that block's, not
        # an entry of the index it follows.
        while fin_indice - 1 > i and lee_marcador(bloques[fin_indice - 1].text) is not None:
            hit("S14", "marker-line-not-an-entry", True)
            fin_indice -= 1
        rangos[i] = fin_indice
        hit("S14", "by-text-repeat", _con_marcas(bloques))
        i = fin_indice
    return rangos


def _con_marcas(bloques) -> bool:
    """Whether the document carries outline markers (only asked when a rule is counted)."""
    return any(b.text.startswith("<!-- scjn:block ") for b in bloques)


def es_firma_de_cierre(bloques, i: int) -> bool:
    """Whether block `i` opens the closing signatures of the document (rule S11): it
    carries the words of a rubric (`CONCLUSIONES`) and no API block follows it -- on a
    snapshot with outline markers the closing signatures never cross an API block.

    This is the predicate behind the named deviation `documented-closing-signature`
    (`md2akn.validate.CLASES_DOCUMENTADAS`): an API block the SCJN labels `transitorios`
    whose whole text is `Ciudad de México, a ... Rúbrica.` is the `conclusions` the text
    says it is, not the transitorios the marker says.

    >>> from md2akn.segmenter import iter_blocks
    >>> bloques = list(iter_blocks("**Artículo 1.** Uno.\\n\\nCiudad de México, a 1 de enero.- Rúbrica."))
    >>> es_firma_de_cierre(bloques, 1)
    True
    >>> es_firma_de_cierre(bloques, 0)
    False
    """
    return bool(CONCLUSIONES.search(bloques[i].text)) and not any(
        lee_marcador(b.text) is not None for b in bloques[i + 1:]
    )


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
            if not es_firma_de_cierre(bloques, i):
                # Rule S11 (review fix-1 of #301, a correction found after the fact):
                # on a snapshot with outline markers the closing signatures never
                # cross an API block. A dated closing line followed by another API
                # block (the decree's certification after a treaty's own `PARTE FINAL
                # DEL ACTA`) is not the end of the document: that block and every
                # one after it keep their own nodes, by their own role (rule S8).
                hit("S11", "closing-blocked-by-marker", True)
                return len(bloques)
            hit("S11", "closing-by-text", _con_marcas(bloques))
            return i
    return len(bloques)

#: Rule S9 (review fix-6 of #291): the label of an `other` block that is a numbered
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
    """The number an article block is identified by (rule S1): what its text
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

    >>> from md2akn.skeleton import _numero_de_articulo
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
        hit("S1", "number-no-text")
        return etiqueta
    t, e = _forma_comparable(texto), _forma_comparable(etiqueta)
    if t == e or _extiende(t, e):
        hit("S1", "number-text-equal-or-extends")
        return texto
    if _es_el_mismo_numero_en_otra_cifra(texto, etiqueta):
        hit("S1", "number-other-numeral")
        return texto
    if _extiende(e, t):
        hit("S1", "number-label-extends")
        return etiqueta
    if es_numero_por_numero(texto, etiqueta, numero):
        hit("S1", "number-by-numero")
        return texto
    hit("S1", "number-label-default")
    return etiqueta


def _quita_etiqueta(linea: str, etiqueta: str) -> tuple[bool, str | None]:
    """Whether `linea` starts with the label `etiqueta`, and what follows it as
    a heading (`None` when nothing does): rule S1 reads `PARTE 1- ENSAYO DE
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


# -- the named deviations --------------------------------------------------------------
#
# The skeleton follows the markers, and takes the five departures below on purpose. Each is
# a named predicate used by the code that makes the decision, listed in `DEVIATIONS` under
# the class `md2akn.validate.block_conformance` reports it as (`CLASES_DOCUMENTADAS`), so
# nothing is emergent: a departure from a marker that is not on this list is a defect.


def es_articulo_servido_como_capitulo(kind: str, clase, marca) -> bool:
    """`documented-article-served-as-chapter` (rule S1): a block the API labels `ARTÍCULO
    83` whose text says `Capítulo 83`, with the very number the label gives -- the LIGIE's
    tariff chapters. It is read as the chapter the text says (a container with another
    number, a heading printed above an article, stays inside the article, rule S2).

    >>> es_articulo_servido_como_capitulo("article", ("contenedor", {"num": "83"}), {"num": "83"})
    True
    >>> es_articulo_servido_como_capitulo("article", ("contenedor", {"num": "84"}), {"num": "83"})
    False
    """
    return (kind == "article" and clase[0] == "contenedor"
            and _forma_comparable(clase[1].get("num") or "") == _forma_comparable(marca.get("num", "")))


def es_numero_por_numero(texto: str, etiqueta: str, numero: str | None) -> bool:
    """`documented-number-by-numero` (rule S1): the label and the API's own `numero`
    disagree, the text prints the number `numero` says, and the node takes the text's
    (`ARTICULO XXVII` on the block numbered 37, whose text prints `XXXVII`).

    >>> es_numero_por_numero("XXXVII", "XXVII", "37")
    True
    >>> es_numero_por_numero("XXXVII", "XXXVII", "37")
    False
    """
    return (numero is not None and numero.strip().isdigit()
            and _valor_entero(texto) == int(numero) and _valor_entero(etiqueta) != int(numero))


def es_etiqueta_repetida(numero: str, alternativa: str | None, ya_tomado: bool) -> bool:
    """`documented-number-label-repeated` (rule S1): the label and the text give two
    numbers neither explains and the label's identifier is already taken by an earlier
    block, so the node takes the text's rather than repeat an identifier. `ya_tomado` is
    the builder's own fact (the eId the label would give is in use).

    >>> es_etiqueta_repetida("47-J", "47-K", True)
    True
    >>> es_etiqueta_repetida("47-J", "47-K", False)
    False
    >>> es_etiqueta_repetida("47-J", None, True)
    False
    """
    return alternativa is not None and ya_tomado


def es_articulo_de_encabezado(lineas: list[str]) -> bool:
    """`documented-article-heading` (rule S6): an API `article` block whose text is only
    its heading and a caption -- at most two lines, each short and on a single line --
    is a candidate to open a section of the `provision` blocks that follow (the Reglamento
    General de Infantería's `ARTICULO 1°. Generalidades` over `1.- ...`). A document needs
    `MIN_ENCABEZADOS_DE_ARTICULO` of them for the rule to apply.

    >>> es_articulo_de_encabezado(["**ARTICULO 1°.**", "Generalidades"])
    True
    >>> es_articulo_de_encabezado(["**Artículo 1.** " + "x" * 200])
    False
    >>> es_articulo_de_encabezado([])
    False
    """
    return 0 < len(lineas) <= 2 and all(
        len(linea.strip()) <= MAX_LINEA_DE_ENCABEZADO and "\n" not in linea.strip() for linea in lineas
    )


#: The five departures from a marker, by the class `block_conformance` reports them under.
DEVIATIONS = {
    "documented-number-by-numero": es_numero_por_numero,
    "documented-number-label-repeated": es_etiqueta_repetida,
    "documented-article-served-as-chapter": es_articulo_servido_como_capitulo,
    "documented-closing-signature": es_firma_de_cierre,
    "documented-article-heading": es_articulo_de_encabezado,
}


@dataclass(frozen=True)
class ApiBlock:
    """One API block of a document written with outline markers, as the skeleton read
    it: its marker (`marca`), the index of the Markdown block that is the marker line
    (`inicio`), of the first block after the API block (`fin`), of the block the marker's
    `kind` is about (`encabezado`, `None` when the API block has no text), and what the
    skeleton decided about it. A document with no markers has no `ApiBlock` at all: it is
    one implicit block, and `Skeleton.blocks` is empty."""

    marca: dict
    inicio: int
    fin: int
    encabezado: int | None
    rol: str | None
    continuacion: bool
    abre_seccion_hasta: int | None


class Skeleton:
    """The first phase over one document's Markdown blocks (see the module docstring).

    Attributes, all read-only for the second phase:

    - `bloques`, `clases`: the blocks and `clasifica`'s answer for each, with the markers'
      decisions already applied (the class of an API block's heading is what its marker
      says, never what the text alone would suggest).
    - `marcas`, `de_marca`, `primeros`, `destino_de_marca`, `indices_de_marca`,
      `siguiente_de_marca`: where the markers are and which API block each block is in.
    - `fusion`, `rol_de`, `aperturas`: the second halves of split blocks, the role of each
      `other` block and the implicit sections, by marker index.
    - `indices`: the tables of contents, ``{marker block index: first block after}``.
    - `modo`: rule B3's numbering mode, `fin_cuerpo`: where the closing signatures begin.
    - `divisiones`: the divisions asked for so far (`md2akn.structure.build`), plus the
      model-certificate headings found here.
    """

    def __init__(self, bloques, divisiones=None):
        self.bloques = bloques
        #: Rule B9 (review fix-4 of #291): the blocks that open a division of
        #: their own, ``{block index: (label, role)}``, as the passes before this
        #: one asked for them (`pide_divisiones`); the model-certificate headings
        #: are added below, once the markers are known.
        self.divisiones: dict[int, tuple[str | None, str | None]] = dict(divisiones or {})
        self.fin_cuerpo = _rango_conclusiones(bloques)
        #: `clasifica` of every block, computed once: the table-of-contents
        #: scan, rule B3's gate, the rank of a `PARTE` and the pass itself all
        #: read it.
        self.clases = [clasifica(bloque) for bloque in bloques]
        #: Rule S1 (review fix-2 of #291): the outline markers a snapshot
        #: written by `scjn.api` carries, ``{block index: attributes}``, and
        #: for every block the marker of the API block it belongs to.
        self.marcas: dict[int, dict[str, str]] = {}
        self.de_marca: list[dict[str, str] | None] = [None] * len(bloques)
        #: The first block of each API block that is not an annotation: the
        #: one the marker's `kind` is about, ``{block index: attributes}``.
        self.primeros: dict[int, dict[str, str]] = {}
        #: The block each marker's `primeros` entry is at, ``{marker index: block}``.
        self.destino_de_marca: dict[int, int] = {}
        #: Rule S9: the markers (by identity) of the `other` blocks read as a provision.
        self.provisiones_sic: set[int] = set()
        #: Rule S8: for an article whose caption comes before its heading line,
        #: ``{heading block index: first block index}``.
        self.encabezados_de_leyenda: dict[int, int] = {}
        self._lee_marcas()
        self._lee_encabezados_repetidos()
        #: Rule S14 (review fix-1 of #291): the tables of contents, as
        #: ``{marker block index: first block index after it}``. Rule S1: a
        #: block the SCJN labels `ÍNDICE` is one, whatever its text says.
        self.indices = rangos_de_indice(bloques, self.clases, self.fin_cuerpo)
        for inicio, fin in self._indices_de_marcas().items():
            self.indices = {
                a: b for a, b in self.indices.items() if b <= inicio or a >= fin
            }
            self.indices[inicio] = fin
        self.en_indice = {
            k for inicio, fin in self.indices.items() for k in range(inicio, fin)
        }
        #: Rule B3 (issue #227), decided once for the whole document before
        #: the pass starts -- `None` for every law and every instrument that
        #: numbers articles the ordinary way. A table of contents is not the
        #: document's own numbering, so its blocks are not read. Rule S1: nor
        #: are a reform decree's, or the recitals', which are not the
        #: instrument's own provisions either.
        propios = [
            k for k in range(len(bloques))
            if k not in self.en_indice and not self._es_ajeno_a_la_numeracion(k)
        ]
        self.modo = modo_sin_articulos(
            [bloques[k] for k in propios], [self.clases[k] for k in propios]
        )
        if self.modo is not None:
            hit("B3", f"mode-{self.modo}", bool(self.marcas))
        self._busca_secciones(self.indices_de_marca, self.siguiente_de_marca)
        self._busca_formularios(self.en_indice)
        #: Rule B5 (review fix-1 of #291): a `PARTE` is above the books, titles
        #: and chapters unless the document opens one of those first.
        self.parte_en_capitulo = False
        for k, (kind, campos) in enumerate(self.clases):
            if k in self.en_indice or kind != "contenedor":
                continue
            if campos["akn_type"] == "part":
                break
            if campos["akn_type"] in ("book", "title", "chapter"):
                self.parte_en_capitulo = True
                break

    @property
    def blocks(self) -> list[ApiBlock]:
        """The API blocks, in document order (empty for a document with no markers)."""
        indices = self.indices_de_marca
        return [
            ApiBlock(
                marca=self.marcas[k], inicio=k, fin=self.siguiente_de_marca[k],
                encabezado=self.destino_de_marca.get(k), rol=self.rol_de.get(k),
                continuacion=k in self.fusion, abre_seccion_hasta=self.aperturas.get(k),
            )
            for k in indices
        ]

    def _lee_marcas(self) -> None:
        """Find the markers, and settle with them what the block each one
        heads *is*: the first block of an API block that is not an annotation
        carries its label's meaning (`_clase_de_marca`); for a container, the
        block that is the heading does (`_encabezado_de`)."""
        indices = [k for k, bloque in enumerate(self.bloques)
                   if bloque.text.startswith("<!-- scjn:block ")
                   and lee_marcador(bloque.text) is not None]
        siguiente = {k: (indices[n + 1] if n + 1 < len(indices) else len(self.bloques))
                     for n, k in enumerate(indices)}
        actual = None
        esperando_primero = False
        for k, bloque in enumerate(self.bloques):
            if k in siguiente:
                actual = lee_marcador(bloque.text)
                self.marcas[k] = actual
                esperando_primero = True
                self.clases[k] = ("marcador", {})
                inicio_de_marca = k
            elif esperando_primero and self.clases[k][0] not in ("anotacion", "nota_editorial"):
                esperando_primero = False
                destino = k
                if actual.get("kind") in ("container", "article", "transitorios", "decree"):
                    destino = self._encabezado_de(k, siguiente[inicio_de_marca], actual)
                    # What stands between the marker and its heading is the
                    # heading's own annotation (`(REFORMADA SU DENOMINACIÓN,
                    # ...) (REPUBLICADA, ...)`), whatever it looks like.
                    # Rule S8: that includes the court's `(NOTA: EL PLENO ...)` the
                    # SCJN prints before a chapter's own heading, which used to
                    # hang from the container before it.
                    for i in range(inicio_de_marca + 1, destino):
                        texto = self.bloques[i].text.strip().replace("**", "")
                        if texto.startswith("("):
                            hit("S1", "annotation-above-heading")
                            self.clases[i] = ("anotacion", {"cuerpo": texto.strip("() ")})
                        elif (actual.get("kind") != "article"
                              and self.clases[destino][0] in (
                                  "contenedor", "transitorios", "transitorios_dof")
                              and self.clases[i][0] not in ("anotacion", "nota_editorial")):
                            # Rule S4 (review fix-6 of #291): any other line before
                            # the heading -- the court's elision `[...]` ahead of
                            # `Transitorios`, the institution's name above `SEGUNDA
                            # PARTE` -- is part of the heading of the container this
                            # API block opens. It used to be placed as text of the
                            # container *before* it, the one the block closes.
                            hit("S4", "line-above-heading")
                            self.clases[i] = ("encabezado_previo", {})
                if (actual.get("kind") in BLOQUES_CON_ROTULO and destino > inicio_de_marca + 1
                        and es_linea_de_etiqueta(self.bloques[destino].text, actual.get("label", ""))):
                    # Rule S8: the court's `(NOTA: ...)` printed above the label line
                    # of a labelled block describes that heading, as it does a
                    # container's (rule S8); it is not the block's first paragraph.
                    for i in range(inicio_de_marca + 1, destino):
                        if self.clases[i][0] == "nota_editorial":
                            hit("S8", "note-above-label-line")
                            self.clases[i] = (
                                "anotacion",
                                {"cuerpo": self.bloques[i].text.strip().replace("**", "").strip("() ")},
                            )
                self.primeros[destino] = actual
                self.destino_de_marca[inicio_de_marca] = destino
                self.clases[destino] = self._clase_de_marca(
                    self.clases[destino], actual, self.bloques[destino]
                )
                if actual.get("kind") == "article" and self.clases[destino][0] == "articulo":
                    # Rule S8: what the API block prints before the article's own
                    # heading line (the caption `Objeto`, a `(NOTA: ...)`) is the
                    # article's, not text hanging from the container before it: the
                    # article opens at the first of those blocks and its heading
                    # line goes on in it.
                    primero = next(
                        (i for i in range(k, destino)
                         if self.clases[i][0] not in ("anotacion", "nota_editorial")),
                        None,
                    )  # (a note before `k` is an annotation now: see above)
                    if primero is not None:
                        hit("B2", "caption-before-article-heading")
                        self.clases[primero] = ("articulo", dict(self.clases[destino][1]))
                        self.encabezados_de_leyenda[destino] = primero
                if actual.get("kind") in BLOQUES_DE_UN_SOLO_TEXTO:
                    # Rule S2: a block the SCJN labels as an article is one
                    # article, and one labelled as a title block, a preamble or
                    # recitals opens none: an article, container or
                    # transitorios heading inside its text is quoted text (an
                    # amendment quoting the text it amends, an alternative
                    # wording, a list of the articles that follow), not a
                    # further one.
                    desde = destino + 1 if actual["kind"] in ("article", "provision") else k
                    for i in range(desde, siguiente[inicio_de_marca]):
                        if self.clases[i][0] in APERTURAS_DE_ESTRUCTURA:
                            hit("S2", f"demoted-in-{actual['kind']}")
                            self.clases[i] = ("contenido", {})
            self.de_marca[k] = actual
        self.indices_de_marca = indices
        self.siguiente_de_marca = siguiente

    def _lee_encabezados_repetidos(self) -> None:
        """Rule B1 (issue #300): a heading line that is only `Art. 8`, straight
        before the block `**ARTICULO 8**` with the same number, is a running head and
        not an article of its own. The Radio Regulations the CAMR-92 Final Acts
        reprint print `Art. 8` above each article's own heading; read as an article it
        left an empty `art_8` beside the real one and the number repeated. Only in a
        document with outline markers, and only when the next block opens an article of
        the same number: a lone `Art. 8` line stays what it was."""
        if not self.marcas:
            return
        for i in range(len(self.bloques) - 1):
            kind, campos = self.clases[i]
            siguiente_kind, siguiente = self.clases[i + 1]
            if kind != "articulo" or siguiente_kind != "articulo":
                continue
            if campos.get("num") != siguiente.get("num"):
                continue
            texto = self.bloques[i].text.replace("**", "")
            m = ARTICULO.match(texto.split("\n", 1)[0])
            if m and "\n" not in texto.strip() and not texto[m.end():].strip():
                hit("B1", "running-head")
                self.clases[i] = ("contenido", {})

    def _abre_articulos(self, i: int) -> bool:
        """Whether block `i` opens an article, as the pass will read it: by its
        class, or, in a document that numbers its provisions some other way
        (rule B3), by the shape that document numbers them with."""
        kind, _campos = self.clases[i]
        if kind == "articulo":
            return True
        if self.marcas and kind == "contenido":
            # Rule B2: a radio-regulations provision (`S5.43`) is an article.
            linea = self.bloques[i].text.split("\n", 1)[0]
            if PROVISION_RR.match(linea) or ENCABEZADO_ARTICULO_RR.match(linea):
                return True
        if self.modo == MODO_ORDINAL:
            return kind == "articulo_ordinal"
        if self.modo == MODO_NUMERAL and kind == "contenido":
            return bool(NUMERAL_DECIMAL.match(self.bloques[i].text.split("\n", 1)[0]))
        return False

    def _busca_secciones(self, indices, siguiente) -> None:
        """Rules S6 and S10: which markers open an implicit section, where it
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
        never opens one (rule S8): it is an `hcontainer`.

        Rule S10: a block labelled `CONTENIDO <label of the block before>` (`ARTÍCULOS`
        and `CONTENIDO ARTICULOS`) is the content of that block, not a new one:
        it opens nothing, and the container the first opened goes on through it.

        Rule S10 (issue #300): so is a bare `CONTENIDO` (a `kind=other` block with no
        label after the word) that follows a block holding only its own title -- `ANEXO`
        and `CONTENIDO`, `PROTOCOLO` and `CONTENIDO`. The decision reads the marker and
        the text of the block before it, never an instrument.
        """
        self.aperturas: dict[int, int] = {}
        self.fusion: set[int] = set()
        for n in range(1, len(indices)):
            marca, anterior = self.marcas[indices[n]], self.marcas[indices[n - 1]]
            if marca.get("kind") == "other" and (
                es_continuacion(marca.get("label", ""), anterior.get("label", ""))
                or es_contenido_suelto(
                    marca.get("label", ""), marca.get("kind", ""),
                    "\n\n".join(
                        self.bloques[i].text
                        for i in range(indices[n - 1] + 1, indices[n])
                    ),
                    anterior.get("kind", ""),
                )
            ):
                hit("S10", "continuation" if es_continuacion(marca.get("label", ""), anterior.get("label", "")) else "bare-contenido")
                self.fusion.add(indices[n])
        #: The role of each `other` block, by marker index. Rule U7 (review fix-1 of
        #: #301): a decree step is read by *position*. Only a block in the decree's
        #: leading or trailing run (`bloques_de_decreto`) has the `promulgation` role
        #: -- a block there with no role of its own (`APROBACIÓN DE INSTRUMENTOS`,
        #: `CANJE DE NOTAS`) included -- and a decree-step label inside the
        #: instrument's own text (a treaty's testimonium, `FIRMA`) has none.
        decreto = bloques_de_decreto([self.marcas[k] for k in indices])
        self.rol_de: dict[int, str | None] = {}
        for n, k in enumerate(indices):
            if self.marcas[k].get("kind") != "other":
                continue
            rol = rol_de_etiqueta(self.marcas[k].get("label", ""))
            if n in decreto and rol in (None, "promulgation"):
                rol = "promulgation"
            elif rol == "promulgation":
                rol = None
            self.rol_de[k] = rol
            hit("S8", f"role-{rol}")
        preambulos = 0
        for n, k in enumerate(indices):
            kind = self.marcas[k].get("kind")
            if kind == "preamble":
                preambulos += 1
            if k in self.fusion or not (
                kind == "other" or (kind == "preamble" and preambulos > 1)
            ):
                continue
            rol = self.rol_de.get(k)
            if rol in ROLES_SIN_SECCION:
                continue
            if id(self.marcas[k]) in self.provisiones_sic:
                continue  # rule S9: a provision printed `N (SIC)`, an article
            ultimo = n
            while ultimo + 1 < len(indices) and indices[ultimo + 1] in self.fusion:
                ultimo += 1
            propio = any(
                self._abre_articulos(i) for i in range(k + 1, siguiente[indices[ultimo]])
            )
            fin = ultimo + 1
            while fin < len(indices) and self.marcas[indices[fin]].get("kind") == "article":
                fin += 1
            siguientes = fin > ultimo + 1
            if propio or siguientes or rol == "resolution":
                hit("S6", "own-articles" if propio else "following-articles" if siguientes else "resolution")
                self.aperturas[k] = indices[fin] if fin < len(indices) else len(self.bloques)
        self._busca_articulos_de_encabezado(indices, siguiente)

    def _busca_articulos_de_encabezado(self, indices, siguiente) -> None:
        """Rule S6 (review fix-6 of #291): the Reglamento General de Infantería is
        written `ARTICULO 1°. Generalidades` and, under it, `1.- La preparación ...`
        to `5.- ...`, then `ARTICULO 2°` and `6.- ...`: the numbered provisions run
        on across the articles, which are headings that group them. Read as
        articles, the two numberings interleave (1, 5 then 2°, 6, 9 then 3°, 10 ...)
        and 70 numbers look missing. An API `article` block whose text is only its
        heading line and a caption, followed by `provision` blocks, opens a section
        of those provisions (rule S6's machinery: it lasts to the next marker that is
        neither a provision nor an `other` block) -- when the document does it three
        times or more; one such block in a reglamento is an article whose numbered
        paragraphs the API labelled provisions, and stays an article."""
        candidatos = []
        for n in range(len(indices) - 1):
            k = indices[n]
            if (self.marcas[k].get("kind") != "article"
                    or self.marcas[indices[n + 1]].get("kind") != "provision"):
                continue
            texto = [i for i in range(k + 1, siguiente[k])
                     if self.clases[i][0] not in ("anotacion", "nota_editorial")]
            if es_articulo_de_encabezado([self.bloques[i].text for i in texto]):
                candidatos.append((n, texto))
        if candidatos:
            hit("S6", "article-heading-candidates-below-threshold" if len(candidatos) < MIN_ENCABEZADOS_DE_ARTICULO
                else "article-heading-document")
        if len(candidatos) < MIN_ENCABEZADOS_DE_ARTICULO:
            return
        for n, texto in candidatos:
            fin = n + 1
            while fin < len(indices) and self.marcas[indices[fin]].get("kind") in ("provision", "other"):
                fin += 1
            hit("S6", "article-heading")
            self.aperturas[indices[n]] = indices[fin] if fin < len(indices) else len(self.bloques)
            for i in texto:
                self.clases[i] = ("encabezado_de_seccion", {})

    def _busca_formularios(self, en_indice) -> None:
        """Rule B9: the heading of a model certificate or form opens a division of
        its own -- the `MODELO DE CERTIFICADO ...` an appendix reprints, one per
        certificate, each numbering its boxes from 2.1. A second heading in the
        very next block (`CERTIFICADO DE SEGURIDAD ...` under `MODELO DE
        CERTIFICADO DE SEGURIDAD ...`) is the same certificate's title."""
        if not self.marcas:
            return
        anterior = None
        #: A heading's own `SE CERTIFICA` has not been read yet.
        esperando_cuerpo = False
        for k, bloque in enumerate(self.bloques):
            if k in en_indice or self.clases[k][0] != "contenido" or k >= self.fin_cuerpo:
                continue
            if self.de_marca[k] is not None and self.de_marca[k].get("kind") in (
                BLOQUES_DE_UN_SOLO_TEXTO + ("toc", "transitorios", "decree")
            ):
                continue
            titulo = es_encabezado_de_formulario(bloque.text)
            if titulo is not None:
                if anterior != k - 1 and k not in self.divisiones:
                    hit("B9", "form-heading")
                    self.divisiones[k] = (titulo, "form")
                anterior = k
                esperando_cuerpo = True
            elif SE_CERTIFICA.match(bloque.text.strip()):
                if esperando_cuerpo:
                    esperando_cuerpo = False
                    continue
                # A certificate whose title is a picture: it begins at the
                # placeholder the SCJN prints for it, when there is one.
                inicio = k
                if k and VEASE_ARCHIVO_ANEXO.match(self.bloques[k - 1].text.strip()) and (
                    self.clases[k - 1][0] == "contenido"
                ):
                    inicio = k - 1
                hit("B9", "se-certifica")
                self.divisiones.setdefault(inicio, ("SE CERTIFICA", "form"))

    def _encabezado_de(self, primero: int, fin: int, marca) -> int:
        """Index of the block that is an API block's heading -- the one its
        marker's `kind` is about: the first block the text itself reads as that
        (a container of that type, an article, transitorios, a `D.O.F.` header),
        else, for a container, the first that starts with the label (`PARTE 1-
        ENSAYO...` under `PARTE 1`), else `primero`. Looked for only before the
        block's first article, and not past `VENTANA_ENCABEZADO` blocks."""
        kind = marca.get("kind")
        akn_type, _ = TIPOS_DE_CONTENEDOR.get(marca.get("type"), (None, None))
        limite = min(fin, primero + VENTANA_ENCABEZADO)
        rango = []
        for i in range(primero, limite):
            rango.append(i)
            if self.clases[i][0] in ("articulo", "transitorios", "transitorios_dof"):
                break
        for i in rango:
            clase = self.clases[i]
            if kind == "container":
                if clase[0] == "contenedor" and clase[1].get("akn_type") == akn_type:
                    hit("S1", f"heading-by-class-{kind}")
                    return i
            elif kind == "article":
                if clase[0] == "articulo":
                    hit("S1", f"heading-by-class-{kind}")
                    return i
            elif kind == "transitorios":
                if clase[0] == "transitorios":
                    hit("S1", f"heading-by-class-{kind}")
                    return i
            elif kind == "decree":
                if clase[0] == "transitorios_dof":
                    hit("S1", f"heading-by-class-{kind}")
                    return i
        if kind == "container":
            for i in rango:
                if _quita_etiqueta(self.bloques[i].text.split("\n", 1)[0], marca.get("label", ""))[0]:
                    hit("S1", "heading-by-label-container")
                    return i
        hit("S1", f"heading-first-block-{kind}")
        return primero

    def _clase_de_marca(self, clase, marca, bloque):
        """`clasifica`'s answer for the first block of an API block, as its
        marker says it should read: a container of the type and number the
        label names, a reform decree, transitorios, an article of the number
        the label names. Anything else keeps the text's own reading."""
        kind = marca.get("kind")
        linea = bloque.text.split("\n", 1)[0]
        if kind == "container" and marca.get("type") in TIPOS_DE_CONTENEDOR:
            akn_type, refers_to = TIPOS_DE_CONTENEDOR[marca["type"]]
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
                else:
                    hit("S7", "label-number-extends-text")
            hit("S1", f"container-{marca['type']}")
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
            # Rule S9: `98 (SIC).- El pelotón es ...` is provision `98 (SIC)`.
            self.provisiones_sic.add(id(marca))
            hit("S9", "sic-provision")
            return "articulo", {"num": _limpia(marca["label"])}
        if kind == "decree":
            hit("S3", "decree-block")
            return "transitorios_dof", {"num": _limpia(marca.get("num", "")), "forzado": True}
        if kind == "transitorios":
            hit("S1", "transitorios-block")
            return "transitorios", {}
        if es_articulo_servido_como_capitulo(kind, clase, marca):
            # The SCJN's own label is wrong: its LIGIE calls the tariff's
            # `Capítulo 84` block `ARTÍCULO 84`. The text says container, with the
            # very number the label gives, so it is read as the container it is
            # (a container with another number, a section heading printed above
            # an article, stays inside the article, rule S2).
            hit("S1", "article-served-as-chapter")
            return clase
        if kind == "article" and marca.get("num"):
            texto = clase[1]["num"] if clase[0] == "articulo" else None
            etiqueta = _limpia(marca["num"])
            elegido = _numero_de_articulo(texto, etiqueta, marca.get("numero"))
            otro = texto if elegido == etiqueta else etiqueta
            campos = {"num": elegido}
            if texto is not None and _forma_comparable(otro) != _forma_comparable(elegido):
                hit("S1", "number-alternative")
                campos["alternativa"] = otro
            hit("S1", "article-number")
            return "articulo", campos
        return clase

    def _indices_de_marcas(self) -> dict[int, int]:
        """The blocks a marker labels `ÍNDICE`, as ``{marker index: first
        index after the API block}`` -- rule S14's table of contents, decided
        by the SCJN rather than by the text."""
        rangos = {}
        siguientes = sorted(self.marcas)
        for n, k in enumerate(siguientes):
            if self.marcas[k].get("kind") != "toc" or k >= self.fin_cuerpo:
                continue
            fin = siguientes[n + 1] if n + 1 < len(siguientes) else len(self.bloques)
            hit("S14", "by-marker")
            rangos[k] = min(fin, self.fin_cuerpo)
        return rangos

    def _es_ajeno_a_la_numeracion(self, k: int) -> bool:
        """Whether block `k` is not one of the instrument's own provisions, so
        rule B3 must not read its numbering: a reform decree's text, or the
        recitals."""
        marca = self.de_marca[k]
        return marca is not None and marca.get("kind") in (
            "decree", "recitals", "preamble", "header", "transitorios",
        )

    @staticmethod
    def _es_provision_sic(marca, linea: str) -> bool:
        """Rule S9: whether an `other` block is a numbered provision whose number
        the source prints with a `(SIC)` -- its label is `<number> (SIC)` and its
        text opens with that number and goes on."""
        m = _ETIQUETA_PROVISION_SIC.match(marca.get("label", "").strip())
        if m is None:
            return False
        resto = linea.replace("**", "").strip()
        return (resto.upper().startswith(marca["label"].strip().upper().rstrip(".")[:len(m.group("num"))])
                and re.match(rf"{re.escape(m.group('num'))}[ \t]*\((?:sic|SIC)\)[.\-– \t]*\S", resto) is not None)

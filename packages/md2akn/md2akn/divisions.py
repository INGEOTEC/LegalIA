"""The headings that open a *division* inside one block of an instrument (rule
31, review fix-4 of #291), and the numbering schemes the marker path reads that
the ordinary grammar does not (rule 32).

An API block is often a whole document: the SCJN serves the annexes of a code,
the amending resolutions of a convention or the appendices of a radio-regulations
text each as one block. Inside it, a heading line -- ``A.II - VÁLVULAS DE MARIPOSA
CONTRAINCENDIOS``, ``MODELO DE CERTIFICADO DE SEGURIDAD PARA BUQUE DE PASAJE``,
``Artículos que se refieren al Comité Ejecutivo`` -- starts a text of its own
whose articles are numbered from 1 again, and an identifier is only ever the same
identifier when it is the same article (rule 22). Two functions answer the two
questions that needs: **where does a division start** (`encabezado_de_division`,
which reads blocks) and **when is it needed** (`inicio_de_la_serie`, which reads a
run of article numbers).

This module reads text and numbers only; it has no notion of the tree.
"""

from __future__ import annotations

import re

from md2akn.patterns import ORDINAL_PALABRA

#: A heading that always opens a division of its own, in any numbering mode:
#: the model certificate or form an appendix reproduces. Matched against the
#: heading with its bold marks, quotation marks and surrounding punctuation
#: removed, and only when the whole line reads as a heading (upper case).
FORMULARIO = re.compile(
    r"^(?:MODELOS?(?: DE| PARA)?|CERTIFICADO|FORMULARIO|FORMATO)\b"
)

#: What a document title looks like: `RESOLUCIÓN 5 (Rev.CMR-2000)`,
#: `RECOMENDACIÓN 503`, `PROTOCOLO ADICIONAL`. Strong whatever its case.
TITULO_DE_DOCUMENTO = re.compile(
    r"^(?:RESOLUCI[OÓ]N|RECOMENDACI[OÓ]N|DECISI[OÓ]N|PROTOCOLO|ANEXO|AP[EÉ]NDICE|"
    r"Resoluci[oó]n|Recomendaci[oó]n|Decisi[oó]n|Protocolo|Anexo|Ap[eé]ndice)\b"
)

#: `SE CERTIFICA:`, the line that opens the body of a model certificate, and the
#: `(VÉASE ARCHIVO ANEXO)` placeholder the SCJN prints where the certificate's own
#: title is a picture: the second and later certificates of an appendix have no
#: heading in the text at all, only these two lines (the SOLAS appendix prints
#: five).
SE_CERTIFICA = re.compile(r"^[ \t]*\*{0,2}[ \t]*SE CERTIFICA\b[ \t:]*\*{0,2}[ \t]*$")
VEASE_ARCHIVO_ANEXO = re.compile(
    r"^[ \t]*\*{0,2}[ \t]*\(V[EÉ]ASE (?:EL )?ARCHIVO ANEXO\)[ \t]*\*{0,2}[ \t]*$", re.IGNORECASE
)

_MARCAS = re.compile(r"\*+")
_COMILLAS = "\"“”«»'`"
_VERSAL = re.compile(r"[A-ZÁÉÍÓÚÑÜ]")
_MARCADOR_DE_LISTA = re.compile(r"^\(?[A-Za-z0-9]{1,4}[).]\s")

#: Longest a strong heading may be (characters, markers removed), and a weak one.
MAX_FUERTE = 200
MAX_DEBIL = 300


def limpia_encabezado(texto: str) -> str:
    """A heading line without its Markdown bold marks, quotation marks and the
    punctuation a title ends with.

    >>> limpia_encabezado('**"MODELO DE CERTIFICADO**')
    'MODELO DE CERTIFICADO'
    """
    return _MARCAS.sub("", texto).strip().strip(_COMILLAS + " \t.").strip()


def _proporcion_de_mayusculas(texto: str) -> float:
    letras = [c for c in texto if c.isalpha()]
    if not letras:
        return 0.0
    return sum(1 for c in letras if _VERSAL.match(c)) / len(letras)


def _es_una_linea(texto: str) -> bool:
    return bool(texto.strip()) and "\n" not in texto.strip()


def encabezado_fuerte(texto: str) -> str | None:
    """The heading `texto` is, when it is one unmistakably, else `None`.

    A single line of at least three letters, mostly upper case (or a document
    title, whatever its case), that does not read as a numbered item (`1 ÁMBITO`),
    a reference (`(VÉASE ARCHIVO ANEXO)`), a table row or a lead-in
    (`SE CERTIFICA:`).

    >>> encabezado_fuerte("**A.II- VÁLVULAS DE MARIPOSA CONTRAINCENDIOS**")
    'A.II- VÁLVULAS DE MARIPOSA CONTRAINCENDIOS'
    >>> encabezado_fuerte("**SE CERTIFICA:**") is None
    True
    >>> encabezado_fuerte("**LINEAMIENTOS PARA LA COMPROBACIÓN DEL GASTO REALIZADO:**")
    'LINEAMIENTOS PARA LA COMPROBACIÓN DEL GASTO REALIZADO:'

    >>> encabezado_fuerte("**(VÉASE ARCHIVO ANEXO)**") is None
    True
    >>> encabezado_fuerte("**1 GENERALIDADES**") is None
    True
    >>> encabezado_fuerte("Resolución 5 (Rev.CMR-2000)")
    'Resolución 5 (Rev.CMR-2000)'
    """
    if not _es_una_linea(texto):
        return None
    limpio = limpia_encabezado(texto)
    if not limpio or len(limpio) > MAX_FUERTE or sum(c.isalpha() for c in limpio) < 3:
        return None
    if limpio[0] in "(|-[" or limpio[0].isdigit() or limpio.endswith((";", ",")):
        return None
    if limpio.endswith(":") and len(limpio.split()) < 5:
        return None  # a lead-in (`SE CERTIFICA:`), not the title of a text
    if TITULO_DE_DOCUMENTO.match(limpio) or _proporcion_de_mayusculas(limpio) >= 0.8:
        return limpio
    return None


def encabezado_debil(texto: str) -> str | None:
    """A short plain paragraph that can only be a heading by its position: the
    lead-in of a list that numbers its articles from the start again
    (`Artículos que se refieren al Comité Ejecutivo`). One line, starting with a
    capital, three to forty words, not a list item or a table row.

    >>> encabezado_debil("Artículos que se refieren al Comité Ejecutivo")
    'Artículos que se refieren al Comité Ejecutivo'
    >>> encabezado_debil("2. Las enmiendas que impliquen modificaciones") is None
    True
    """
    if not _es_una_linea(texto):
        return None
    limpio = limpia_encabezado(texto)
    if not 12 <= len(limpio) <= MAX_DEBIL or not 3 <= len(limpio.split()) <= 40:
        return None
    if not _VERSAL.match(limpio[0]) or _MARCADOR_DE_LISTA.match(limpio):
        return None
    if limpio.startswith(("|", "(", "[")) or texto.strip().startswith("|"):
        return None
    return limpio


def es_encabezado_de_formulario(texto: str) -> str | None:
    """The heading of a model certificate or form, as cleaned, else `None`.

    >>> es_encabezado_de_formulario('**"MODELO DE CERTIFICADO DE SEGURIDAD PARA BUQUE DE PASAJE**')
    'MODELO DE CERTIFICADO DE SEGURIDAD PARA BUQUE DE PASAJE'
    >>> es_encabezado_de_formulario("**CERTIFICADO DE SEGURIDAD PARA BUQUE DE PASAJE**")
    'CERTIFICADO DE SEGURIDAD PARA BUQUE DE PASAJE'
    >>> es_encabezado_de_formulario("El certificado se expedirá") is None
    True
    """
    if not _es_una_linea(texto):
        return None
    limpio = limpia_encabezado(texto)
    if (
        limpio
        and len(limpio) <= MAX_FUERTE
        and _proporcion_de_mayusculas(limpio) >= 0.8
        and FORMULARIO.match(limpio)
    ):
        return limpio
    return None


# -- article numbers: is a series restarting? --------------------------------

_PARTES = re.compile(r"\d+|[A-Za-z]+")


def clave_de_numero(numero: str | None):
    """A sortable key for an article number, or `None` when it has no leading
    digits: ``('1.1', 'A')`` for `1.1A`, ``(9, 'A')`` for `9A`. Components are
    compared as integers, the letter suffix as a string, so `9 < 9A < 10` and
    `2.2 < 2.10`.

    >>> clave_de_numero("9A") < clave_de_numero("10")
    True
    >>> clave_de_numero("2.2") < clave_de_numero("2.10")
    True
    >>> clave_de_numero("Primero") is None
    True
    """
    if not numero:
        return None
    texto = numero.strip().lstrip("S")
    m = re.match(r"\d+(?:\.\d+)*", texto)
    if m is None:
        return None
    entero = tuple(int(c) for c in m.group(0).split("."))
    resto = texto[m.end():].strip(" .-").upper()
    return entero, resto


def inicio_de_la_serie(numeros: list[str | None], posicion: int) -> int:
    """Where the run of article numbers that ends at `posicion` begins: the
    earliest index from which the numbers strictly increase up to it.

    The run is what *restarted*: in ``2, 4, 18, 30 | 5, 6, 9, 10, 18`` the second
    `18` is the repeat and the run it belongs to starts at the second `5`, so a
    division that opens before that `5` keeps every number of the run apart from
    the first list's. A number equal to its predecessor (`9A` suppressed, `9A`
    added) is a run of one.

    >>> inicio_de_la_serie(["2", "4", "18", "30", "5", "6", "9", "10", "18"], 8)
    4
    >>> inicio_de_la_serie(["9", "9A", "9A"], 2)
    2
    """
    j = posicion
    while j > 0:
        antes, ahora = clave_de_numero(numeros[j - 1]), clave_de_numero(numeros[j])
        if antes is None or ahora is None or not antes < ahora:
            break
        j -= 1
    return j


# -- numbering the ordinary grammar does not read (rule 32) -------------------

#: A provision of a radio-regulations text, `S5.43`, `S5.362B`, `S9.36.2`: an
#: `S`, the article, a dot and the provision, optionally a letter suffix.
#: Anchored at the start of a block and followed by the provision's own text.
PROVISION_RR = re.compile(
    r"^[ \t]*(?:\*\*)?[ \t]*(?P<num>S\d+\.\d+(?:\.\d+)*[A-Z]{0,2}(?:\.\d+)*)(?=[ \t]|\*|$)"
)

#: `ARTÍCULO S5`, the article of the same text that holds those provisions: a
#: container, since the provisions under it are the articles.
ENCABEZADO_ARTICULO_RR = re.compile(
    r"^[ \t]*(?:\*\*)?[ \t]*ART[IÍ]CULO[ \t]+(?P<num>S\d+[A-Z]?)[ \t]*(?:\*\*)?[ \t]*$"
)

#: `Artículo Primero.-`, `Artículo Único`: the transitory article of a decree
#: whose ordinal is written in mixed case. `patterns.ARTICULO` reads an ordinal
#: only in capitals (`ARTICULO PRIMERO`), so inside a decree these 966 articles of
#: the laws' latest snapshots fell out as loose text. Read only inside
#: transitorios, where an ordinal is the normal way a provision is numbered
#: (`patterns.ARTICULO_ORDINAL` has the same restriction).
ARTICULO_ORDINAL_MIXTO = re.compile(
    r"^[ \t]*(?:\*\*)?[ \t]*(?i:ART[IÍ]CULO)[ \t]+(?P<num>(?i:" + ORDINAL_PALABRA + r"))"
    r"(?=[ \t]*(?:[.\-–:*]|\s|$))"
)

#: `APÉNDICE S30`, `**APÉNDICE S26 ***`: the appendices of the same text. The
#: ordinary container grammar takes a number token (`1`, `4.31`, `II`), and `S30`
#: is a letter and a number.
ENCABEZADO_APENDICE_RR = re.compile(
    r"^[ \t]*\*{0,2}[ \t]*AP[EÉ]NDICE[ \t]+(?P<num>S\d+[A-Z]?)[ \t]*\*{0,3}[ \t]*$"
)

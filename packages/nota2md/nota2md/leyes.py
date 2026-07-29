"""Build a law's current (vigente) text two independent ways, so one can be
checked against the other.

`pdf_a_markdown()` cleans up Diputados' own consolidated-text PDF for a law
(https://www.diputados.gob.mx/LeyesBiblio/pdf/<archivo>.pdf) — a born-digital
PDF, not a scan, so plain text extraction is enough and no OCR is needed. This
is the *real* current text, used as ground truth.

`construye_ley()` instead derives the current text from nothing but the DOF
notes themselves: it starts from the law's original publication (parsed into
one entry per article) and replays each reform decree's own "se reforma /
adiciona / deroga el artículo N ... para quedar como sigue" instruction on top
of it, article by article. It never reads Diputados' consolidated text — the
two are meant to be compared, not share a source.

A decree often does more than one law in one go — "Artículo Primero.- Se
expide la Ley X... Artículo Segundo.- Se reforman los artículos ... de la Ley
Y..." — so both parsing steps first split the note into one segment per such
ordinal instruction and, when `nombre_ley` is given, keep only the segment
that names the law being built. Left unscoped, article numbers from an
unrelated law in the same decree would silently overwrite this one's.

Both stop at the point a reform decree's own transitory articles would start:
Diputados' PDF collects every decree's transitorios into a trailing appendix
("ARTÍCULOS TRANSITORIOS DE DECRETOS DE REFORMA") that is no longer part of
the law's substantive text, and a reform decree's own "Transitorios" section
(governing how *that* decree enters into force) is likewise not folded back
into the law. The original publication's own Transitorios section is kept —
it is still in force until a decree explicitly replaces it, which none of the
44 laws this was built against ever does.
"""

import re
import unicodedata

from nota2md.builder import fetch_nota
from nota2md.html_converter import html_to_markdown

# --- ground truth: Diputados' own consolidated-text PDF --------------------

_PIE_PAGINA = re.compile(r"^\s*\d+\s+de\s+\d+\s*$")
_ULTIMA_REFORMA = re.compile(r"^\s*[UÚ]ltima reforma publicada", re.I)
_APENDICE_TRANSITORIOS = re.compile(
    r"^\s*ART[ÍI]CULOS?\s+TRANSITORIOS\s+DE\s+(?:LOS\s+)?DECRETOS?\s+DE\s+REFORMA",
    re.I | re.M,
)


def _cuerpo_de_pagina(pagina: str) -> list[str]:
    """A page's lines, past the header Diputados repeats on every page (title,
    "CÁMARA DE DIPUTADOS...", "Última Reforma DOF ..." and the "N de M"
    marker)."""
    lineas = pagina.splitlines()
    for i, linea in enumerate(lineas):
        if _PIE_PAGINA.match(linea):
            return lineas[i + 1 :]
    return lineas


# --- turning the extracted text into actual Markdown ------------------------
#
# The PDF's text layer is just lines wrapped for print, with no markup at
# all — plain extraction reads nothing like nota2md's own output (headings,
# **bold** leads) even though it is the same DOF prose. These patterns give
# it the same shape: a paragraph's opening "Artículo N.", ordinal ("Primero.")
# or list marker ("I.", "a)") is bolded, an ALL-CAPS caption line is bolded
# whole, and "Al margen un sello..."/"Transitorios" become headings — the same
# elements html_to_markdown marks up, read off typography instead of CSS
# classes.

_ORDINAL = (
    r"(?:Único|Unico|Primero|Segundo|Tercero|Cuarto|Quinto|Sexto|S[ée]ptimo|"
    r"Octavo|Noveno|D[ée]cimo(?:\s+(?:Primero|Segundo|Tercero|Cuarto|Quinto|"
    r"Sexto|S[ée]ptimo|Octavo|Noveno))?|Vig[ée]simo(?:\s+\w+)?|"
    r"Trig[ée]simo(?:\s+\w+)?|Cuadrag[ée]simo(?:\s+\w+)?|"
    r"Quincuag[ée]simo(?:\s+\w+)?|Sexag[ée]simo(?:\s+\w+)?)"
)
_LEAD_ARTICULO = re.compile(
    r"^(Art[íi]culo\s+(?:\d+\s*(?:o\b\.?|[°º])?\s*"
    r"(?:Bis|Ter|Qu[áa]ter|Quinquies|Sexies|Septies|Octies|Nonies|Decies)?|"
    rf"{_ORDINAL})\.?-?)",
    re.I,
)
# A Transitorios item numbered by bare ordinal, "Primero." not "Artículo Primero.".
_LEAD_ORDINAL = re.compile(rf"^({_ORDINAL}\.)", re.I)
_LEAD_LISTA = re.compile(r"^((?:[IVXLCDM]+|[a-záA-Z])[\.\)])(?=\s)")
_MARGEN = re.compile(r"^Al margen un sello\b.*", re.I)
_TRANSITORIOS_PARRAFO = re.compile(r"^TRANSITORIOS?$", re.I)
# Words only, no trailing space inside the group — a stray space pypdf's
# extraction sometimes inserts before the comma must land outside the bold.
_LEAD_NOMBRE = re.compile(r"^((?:[A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ.]*\s+){1,6}[A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ.]*)(\s*,\s)")


def _es_titular(parrafo: str) -> bool:
    """Whole-paragraph ALL-CAPS captions ("DECRETO", "SE EXPIDE LA LEY...") are
    bolded in full — mirroring how nota2md's HTML path bolds them (they are
    styled bold in the DOF's own markup, which the PDF's plain text loses)."""
    letras = [c for c in parrafo if c.isalpha()]
    return len(letras) >= 3 and len(parrafo) < 300 and all(c.isupper() for c in letras)


def _formatea_parrafo(parrafo: str) -> str:
    if _MARGEN.match(parrafo):
        return f"## {parrafo}"
    if _TRANSITORIOS_PARRAFO.match(parrafo):
        return f"## {parrafo.capitalize()}"
    if _es_titular(parrafo):
        return f"**{parrafo}**"
    for patron in (_LEAD_ARTICULO, _LEAD_ORDINAL, _LEAD_LISTA):
        m = patron.match(parrafo)
        if m:
            return f"**{m.group(1)}**{parrafo[m.end(1):]}"
    m = _LEAD_NOMBRE.match(parrafo)
    if m:
        return f"**{m.group(1)}**{m.group(2)}{parrafo[m.end(2):]}"
    return parrafo


def _parrafos(texto: str) -> list[str]:
    """`texto`'s paragraphs, each reflowed to one line — the PDF's own line
    breaks are just where the printed page ran out of width, not paragraph
    boundaries."""
    parrafos = []
    for crudo in re.split(r"\n\s*\n+", texto):
        junto = re.sub(r"\s+", " ", " ".join(crudo.splitlines())).strip()
        if junto:
            parrafos.append(junto)
    return parrafos


def limpia_texto_ley(paginas: list[str]) -> str:
    """The law's current substantive text, out of its PDF's page texts —
    reflowed into Markdown, not just cleaned-up plain text.

    Strips the per-page header, the front-matter Diputados adds before the
    decree's own text ("Nueva Ley publicada...", "TEXTO VIGENTE", "Última
    reforma publicada..."), and the trailing appendix of every reform decree's
    own transitory articles — none of the three are part of the law itself —
    then reflows what remains into Markdown paragraphs (see
    _formatea_parrafo()) so the result reads like nota2md's own output
    instead of a plain-text PDF dump.
    """
    cuerpo: list[str] = []
    for i, pagina in enumerate(paginas):
        lineas = _cuerpo_de_pagina(pagina)
        if i == 0:
            for j, linea in enumerate(lineas):
                if _ULTIMA_REFORMA.match(linea):
                    lineas = lineas[j + 1 :]
                    break
        cuerpo.extend(lineas)
        cuerpo.append("")

    texto = "\n".join(cuerpo)
    corte = _APENDICE_TRANSITORIOS.search(texto)
    if corte:
        texto = texto[: corte.start()]

    parrafos = [_formatea_parrafo(p) for p in _parrafos(texto)]
    return "\n\n".join(parrafos) + "\n"


def pdf_a_markdown(pdf_path) -> str:
    """`limpia_texto_ley()`, reading the pages straight out of a PDF file."""
    from pypdf import PdfReader

    paginas = [p.extract_text() for p in PdfReader(str(pdf_path)).pages]
    return limpia_texto_ley(paginas)


# --- construye_ley: replay the reforms on top of the original text ---------

_SUFIJOS = {
    "bis": "Bis", "ter": "Ter", "quater": "Quáter", "quáter": "Quáter",
    "quinquies": "Quinquies", "sexies": "Sexies", "septies": "Septies",
    "octies": "Octies", "nonies": "Nonies", "decies": "Decies",
}
_ARTICULO = re.compile(
    r"^\*{0,2}Art[íi]culo\s+(\d+)\s*(?:o\b\.?|[°º])?\s*"
    r"(bis|ter|qu[áa]ter|quinquies|sexies|septies|octies|nonies|decies)?\b",
    re.I,
)
_TRANSITORIOS = re.compile(r"^#+\s*Transitorios?\b", re.I)
_VERBO_REFORMA = re.compile(r"\bse\s+(reforman?|adicionan?|derogan?)\b", re.I)
_NUM_TOKEN = re.compile(
    r"\d+(?:\s+(?:Bis|Ter|Qu[áa]ter|Quinquies|Sexies|Septies|Octies|Nonies|Decies))?",
    re.I,
)


def _canon_numero(base: str, sufijo: str | None) -> str:
    if not sufijo:
        return base
    return f"{base} {_SUFIJOS.get(sufijo.lower(), sufijo.capitalize())}"


def _clave_orden(numero: str) -> tuple[int, int]:
    m = re.match(r"(\d+)\s*(.*)", numero)
    base, sufijo = int(m.group(1)), m.group(2).strip().lower()
    rango = {"": 0, "bis": 1, "ter": 2, "quáter": 3, "quinquies": 4,
              "sexies": 5, "septies": 6, "octies": 7, "nonies": 8, "decies": 9}
    return base, rango.get(sufijo, 50)


def _inserta_en_orden(orden: list[str], numero: str) -> None:
    clave = _clave_orden(numero)
    for i, existente in enumerate(orden):
        if _clave_orden(existente) > clave:
            orden.insert(i, numero)
            return
    orden.append(numero)


def _bloques(markdown: str) -> list[str]:
    return [b for b in markdown.split("\n\n")]


def _encabezado(bloque: str) -> str:
    """`bloque`'s text with Markdown emphasis markers removed, so a heading
    html_to_markdown splits across adjacent bold runs ("**Artículo**
    **Segundo.-**", instead of one "**Artículo Segundo.-**" span) still
    matches as the one phrase it is."""
    return re.sub(r"\*+", "", bloque.strip())


_TITULO_NOTA = re.compile(r"^#\s")
# A block opening a new instrument's own instruction ("Artículo Primero.-",
# "Artículo Único.-"...): "Artículo" followed by a word, not a number — a
# restated article ("Artículo 5.") never matches this.
_INSTRUCCION_INICIO = re.compile(r"^\*{0,2}Art[íi]culo\s+[A-Za-zÁÉÍÓÚáéíóú]+\b", re.I)


def _segmentos_por_instrumento(bloques: list[str]) -> list[tuple[int, int]]:
    """`bloques` split at each "Artículo Primero/Segundo/Único.-..." instruction,
    one segment per instrument a decree touches. A decree naming only one
    instrument yields a single segment spanning the whole list.

    Only markers before the decree's own Transitorios section count: a
    decree's transitorios are commonly numbered "Artículo Primero.-/Segundo.-"
    too, and a decree that opens with a bare "Único.-" (no "Artículo") has
    none of the real kind at all — reading those transitorio markers as
    instrument boundaries then leaves the entire real body outside every
    segment `_elige_segmento` could pick.
    """
    limite = next(
        (i for i, b in enumerate(bloques) if _TRANSITORIOS.match(_encabezado(b))),
        len(bloques),
    )
    indices = [
        i for i in range(limite) if _INSTRUCCION_INICIO.match(_encabezado(bloques[i]))
    ]
    if not indices:
        return [(0, len(bloques))]
    return [
        (i, indices[idx + 1] if idx + 1 < len(indices) else len(bloques))
        for idx, i in enumerate(indices)
    ]


def _texto_instruccion(bloques: list[str], inicio: int, fin: int) -> str:
    """A segment's own instruction clause — everything up to its first
    restated article or Transitorios heading, not the segment's whole body.

    A long segment can mention another law in passing somewhere in its own
    articles (a cross-reference); only the clause that opens it ("Se expide/
    reforma/adiciona/deroga ... de la Ley X") reliably says which law the
    segment itself is about.
    """
    i = inicio
    while i < fin and not _ARTICULO.match(_encabezado(bloques[i])) and not _TRANSITORIOS.match(_encabezado(bloques[i])):
        i += 1
    return " ".join(bloques[inicio:i])


def _elige_segmento(bloques: list[str], nombre_ley: str | None) -> tuple[int, int]:
    """The segment naming `nombre_ley`, or the first one if there is only one
    segment, `nombre_ley` is not given, or none names it (best effort)."""
    segmentos = _segmentos_por_instrumento(bloques)
    if nombre_ley is None or len(segmentos) == 1:
        return segmentos[0]
    objetivo = normaliza_para_comparar(nombre_ley)
    if objetivo:
        for inicio, fin in segmentos:
            if objetivo in normaliza_para_comparar(_texto_instruccion(bloques, inicio, fin)):
                return (inicio, fin)
    return segmentos[0]


def _segmenta_original(markdown: str, nombre_ley: str | None = None) -> tuple[str, dict, str | None]:
    """The original publication note, split into its preamble, its articles
    (número -> texto, in document order) and its own Transitorios section."""
    bloques = _bloques(markdown)

    # The note's own H1 is dofjson's title for it (used to index/search notes),
    # not part of the decree — Diputados' consolidated text does not carry it.
    inicio_doc = 1 if bloques and _TITULO_NOTA.match(_encabezado(bloques[0])) else 0
    resto = bloques[inicio_doc:]

    # The front matter common to every instrument in the decree ("Al margen un
    # sello...", "DECRETO", "...DECRETA:") sits before the *first* instrument
    # segment, whether or not it is the one chosen — using the chosen
    # segment's own start here would swallow whole any unrelated instrument
    # that happens to come before it in the same decree.
    primer_inicio = _segmentos_por_instrumento(resto)[0][0]
    inicio_seg, fin_seg = _elige_segmento(resto, nombre_ley)
    comun, segmento = resto[:primer_inicio], resto[inicio_seg:fin_seg]
    n = len(segmento)

    i = 0
    while i < n and not _ARTICULO.match(_encabezado(segmento[i])):
        i += 1
    preambulo = "\n\n".join(comun + segmento[:i])

    articulos: dict[str, str] = {}
    while i < n and not _TRANSITORIOS.match(_encabezado(segmento[i])):
        m = _ARTICULO.match(_encabezado(segmento[i]))
        if not m:
            i += 1
            continue
        numero = _canon_numero(m.group(1), m.group(2))
        cuerpo = [segmento[i]]
        i += 1
        while i < n and not _ARTICULO.match(_encabezado(segmento[i])) and not _TRANSITORIOS.match(_encabezado(segmento[i])):
            cuerpo.append(segmento[i])
            i += 1
        articulos[numero] = "\n\n".join(cuerpo)

    transitorios = "\n\n".join(segmento[i:]) if i < n else None
    return preambulo, articulos, transitorios


def _extrae_reforma(markdown: str, nombre_ley: str | None = None) -> tuple[str, list[tuple[str, str]]]:
    """A reform decree's instruction clause, and the articles it restates in
    full (número, texto), in the order the decree prints them.

    Only the numbered "Artículo N" blocks are read as restatements — the
    decree's own "Artículo Único/Primero.- Se reforma..." instruction is
    never mistaken for one, since it names an ordinal, not a number.
    """
    bloques = _bloques(markdown)
    inicio, fin = _elige_segmento(bloques, nombre_ley)
    segmento = bloques[inicio:fin]
    n = len(segmento)

    i = 0
    while i < n and not _ARTICULO.match(_encabezado(segmento[i])) and not _TRANSITORIOS.match(_encabezado(segmento[i])):
        i += 1
    instruccion = "\n\n".join(segmento[:i])

    nuevos = []
    while i < n and not _TRANSITORIOS.match(_encabezado(segmento[i])):
        m = _ARTICULO.match(_encabezado(segmento[i]))
        if not m:
            i += 1
            continue
        numero = _canon_numero(m.group(1), m.group(2))
        cuerpo = [segmento[i]]
        i += 1
        while i < n and not _ARTICULO.match(_encabezado(segmento[i])) and not _TRANSITORIOS.match(_encabezado(segmento[i])):
            cuerpo.append(segmento[i])
            i += 1
        nuevos.append((numero, "\n\n".join(cuerpo)))
    return instruccion, nuevos


def _numeros_derogados(instruccion: str) -> list[str]:
    """Articles the instruction clause repeals, read off its "se deroga(n) el
    artículo(s) N" clauses — the only operation that prints no restated text
    to key a replacement off of."""
    verbos = list(_VERBO_REFORMA.finditer(instruccion))
    derogados = []
    for idx, m in enumerate(verbos):
        if "derog" not in m.group(1).lower():
            continue
        fin = verbos[idx + 1].start() if idx + 1 < len(verbos) else len(instruccion)
        segmento = instruccion[m.end() : fin]
        for tok in _NUM_TOKEN.findall(segmento):
            partes = tok.split(None, 1)
            derogados.append(_canon_numero(partes[0], partes[1] if len(partes) > 1 else None))
    return derogados


def _markdown_de_nota(cod_nota: int) -> str:
    nota = fetch_nota(cod_nota)
    if not nota.get("cadenaContenido"):
        raise ValueError(
            f"la nota {cod_nota} no tiene cadenaContenido (HTML); construye_ley "
            "sólo soporta notas con texto digital"
        )
    return html_to_markdown(nota["cadenaContenido"])


class LeyNoReconstruible(ValueError):
    """construye_ley() cannot build this law from the notes it was given.

    Raised when the original publication yields no recognized article at
    all — building reforms on top of that would only manufacture a
    confident-looking document out of whatever a later reform happens to
    restate, not a reconstruction of the law. The DOF sometimes publishes a
    note with no real body at all: a large annex (e.g. a tariff schedule)
    can go out as a PDF embedded in the DOF's own page rather than as
    parseable HTML, in which case SIDOF and the DOF's own fallback page
    both carry only the note's title.
    """


def _diagnostico_original_vacia(markdown: str) -> str:
    if not re.search(r"^\s*#+\s*Al margen un sello\b", markdown, re.M | re.I):
        return (
            "la nota no trae el cuerpo del decreto, solo su título — el DOF "
            "puede haber publicado el contenido real como un PDF/anexo aparte "
            "(p. ej. una tarifa arancelaria) en vez de HTML navegable"
        )
    return (
        "el texto sí tiene cuerpo, pero no se reconoció en él ningún "
        '"Artículo N" (¿usa una numeración o formato distinto?)'
    )


def construye_ley(cod_notas: list[int], nombre_ley: str | None = None) -> str:
    """The law's current text, built only from the DOF notes in `cod_notas`.

    `cod_notas` is a law's reform history as `leyesmx.historial` returns it:
    oldest first, index 0 the original publication and the rest its reform
    decrees in order. Each decree is replayed on top of the previous state —
    a restated "Artículo N" replaces or inserts that article, a "se deroga el
    artículo N" with no restated text marks it repealed — never touching the
    preamble or the original Transitorios section.

    `nombre_ley` (as `leyesmx.historial` names it, e.g. "LEY de Amnistía")
    scopes every note to the one instrument among the several a single decree
    may touch — pass it whenever a note is shared with another law's history.
    Left as None, a note is assumed to concern only this law, which holds for
    most of them but silently mixes in another law's articles for the rest.

    Raises LeyNoReconstruible if the original publication yields no article
    at all to build on — see that exception for why this can happen.
    """
    if not cod_notas or cod_notas[0] is None:
        raise ValueError("cod_notas necesita al menos la publicación original")

    md_original = _markdown_de_nota(cod_notas[0])
    preambulo, articulos, transitorios = _segmenta_original(md_original, nombre_ley)
    if not articulos:
        raise LeyNoReconstruible(
            f"no se pudo reconstruir la ley a partir de la nota original "
            f"(codNota {cod_notas[0]}): {_diagnostico_original_vacia(md_original)}"
        )
    orden = list(articulos.keys())

    for cod_nota in cod_notas[1:]:
        if cod_nota is None:
            continue
        instruccion, nuevos = _extrae_reforma(_markdown_de_nota(cod_nota), nombre_ley)
        for numero, texto in nuevos:
            if numero not in articulos:
                _inserta_en_orden(orden, numero)
            articulos[numero] = texto
        restatados = {numero for numero, _ in nuevos}
        for numero in _numeros_derogados(instruccion):
            if numero in restatados:
                continue
            if numero not in articulos:
                _inserta_en_orden(orden, numero)
            articulos[numero] = f"**Artículo {numero}.** Derogado."

    partes = [preambulo] if preambulo else []
    partes.extend(articulos[numero] for numero in orden)
    if transitorios:
        partes.append(transitorios)
    return "\n\n".join(p for p in partes if p)


# --- comparing the two ------------------------------------------------------

_MARKDOWN_SYNTAX = re.compile(r"[#*_`]|\|(?=[^|]*\|)")


def normaliza_para_comparar(texto: str) -> str:
    """Fold away formatting differences (Markdown syntax, accents, case,
    whitespace) that don't reflect a real difference in the law's content, so
    similarity is measured on words, not on typesetting."""
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = _MARKDOWN_SYNTAX.sub("", texto)
    texto = texto.lower()
    return re.sub(r"\s+", " ", texto).strip()

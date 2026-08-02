"""Diputados' own consolidated "texto vigente" PDF for a law
(https://www.diputados.gob.mx/LeyesBiblio/pdf/<archivo>.pdf), cleaned up into
Markdown — a born-digital PDF, not a scan, so plain text extraction is enough
and no OCR is needed.

This is the *real* current text of a law. It exists here only as ground
truth to check `reconstruct_legal_provisions()` (see nota2md.leyes) against —
nothing in this module feeds into that function's own output, and it never
reads this module's output either; the two are meant to be compared, not
share a source.
"""

import re

# The "N de M" page-number marker Diputados prints at the bottom of every page.
_PIE_PAGINA = re.compile(r"^\s*\d+\s+de\s+\d+\s*$")
# The front-matter line naming the law's most recent reform, printed once on
# the PDF's first page before the decree's own text starts.
_ULTIMA_REFORMA = re.compile(r"^\s*[UÚ]ltima reforma publicada", re.I)
# The heading that opens the trailing appendix collecting every reform
# decree's own transitory articles — not part of the law's substantive text.
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

# Every ordinal word Diputados' PDFs use to number a Transitorios item
# ("Primero.", "Décimo Segundo.") or an instrument ("Artículo Único.-").
_ORDINAL = (
    r"(?:Único|Unico|Primero|Segundo|Tercero|Cuarto|Quinto|Sexto|S[ée]ptimo|"
    r"Octavo|Noveno|D[ée]cimo(?:\s+(?:Primero|Segundo|Tercero|Cuarto|Quinto|"
    r"Sexto|S[ée]ptimo|Octavo|Noveno))?|Vig[ée]simo(?:\s+\w+)?|"
    r"Trig[ée]simo(?:\s+\w+)?|Cuadrag[ée]simo(?:\s+\w+)?|"
    r"Quincuag[ée]simo(?:\s+\w+)?|Sexag[ée]simo(?:\s+\w+)?)"
)
# A paragraph's opening "Artículo N." or "Artículo Ordinal.-" lead, numeric or
# ordinal, to be bolded the same way html_to_markdown bolds a note's own.
_LEAD_ARTICULO = re.compile(
    r"^(Art[íi]culo\s+(?:\d+\s*(?:o\b\.?|[°º])?\s*"
    r"(?:Bis|Ter|Qu[áa]ter|Quinquies|Sexies|Septies|Octies|Nonies|Decies)?|"
    rf"{_ORDINAL})\.?-?)",
    re.I,
)
# A Transitorios item numbered by bare ordinal, "Primero." not "Artículo Primero.".
_LEAD_ORDINAL = re.compile(rf"^({_ORDINAL}\.)", re.I)
# A fracción/inciso list marker opening a paragraph ("I.", "a)").
_LEAD_LISTA = re.compile(r"^((?:[IVXLCDM]+|[a-záA-Z])[\.\)])(?=\s)")
# The "Al margen un sello..." caption every DOF decree opens with.
_MARGEN = re.compile(r"^Al margen un sello\b.*", re.I)
# A "Transitorios"/"Transitorio" paragraph on its own, with nothing else in it.
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
    """`parrafo` reformatted to match nota2md's own Markdown conventions —
    a heading, a bolded caption, or a bolded lead ("Artículo N.", "I.", "a)")
    — or `parrafo` itself unchanged if none of those patterns apply."""
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

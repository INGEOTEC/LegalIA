"""The plain-text-to-Markdown writer of the SCJN's consolidated texts: what
`GET /api/SCOW/Articulos` answers, block by block, becomes the light Markdown a
snapshot stores (issue #175; split out of `scjn.api` by issue #303, a move with
no behaviour change -- `scjn.api` re-exports every name below).

**What the writer promises.**

- *The text.* One blank-line-separated paragraph per source paragraph, editorial
  asides (`N. DE E. ...`) removed, a bold lead on an `Artículo N`/ordinal/list
  marker, a bold caption on an ALL-CAPS line, a `##` heading for "Al margen un
  sello"/"Transitorios", and one `| cell | cell |` paragraph per recovered table row
  (issue #253). It reproduces the retired `.docx` path byte for byte wherever a block
  has no tab.
- *The outline* (`outline=True`, what `scjn.api.snapshot` passes; review fix-2 of
  #291). Every API block that yields text is preceded by exactly one marker line
  (`scjn.outline`) carrying its own `orden`, `numero` and `referencia`. A block that
  yields no text (its whole text was an editorial aside, or it is empty) has **no**
  marker: its `orden` is a gap in the markers, *explained* when the raw answer kept
  beside the snapshot serves it. `scjn.outline.strip_markers` of the result is
  exactly the `outline=False` output, which is the text as it always was.
- *Nothing else.* `referencia`/`orden` never shape the text. The vocabulary of a
  `referencia` is open, so a block with an unexpected one comes out as the same
  paragraph it always did.

**How it is verified.** `scripts/verify_scjn_outline.py` re-runs this writer over every
raw answer kept on disk and reports, per collection, writer drift, marker
completeness, the attribute round-trip and text invariance (see `scjn`'s README,
"Verifying the outline"); `tests/test_markdown.py` and `tests/test_api.py` pin the
paragraph rules.

The ordinal/Latin/roman tables below are the *SCJN side* of a comparison and are a
copy of `md2akn.patterns.NUM_ARTICULO`'s shapes on purpose (`scjn` imports nothing
else of this monorepo). `scjn.outline` has its own, different, accent-folded
`_ORDINAL` for classifying a *label*; the two read different strings (a paragraph's
lead, a block's label) and share no table.
"""

import re
from typing import TYPE_CHECKING

from scjn.outline import marker_line
from scjn.text import quita_notas_editoriales

if TYPE_CHECKING:  # pragma: no cover
    from scjn.api import Articulo

# Hard rule: the on-disk format does not change. `scripts/scjn/`'s whole
# corpus, its `indice.json`, `empaqueta_scjn_leyes.py`, `snapshot_de_codNota`
# and `legal_provisions(source="scjn")` all read what the retired
# `scjn.descarga_ordenamiento` wrote, and the migration is only auditable while a snapshot written
# from the API can be diffed against the one the WebForms crawler wrote for
# the same law and the same date.
#
# So the per-paragraph pipeline is literally the .docx one: split into
# paragraphs, `quita_notas_editoriales`, `_formatea_parrafo`. Issue #173's
# question 3
# settled that this is right rather than merely convenient — the API's own
# `contenido` carries the "N. DE E." markers with the same spelling as the
# .docx (181 of them in one CPEUM reform, 193 in one of the Código de
# Comercio, against 0 in the corresponding file on disk, which is exactly the
# stripping already having happened), so the logic is reused unchanged.
#
# The one exception is a blank-line-separated block that carries a tab
# (issue #253): the SCJN's plain-text `contenido` uses a run of tabs as its
# only column separator, so such a block is diverted to
# `_paragraphs_from_tabbed_block` and recovers one Markdown paragraph per
# table row instead of one per wrapped source line. A block with no tab is
# completely unaffected and still reproduces the .docx path byte for byte.
#
# `referencia`/`orden` do not shape the *text* of the file. They are reliable
# (issue #173: never empty over ~13 000 articles, `orden` always contiguous)
# but their vocabulary is open — besides `ENCABEZADO`/`TÍTULO PRIMERO`/
# `ARTÍCULO 1` it includes chapter names and the editorial `D.O.F. <fecha>`
# rows, which are precisely what `quita_notas_editoriales` removes. An
# article whose `referencia` is unexpected has to come out as the same
# paragraph it does today, not as a differently-shaped file.
#
# Review fix-2 of #291 (a finding made after the fact) is the one place they
# are used: with `outline=True` (what `snapshot` passes) each block that yields
# text is preceded by one marker line stating its `orden`, `numero` and
# `referencia` (`scjn.outline`), so a reader no longer has to guess the
# structure back from flattened text. Strip the markers and the file is the
# text it has always been (a test pins it).

# --- paragraph classification ------------------------------------------
#
# The SCJN's text carries no formatting of its own: "TEXTO ORIGINAL.",
# "Artículo N.-" leads and "TRANSITORIOS" captions are plain text, told apart
# only by their own wording/casing. This lived alongside the crawl itself
# while the source was the reform row's .docx (one docx paragraph already is
# one clean block, so only this per-paragraph classification was ever needed
# on top of it); it moved here with issue #179, when the .docx path went away
# and `articulos_a_markdown` became its only caller. The API's `contenido` is
# in exactly the same shape, which is why the classifier is reused byte for
# byte rather than rewritten — that reuse is what makes a snapshot written
# from the API diffable against the one the WebForms crawler wrote for the
# same law and the same date. That diffability holds for a block with no
# tab; `_LEAD_ARTICULO`/`_LEAD_ORDINAL` are reused by
# `_paragraphs_from_tabbed_block` too (issue #253), as the guard that keeps
# an article/ordinal lead out of a recovered table row.
#
# These patterns are the SCJN side of a comparison, so they stay their own
# copy on purpose. A near-identical set used to live in a retired module
# that shaped the Cámara de Diputados' consolidated PDF into the same
# Markdown conventions; that module was deleted with its source (issues
# #184/#188) and nothing here ever imported it, precisely so the two
# renderings of the same law could be compared rather than share a bug.
_ORDINAL = (
    r"(?:[UÚ]nico|Primero|Segundo|Tercero|Cuarto|Quinto|Sexto|S[ée]ptimo|"
    r"Octavo|Noveno|D[ée]cimo(?:\s+(?:Primero|Segundo|Tercero|Cuarto|Quinto|"
    r"Sexto|S[ée]ptimo|Octavo|Noveno))?)"
)
# The number of an `Artículo` lead, whole. Review fix-1 of #291: the lead used
# to cover digits, an ordinal marker and one of eight Latin suffixes, so
# `Artículo 27-A.-`, `Artículo 1o.-A BIS.-`, `Artículo 228 a.-`, `Artículo
# 12 Quintus` and `Artículo IV.-` were bolded only up to the part it knew
# (`**Artículo 27**-A.-`) and the rest of the number was left in the body,
# where every reader took it for the article's text -- 209 instruments of the
# Atlas ended up with a number repeated for it. The shapes below are the ones
# `md2akn.patterns.NUM_ARTICULO` reads; they are a copy on purpose (`scjn`
# imports nothing else of this monorepo), so a shape added there is added here.
_LATINO_UNIDAD = (
    r"PRIM[UO]S|SEC[UA]ND[UA]S|TERTIUS|QUARTUS|QUINTUS|SEXTUS|S[EÉ]PTIMUS|OCTAVUS|NONUS"
)
_LATINO = (
    r"(?:QUINQUIES|SEXIES|SEPTIES|OCTIES|NONIES)[ \t]*DECIES|"
    r"(?:SEPT|OCTO|NOVO|NOVEN)DECIES|SEXTIES|SEX[IÍ]ES|QUARTER|NOVENUS|"
    r"BIS|TER|QU[AÁ]TER|QUINQUIES|SEXIES|SEPTIES|OCTIES|NONIES|DECIES|"
    r"UNDECIES|DUODECIES|TERDECIES|QUATERDECIES|"
    rf"(?:(?:UNDE|DUODE)?(?:VICESIMUS|TRICESIMUS)|UNDECIMUS|DUODECIMUS|DECIMUS)"
    rf"(?:[ \t]+(?:{_LATINO_UNIDAD}))?|"
    rf"(?:{_LATINO_UNIDAD})(?:[ \t]+DECIMUS)?"
)
_ORDINAL_UNIDAD = (
    r"PRIMER[OA]?|SEGUND[OA]|TERCER[OA]?|CUART[OA]|QUINT[OA]|SEXT[OA]|"
    r"S[EÉ]PTIM[OA]|OCTAV[OA]|NOVEN[OA]|D[EÉ]CIM[OA]|UND[EÉ]CIM[OA]|"
    r"DUOD[EÉ]CIM[OA]|[UÚ]NIC[OA]"
)
_ORDINAL_DECENA = (
    r"D[EÉ]CIM[OA]|VIG[EÉ]SIM[OA]|TRIG[EÉ]SIM[OA]|CUADRAG[EÉ]SIM[OA]|"
    r"QUINCUAG[EÉ]SIM[OA]|SEXAG[EÉ]SIM[OA]|SEPTUAG[EÉ]SIM[OA]|"
    r"OCTOG[EÉ]SIM[OA]|NONAG[EÉ]SIM[OA]|CENT[EÉ]SIM[OA]"
)
_ORDINAL_PALABRA = (
    rf"D[EÉ]CIMOCTAV[OA]|VIG[EÉ]SIMOCTAV[OA]|(?:(?:{_ORDINAL_DECENA})[ \t]*)?(?:{_ORDINAL_UNIDAD})"
)
_COMPONENTE = (
    r"(?:"
    rf"(?:[ \t]*[-–][ \t]*|[ \t]*|\.[ \t]+|(?<=[oº°])\.[ \t]*[-–][ \t]*)"
    rf"(?i:{_LATINO})\b(?:[ \t]+\d+)?"
    r"|[ \t]*[-–][ \t]*(?:\d+|[A-Za-zÁÉÍÓÚÑ]\b)"
    r"|[ \t]+[A-ZÁÉÍÓÚÑ](?=[ \t]*[.\-–])"
    rf"|[ \t]+[a-zñ](?=[.\-–]|[ \t]+(?i:{_LATINO})\b)"
    rf"|[ \t]+[A-ZÁÉÍÓÚÑ](?=[ \t]+(?i:{_LATINO})\b)"
    r"|(?<=\d)[A-ZÁÉÍÓÚÑ](?=[ \t]*[.\-–:]|[ \t]*$)"
    r"|(?<=\d)\.[ \t]*-[A-ZÁÉÍÓÚÑ](?=[.\-–])"
    r"|(?<=[oº°])[ \t]*\.?[ \t]*[-–]?[ \t]*[A-ZÁÉÍÓÚÑ]"
    rf"(?=[.\-–]|[ \t]+(?i:{_LATINO})\b|[ \t]*\((?i:sic)\))"
    r")"
)
_ROMANO = (
    r"(?=[IVXLCDM])"
    r"M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})"
    r"(?=[ \t]*(?:\.|\-|–)|\s|\*|$)"
    rf"(?:[ \t]+(?i:{_LATINO})\b)*"
)
_NUM_ARTICULO = (
    r"(?:"
    r"(?:\d{1,3}(?:,\d{3})+(?!\d)|\d+)(?:[ \t]*/?[oº°])?"
    rf"{_COMPONENTE}*"
    r"(?:[ \t]*,[ \t]*\d+(?:[ \t]*[oº°])?)*"
    rf"|{_ROMANO}"
    rf"|(?i:{_ORDINAL_PALABRA})(?:[ \t]+(?i:{_LATINO})\b)?"
    r")"
)
# A decimal number (`Artículo 1.1:`, chapter 1 article 1 of a treaty such as
# the T-MEC, issue #291; `Artículo 2.A.1:`, its annex) is taken whole *before*
# the plain-digit shape: that one would stop at `1.` and bold only
# `**Artículo 1.**`, leaving `1:` outside the lead and turning every
# `Artículo 1.N` of the treaty into article `1`.
#
# What follows the number reproduces the old pattern's whitespace to the byte,
# on purpose: the lead of a snapshot nothing was wrong with must not change,
# or every instrument would have to be re-downloaded for a space. A plain-digit
# number (`5`, `1o`, `1°`) swallows the blanks after it when no separator
# follows -- `**Artículo 1o. **La presente` -- and an `o.` swallows them too
# (`test_mismo_markdown_que_el_camino_docx` pins the quirk).
_LEAD_ARTICULO = re.compile(
    rf"^((?i:Art[íi]culo)\s+(?:\d+(?:\.(?:\d+|[A-Z](?=\.\d)))+|{_NUM_ARTICULO})"
    r"(?:[ \t]*\.-|[ \t]*-|(?<=\do)\.\s*|[ \t]*\.|(?:(?<=\d)|(?<=\d[oº°]))\s*)?)",
    re.UNICODE,
)
_LEAD_ORDINAL = re.compile(rf"^({_ORDINAL}\.-?)", re.I)
_LEAD_LISTA = re.compile(r"^((?:[IVXLCDM]+|[a-záA-Z])[\.\)])(?=\s)")
_MARGEN = re.compile(r"^Al margen un sello\b.*", re.I)
_TRANSITORIOS_PARRAFO = re.compile(r"^TRANSITORIOS?$", re.I)


def _es_titular(parrafo: str) -> bool:
    """A whole-paragraph ALL-CAPS caption ("DECRETO", "TEXTO ORIGINAL.") is
    bolded in full."""
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
    return parrafo


_PARRAFOS = re.compile(r"\r\n|\n|\r")
# A handful of `contenido` values are not plain text after all: the ENCABEZADO
# of some laws opens with the SCJN's own inline markup
# (`<p style='color:#7D2007;'></p> <br>LEY FEDERAL DEL TRABAJO`) — 6 tags over
# the 5 319 articles surveyed, all in an ENCABEZADO, but they land on the
# snapshot's very first line, so leaving them in makes every such file differ
# from the .docx one. `<br>` and `</p>` end a paragraph the way a newline
# does; the rest of a tag is dropped.
_SALTO_HTML = re.compile(r"<\s*br\s*/?\s*>|</\s*p\s*>", re.I)
_ETIQUETA_HTML = re.compile(r"<[^>]*>")


# --- Table rows inside a tabbed block (issue #253) -----------------------
#
# A blank line ends a table row in the SCJN's plain-text `contenido`, a bare
# newline is a wrapped line inside a cell, and a run of tabs separates
# columns — see this module's own docstring section above for how that was
# found. `_BLOCK_SEPARATOR` recovers the first signal (tolerant of a blank
# line that itself carries stray spaces/tabs, and of `_SALTO_HTML` having
# turned an HTML `<br>`/`</p>` into a bare `\n`); a block with no tab keeps
# converting exactly as before, one paragraph per line — only a tab-bearing
# block is handed to `_paragraphs_from_tabbed_block`.
_BLOCK_SEPARATOR = re.compile(r"\r?\n[ \t]*(?:\r?\n)+")

#: A whole line that is nothing but a parenthesised, uppercase-led
#: annotation (`(REFORMADO, D.O.F. 9 DE DICIEMBRE DE 2019)`) — the same
#: shape `md2akn.patterns.ANOTACION` recognises, checked here only so a
#: reform annotation inside a tabbed block still comes out as its own bold
#: paragraph rather than as a table row.
_BLOCK_ANNOTATION = re.compile(r"^\(([A-ZÁÉÍÓÚÑ][^()]*)\)$")


def _normalize_cell(texto: str) -> str:
    """One tab-separated cell, trimmed, with inner whitespace collapsed to a
    single space and a literal ``|`` escaped so it cannot be read as the
    column separator of the row paragraph it ends up in — the same escaping
    a Markdown table cell needs regardless of what it was extracted from."""
    return re.sub(r"\s+", " ", texto.strip()).replace("|", "\\|")


def _merge_row(previous: list[str], current: list[str]) -> list[str]:
    """`previous` with `current`'s cells folded into it, cell by cell — how
    a header wrapped over several tabbed lines (each continuation line
    starting with an empty first cell) is rebuilt into the single row it
    always was. An empty cell on either side contributes nothing; two
    non-empty ones are space-joined; a cell `current` has and `previous`
    does not is appended rather than dropped."""
    combinada = list(previous)
    for indice, celda in enumerate(current):
        if indice >= len(combinada):
            combinada.append(celda)
        elif celda:
            combinada[indice] = f"{combinada[indice]} {celda}".strip()
    return combinada


def _paragraphs_from_tabbed_block(bloque: str) -> list[str]:
    """The Markdown paragraphs recovered from one blank-line-separated block
    of an article's `contenido` that carries at least one tab — the SCJN's
    only column signal in plain text (issue #253).

    Each line of the block (split on the same `\\r\\n`/`\\n`/`\\r` newlines
    as `_PARRAFOS`, trimmed of a trailing `\\r` only — a leading run of tabs
    has to survive) is one of four things, tested in this order:

    - a whole line that is a parenthesised, uppercase annotation flushes the
      row being built and any pending wrapped label, and is emitted as its
      own paragraph through `_formatea_parrafo` (which bolds it, the same
      treatment a block with no tab already gives one);
    - a line with no tab is a wrapped label: folded, space-joined, into the
      *pending* first cell of the row about to start;
    - a line whose own first cell (up to its first run of tabs) reads as an
      `Artículo N`/ordinal lead (`_LEAD_ARTICULO`/`_LEAD_ORDINAL`) is never a
      row — turning it into one would drop the article from `md2akn`'s own
      tree — so it is emitted as an ordinary paragraph instead, its tab runs
      collapsed to single spaces;
    - every other tabbed line is split on runs of tabs into cells; one whose
      own first cell is empty continues the row already being built, cell by
      cell (`_merge_row`) — this is how a header wrapped over several tabbed
      lines (`Cobertura` / `Cuota por cada kilohertz` / `concesionado o` /
      `permisionado 1MHz=1000 KHz`) comes back as one row — anything else
      starts a new one.

    `quita_notas_editoriales` runs on every line first; one that comes back
    empty (an `[N. DE E. ...]` opener) is dropped outright, same as a block
    with no tab. A finished row renders as `"| cell | cell |"`, blank-line
    separated from the next paragraph like any other, and — unlike every
    other paragraph this module writes — is never passed through
    `_formatea_parrafo`: an all-caps row would otherwise be bolded whole, and
    a cell led by `a).-` would be read as a list marker.
    """
    paragraphs: list[str] = []
    row: list[str] | None = None
    pending_label: str | None = None

    def _flush_row() -> None:
        nonlocal row
        if row is not None:
            paragraphs.append("| " + " | ".join(row) + " |")
            row = None

    def _flush_label() -> None:
        nonlocal pending_label
        if pending_label is not None:
            paragraphs.append(_formatea_parrafo(pending_label))
            pending_label = None

    for cruda in _PARRAFOS.split(bloque):
        linea = cruda.strip("\r")
        if not linea:
            continue

        if _BLOCK_ANNOTATION.match(linea.strip()):
            _flush_row()
            _flush_label()
            anotacion = quita_notas_editoriales(linea.strip())
            if anotacion:
                paragraphs.append(_formatea_parrafo(anotacion))
            continue

        linea = quita_notas_editoriales(linea)
        if not linea:
            continue

        if "\t" not in linea:
            etiqueta = linea.strip()
            pending_label = etiqueta if pending_label is None else f"{pending_label} {etiqueta}"
            continue

        primera_celda = re.split(r"\t+", linea, maxsplit=1)[0].strip()
        if _LEAD_ARTICULO.match(primera_celda) or _LEAD_ORDINAL.match(primera_celda):
            _flush_row()
            _flush_label()
            paragraphs.append(_formatea_parrafo(re.sub(r"\t+", " ", linea)))
            continue

        celdas_crudas = re.split(r"\t+", linea)
        vacia_al_inicio = celdas_crudas[0] == ""
        celdas = [_normalize_cell(c) for c in celdas_crudas]
        if pending_label is not None:
            celdas[0] = f"{pending_label} {celdas[0]}".strip()
            pending_label = None
        if vacia_al_inicio and row is not None:
            row = _merge_row(row, celdas)
        else:
            _flush_row()
            row = celdas

    _flush_row()
    _flush_label()
    return paragraphs


def articulos_a_markdown(articulos: list["Articulo"], *, outline: bool = False) -> str:
    """The reform's consolidated text as the same light Markdown the
    retired `scjn.docx_a_markdown` produced from the .docx: one
    blank-line-separated
    paragraph per source paragraph, editorial asides removed, a heading for
    "Al margen un sello"/"Transitorios", a bolded caption for an ALL-CAPS
    line and a bolded lead for an "Artículo N"/ordinal/list-marker one.

    A blank-line-separated block that carries a tab is the one exception
    (issue #253): the SCJN uses a run of tabs as a table's column separator
    in otherwise plain text, so such a block is handed to
    `_paragraphs_from_tabbed_block` and comes back as one paragraph per
    recovered row instead of one per wrapped line. A block with no tab is
    unaffected.

    With `outline=True` (what `snapshot` passes, review fix-2 of #291) every
    API block that yields text opens with a marker paragraph carrying its
    `orden`, `numero` and `referencia` and the `kind` derived from the label
    -- see `scjn.outline`. No character of the block's own text changes:
    `scjn.outline.strip_markers` of the result is the `outline=False`
    output, which a test pins."""
    bloques: list[str] = []
    for articulo in articulos:
        contenido = _ETIQUETA_HTML.sub("", _SALTO_HTML.sub("\n", articulo.contenido))
        antes = len(bloques)
        for bloque in _BLOCK_SEPARATOR.split(contenido):
            if not bloque.strip():
                continue
            if "\t" in bloque:
                # `bloque` itself is passed through unstripped: a leading
                # run of tabs is real content here — the empty first cell
                # `_paragraphs_from_tabbed_block` reads as "continue the row
                # already being built" (a header wrapped over several
                # tabbed lines) — and stripping it away would silently turn
                # that continuation into a label instead.
                bloques.extend(_paragraphs_from_tabbed_block(bloque))
                continue
            for linea in _PARRAFOS.split(bloque):
                linea = linea.strip()
                if not linea:
                    continue
                linea = quita_notas_editoriales(linea)
                if linea:
                    bloques.append(_formatea_parrafo(linea))
        if outline and len(bloques) > antes:
            bloques.insert(antes, marker_line(articulo.orden, articulo.numero, articulo.referencia))
    return "\n\n".join(bloques) + "\n"

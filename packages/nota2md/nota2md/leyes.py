"""Build a law's current (vigente) text from nothing but the DOF notes
themselves: starts from the law's original publication (parsed into one
entry per article) and replays each reform decree's own "se reforma /
adiciona / deroga el artículo N ... para quedar como sigue" instruction on
top of it, article by article. It never reads Diputados' own consolidated
text (see nota2md.texto_vigente, kept separate because it exists only to
check this module against, not to feed it) — the two are meant to be
compared, not share a source.

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
43 laws this was built against ever does.

A restated "Artículo N" rarely repeats the whole article: the DOF only spells
out the fracciones/incisos it actually touches and marks the rest with a
placeholder — a bare "…", a "**I.**"/"**a)**" with nothing else, or a span
like "**I.** a **XVI. …**" — to show where the change belongs without
retyping everything around it. `_fusiona_articulo()` fills every one of those
placeholders back in from the article's previous text before the restatement
replaces it, matching each by its own fracción/inciso label (incisos are
lettered a), b), c)... independently under every fracción, so a label is
looked up from wherever the merge last left off, not from the top of the
article) or, for an unlabelled paragraph, by position.
"""

import re
import shutil
import tempfile
import unicodedata
from pathlib import Path

from nota2md.builder import build_nota_markdown, fetch_nota

# Where a note's Markdown lands once build_nota_markdown() downloads and
# converts it, when normative_reconstruction() isn't told otherwise — a
# fixed path (not a fresh directory per call) so a note already fetched by
# an earlier call, possibly in an earlier process, is never fetched again.
DIRECTORIO_NOTAS_POR_DEFECTO = Path(tempfile.gettempdir()) / "nota2md-notas"

# --- normative_reconstruction: replay the reforms on top of the original text ---------

# Canonical (capitalized, accented) spelling of each article-number suffix,
# keyed by every lowercase/unaccented way a note might spell it.
_SUFIJOS = {
    "bis": "Bis", "ter": "Ter", "quater": "Quáter", "quáter": "Quáter",
    "quinquies": "Quinquies", "sexies": "Sexies", "septies": "Septies",
    "octies": "Octies", "nonies": "Nonies", "decies": "Decies",
}
# A restated "Artículo N" (optionally suffixed "Bis"/"Ter"/...), bold or not —
# matched against an already `_encabezado()`-stripped block.
_ARTICULO = re.compile(
    r"^\*{0,2}Art[íi]culo\s+(\d+)\s*(?:o\b\.?|[°º])?\s*"
    r"(bis|ter|qu[áa]ter|quinquies|sexies|septies|octies|nonies|decies)?\b",
    re.I,
)
# A heading is a real article boundary; the same phrase mid-sentence
# ("...conforme al artículo 12 de esta Ley...") is not, and html_to_markdown
# sometimes wraps a sentence like that onto its own paragraph, putting it at
# the very start of a block — matching it there would splice that
# paragraph's tail onto the wrong article. A genuine heading always opens
# with a capital "Artículo"/"ARTÍCULO"; a cross-reference embedded in prose
# is grammatically lowercase ("...artículo 12 de esta Ley...") because it
# isn't the start of its own sentence — `_ARTICULO` itself stays
# case-insensitive (headings are written both ways), so the capital has to
# be checked separately.
def _inicio_de_articulo(bloque: str) -> re.Match | None:
    encabezado = _encabezado(bloque)
    if not encabezado[:1].isupper():
        return None
    return _ARTICULO.match(encabezado)


# "Transitorios" is sometimes its own heading ("## Transitorios") and
# sometimes just a bold caption paragraph with no heading markup at all
# ("**TRANSITORIOS**") — html_to_markdown only promotes it to a heading when
# the note's own HTML marks it up as one. Only _TRANSITORIOS_BLOQUE (used once
# a segment has already been chosen, to tell where its own body ends) accepts
# the bare-bold form; _TRANSITORIOS stays heading-only for
# _segmentos_por_instrumento's `limite`, which looks for the *decree's own*
# closing Transitorios before searching for more instruments — an instrument
# that itself expedites a brand new law commonly has its own Transitorios
# for when that law takes effect, often as a bare bold caption too, and
# accepting that there would truncate the search before the decree's later
# instruments, collapsing them into whichever one came first.
_TRANSITORIOS = re.compile(r"^#+\s*Transitorios?\b", re.I)
_TRANSITORIOS_BLOQUE = re.compile(r"^(?:#+\s*Transitorios?\b|Transitorios?\s*$)", re.I)
# "Se reforma(n)/adiciona(n)/deroga(n)", the verb an instruction clause uses
# to say what it does to the article(s) it names.
_VERBO_REFORMA = re.compile(r"\bse\s+(reforman?|adicionan?|derogan?)\b", re.I)
# An article number (optionally suffixed) inside an instruction clause's own
# prose, e.g. one of the numbers listed after "se derogan los artículos".
_NUM_TOKEN = re.compile(
    r"\d+(?:\s+(?:Bis|Ter|Qu[áa]ter|Quinquies|Sexies|Septies|Octies|Nonies|Decies))?",
    re.I,
)


def _canon_numero(base: str, sufijo: str | None) -> str:
    """`base` (an article's bare number) with `sufijo` (as `_ARTICULO`
    captured it, any case/accent) appended in its canonical spelling — the
    dict key every occurrence of this article, suffixed or not, is stored
    and looked up under."""
    if not sufijo:
        return base
    return f"{base} {_SUFIJOS.get(sufijo.lower(), sufijo.capitalize())}"


def _clave_orden(numero: str) -> tuple[int, int]:
    """A sort key for `numero` ("5", "5 Bis", "5 Ter"...) that orders it
    right after its base number and before the next one, in suffix order."""
    m = re.match(r"(\d+)\s*(.*)", numero)
    base, sufijo = int(m.group(1)), m.group(2).strip().lower()
    rango = {"": 0, "bis": 1, "ter": 2, "quáter": 3, "quinquies": 4,
              "sexies": 5, "septies": 6, "octies": 7, "nonies": 8, "decies": 9}
    return base, rango.get(sufijo, 50)


def _inserta_en_orden(orden: list[str], numero: str) -> None:
    """Insert `numero` into `orden` (a law's article numbers, already in
    document order) at the position `_clave_orden` says it belongs —
    used when a reform adds an article that was never in `orden` yet."""
    clave = _clave_orden(numero)
    for i, existente in enumerate(orden):
        if _clave_orden(existente) > clave:
            orden.insert(i, numero)
            return
    orden.append(numero)


def _bloques(markdown: str) -> list[str]:
    """`markdown` split into its paragraph-level blocks — html_to_markdown's
    own unit, one per "\n\n"-separated chunk."""
    return [b for b in markdown.split("\n\n")]


def _encabezado(bloque: str) -> str:
    """`bloque`'s text with Markdown emphasis markers removed, so a heading
    html_to_markdown splits across adjacent bold runs ("**Artículo**
    **Segundo.-**", instead of one "**Artículo Segundo.-**" span) still
    matches as the one phrase it is."""
    return re.sub(r"\*+", "", bloque.strip())


# A note's own H1 — dofjson's title for it, not part of the decree.
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
    while i < fin and not _inicio_de_articulo(bloques[i]) and not _TRANSITORIOS_BLOQUE.match(_encabezado(bloques[i])):
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
    while i < n and not _inicio_de_articulo(segmento[i]):
        i += 1
    preambulo = "\n\n".join(comun + segmento[:i])

    articulos: dict[str, str] = {}
    while i < n and not _TRANSITORIOS_BLOQUE.match(_encabezado(segmento[i])):
        m = _inicio_de_articulo(segmento[i])
        if not m:
            i += 1
            continue
        numero = _canon_numero(m.group(1), m.group(2))
        cuerpo = [segmento[i]]
        i += 1
        while i < n and not _inicio_de_articulo(segmento[i]) and not _TRANSITORIOS_BLOQUE.match(_encabezado(segmento[i])):
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
    while i < n and not _inicio_de_articulo(segmento[i]) and not _TRANSITORIOS_BLOQUE.match(_encabezado(segmento[i])):
        i += 1
    instruccion = "\n\n".join(segmento[:i])

    nuevos = []
    while i < n and not _TRANSITORIOS_BLOQUE.match(_encabezado(segmento[i])):
        m = _inicio_de_articulo(segmento[i])
        if not m:
            i += 1
            continue
        numero = _canon_numero(m.group(1), m.group(2))
        cuerpo = [segmento[i]]
        i += 1
        while i < n and not _inicio_de_articulo(segmento[i]) and not _TRANSITORIOS_BLOQUE.match(_encabezado(segmento[i])):
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


# A fracción-number suffix ("Bis", "Ter"...), same spelling as an article's own.
_SUFIJO_ETIQUETA = (
    r"(?:\s+(?:Bis|Ter|Qu[áa]ter|Quinquies|Sexies|Septies|Octies|Nonies|Decies))?"
)
# A fracción numeral ("I", "XIII Bis"), roman with an optional suffix.
_NUMERAL_ETIQUETA = rf"[IVXLCDM]+{_SUFIJO_ETIQUETA}"
# A single lowercase inciso letter ("a", "b"...).
_LETRA_ETIQUETA = r"[a-záéíóúñ]"

# A single fracción label ("I.", "XIII Bis.-") and whatever follows it.
_ETIQUETA_NUM = re.compile(rf"^({_NUMERAL_ETIQUETA})\.-?\s*(.*)$", re.S)
# A single inciso label ("a)") and whatever follows it.
_ETIQUETA_LETRA = re.compile(rf"^({_LETRA_ETIQUETA})\)\s*(.*)$", re.S)
# A fracción span ("I. a XVI.", "X. y XI.") and whatever follows it.
_RANGO_NUM = re.compile(
    rf"^({_NUMERAL_ETIQUETA})\.-?\s+(?:a|y)\s+({_NUMERAL_ETIQUETA})\.-?\s*(.*)$", re.S
)
# An inciso span ("a) a e)", "a) y b)") and whatever follows it.
_RANGO_LETRA = re.compile(
    rf"^({_LETRA_ETIQUETA})\)\s+(?:a|y)\s+({_LETRA_ETIQUETA})\)\s*(.*)$", re.S
)


def _es_elipsis(texto: str) -> bool:
    """`texto` is nothing but an ellipsis ("...", "….", "…") — a placeholder
    marking a fracción/inciso/párrafo the reform leaves untouched, not real
    text to keep."""
    return bool(re.fullmatch(r"\.{3,}", texto.strip().replace("…", "...")))


def _analiza_bloque(bloque: str) -> tuple[str | None, str | None, str]:
    """(tipo, etiqueta, resto) for `bloque`: tipo is "num" for a fracción
    numeral ("I.", "XIII Bis."), "letra" for a lettered inciso ("a)"), or
    None for a plain paragraph with no marker at all. `resto` is the text
    after the marker — the whole block, for a plain paragraph — and is what
    `_es_elipsis` is checked against to tell a placeholder from real text.
    """
    encabezado = _encabezado(bloque)
    m = _ETIQUETA_NUM.match(encabezado)
    if m:
        return "num", m.group(1), m.group(2)
    m = _ETIQUETA_LETRA.match(encabezado)
    if m:
        return "letra", m.group(1), m.group(2)
    return None, None, encabezado


def _analiza_rango(bloque: str) -> tuple[str, str, str] | None:
    """(tipo, etiqueta_inicio, etiqueta_fin) if `bloque` is a whole span of
    fracciones/incisos left untouched ("I. a XVI. …", "X. y XI. …") — None
    otherwise, including when it names a span but goes on to say something
    other than "…" about it."""
    encabezado = _encabezado(bloque)
    for tipo, patron in (("num", _RANGO_NUM), ("letra", _RANGO_LETRA)):
        m = patron.match(encabezado)
        if m and _es_elipsis(m.group(3)):
            return tipo, m.group(1), m.group(2)
    return None


def _normaliza_etiqueta(etiqueta: str) -> str:
    """`etiqueta` ("I", "XIII  Bis") folded to a case/whitespace-insensitive
    form, so `_busca_etiqueta` can match it regardless of how either side
    happened to capitalize or space a suffix."""
    return re.sub(r"\s+", " ", etiqueta.strip().lower())


def _busca_etiqueta(bloques: list[str], tipo: str, etiqueta: str, desde: int) -> int | None:
    """Index in `bloques` of the block labelled `etiqueta` of `tipo`
    ("num"/"letra"), searching from `desde` onward first and only falling
    back to what precedes it if that finds nothing — incisos are lettered
    a), b), c)... independently under every fracción, so resuming from
    where the merge left off is what tells one fracción's "a)" from
    another's instead of always finding the first."""
    objetivo = _normaliza_etiqueta(etiqueta)
    for indices in (range(desde, len(bloques)), range(0, desde)):
        for i in indices:
            t, e, _ = _analiza_bloque(bloques[i])
            if t == tipo and e is not None and _normaliza_etiqueta(e) == objetivo:
                return i
    return None


def _fusiona_cabecera(vieja: str, nueva: str) -> str:
    """The article's opening block ("Artículo N.- <lo que sea>"): `nueva`
    itself if it says anything past the "Artículo N" lead, `vieja` if all it
    says is "…" (the lead paragraph is left untouched)."""
    m = _ARTICULO.match(nueva)
    resto = _encabezado(nueva[m.end() :]) if m else _encabezado(nueva)
    # Only the punctuation right after "Artículo N" ("." or ".-"), never more
    # than that — a greedy strip would eat straight into a real "…" instead
    # of stopping at it.
    resto = re.sub(r"^\s*[.\-:]{1,2}\s*", "", resto)
    return vieja if _es_elipsis(resto) else nueva


def _fusiona_articulo(anterior: str, nuevo: str) -> str:
    """`nuevo`'s own restated text for an article, with every fracción,
    inciso or párrafo it marks as untouched — a bare "…", a
    "**I.**"/"**a)**" with nothing else, or a span like "**I.** a **XVI.
    …**" — filled back in from `anterior`'s own text for it, matched by
    label (or, for an unlabelled paragraph, by position). Whatever `nuevo`
    does spell out — real text, "Se deroga.", or a fracción reprinted under
    a new number because the ones after it were "recorridas" — is kept
    exactly as given: only a placeholder ever needs `anterior` at all.
    """
    viejos = _bloques(anterior)
    bloques_nuevos = _bloques(nuevo)
    if not viejos or not bloques_nuevos:
        return nuevo

    resultado = [_fusiona_cabecera(viejos[0], bloques_nuevos[0])]
    cursor = 1
    for bloque in bloques_nuevos[1:]:
        rango = _analiza_rango(bloque)
        if rango:
            tipo, inicio, fin = rango
            idx_inicio = _busca_etiqueta(viejos, tipo, inicio, cursor)
            idx_fin = (
                _busca_etiqueta(viejos, tipo, fin, idx_inicio)
                if idx_inicio is not None
                else None
            )
            if idx_inicio is not None and idx_fin is not None:
                resultado.extend(viejos[idx_inicio : idx_fin + 1])
                cursor = idx_fin + 1
                continue
            resultado.append(bloque)
            continue

        tipo, etiqueta, resto = _analiza_bloque(bloque)
        if _es_elipsis(resto):
            idx = (
                _busca_etiqueta(viejos, tipo, etiqueta, cursor)
                if etiqueta is not None
                else (cursor if cursor < len(viejos) else None)
            )
            if idx is not None:
                resultado.append(viejos[idx])
                cursor = idx + 1
                continue

        resultado.append(bloque)
        if etiqueta is not None:
            idx = _busca_etiqueta(viejos, tipo, etiqueta, cursor)
            if idx is not None:
                cursor = idx + 1

    return "\n\n".join(resultado)


def _markdown_de_nota(cod_nota: int, directorio_notas: Path) -> str:
    """`cod_nota`'s note as Markdown, via build_nota_markdown() — written to
    `directorio_notas/nota-{cod_nota}.md` and, once it is there, read back
    from that file instead of downloaded again."""
    md_path = directorio_notas / f"nota-{cod_nota}.md"
    if md_path.exists():
        return md_path.read_text(encoding="utf-8")

    nota = fetch_nota(cod_nota)
    if not nota.get("cadenaContenido"):
        raise ValueError(
            f"la nota {cod_nota} no tiene cadenaContenido (HTML); normative_reconstruction "
            "sólo soporta notas con texto digital"
        )
    md_path = build_nota_markdown(cod_nota, directorio_notas, source="html", nota=nota)
    return md_path.read_text(encoding="utf-8")


class LeyNoReconstruible(ValueError):
    """normative_reconstruction() cannot build this law from the notes it was given.

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
    """A human-readable guess at why `markdown` (the original publication's
    own text) yielded no article at all — the LeyNoReconstruible message
    tells which of the two known causes it looks like."""
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


def normative_reconstruction(
    cod_notas: list[int],
    outdir: str | Path,
    nombre_ley: str | None = None,
    *,
    directorio_notas: str | Path | None = None,
    borrar_directorio_notas: bool = False,
) -> Path:
    """Build the law's current text from the DOF notes in `cod_notas` and
    write it to ``outdir/ley-{cod_notas[0]}.md``; return that path — the
    same shape as build_nota_markdown(), since a law's reconstruction is, in
    the end, one more piece of Markdown built from notes and written to disk.

    `cod_notas` is a law's reform history as `nota2md.utils` returns it:
    oldest first, index 0 the original publication and the rest its reform
    decrees in order. Each decree is replayed on top of the previous state —
    a restated "Artículo N" merges into that article (see
    `_fusiona_articulo`) or inserts it if it is new, a "se deroga el artículo
    N" with no restated text marks it repealed — never touching the
    preamble or the original Transitorios section.

    `nombre_ley` (as `nota2md.utils` names it, e.g. "LEY de Amnistía")
    scopes every note to the one instrument among the several a single decree
    may touch — pass it whenever a note is shared with another law's history.
    Left as None, a note is assumed to concern only this law, which holds for
    most of them but silently mixes in another law's articles for the rest.

    Every note is fetched through build_nota_markdown() into
    `directorio_notas` (DIRECTORIO_NOTAS_POR_DEFECTO if not given) — a note
    already downloaded there by an earlier call is read back from disk
    instead of fetched again. That directory is left in place once this
    returns, so a later call reusing the same `cod_notas` (a rerun of this
    same suite, say) does not need the network at all; pass
    `borrar_directorio_notas=True` to delete it instead once this call is
    done with it. `outdir` (the law's own output, as opposed to the notes it
    was built from) is never deleted — that is the caller's own to keep.

    Raises LeyNoReconstruible if the original publication yields no article
    at all to build on — see that exception for why this can happen.
    """
    if not cod_notas or cod_notas[0] is None:
        raise ValueError("cod_notas necesita al menos la publicación original")

    directorio_notas = Path(directorio_notas or DIRECTORIO_NOTAS_POR_DEFECTO)
    directorio_notas.mkdir(parents=True, exist_ok=True)
    try:
        texto = _normative_reconstruction(cod_notas, nombre_ley, directorio_notas)
    finally:
        if borrar_directorio_notas:
            shutil.rmtree(directorio_notas, ignore_errors=True)

    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    dest = outdir / f"ley-{cod_notas[0]}.md"
    dest.write_text(texto + "\n", encoding="utf-8")
    return dest


def _normative_reconstruction(
    cod_notas: list[int], nombre_ley: str | None, directorio_notas: Path
) -> str:
    """`normative_reconstruction`'s own body, once `directorio_notas` is
    resolved and created — split out only so the public function can wrap
    it in the try/finally that handles `borrar_directorio_notas`."""
    md_original = _markdown_de_nota(cod_notas[0], directorio_notas)
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
        markdown = _markdown_de_nota(cod_nota, directorio_notas)
        instruccion, nuevos = _extrae_reforma(markdown, nombre_ley)
        for numero, texto in nuevos:
            if numero in articulos:
                articulos[numero] = _fusiona_articulo(articulos[numero], texto)
            else:
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

# Markdown syntax (headings, emphasis, code spans, table pipes) to strip
# before comparing two texts, so formatting differences don't count as content
# differences — a table cell's leading "|" is left alone, only a separator
# "|" with another one somewhere later on the same line counts as syntax.
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

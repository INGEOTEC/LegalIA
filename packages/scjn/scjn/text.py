"""Text normalization and SCJN editorial-note removal — the parts of the
crawl's own text handling that have nothing to do with slugs, crawl state,
the provenance header, or the release format.
"""

import re
import unicodedata
from difflib import SequenceMatcher


def _normaliza(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", texto).strip().lower()


# --- Fase 3 (issue #115): guard against a wrong-document match ---------
#
# Candidate selection narrowing by Ámbito/Vigencia (issue #105's Fase 0 finding
# 5) resolves *most* of "searching by name alone can return something
# unrelated", but not all of it — issue #115's manual audit of the already-
# crawled corpus found 5 leyes/reglamentos where the SCJN's search returned,
# and the crawler saved, a document with nothing to do with the catalogue
# entry it was searching for. No single similarity threshold catches all 5
# (a title that merely contains the searched name as a substring — e.g. a
# reglamento of the searched-for ley — scores as high as a genuine match), so
# this is three separate guards, each aimed at one shape of the problem:

_ACUERDO_INTERNO = re.compile(
    r"PLENO DE LA (SUPREMA CORTE|SCJN)|ACUERDO GENERAL N[ÚU]MERO\s+\d+/\d{4}", re.I
)
_GRUPO_LEY = re.compile(r"^(ley|c[oó]digo)\b", re.I)
_GRUPO_REGLAMENTO = re.compile(r"^reglamento\b", re.I)
_NOMBRE_ANTERIOR = re.compile(r"\s*-\s*ANTES\b.*$", re.I)

# Below UMBRAL_MINIMO the best candidate left is rejected outright (`ccf`'s
# 0.436: a title that shares only stray words with what was searched).
# Between the two, a candidate is kept but flagged `sospechoso` (`lfd`'s
# 0.676: "LEY Federal de Derechos" vs "LEY FEDERAL DE LOS DERECHOS DEL
# CONTRIBUYENTE" — a real but *different* law, not resolvable by text alone
# without risking false rejections on legitimate near-duplicate titles).
UMBRAL_MINIMO_SIMILITUD = 0.55
UMBRAL_CONFIANZA_SIMILITUD = 0.75


def ratio_similitud(titulo: str, nombre: str) -> float:
    """How closely a candidate's own `titulo` matches the catalogue's
    `nombre` for it, accent/case/whitespace-insensitive — the same
    `SequenceMatcher` ratio `scjn_api.elige_ordenamiento` picks its winner
    by, and that `scjn_api.cabecera` recomputes to decide whether
    `nombre_buscado` is worth
    writing (issue #132). Exposed too so `scripts/empaqueta_scjn_leyes.py`
    can classify an already-crawled snapshot's confidence offline, against
    whatever `ordenamiento` a past crawl already saved to its own header,
    without needing to re-crawl anything.

    A renamed ordenamiento's SCJN title also carries its own former name, as
    a trailing ``-ANTES <título anterior>-`` (confirmed live re-crawling
    `ccf`: "CODIGO CIVIL FEDERAL -ANTES CODIGO CIVIL PARA EL DISTRITO
    FEDERAL...-" scores 0.270 against the catalogue's "Código Civil
    Federal" with the suffix counted in, below even the worst of the 5
    confirmed wrong-document cases — `UMBRAL_MINIMO_SIMILITUD` would reject
    the *correct* document). Stripped before comparing, so a rename never
    counts against the title that is actually current."""
    titulo = _NOMBRE_ANTERIOR.sub("", titulo)
    return SequenceMatcher(None, _normaliza(titulo), _normaliza(nombre)).ratio()


def es_acuerdo_interno(titulo: str) -> bool:
    """Whether `titulo` is one of the SCJN's own internal administrative
    agreements (a Pleno "ACUERDO GENERAL") rather than an ordenamiento of
    the catalogue's own three collections — `lisr`/`lsint`'s failure mode:
    the search returned no actual law as a candidate, only an unrelated
    SCJN acuerdo that happened to mention the searched name in its own long
    title, and nothing in Ámbito/Vigencia/similarity tells those apart from
    a genuine (if oddly worded) match."""
    return bool(_ACUERDO_INTERNO.search(titulo))


def grupo_instrumento(texto: str) -> str | None:
    """"ley" or "reglamento" when `texto` unambiguously starts with one of
    those (a LEY/CÓDIGO is never the REGLAMENTO of itself, or vice versa),
    None when it starts with neither (a tratado's name, mostly) — used to
    reject `lopgjdf`'s failure mode: a reglamento's title can score high on
    pure text similarity against the ley it regulates, since it literally
    contains that ley's own name."""
    if _GRUPO_LEY.match(texto):
        return "ley"
    if _GRUPO_REGLAMENTO.match(texto):
        return "reglamento"
    return None


# --- Editorial commentary removal ("N. DE E." / "NOTA N") --------------
#
# The SCJN's own Markdown mixes two things a reform-annotated paragraph can
# carry: a reform annotation with a real DOF counterpart ("(REFORMADO,
# D.O.F. <date>)" — kept, see reconstruct_legal_provisions and issue #52),
# and the SCJN's own editorial aside, which it marks "N. DE E." (Nota de
# Editor) or, for a sibling convention citing external DOF fee-update
# agreements, "NOTA N" — neither ever published by the DOF itself. Issue
# #114's sweep of the 3,548 snapshots already crawled for `leyes` found this
# in 91% of them (~85k marker occurrences) and catalogued how it is placed:
#
#   - Three ways the marker itself is spelled, all SCJN's own typos of
#     "N. DE E.": missing a period, doubling one, or splitting one across a
#     space ("N DE E", "N. DE. E", "N. DE . E"). The sibling "NOTA N" is
#     only ever this marker when spelled in full caps — a lowercase/mixed
#     "Nota N" is `ligie`'s tariff schedule citing its own explanatory notes
#     ("Nota 2 del Capítulo 22"), real legal text no DOF/SCJN divide applies
#     to, never an SCJN insertion.
#   - Three ways the note is placed relative to real text: (a) an entire
#     `[...]`/`(...)` paragraph of its own; (b) embedded inside a reform
#     annotation's own parenthesis, which resumes with ", D.O.F. <date>)"
#     right after it; (c) trailing bare after a reform annotation has
#     already closed, running to the end of that paragraph (SCJN's own
#     "N. DE E." is not always bracketed at all).
#   - One no-marker case (Fase 0 finding 3): an unmarked, all-caps bracket
#     ("[REPUBLICADAS]", "[ANTES ARTÍCULO 57]"). The one thing that rules out
#     treating "any bracket" as editorial is that real legal text also uses
#     them — tariff formulas and chemical nomenclature — but every instance
#     of those in the corpus is either letter-free or mixed-case, never a
#     bare run of upper-case words, so requiring both traits (all-caps *and*
#     at least one 3+ letter word) tells the two apart without a formula-
#     specific pattern to maintain.

_MARCADOR_N_DE_E = re.compile(r"N\.?\s*DE\.?\s*\.?\s*E\.?\b", re.I)
# Case-sensitive on purpose — see the section docstring's `ligie` case.
_MARCADOR_NOTA = re.compile(r"NOTA\s+\d+\b")
_CORCHETE = re.compile(r"\[([^\[\]]*)\]")
_PALABRA_LARGA = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]{3,}")
_ANOTACION_REANUDA = re.compile(r",\s*D\.O\.F\.", re.I)


def _empieza_con_marcador(texto: str) -> bool:
    despojado = texto.lstrip()
    return bool(_MARCADOR_N_DE_E.match(despojado) or _MARCADOR_NOTA.match(despojado))


def _es_nota_editorial(contenido: str) -> bool:
    """Whether one `[...]` bracket's content is SCJN editorial commentary —
    its own marker, or (no marker) an all-caps run with an actual word in
    it, never a tariff/chemical bracket (see the section docstring)."""
    if _empieza_con_marcador(contenido):
        return True
    if not _PALABRA_LARGA.search(contenido):
        return False
    letras = [c for c in contenido if c.isalpha()]
    return all(c.isupper() for c in letras)


def _marcadores_sueltos(texto: str):
    """Every bare (not inside a `[...]`) occurrence of the marker in
    `texto`, oldest-first — a `[...]` bracket's own content is handled by
    `_es_nota_editorial` instead, so it is excluded here."""
    corchetes = [(m.start(), m.end()) for m in _CORCHETE.finditer(texto)]

    def en_corchete(pos: int) -> bool:
        return any(inicio <= pos < fin for inicio, fin in corchetes)

    candidatos = [
        m
        for patron in (_MARCADOR_N_DE_E, _MARCADOR_NOTA)
        for m in patron.finditer(texto)
        if not en_corchete(m.start())
    ]
    return sorted(candidatos, key=lambda m: m.start())


def _quita_marcador_suelto(texto: str) -> str:
    """`texto` with its first bare marker (see `_marcadores_sueltos`)
    removed, if any.

    A bare marker always runs to the end of its own paragraph — SCJN never
    gives it a closing delimiter of its own to bound it, *unless* it sits
    inside a reform annotation's still-open parenthesis (a positive paren
    balance right before it) whose own opening was real annotation text
    ("(REFORMADO N. DE E. ..., D.O.F. ...)"): there, the annotation resumes
    right after the note with its own ", D.O.F. <date>)" field, which is
    kept. When that open parenthesis instead belongs to the note itself
    (nothing but whitespace between it and the marker, e.g. "(N. DE E.,
    ..." or "(NOTA 1: ..."), the note still runs to the paragraph's end —
    only a real annotation verb before the marker bounds it early.
    """
    candidatos = _marcadores_sueltos(texto)
    if not candidatos:
        return texto
    m = candidatos[0]
    antes = texto[: m.start()]
    balance = antes.count("(") - antes.count(")")
    if balance > 0:
        apertura = antes.rfind("(")
        if antes[apertura + 1 :].strip():
            resto = texto[m.start() :]
            reanuda = _ANOTACION_REANUDA.search(resto)
            if reanuda is not None:
                inicio = m.start()
                if antes.rstrip().endswith(("(", "[")):
                    inicio = len(antes.rstrip()) - 1
                return texto[:inicio].rstrip() + resto[reanuda.start() :]
        else:
            return antes[:apertura].rstrip()
    return antes.rstrip()


def _quita_notas_editoriales(nucleo: str) -> str:
    if len(nucleo) >= 2 and nucleo[0] in "([" and nucleo[-1] in ")]":
        if _empieza_con_marcador(nucleo[1:-1].lstrip()):
            return ""

    piezas = []
    cursor = 0
    cambios = False
    for m in _CORCHETE.finditer(nucleo):
        if not _es_nota_editorial(m.group(1)):
            continue
        cambios = True
        antes = nucleo[cursor : m.start()].rstrip(" ")
        if antes.endswith(":") and nucleo[m.end() : m.end() + 1] == ".":
            antes = antes[:-1]  # the note's own closing "." now dangles after ":"
        piezas.append(antes)
        cursor = m.end()
    piezas.append(nucleo[cursor:])
    resultado = "".join(piezas) if cambios else nucleo

    sin_suelto = _quita_marcador_suelto(resultado)
    if sin_suelto != resultado:
        cambios = True
        resultado = sin_suelto

    return resultado.strip() if cambios else nucleo


def quita_notas_editoriales(parrafo: str) -> str:
    """`parrafo` with every SCJN editorial insertion removed (see the
    section docstring above) — a paragraph that turns out to be *only* one
    such insertion comes back empty, rather than as a blank paragraph.

    Takes the already-bolded output of `scjn_api._formatea_parrafo` just as
    readily as a raw source paragraph — a whole-paragraph editorial insertion
    in an already-written snapshot is wrapped in its own "**...**"
    (`scjn_api._es_titular` bolds every all-caps paragraph, editorial or not),
    stripped and restored around the result so a second pass over already-clean output is a no-op,
    byte for byte. That property is what let issue #114's Paso 5 repair, in
    place, the snapshots an earlier crawl had written before this existed;
    that one-time script (`scripts/repara_notas_editoriales_scjn.py`) was
    retired in issue #129 once it ran to a no-op over the whole corpus, but
    the idempotence it relied on is still worth keeping.
    """
    negrita = parrafo.startswith("**") and parrafo.endswith("**") and len(parrafo) > 4
    nucleo = parrafo[2:-2] if negrita else parrafo
    resultado = _quita_notas_editoriales(nucleo)
    if resultado == nucleo:
        return parrafo
    if not resultado:
        return ""
    return f"**{resultado}**" if negrita else resultado

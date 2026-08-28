"""Crawl the SCJN Buscador (https://legislacion.scjn.gob.mx/Buscador/) for an
instrument's own reform-dated snapshots.

Each reform row on an instrument's detail page carries a "Ver texto completo
de la última publicación" .docx that is not its *current* text but the
consolidated text exactly as it read right after that particular reform —
see issue #105 for how this was found and validated live against 22 real
instruments (leyes, reglamentos, tratados, and state legislation).

A whole instrument's history needs one `requests.Session` walked through
search -> detail -> each row's download in turn: the detail page's `q`
query-string token is scoped to the session that requested it, so a fresh
session can never reuse a URL a previous one already resolved (see
`nueva_sesion`) — that scoping is also why this crawls collection by
collection, instrument by instrument, rather than trying to precompute or
cache any of these URLs across a run.

The SCJN is not an official source of legal text — dof.gob.mx/SIDOF remains
that (the SCJN's own site marks its editorial insertions as "N. DE E." —
Nota de Editor). Every Markdown file this writes is therefore tagged with a
`fuente: scjn` header, so it is never mistaken for text reconstructed from
the DOF's own notes — see nota2md.leyes.reconstruct_legal_provisions, the
DOF-only equivalent this crawl is meant to eventually stand in for once
matched by date to a codNota (issue #105's Fase 2, not yet built).
"""

import io
import json
import re
import tarfile
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from nota2md.leyes import normaliza_para_comparar

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; LegalIA-scjn-crawler/1.0)"}
BASE_URL = "https://legislacion.scjn.gob.mx/Buscador/"
_LABEL_TEXTO_COMPLETO = "Ver texto completo de la última publicación"

_VIGENCIA = re.compile(r"Vigencia:\s*(\S+)")
_AMBITO = re.compile(r"Ambito:\s*(.+?)(?:\s+Ver\b|$)")
_TITULO_CANDIDATO = re.compile(r"^(.*?)\s*[UÚ]ltima actualizaci[oó]n:")
_FECHA_PUBLICACION = re.compile(r"Fecha de publicaci[oó]n:\s*(\d{2}/\d{2}/\d{4})")
_FECHA_EXPEDICION = re.compile(r"Fecha de expedici[oó]n:\s*(\d{2}/\d{2}/\d{4})")
_CATEGORIA = re.compile(r"Categor[ií]a:\s*(.+?)\s+No\.\s+y\s+secci[oó]n")
_TOTAL_PAGINAS = re.compile(r"gina\s+\d+\s+de\s+(\d+)", re.I)
_PAGER_TARGET = "ctl00$MainContentPlaceHolder$pagerGridReformas"


def nueva_sesion() -> requests.Session:
    """A fresh, unauthenticated session — always start one of these for a
    new instrument's own search->detail->download walk; never reuse a
    session (or a URL it obtained) across instruments or across runs, since
    the SCJN scopes a detail page's `q` token to the session that requested
    it."""
    sesion = requests.Session()
    sesion.headers.update(_HEADERS)
    return sesion


def _campos_formulario(form) -> dict:
    """Every hidden/visible field of `form` (including ASP.NET's own
    `__VIEWSTATE`/`__EVENTVALIDATION`), name -> current value — the payload a
    POST to this WebForms site must resubmit unchanged apart from whichever
    field the caller means to actually drive (`txtPalabra`, `__EVENTTARGET`,
    ...)."""
    data = {}
    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        tipo = inp.get("type", "text")
        if tipo in ("checkbox", "radio"):
            if inp.get("checked"):
                data[name] = inp.get("value", "on")
            continue
        data[name] = inp.get("value", "")
    for sel in form.find_all("select"):
        name = sel.get("name")
        if not name:
            continue
        opt = sel.find("option", selected=True) or sel.find("option")
        data[name] = opt.get("value", "") if opt else ""
    return data


@dataclass
class Candidato:
    """One ordenamiento the SCJN's search returned: its own title as
    highlighted in the results list, the (session-scoped) URL of its detail
    page, and the Ámbito/Vigencia the results page already shows before
    opening that page at all.

    `ratio`/`sospechoso` are filled in by `elige_candidato` once it has
    picked a winner (see issue #115) — a candidate straight out of `buscar`
    carries neither, since there is no `nombre` to compare it against yet."""

    titulo: str
    url: str
    ambito: str | None
    vigencia: str | None
    ratio: float | None = None
    sospechoso: bool | None = None


def _candidato(a, url: str) -> Candidato:
    texto = a.get_text(" ", strip=True)
    m_titulo = _TITULO_CANDIDATO.search(texto)
    m_vigencia = _VIGENCIA.search(texto)
    m_ambito = _AMBITO.search(texto)
    return Candidato(
        titulo=(m_titulo.group(1) if m_titulo else texto).strip(),
        url=url,
        ambito=m_ambito.group(1).strip() if m_ambito else None,
        vigencia=m_vigencia.group(1).strip() if m_vigencia else None,
    )


def buscar(sesion: requests.Session, nombre: str) -> tuple[list[Candidato], str]:
    """Every ordenamiento the SCJN's search returns for `nombre`, and the
    results page's own URL (the `Referer` a detail-page request needs).

    The results themselves come back in a paginated grid (`pagerGridLeyes`,
    10 rows/page by default) that this never walks page by page — instead,
    the search itself asks for the grid's own largest page size (50, its
    `ddlPageSize` dropdown's highest option), so every result still arrives
    in this one request. Confirmed live: "LEY del Impuesto sobre la Renta"
    (issue #124) has 42 total hits, all 10 of page 1 pure ACUERDOs that only
    mention the law in passing (`es_acuerdo_interno` excludes every one) —
    the actual ordenamiento sits at position 14, invisible to a caller that
    only ever reads page 1. 50 covers every case seen so far; an instrument
    with more than 50 hits would still only be found by paginating the grid
    for real, not attempted here."""
    r = sesion.get(BASE_URL, timeout=20)
    soup = BeautifulSoup(r.text, "html.parser")
    form = soup.find("form", id="aspnetForm")
    action_url = urljoin(r.url, form.get("action"))
    data = _campos_formulario(form)
    data["ctl00$MainContentPlaceHolder$ucBusqueda1$txtPalabra"] = nombre
    data["ctl00$MainContentPlaceHolder$ucBusqueda1$cbxTitulo"] = "on"
    data["ctl00$MainContentPlaceHolder$ucBusqueda1$ddlPageSize"] = "50"
    data["__EVENTTARGET"] = "ctl00$MainContentPlaceHolder$ucBusqueda1$btnBuscar"
    r2 = sesion.post(action_url, data=data, headers={"Referer": r.url}, timeout=30)
    soup2 = BeautifulSoup(r2.text, "html.parser")
    candidatos = [
        _candidato(a, urljoin(r2.url, a.get("href")))
        for a in soup2.find_all("a")
        if a.get("href") and "wfOrdenamiento" in a.get("href")
    ]
    return candidatos, r2.url


def _normaliza(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", texto).strip().lower()


# --- Fase 3 (issue #115): guard against a wrong-document match ---------
#
# `elige_candidato` narrowing by Ámbito/Vigencia (issue #105's Fase 0 finding
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
    `SequenceMatcher` ratio `elige_candidato` picks its winner by, and that
    `_cabecera` recomputes to decide whether `nombre_buscado` is worth
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


def elige_candidato(candidatos: list[Candidato], nombre: str) -> Candidato | None:
    """The candidate that best matches `nombre` among the SCJN's own search
    results for it — see issue #105's Fase 0 finding 5: searching by name
    alone can return an abrogated instrument alongside its still-current
    successor, or something unrelated that merely mentions `nombre`, so
    narrowing by Ámbito/Vigencia before comparing titles resolves most of
    that ambiguity. `download_legal_provisions_provenance_ids` only ever
    covers federal instruments, so FEDERAL is preferred whenever at least one
    candidate has it; VIGENTE is preferred the same way, but neither
    preference ever discards the only candidate(s) on offer — an abrogated
    law is still worth crawling its own reform history.

    Before any of that, two hard exclusions (issue #115, see the section
    docstring above) drop candidates that are never a legitimate match
    regardless of Ámbito/Vigencia/similarity: the SCJN's own internal
    acuerdos (`es_acuerdo_interno`), and — only when `nombre` itself
    unambiguously names a ley/código or a reglamento — a candidate of the
    opposite kind (`grupo_instrumento`). Unlike the Ámbito/Vigencia
    preference, these two never fall back to "keep everyone" when they
    would empty the list: a document of the wrong kind is worse than no
    document at all.

    The winner returned then also needs to clear `UMBRAL_MINIMO_SIMILITUD`
    on `ratio_similitud`, or this returns None the same as "no candidates"
    — and comes back flagged `sospechoso` when it clears that floor but not
    `UMBRAL_CONFIANZA_SIMILITUD`, for a caller to route to manual review
    instead of trusting outright.

    Returns None (rather than raising) when `candidatos` is empty (or every
    candidate got excluded by the two hard exclusions above), so a batch
    crawl can log a miss and move on to the next instrument."""
    excluidos = [c for c in candidatos if not es_acuerdo_interno(c.titulo)]
    grupo_objetivo = grupo_instrumento(nombre)
    if grupo_objetivo is not None:
        excluidos = [
            c for c in excluidos if grupo_instrumento(c.titulo) in (None, grupo_objetivo)
        ]
    if not excluidos:
        return None

    federales = [c for c in excluidos if c.ambito == "FEDERAL"] or excluidos
    vigentes = [c for c in federales if c.vigencia == "VIGENTE"] or federales
    elegido = max(vigentes, key=lambda c: ratio_similitud(c.titulo, nombre))

    ratio = ratio_similitud(elegido.titulo, nombre)
    if ratio < UMBRAL_MINIMO_SIMILITUD:
        return None
    return replace(elegido, ratio=ratio, sospechoso=ratio < UMBRAL_CONFIANZA_SIMILITUD)


@dataclass
class FilaReforma:
    """One row of an instrument's own reform table: the publication/
    expedition dates and category the SCJN prints for it, and the URL of its
    "texto completo" .docx — the reform-dated snapshot Fase 0 validated."""

    fecha_publicacion: str
    fecha_expedicion: str | None
    categoria: str | None
    url_docx: str


def _filas_de_pagina(html: str, base_url: str) -> list["FilaReforma"]:
    """Every reform row on one already-fetched page of the grid."""
    soup = BeautifulSoup(html, "html.parser")
    filas = []
    for a in soup.find_all("a"):
        if a.get_text(strip=True) != _LABEL_TEXTO_COMPLETO:
            continue
        tr = a.find_parent("tr")
        texto_fila = tr.get_text(" ", strip=True) if tr is not None else ""
        m_pub = _FECHA_PUBLICACION.search(texto_fila)
        if not m_pub:
            continue
        m_exp = _FECHA_EXPEDICION.search(texto_fila)
        m_cat = _CATEGORIA.search(texto_fila)
        filas.append(
            FilaReforma(
                fecha_publicacion=m_pub.group(1).replace("/", "-"),
                fecha_expedicion=m_exp.group(1).replace("/", "-") if m_exp else None,
                categoria=m_cat.group(1).strip() if m_cat else None,
                url_docx=urljoin(base_url, a.get("href")),
            )
        )
    return filas


def filas_de_reforma(
    sesion: requests.Session,
    detail_url: str,
    referer: str,
    *,
    espera: float = 1.0,
    on_pagina: Callable[[int, int], None] | None = None,
) -> tuple[list[FilaReforma], str]:
    """Every reform row of the instrument at `detail_url`, most recent first
    (the SCJN's own order — see `descarga_ordenamiento`, which reverses this
    before returning) — and the detail page's own URL (the `Referer` each
    row's docx download needs).

    The grid only ever renders 10 rows per page (a DevExpress ASPxGridView);
    an instrument with more reforms than that — confirmed live against the
    CPEUM, 301 rows across 31 pages — needs its remaining pages walked via
    the same plain ASP.NET postback its own pager link uses
    (`__EVENTTARGET=pagerGridReformas`, `__EVENTARGUMENT=PN<n>`), resubmitting
    the *detail* page's own `aspnetForm` fields (not the search page's) each
    time, since every postback returns a fresh `__VIEWSTATE` the next page
    request must carry forward.

    Issue #140: walking a multi-page grid like the CPEUM's is ~31 real
    requests with nothing else to show for it until the very last one comes
    back — indistinguishable from a hung process. `on_pagina`, when given,
    is called with (`pagina_actual`, `total_paginas`) after each page is
    fetched (1-based, so the first call is always `(1, total_paginas)`) —
    only when there is more than one page to begin with, since a single-page
    instrument (the common case) has nothing worth narrating."""
    r = sesion.get(detail_url, headers={"Referer": referer}, timeout=20)
    soup = BeautifulSoup(r.text, "html.parser")
    filas = _filas_de_pagina(r.text, r.url)
    m_total = _TOTAL_PAGINAS.search(r.text)
    total_paginas = int(m_total.group(1)) if m_total else 1
    if on_pagina is not None and total_paginas > 1:
        on_pagina(1, total_paginas)
    for n in range(1, total_paginas):
        time.sleep(espera)
        form = soup.find("form", id="aspnetForm")
        campos = _campos_formulario(form)
        campos["__EVENTTARGET"] = _PAGER_TARGET
        campos["__EVENTARGUMENT"] = f"PN{n}"
        r = sesion.post(detail_url, data=campos, headers={"Referer": detail_url}, timeout=30)
        soup = BeautifulSoup(r.text, "html.parser")
        filas.extend(_filas_de_pagina(r.text, r.url))
        if on_pagina is not None:
            on_pagina(n + 1, total_paginas)
    return filas, r.url


def descarga_docx(
    sesion: requests.Session,
    url: str,
    referer: str,
    *,
    intentos: int = 3,
    espera: float = 2.0,
) -> bytes:
    """`url`'s raw .docx bytes, retrying on a dropped connection — the SCJN's
    server occasionally closes a request outright rather than erroring, the
    same flakiness issue #105's Fase 0 spike already had to work around."""
    r = None
    for intento in range(intentos):
        try:
            r = sesion.get(url, headers={"Referer": referer}, timeout=30)
            break
        except requests.exceptions.ConnectionError:
            if intento == intentos - 1:
                raise
            time.sleep(espera)
    if r.content[:2] != b"PK":
        raise ValueError(
            f"la respuesta de {url} no es un .docx "
            f"(content-type={r.headers.get('content-type')})"
        )
    return r.content


# --- .docx -> Markdown -------------------------------------------------
#
# Every docx sampled in Fase 0 (a ley, a reglamento — see issue #105) carries
# no run-level formatting at all: "TEXTO ORIGINAL.", "Artículo N.-" leads and
# "TRANSITORIOS" captions are plain text, told apart only by their own
# wording/casing — the same situation nota2md.texto_vigente's Diputados PDFs
# are in, except a docx paragraph already is one clean block (no per-page
# header/footer to strip first, no line-wrapped text to reflow), so only the
# per-paragraph classification below is needed. Kept independent of
# texto_vigente's own patterns rather than imported, for the same reason that
# module gives for staying independent of this package's DOF-derived output:
# the two are meant to be compared, not to share a source.

_ORDINAL = (
    r"(?:[UÚ]nico|Primero|Segundo|Tercero|Cuarto|Quinto|Sexto|S[ée]ptimo|"
    r"Octavo|Noveno|D[ée]cimo(?:\s+(?:Primero|Segundo|Tercero|Cuarto|Quinto|"
    r"Sexto|S[ée]ptimo|Octavo|Noveno))?)"
)
_LEAD_ARTICULO = re.compile(
    rf"^(Art[íi]culo\s+(?:\d+\s*(?:o\b\.?|[°º])?\s*"
    r"(?:Bis|Ter|Qu[áa]ter|Quinquies|Sexies|Septies|Octies|Nonies|Decies)?|"
    rf"{_ORDINAL})\.?-?)",
    re.I,
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
#     already closed, running to the end of that docx paragraph (SCJN's own
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

    Takes the already-bolded output of `_formatea_parrafo` just as readily
    as a raw docx paragraph: `scripts/repara_notas_editoriales_scjn.py`
    (issue #114's Paso 5) reprocesses paragraphs of files a previous crawl
    already wrote, where a whole-paragraph editorial insertion is already
    wrapped in its own "**...**" (`_es_titular` bolds every all-caps
    paragraph, editorial or not) — stripped and restored around the result
    so a second pass over already-clean output is a no-op, byte for byte.
    """
    negrita = parrafo.startswith("**") and parrafo.endswith("**") and len(parrafo) > 4
    nucleo = parrafo[2:-2] if negrita else parrafo
    resultado = _quita_notas_editoriales(nucleo)
    if resultado == nucleo:
        return parrafo
    if not resultado:
        return ""
    return f"**{resultado}**" if negrita else resultado


def docx_a_markdown(contenido: bytes) -> str:
    """A reform row's .docx, reformatted into the same light Markdown
    nota2md's other sources use: a heading for "Al margen un sello"/
    "Transitorios", a bolded whole-paragraph caption for an ALL-CAPS line, a
    bolded lead for an "Artículo N"/ordinal/list-marker paragraph — see the
    section docstring above for why this doesn't share code with
    nota2md.texto_vigente's own (very similar-looking) PDF conversion.

    Every paragraph is also stripped of the SCJN's own editorial asides
    before formatting (`quita_notas_editoriales`, see issue #114) — the
    result is meant to read as if it had been reconstructed from the DOF's
    own notes, which never carried them.

    python-docx is only needed here — the extra that pulls it in
    (``pip install nota2md[scjn]``) is optional, same as dof2md is for the
    OCR paths in nota2md.builder.

    Issue #125 evaluated replacing this with a public docx→Markdown package
    (pandoc via pypandoc's ``gfm`` writer, and mammoth) against real SCJN
    reform docx (e.g. "LEY de Firma Electrónica Avanzada", "LEY de la Casa
    de Moneda de México"): both failed on the two things this module is
    actually for. Neither strips the SCJN's own "N. DE E."/"NOTA N"
    insertions — both pass them straight through (mammoth even markdown-
    escapes the periods in them: ``N\\. DE E\\.``), which issue #114 found in
    91% of snapshots. And neither reconstructs any paragraph structure at
    all — no heading, no bold lead — because the source docx itself carries
    none (see the module docstring), so both would need this exact per-
    paragraph classification layered back on top of their own output anyway.
    Pulling raw paragraph text out of a docx (``python-docx``, which this
    already uses) was never the hard part of this module; both packages
    would only ever replace that one trivial step, never the editorial-
    note-stripping or classification that is the actual work — so there is
    nothing to gain by switching. Kept as is.
    """
    import docx

    documento = docx.Document(io.BytesIO(contenido))
    parrafos = [p.text.strip() for p in documento.paragraphs]
    parrafos = [quita_notas_editoriales(p) for p in parrafos if p]
    bloques = [_formatea_parrafo(p) for p in parrafos if p]
    return "\n\n".join(bloques) + "\n"


def slug_instrumento(entrada: dict) -> str:
    """A filesystem-safe directory name for one catalogue entry (as
    `download_legal_provisions_provenance_ids` returns it): its `abrev` when
    the collection gives one (leyes, reglamentos), otherwise a slug of its
    `nombre` (tratados have no `abrev`)."""
    base = entrada.get("abrev") or entrada.get("nombre") or entrada.get("codigo") or ""
    slug = re.sub(r"[^a-z0-9]+", "-", _normaliza(base)).strip("-")
    return slug or "instrumento"


# --- issue #124 (follow-up): closing the catalogue's own coverage gaps ----
#
# Two catalogue entries the coverage sweep above never finds anything for,
# each for a different reason. `lisipl`: Diputados' own `nombre` for it
# carries a 250+ character trailing parenthetical alternate name that the
# SCJN's full-text search never matches, even though the SCJN's own title
# for it scores 0.774 on `ratio_similitud` against that `nombre` — above
# UMBRAL_CONFIANZA_SIMILITUD — once it is actually found by a different
# search string. `lfca`: a brand-new law (DOF 2026-05-24) the SCJN has not
# indexed at all yet — confirmed live, searching its own name (or even just
# "Cine y el Audiovisual") returns nothing; not a title mismatch.
#
# search_name/merge_catalog_overrides are Mecanismo 1: an optional
# `nombre_scjn` override a human can add to one catalogue entry (applied to
# `lisipl`, not `lfca` — its gap is indexing lag, not a different title),
# which `extract_scjn_titles.py` now preserves across its own refreshes
# instead of overwriting the whole file blindly, and which
# `fetch_scjn_legislacion.py` searches with in place of `nombre`.
#
# instrument_up_to_date/iso_date_from_note are Mecanismo 2: an `actualizado`
# field (the ISO date of an instrument's own most recent reform, read from
# Diputados' `historial` via dofjson) that lets a refresh run skip
# re-searching the SCJN for an instrument nothing has changed on since the
# collection's own last full crawl. This is the safety net for a case like
# `lfca`: nothing needs to be typed by hand — a brand-new law's `actualizado`
# is newer than any previous full crawl, so it keeps getting retried on
# every refresh, automatically, until the SCJN catches up.


def search_name(entry: dict) -> str:
    """The string to actually search the SCJN with for one catalogue entry:
    its manual `nombre_scjn` override when present, Diputados' own `nombre`
    otherwise. `nombre` itself is never touched by this — it stays what
    `enlaza_por_titulo`/`title_candidates_por_fecha` compare against DOF
    titles (issue #126), and what a caller shows in its own progress
    output; only the string handed to `buscar` changes."""
    return entry.get("nombre_scjn") or entry["nombre"]


def catalog_key(entry: dict) -> str:
    """The key two catalogue entries from separate runs are considered the
    same instrument by: `abrev` when the collection gives one (leyes,
    reglamentos), `nombre` otherwise (tratados) — the same precedence
    `slug_instrumento` already uses to pick a directory name."""
    return entry.get("abrev") or entry["nombre"]


def merge_catalog_overrides(catalog: list[dict], previous_catalog: list[dict] | None) -> list[dict]:
    """`catalog` (a fresh projection of Diputados' own instruments) with
    each entry's own `nombre_scjn` carried over from whichever entry of
    `previous_catalog` it corresponds to (`catalog_key`), when that
    previous entry had one.

    `extract_scjn_titles.py` used to overwrite `catalogo.json` from scratch
    on every run — there was nowhere to keep a manual override, and even a
    hand-edited one would vanish on the next refresh. This is what makes it
    survive instead: a fresh run only ever adds or updates what Diputados
    itself gives (`nombre`, `abrev`, `actualizado`), and only ever keeps —
    never invents — `nombre_scjn`.

    `previous_catalog` being empty or None (first run, no `catalogo.json`
    yet) returns `catalog` unchanged."""
    if not previous_catalog:
        return catalog
    previous_by_key = {catalog_key(entry): entry for entry in previous_catalog}
    merged = []
    for entry in catalog:
        previous = previous_by_key.get(catalog_key(entry))
        if previous and previous.get("nombre_scjn"):
            entry = {**entry, "nombre_scjn": previous["nombre_scjn"]}
        merged.append(entry)
    return merged


def iso_date_from_note(note: dict) -> str | None:
    """`note`'s own `fecha` (dofjson's `DD-MM-YYYY`, the shape
    `nota2md.builder.fetch_nota` returns it in) as ISO `YYYY-MM-DD`, or None
    when `note` carries no `fecha` at all."""
    fecha = note.get("fecha")
    if not fecha:
        return None
    return datetime.strptime(fecha, "%d-%m-%Y").date().isoformat()


def instrument_up_to_date(directory: Path, updated: str | None, corpus_date: str | None) -> bool:
    """Whether the instrument crawled into `directory` can be skipped on a
    refresh run without touching the SCJN at all: it already has at least
    one snapshot on disk, and its own `updated` (`catalogo.json`'s
    `actualizado`, an ISO date) is no later than `corpus_date` (the ISO
    date this collection was last crawled start-to-finish) — plain string
    comparison, since both are ISO `YYYY-MM-DD`.

    An instrument with no snapshot on disk yet is never skipped, regardless
    of `updated`/`corpus_date`: "nothing downloaded" is never mistaken for
    "already up to date" — the safety net for an instrument the SCJN has
    not indexed at all yet (see the section docstring above, `lfca`): every
    refresh keeps retrying it until the SCJN catches up, with nothing to
    configure by hand."""
    if corpus_date is None or not updated:
        return False
    if not any(directory.glob("*.md")):
        return False
    return updated <= corpus_date


def _cabecera(candidato: Candidato, fila: FilaReforma, nombre_buscado: str) -> str:
    """The provenance header every file this writes carries, so it is never
    mistaken for Markdown built from the DOF's own notes (see the module
    docstring). `ratio_similitud`/`sospechoso` (issue #115) record how
    confident `elige_candidato` was that `candidato` is genuinely the
    instrument that was searched for.

    `nombre_buscado` is the exact `nombre` `descarga_ordenamiento` was called
    with — the SCJN's own search has no fixed per-document URL (its `?q=`
    token is scoped to the session that generated it, see the module
    docstring), so this and `ordenamiento` together are the only way to
    reproduce by hand, later, how this particular file was reached (issue
    #124). Written only when `ratio_similitud(candidato.titulo,
    nombre_buscado) < 1.0` — i.e. when the title `elige_candidato` picked is
    not, after normalizing (accents/case/whitespace), identical to what was
    searched for; when it is, `ordenamiento` alone already says everything
    `nombre_buscado` would add. That makes the mere presence of
    `nombre_buscado` in a snapshot the signal that its title is worth a
    second look — `grep -l nombre_buscado: <coleccion>/**/*.md` finds it
    directly, without needing `catalogo.json` or a separate offline pass
    (issue #132, retiring `scripts/audita_scjn_legislacion.py`)."""
    lineas = [
        "---",
        "fuente: scjn",
    ]
    if ratio_similitud(candidato.titulo, nombre_buscado) < 1.0:
        lineas.append(f"nombre_buscado: {nombre_buscado}")
    lineas += [
        f"ordenamiento: {candidato.titulo}",
        f"fecha_publicacion: {fila.fecha_publicacion}",
    ]
    if fila.fecha_expedicion:
        lineas.append(f"fecha_expedicion: {fila.fecha_expedicion}")
    if fila.categoria:
        lineas.append(f"categoria: {fila.categoria}")
    if candidato.ratio is not None:
        lineas.append(f"ratio_similitud: {candidato.ratio:.3f}")
        lineas.append(f"sospechoso: {'true' if candidato.sospechoso else 'false'}")
    lineas.append("---")
    return "\n".join(lineas)


def descarga_ordenamiento(
    sesion: requests.Session,
    nombre: str,
    outdir: Path,
    *,
    espera: float = 1.0,
    on_progreso: Callable[[str], None] | None = None,
) -> list[Path]:
    """Every reform-dated snapshot the SCJN has for `nombre`, written as
    ``outdir/<fecha_publicacion>.md`` — `outdir` is already the instrument's
    own directory (e.g. ``<coleccion>/<abrev-o-nombre>/``; picking that split
    is left to the caller, the same way it is left to
    ``download_legal_provisions_provenance_ids``'s own per-collection
    helpers). A file already there is left untouched and its row's download
    skipped entirely — what makes a crawl over hundreds of instruments
    resumable after a partial run, instead of starting over.

    Two rows can share the same `fecha_publicacion` — confirmed live on the
    CPEUM, whose 301 reforms include 39 dates published more than once (up
    to 4 decrees on the same day) — so the 2nd+ row for a date gets
    `<fecha_publicacion>-2.md`, `-3.md`, ... appended, in the SCJN's own
    (most-recent-first) row order, instead of silently overwriting the
    first row's file.

    Returns the paths written (or already present), oldest first — an empty
    list, without raising, when the search finds nothing or every candidate
    looks unrelated (see `elige_candidato`): a batch crawl over a whole
    collection is expected to log that miss and keep going, not stop at the
    first one.

    Issue #140: a caller crawling a whole collection only ever sees one line
    per instrumento, so a large one (again, the CPEUM) walking its own
    multi-page reform grid or downloading many rows in a row goes silent for
    as long as that takes. `on_progreso`, when given, is called with a
    ready-to-print message: forwarded from `filas_de_reforma`'s own
    `on_pagina` while walking the grid, then once per row (only when there
    is more than one) while downloading — a caller owns how/where that
    message is shown (`fetch_scjn_legislacion.py` prints it to stderr).
    """
    candidatos, referer_busqueda = buscar(sesion, nombre)
    candidato = elige_candidato(candidatos, nombre)
    if candidato is None:
        return []

    on_pagina = (
        (lambda actual, total: on_progreso(f"grid de reformas: pagina {actual}/{total}"))
        if on_progreso is not None
        else None
    )
    filas, referer_detalle = filas_de_reforma(
        sesion, candidato.url, referer_busqueda, espera=espera, on_pagina=on_pagina
    )
    outdir.mkdir(parents=True, exist_ok=True)

    escritos = []
    repeticiones: dict[str, int] = {}
    total_filas = len(filas)
    for indice, fila in enumerate(filas, 1):
        if on_progreso is not None and total_filas > 1:
            on_progreso(f"fila {indice}/{total_filas}")
        repeticiones[fila.fecha_publicacion] = repeticiones.get(fila.fecha_publicacion, 0) + 1
        orden = repeticiones[fila.fecha_publicacion]
        sufijo = f"-{orden}" if orden > 1 else ""
        destino = outdir / f"{fila.fecha_publicacion}{sufijo}.md"
        if destino.exists():
            escritos.append(destino)
            continue
        contenido = descarga_docx(sesion, fila.url_docx, referer_detalle)
        time.sleep(espera)
        markdown = docx_a_markdown(contenido)
        cabecera = _cabecera(candidato, fila, nombre)
        destino.write_text(f"{cabecera}\n\n{markdown}", encoding="utf-8")
        escritos.append(destino)
    return list(reversed(escritos))


# --- issue #123/#126: match each snapshot to the codNota that published it,
# by title mention alone --------------------------------------------------
#
# `descarga_ordenamiento` only knows the SCJN's own view of an instrument: a
# publication date per snapshot, nothing that ties back to a DOF `codNota`.
# Issue #105's original design paired that date against Diputados'
# `historial` (`download_legal_provisions_provenance_ids`'s own list of
# codNota for the instrument) — but issue #123's corrected goal is for the
# SCJN, plus the DOF's own title dataset, to be the *only* source once an
# instrument's `nombre` has picked which one to crawl: `historial` is never
# consulted here, not even as a tie-breaker or a fallback. The only thing
# borrowed from Diputados is `nombre` itself, used for two things: searching
# the SCJN (`buscar`) and, here, testing which same-day DOF note's own title
# actually names the instrument. Two SCJN-dated snapshots can share a
# `fecha_publicacion` (issue #105's Fase 0 found up to 4 on the CPEUM alone),
# so this also has to resolve same-day exclusivity: once an earlier snapshot
# of that date has claimed a candidate, a later one sharing the date never
# claims it again.


def _fecha(cadena: str) -> datetime:
    return datetime.strptime(cadena, "%d-%m-%Y")


_CABECERA_CAMPO = re.compile(r"^([a-z_]+):\s*(.*)$")


def lee_cabecera(archivo: Path) -> dict:
    """The provenance header `_cabecera` writes at the top of `archivo`, back
    as a dict (`fuente`, `nombre_buscado`, `ordenamiento`,
    `fecha_publicacion`, and whichever of `fecha_expedicion`/`categoria` that
    snapshot's row had) — reading back a file a previous crawl run already
    wrote, without re-fetching it. `nombre_buscado` is absent on a file a
    crawl wrote before issue #124 added it, same as `ratio_similitud`/
    `sospechoso` for issue #115 — and, since issue #132, also absent on a
    file whose `ordenamiento` was already identical to what was searched
    for, which is not a missing field but `_cabecera` declining to write a
    redundant one."""
    texto = archivo.read_text(encoding="utf-8")
    lineas = texto.split("\n")
    campos = {}
    for linea in lineas[1:]:
        if linea.strip() == "---":
            break
        m = _CABECERA_CAMPO.match(linea)
        if m:
            campos[m.group(1)] = m.group(2)
    return campos


@dataclass
class VersionInstrumento:
    """One SCJN snapshot already on disk: its own publication date and the
    file `descarga_ordenamiento` wrote it to."""

    fecha_publicacion: str
    archivo: Path


def _orden_repeticion(version: "VersionInstrumento") -> int:
    """The `-N` suffix `descarga_ordenamiento` appends to the 2nd+ file of a
    repeated `fecha_publicacion` (see its own docstring), as a sort key: 1
    for a plain `<fecha>.md` (no suffix), N for `<fecha>-N.md`. `fecha` is
    known already (the header, not the filename, is the source of truth),
    so only the part of the stem after it is a repetition suffix — the
    date's own dashes never get mistaken for one."""
    resto = version.archivo.stem[len(version.fecha_publicacion) :]
    return int(resto[1:]) if resto else 1


def versiones_de_directorio(outdir: Path) -> list[VersionInstrumento]:
    """Every snapshot `descarga_ordenamiento` has already written to
    `outdir`, oldest first — read back from each file's own header rather
    than re-crawling, so a later Fase 2 pass can run over a crawl's output
    independently of the crawl itself."""
    versiones = [
        VersionInstrumento(lee_cabecera(archivo)["fecha_publicacion"], archivo)
        for archivo in outdir.glob("*.md")
    ]
    return sorted(versiones, key=lambda v: (_fecha(v.fecha_publicacion), _orden_repeticion(v)))


_TITLE_MEANINGFUL_WORD = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]{4,}")


def _title_mentions_name(nombre: str, titulo: str) -> bool:
    """Whether `titulo` (some other same-day DOF note's own title) explicitly
    names the instrument `nombre` refers to: every one of `nombre`'s own
    meaningful words (4+ letters, so "LEY"/"DEL"/"DE" never count on their
    own) must appear in `titulo`, case/accent-insensitive. Mirrors the same
    "does this title name that instrument" question
    `leyesmx.dof.similitud_nombre` answers for regulations with no decree
    title of their own — kept as nota2md's own small copy rather than an
    import, since nota2md carries no dependency on leyesmx.

    A `nombre` left with fewer than 2 meaningful words (e.g. "LEY de
    Amparo" — only "Amparo" survives the filter) never counts as mentioned,
    even when that lone word does appear: a single common legal term is too
    weak a signal on its own, and would otherwise turn this into a blanket
    keyword search matching any unrelated same-day note that happens to use
    it in passing. Those instruments simply get no candidate at all from
    `title_candidates_por_fecha` (`status="none"`) rather than an unreliable
    one."""
    palabras = _TITLE_MEANINGFUL_WORD.findall(_normaliza(nombre))
    if len(palabras) < 2:
        return False
    titulo_normalizado = _normaliza(titulo)
    return all(palabra in titulo_normalizado for palabra in palabras)


_DECRETO_O_LEY = re.compile(r"^(?:decreto|ley)\b", re.I)


def _title_opens_with_decreto_or_ley(titulo: str) -> bool:
    """Whether `titulo`'s own first word is "DECRETO" or "LEY", case-
    insensitive — the fallback `title_candidates_por_fecha` reaches for
    when no same-day title names the instrument at all (`_title_mentions_name`
    finds nothing). A reform decree does not always spell out every law it
    amends in its own title: `ccf`'s 14-11-2025, "DECRETO por el que se
    reforman diversas disposiciones de diversos ordenamientos legales, en
    materia de homologación normativa relativa al Código Nacional de
    Procedimientos Civiles y Familiares", reforms the Código Civil Federal in
    its own artículo primero without "Código Civil Federal" ever appearing in
    the title. But every DOF publication that actually reforms or enacts
    something still opens with one of these two words, unlike an "ACUERDO"/
    "AVISO"/"RESOLUCIÓN", which never is one — so a same-day note that opens
    with neither is not worth considering even as a weak fallback."""
    return bool(_DECRETO_O_LEY.match(titulo.strip()))


def title_candidates_por_fecha(fechas, nombre: str, porf: dict) -> dict[str, list[int]]:
    """Every same-day DOF codNota whose own title explicitly names `nombre`
    (`_title_mentions_name`), grouped by `fecha_publicacion` — the primary
    source of candidate codNota this module uses for a reform's own link
    (issue #123's corrected design: Diputados' `historial` is never
    consulted, here or anywhere downstream of it). `fechas` only needs to
    cover the dates actually worth checking (typically the ones
    `versiones_de_directorio` returns for this instrument); `porf` groups by
    fecha every dofjson title record worth considering (see
    `leyesmx.dof.notas_por_fecha` / `dofjson.download_legal_provisions_titles`).

    When no same-day title names the instrument at all, this falls back to
    every same-day codNota whose own title opens with "DECRETO" or "LEY"
    (`_title_opens_with_decreto_or_ley`) — a reform's own title does not
    always spell out every law it amends (see that helper's own docstring
    for `ccf`'s 14-11-2025 case). The fallback only ever fills in an
    otherwise-empty pool for a date; it never adds candidates on top of an
    already-found explicit mention.

    A date absent from `porf` entirely comes back with an empty candidate
    list, same as a date where no same-day note mentions the name or opens
    with DECRETO/LEY — both read downstream as "nothing to link this date",
    not an error."""
    resultado = {}
    for fecha in dict.fromkeys(fechas):
        notas_dia = porf.get(fecha, [])
        candidatos = sorted(
            n["codNota"] for n in notas_dia if _title_mentions_name(nombre, n["titulo"])
        )
        if not candidatos:
            candidatos = sorted(
                n["codNota"] for n in notas_dia if _title_opens_with_decreto_or_ley(n["titulo"])
            )
        resultado[fecha] = candidatos
    return resultado


@dataclass
class VersionEnlazada:
    """One SCJN snapshot together with the `codNota` of the DOF note that
    published it, when `title_candidates_por_fecha` left exactly one
    same-day candidate for its date and no earlier same-day snapshot has
    already claimed it (`enlaza_por_titulo`) — `codNota` is None otherwise
    (no candidate, more than one, or already claimed), left for issue #127's
    content diff to resolve when it can."""

    fecha_publicacion: str
    codNota: int | None
    archivo: Path


def enlaza_por_titulo(
    versiones: list[VersionInstrumento], candidatos_por_fecha: dict[str, list[int]]
) -> list[VersionEnlazada]:
    """Pair every SCJN snapshot of one instrument with the codNota of the DOF
    note that published it, using only `candidatos_por_fecha`
    (`title_candidates_por_fecha`'s own output) — Diputados' historial is
    never consulted, here or anywhere in this module.

    A date whose candidate pool (after excluding any codNota already claimed
    by an earlier same-day snapshot) is empty or has more than one entry
    comes back with `codNota=None`: title mention alone cannot pick a winner
    without risking a wrong pick, and a missing link is a fact about the
    source worth surfacing, not an error.

    When two snapshots share a date and its pool has exactly one candidate
    (issue #105's Fase 0 found up to 4 same-day reforms on the CPEUM), only
    the first — `versiones` is already oldest-first — claims it; the second
    sees an empty remaining pool and stays unlinked, the same same-date
    exclusivity `confirm_by_content_diff` (#127) already enforces on its own
    `usados_por_fecha`."""
    usados: set[int] = set()
    enlazadas = []
    for version in versiones:
        candidatos = [
            cod
            for cod in candidatos_por_fecha.get(version.fecha_publicacion, [])
            if cod not in usados
        ]
        cod = candidatos[0] if len(candidatos) == 1 else None
        if cod is not None:
            usados.add(cod)
        enlazadas.append(VersionEnlazada(version.fecha_publicacion, cod, version.archivo))
    return enlazadas


def title_link_status(codNota: int | None, candidatos: list[int]) -> str:
    """How `enlaza_por_titulo` resolved one snapshot's own date, for
    `indice.json`/manual audit:

    - "linked": it has a `codNota`.
    - "none": no same-day candidate names the instrument at all.
    - "claimed": the date's one candidate was already taken by an earlier
      same-day snapshot.
    - "ambiguous": more than one same-day candidate names the instrument,
      with nothing but content diff (#127) able to break the tie.
    """
    if codNota is not None:
        return "linked"
    if not candidatos:
        return "none"
    if len(candidatos) == 1:
        return "claimed"
    return "ambiguous"


# --- issue #127: confirm the reform-codNota link by content diff ----------
#
# #126's title mention is still just text similarity: it says a same-day
# note plausibly concerns this instrument, not that its content is what
# actually changed. The SCJN gives, at each date, the *whole* law's text
# (never a diff) — so the change a reform made between two consecutive
# SCJN-dated snapshots has to be computed here, and then checked against
# each candidate codNota's own DOF text: whichever candidate's own content
# actually accounts for that change is confirmed with certainty, resolving
# the ambiguous, same-day-multiple-candidate cases #126 alone cannot. This
# is only possible for a codNota with digital DOF text (`cadenaContenido`)
# — never a reason to fall back to OCR just for one more confidence signal
# — and the diff itself is never kept: only whether a candidate was
# confirmed this way, alongside the score that decided it.

# A reform that only changes a short number or date (a tasa, monto, plazo
# or fecha — common in Mexican decrees) leaves a diff whose only real
# content is a token shorter than 4 letters; requiring 4+ letters alone
# would make that change invisible to `_overlap_score`, letting two
# candidates that differ only in which number they restate score
# identically. Digits are matched at any length (first alternative) so
# "10" vs "20" still tells two otherwise-similar candidates apart.
_PALABRA_DIFF = re.compile(r"\d+(?:[.,]\d+)?|\w{4,}")


def _cuerpo_de_snapshot(archivo: Path) -> str:
    """`archivo`'s own body, with the provenance header `_cabecera` wrote at
    its top stripped off — the same header/body split
    `scripts/repara_notas_editoriales_scjn.py` already uses, so a diff
    between two snapshots never mistakes a header field (e.g. a different
    `ratio_similitud`) for a change in the law's own text."""
    return archivo.read_text(encoding="utf-8").partition("\n\n")[2]


def _added_blocks(anterior: str, nuevo: str) -> list[str]:
    """`nuevo`'s own paragraph-level blocks (already run through
    `normaliza_para_comparar`) that are not already in `anterior` — a
    block-granularity diff via `SequenceMatcher.get_opcodes`, so a
    paragraph that merely got reflowed (same words, different line breaks)
    is not mistaken for a change. This approximates what a reform between
    two consecutive SCJN-dated snapshots actually changed in the law's full
    text; it is never persisted, only used to score candidates below."""
    bloques_antes = [normaliza_para_comparar(b) for b in anterior.split("\n\n") if b.strip()]
    bloques_despues = [normaliza_para_comparar(b) for b in nuevo.split("\n\n") if b.strip()]
    sm = SequenceMatcher(None, bloques_antes, bloques_despues, autojunk=False)
    agregados = []
    for tag, _, _, j1, j2 in sm.get_opcodes():
        if tag in ("insert", "replace"):
            agregados.extend(bloques_despues[j1:j2])
    return agregados


def _overlap_score(bloques_agregados: list[str], texto_candidato_normalizado: str) -> float:
    """How much of what `_added_blocks` says changed also shows up in
    `texto_candidato_normalizado` (a candidate codNota's own DOF text,
    already run through `normaliza_para_comparar`): the fraction of the
    added blocks' own meaningful words (4+ letters) that also appear in
    it. 1.0 when everything that changed is accounted for by this one
    candidate's own text; 0.0 when nothing changed to compare against, or
    when the candidate's text shares none of it."""
    palabras_agregadas = set(_PALABRA_DIFF.findall(" ".join(bloques_agregados)))
    if not palabras_agregadas:
        return 0.0
    palabras_candidato = set(_PALABRA_DIFF.findall(texto_candidato_normalizado))
    return len(palabras_agregadas & palabras_candidato) / len(palabras_agregadas)


#: Below this fraction of the diff's own words found in a candidate's text,
#: the match is treated as coincidental rather than confirmed — kept
#: deliberately higher than #126's title-mention bar (which only needs to
#: single out one plausible candidate) since this signal is meant to give
#: *certainty*, not just plausibility.
UMBRAL_CONFIRMACION_DIFF = 0.6


@dataclass
class ContentDiffConfirmation:
    """One SCJN-dated snapshot's content-diff confirmation (issue #127).

    `confirmed_codNota` is the candidate whose own DOF text best accounts
    for what changed between this snapshot and the previous one, when its
    score clears `UMBRAL_CONFIRMACION_DIFF`; None otherwise — including
    when this is the instrument's very first (oldest) snapshot, which has
    no previous version to diff against at all.

    `score` is that best candidate's own overlap score even when it did
    NOT clear the threshold (so a near-miss is visible, not indistinguishable
    from "nothing to compare"), and is None only when no candidate for this
    date had any DOF text available to compare in the first place — the
    case issue #127 says must leave the link exactly as #124/#126 already
    settled it, neither blocked nor degraded.
    """

    fecha_publicacion: str
    confirmed_codNota: int | None
    score: float | None


def confirm_by_content_diff(
    versiones: list[VersionInstrumento],
    candidatos_por_fecha: dict[str, list[int]],
    markdown_por_codNota: dict[int, str],
) -> list[ContentDiffConfirmation]:
    """One `ContentDiffConfirmation` per entry of `versiones` (oldest first,
    as `versiones_de_directorio` already returns them) — always the same
    length, so a caller can `zip` this against `versiones`/`enlaza_por_titulo`'s
    own per-version results.

    `candidatos_por_fecha` is every codNota worth checking for a given
    `fecha_publicacion` — typically `title_candidates_por_fecha`'s own
    output (issue #126), the same dict `enlaza_por_titulo` itself takes.
    `markdown_por_codNota` is each of those candidates' own DOF Markdown,
    already fetched by the caller (never fetched here — this module does no
    network I/O) and present only for candidates that actually have digital
    text; a candidate absent from it is simply skipped, not treated as
    disqualifying.

    Two snapshots can share a `fecha_publicacion` (up to 4 on the CPEUM
    alone, per `enlaza_por_titulo`'s own docstring) — a codNota already
    confirmed for an earlier same-day entry is excluded from every later
    one sharing that date, the same one-codNota-per-snapshot exclusivity
    `enlaza_por_titulo` itself already enforces via its own `usados` set.
    Without this, two distinct same-day reforms with genuinely overlapping
    decree text (confirmed live on `ccf`'s 27-12-1983, two companion
    decrees both amending the Código Civil and Código de Procedimientos
    Civiles) can otherwise both score highest against the same one
    candidate, silently claiming it twice and discarding the other
    snapshot's own already-correct title-based link for no reason.
    """
    if not versiones:
        return []
    resultados = [ContentDiffConfirmation(versiones[0].fecha_publicacion, None, None)]
    usados_por_fecha: dict[str, set[int]] = {}
    for anterior, actual in zip(versiones, versiones[1:]):
        agregados = _added_blocks(
            _cuerpo_de_snapshot(anterior.archivo), _cuerpo_de_snapshot(actual.archivo)
        )
        usados = usados_por_fecha.setdefault(actual.fecha_publicacion, set())
        candidatos = candidatos_por_fecha.get(actual.fecha_publicacion, [])
        mejor_cod, mejor_score = None, 0.0
        tiene_texto = False
        for cod in candidatos:
            if cod in usados:
                continue
            texto = markdown_por_codNota.get(cod)
            if texto is None:
                continue
            tiene_texto = True
            score = _overlap_score(agregados, normaliza_para_comparar(texto))
            if score > mejor_score:
                mejor_cod, mejor_score = cod, score
        confirmado = mejor_cod if mejor_score >= UMBRAL_CONFIRMACION_DIFF else None
        if confirmado is not None:
            usados.add(confirmado)
        resultados.append(
            ContentDiffConfirmation(
                actual.fecha_publicacion, confirmado, mejor_score if tiene_texto else None
            )
        )
    return resultados


# --- issue #128: download the packaged corpus (release loader) -----------
#
# `scripts/empaqueta_scjn_leyes.py` packages every already-crawled+linked
# `leyes` instrument (snapshots plus `indice.json`, carrying #115/#126/#127's
# confidence signals) into the `scjn-leyes` release's own `leyes.tgz` — see
# that script for why publishing it is, and stays, a deliberate manual step,
# never automated. This is that release's own reader, same shape as
# `nota2md.utils.download_legal_provisions_provenance_ids` (download the
# tarball straight into memory, nothing touches disk) but kept as its own
# small copy rather than sharing code with it: the two releases have
# different tags, different asset layouts, and no caller in common.

_SCJN_LEYES_RELEASE = "scjn-leyes"
_SCJN_LEYES_RELEASES_API = (
    f"https://api.github.com/repos/INGEOTEC/LegalIA/releases/tags/{_SCJN_LEYES_RELEASE}"
)


def _assets_scjn_leyes(timeout: int = 30) -> dict[str, str]:
    """Every asset of the `scjn-leyes` release, name -> download URL."""
    response = requests.get(_SCJN_LEYES_RELEASES_API, headers=_HEADERS, timeout=timeout)
    response.raise_for_status()
    return {
        asset["name"]: asset["browser_download_url"] for asset in response.json()["assets"]
    }


def download_scjn_leyes_corpus(timeout: int = 60) -> list[dict]:
    """Every `leyes` instrument the SCJN-based corpus (issue #128) has
    packaged, each as ``{"slug": ..., "snapshots": [...]}`` — one entry per
    snapshot, each carrying its own `indice.json` fields (`fecha_publicacion`,
    `codNota`, `ratio_similitud`, `sospechoso`, `title_candidates`,
    `title_link_status`, `content_diff_confirmed_codNota`,
    `content_diff_score`) plus its own Markdown body as `markdown`.

    An instrument crawled but never linked (`scripts/enlaza_scjn_legislacion.py`
    has not run for it yet — Fase 2 pendiente) is still packaged with its raw
    snapshots; each of those comes back with only `archivo`/`codNota=None`/
    `markdown` set, no confidence fields, rather than being dropped.

    Downloads the `leyes.tgz` asset into memory; nothing touches disk.
    Raises `KeyError` while the `scjn-leyes` release does not exist yet —
    expected before a human has read
    `scripts/empaqueta_scjn_leyes.py`'s own manifest and published it by
    hand (this corpus has no automated publish path, on purpose — see that
    script). Not re-exported from `nota2md/__init__.py`: this stays
    `nota2md.scjn`-internal infrastructure until something like issue #117
    integrates it into `legal_provisions`.
    """
    urls = _assets_scjn_leyes(timeout)
    if "leyes.tgz" not in urls:
        raise KeyError(
            "el release 'scjn-leyes' no publica el asset 'leyes.tgz' todavia — "
            "ver issue #128: este corpus solo se publica a mano, tras revision humana"
        )

    response = requests.get(urls["leyes.tgz"], headers=_HEADERS, timeout=timeout)
    response.raise_for_status()

    with tarfile.open(fileobj=io.BytesIO(response.content), mode="r:gz") as tar:
        miembros = {m.name: tar.extractfile(m).read() for m in tar if m.isfile()}

    por_instrumento: dict[str, dict] = {}
    for nombre, contenido in miembros.items():
        slug, _, archivo = nombre.partition("/")
        datos = por_instrumento.setdefault(slug, {"indice": None, "cuerpos": {}})
        if archivo == "indice.json":
            datos["indice"] = json.loads(contenido)
        else:
            datos["cuerpos"][archivo] = contenido.decode("utf-8")

    resultado = []
    for slug, datos in sorted(por_instrumento.items()):
        cuerpos, indice = datos["cuerpos"], datos["indice"]
        if indice is not None:
            snapshots = [
                {**entrada, "markdown": cuerpos.get(entrada["archivo"])} for entrada in indice
            ]
        else:
            snapshots = [
                {"archivo": nombre, "codNota": None, "markdown": texto}
                for nombre, texto in sorted(cuerpos.items())
            ]
        resultado.append({"slug": slug, "snapshots": snapshots})
    return resultado

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
import re
import time
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

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
    opening that page at all."""

    titulo: str
    url: str
    ambito: str | None
    vigencia: str | None


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
    results page's own URL (the `Referer` a detail-page request needs)."""
    r = sesion.get(BASE_URL, timeout=20)
    soup = BeautifulSoup(r.text, "html.parser")
    form = soup.find("form", id="aspnetForm")
    action_url = urljoin(r.url, form.get("action"))
    data = _campos_formulario(form)
    data["ctl00$MainContentPlaceHolder$ucBusqueda1$txtPalabra"] = nombre
    data["ctl00$MainContentPlaceHolder$ucBusqueda1$cbxTitulo"] = "on"
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

    Returns None (rather than raising) when `candidatos` is empty, so a
    batch crawl can log a miss and move on to the next instrument."""
    if not candidatos:
        return None
    federales = [c for c in candidatos if c.ambito == "FEDERAL"] or candidatos
    vigentes = [c for c in federales if c.vigencia == "VIGENTE"] or federales
    objetivo = _normaliza(nombre)
    return max(
        vigentes,
        key=lambda c: SequenceMatcher(None, _normaliza(c.titulo), objetivo).ratio(),
    )


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
    sesion: requests.Session, detail_url: str, referer: str, *, espera: float = 1.0
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
    request must carry forward."""
    r = sesion.get(detail_url, headers={"Referer": referer}, timeout=20)
    soup = BeautifulSoup(r.text, "html.parser")
    filas = _filas_de_pagina(r.text, r.url)
    m_total = _TOTAL_PAGINAS.search(r.text)
    total_paginas = int(m_total.group(1)) if m_total else 1
    for n in range(1, total_paginas):
        time.sleep(espera)
        form = soup.find("form", id="aspnetForm")
        campos = _campos_formulario(form)
        campos["__EVENTTARGET"] = _PAGER_TARGET
        campos["__EVENTARGUMENT"] = f"PN{n}"
        r = sesion.post(detail_url, data=campos, headers={"Referer": detail_url}, timeout=30)
        soup = BeautifulSoup(r.text, "html.parser")
        filas.extend(_filas_de_pagina(r.text, r.url))
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


def docx_a_markdown(contenido: bytes) -> str:
    """A reform row's .docx, reformatted into the same light Markdown
    nota2md's other sources use: a heading for "Al margen un sello"/
    "Transitorios", a bolded whole-paragraph caption for an ALL-CAPS line, a
    bolded lead for an "Artículo N"/ordinal/list-marker paragraph — see the
    section docstring above for why this doesn't share code with
    nota2md.texto_vigente's own (very similar-looking) PDF conversion.

    python-docx is only needed here — the extra that pulls it in
    (``pip install nota2md[scjn]``) is optional, same as dof2md is for the
    OCR paths in nota2md.builder.
    """
    import docx

    documento = docx.Document(io.BytesIO(contenido))
    parrafos = [p.text.strip() for p in documento.paragraphs]
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


def _cabecera(nombre: str, fila: FilaReforma) -> str:
    """The provenance header every file this writes carries, so it is never
    mistaken for Markdown built from the DOF's own notes (see the module
    docstring)."""
    lineas = [
        "---",
        "fuente: scjn",
        f"ordenamiento: {nombre}",
        f"fecha_publicacion: {fila.fecha_publicacion}",
    ]
    if fila.fecha_expedicion:
        lineas.append(f"fecha_expedicion: {fila.fecha_expedicion}")
    if fila.categoria:
        lineas.append(f"categoria: {fila.categoria}")
    lineas.append("---")
    return "\n".join(lineas)


def descarga_ordenamiento(
    sesion: requests.Session, nombre: str, outdir: Path, *, espera: float = 1.0
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
    """
    candidatos, referer_busqueda = buscar(sesion, nombre)
    candidato = elige_candidato(candidatos, nombre)
    if candidato is None:
        return []

    filas, referer_detalle = filas_de_reforma(
        sesion, candidato.url, referer_busqueda, espera=espera
    )
    outdir.mkdir(parents=True, exist_ok=True)

    escritos = []
    repeticiones: dict[str, int] = {}
    for fila in filas:
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
        destino.write_text(f"{_cabecera(candidato.titulo, fila)}\n\n{markdown}", encoding="utf-8")
        escritos.append(destino)
    return list(reversed(escritos))

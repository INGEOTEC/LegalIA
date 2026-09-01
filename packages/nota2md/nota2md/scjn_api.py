"""Client for the SCJN's SCOW JSON API, the backend of
https://legislacion.scjn.gob.mx/consulta/buscador.

This is the transport that replaced `nota2md.scjn`'s WebForms crawler
(issue #172, retired in #179). Where that one round-tripped `__VIEWSTATE`,
held a session-scoped `q` token, scraped a paged HTML grid and downloaded
one `.docx` per reform, this asks three unauthenticated JSON endpoints:

- `BusquedaFrase` — the ordenamientos matching a name,
- `Reforma` — that ordenamiento's whole reform table,
- `Articulos` — the consolidated text as it read right after one reform,
  article by article, already segmented and labelled.

The per-reform snapshot semantics are the same ones issue #105 validated
for the `.docx`, so the corpus this feeds keeps meaning what it meant.

The SCJN is still **not** an official source of legal text — dof.gob.mx /
SIDOF remains that, and every file written from this keeps its
`fuente: scjn` header. A public Swagger page is not a stability contract
either: same posture as `dofjson.dofweb` and `leyesmx.diputados`, so the
rate limiting and the retries stay.

Measured live against the corpus the old crawler wrote (issue #173, whose
numbers are the comment on #172); three of those findings are load-bearing
here and each is marked at the code that depends on it.
"""

import re
import time
from dataclasses import dataclass

import requests

BASE_URL = "https://legislacion.scjn.gob.mx/SCOW-API"

# The `/SCOW-API` paths answer an ordinary browser User-Agent fine, but
# `legislacion.scjn.gob.mx/` itself sits behind Imperva/Incapsula and 403s a
# bare client — so send one. The site's own bundle also ships a hardcoded
# `Authorization: Basic` credential it never actually interpolates (a
# template bug on their side); the endpoints answer without it and it is
# deliberately not copied here (issue #172).
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; LegalIA-scjn-crawler/1.0)",
    "Content-Type": "application/json",
}

ESPERA_DEFAULT = 0.5
REINTENTOS_DEFAULT = 3

# A page size the API honours *exactly*, which is what makes "a short page is
# the last page" a safe stop condition — and that condition is the only safe
# one here, because two things are true of this API at once (issue #178, found
# while auditing why `lfd` had 92 snapshots against the 98 reforms its own
# detail page lists):
#
#   - Asking for a page past the end answers HTTP 500, not an empty page.
#   - `tamanio` can overcount what the endpoint will ever return: `lfd`
#     reforma 99 reports `tamanio` 995 and serves 991 articles, complete,
#     spanning `orden` 1..995 (4 `orden` values simply do not exist).
#
# Together those made a `len(filas) >= tamanio` stop condition fail closed: it
# never came true for such a reform, so a second page was always requested, it
# answered 500, and the whole reform was discarded as unavailable. That is
# what produced ~106 of the 231 "the SCJN cannot serve this" reports of the
# first full crawl — a bug here, not a defect there. Above 500 the endpoint
# also stops honouring the requested size (a ~1.7 MB payload cap), which would
# make a short page ambiguous between "last page" and "truncated"; at 500 it
# does not.
#
# So *both* stop conditions are needed, and neither alone is enough:
#
#   - a page shorter than requested is the last one (covers the overcounting
#     `tamanio`, e.g. `lfd` reforma 99);
#   - having collected `tamanio` rows is also the end, even on a full page
#     (covers a count that is an exact multiple of the page size — `lss`
#     reforma 42 declares exactly 500 and serves exactly 500, and asking for
#     its page 2 answers 500 too).
TAMANIO_PAGINA_ARTICULOS = 500
TAMANIO_PAGINA_REFORMAS = 500

_EM = re.compile(r"</?em>")


class ScjnApiError(RuntimeError):
    """The API did not answer usefully — an HTTP status, a `codigo` other
    than 200 in the body, or a non-JSON answer (a WAF challenge page). Kept
    distinct from an empty result, which is a legitimate answer."""


class ScjnApiWafError(ScjnApiError):
    """Imperva/Incapsula answered instead of the API. Raised on its own so a
    crawl can back off and retry the whole instrument later, rather than
    recording "not indexed" for a law that is perfectly well indexed."""


def _limpia_resaltado(texto: str | None) -> str:
    """`BusquedaFrase` wraps every matched word of a title in `<em>`; the
    title itself is what callers compare against a name."""
    return _EM.sub("", texto or "").strip()


def _fecha(cadena: str | None) -> str | None:
    """`22/05/2026 00:00:00` -> `22-05-2026`, the form `_cabecera` writes and
    a snapshot file is named after."""
    if not cadena:
        return None
    return cadena.split(" ")[0].replace("/", "-")


@dataclass
class Ordenamiento:
    """One search hit: an instrument addressable by a stable
    `idOrdenamiento` instead of a session-scoped URL, plus the signals the
    results list already carries — which is the whole reason candidate
    selection (issue #176) can improve on the old one."""

    idOrdenamiento: str
    ordenamiento: str
    iweight: int | None = None
    vigencia: str | None = None
    ambito: str | None = None
    categoriaOrdenamiento: str | None = None
    materia: str | None = None
    fechaPublicado: str | None = None
    # Filled in by candidate selection, the same way the retired `scjn.Candidato` did.
    ratio: float | None = None
    sospechoso: bool | None = None


@dataclass
class Reforma:
    """One row of an instrument's reform table — every field the retired `scjn._cabecera`
    writes today, plus `seccionPublicacion`, which the WebForms grid never
    showed."""

    reformaId: int | str
    fecha_publicacion: str
    fecha_expedicion: str | None = None
    categoria: str | None = None
    seccionPublicacion: str | None = None
    pdf: str | None = None
    tieneArticulos: bool = True
    tieneProcesos: bool = False


@dataclass
class Articulo:
    """One article of the consolidated text as it read right after a
    reform. `referencia` is its structural label (`ENCABEZADO`,
    `TÍTULO PRIMERO`, `ARTÍCULO 1`, and also the editorial `D.O.F. <fecha>`
    rows); issue #173 found it never empty and `orden` always contiguous
    over ~13 000 articles, but the vocabulary is open, so a writer labels
    with it and never depends on it."""

    numero: int | None
    orden: int | None
    referencia: str
    contenido: str


class ScjnApi:
    """A `requests.Session` against `BASE_URL`, rate-limited and retrying.

    Unlike the WebForms crawler, nothing here is session-scoped: an
    `idOrdenamiento` obtained by one instance is usable by any other, at any
    later time, which is what makes a crawl resumable and auditable."""

    def __init__(
        self,
        *,
        base_url: str = BASE_URL,
        espera: float = ESPERA_DEFAULT,
        reintentos: int = REINTENTOS_DEFAULT,
        timeout: int = 120,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.espera = espera
        self.reintentos = reintentos
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update(HEADERS)

    # -- transport ---------------------------------------------------------

    def _cuerpo(self, respuesta: requests.Response) -> dict:
        try:
            datos = respuesta.json()
        except ValueError:
            raise ScjnApiWafError(
                f"{respuesta.url} answered {len(respuesta.content)} bytes that are not "
                "JSON — most likely an Imperva/Incapsula challenge, not the API"
            )
        # The API answers HTTP 200 with its own `codigo` in the body, so a
        # failure here is an error, never an empty result.
        codigo = datos.get("codigo")
        if codigo is not None and int(codigo) != 200:
            raise ScjnApiError(f"{respuesta.url} -> codigo {codigo}: {datos.get('mensaje')}")
        return datos

    def _pide(self, metodo: str, ruta: str, **kwargs) -> dict:
        url = f"{self.base_url}{ruta}"
        ultimo = None
        for intento in range(self.reintentos + 1):
            respuesta = self.session.request(
                metodo, url, timeout=self.timeout, **kwargs
            )
            if respuesta.status_code == 200:
                datos = self._cuerpo(respuesta)
                time.sleep(self.espera)
                return datos
            ultimo = respuesta.status_code
            if intento < self.reintentos:
                time.sleep(self.espera * (2**intento))
        raise ScjnApiError(f"{url} -> HTTP {ultimo}")

    # -- endpoints ---------------------------------------------------------

    def search_ordenamiento(
        self,
        name: str,
        *,
        tipo_publicacion: int = 1,
        tipo_busqueda: int = 1,
        tamanio_pagina: int = 25,
        ambito: str = "",
        categoria: str = "",
        vigencia: str = "",
        pagina: int = 1,
    ) -> list[Ordenamiento]:
        """Every ordenamiento `BusquedaFrase` returns for `name`.

        Issue #173: the optional filters must be sent, as empty strings —
        a body carrying only `q`/`tipoBusqueda`/`tipoPublicacion` gets an
        HTTP 500, not a validation error.

        `ambito`/`categoria`/`vigencia`/`pagina` are those filters made
        reachable (issue #186), for the one caller that wants a *listing*
        rather than a lookup: `extract_scjn_titles.py --discover` pages
        `FEDERAL`+`LEY`/`CODIGO`/`CONSTITUCION`+`VIGENTE` to find laws the
        catalogue does not have yet. `name` stays mandatory — an empty `q`
        answers zero results, so there is no "list everything" mode to
        expose."""
        cuerpo = {
            "q": name,
            "tipoBusqueda": tipo_busqueda,
            "tipoPublicacion": tipo_publicacion,
            "ambitoF": ambito,
            "categoriaF": categoria,
            "vigenciaF": vigencia,
            "entidadFederativaF": "",
            "materiaF": "",
            "municipioF": "",
            "fechaPublicacionInicio": "",
            "fechaPublicacionFin": "",
            "numeroPagina": pagina,
            "tamanioPagina": tamanio_pagina,
            "consultaArticulos": 0,
        }
        datos = self._pide("POST", "/api/SCOW/BusquedaFrase", json=cuerpo)
        return [
            Ordenamiento(
                idOrdenamiento=str(r.get("idOrdenamiento")),
                ordenamiento=_limpia_resaltado(r.get("ordenamiento")),
                iweight=r.get("iweight"),
                vigencia=r.get("vigencia"),
                ambito=r.get("ambito"),
                categoriaOrdenamiento=r.get("categoriaOrdenamiento"),
                materia=r.get("materia"),
                fechaPublicado=_fecha(r.get("fechaPublicado")),
            )
            for r in (datos.get("resultados") or [])
        ]

    def reformas_of_ordenamiento(self, id_ordenamiento: str | int) -> list[Reforma]:
        """The instrument's whole reform table, newest first — the SCJN's own
        row order, which `descarga_ordenamiento` relies on to number two
        rows sharing a `fecha_publicacion`."""
        filas: list[Reforma] = []
        pagina = 1
        while True:
            datos = self._pide(
                "GET",
                "/api/SCOW/Reforma",
                params={
                    "idOrdenamiento": id_ordenamiento,
                    "numeroPagina": pagina,
                    "tamanioPagina": TAMANIO_PAGINA_REFORMAS,
                },
            )
            lote = datos.get("resultados") or []
            filas += [
                Reforma(
                    reformaId=r.get("reformaId"),
                    fecha_publicacion=_fecha(r.get("fechaPublicacion")) or "",
                    fecha_expedicion=_fecha(r.get("fechaExpedicion")),
                    categoria=(r.get("categoriaReforma") or None),
                    seccionPublicacion=(r.get("seccionPublicacion") or "").strip() or None,
                    pdf=r.get("pdf"),
                    tieneArticulos=bool(r.get("tieneArticulos", True)),
                    tieneProcesos=bool(r.get("tieneProcesos", False)),
                )
                for r in lote
            ]
            # A page shorter than asked for is the last one, and so is a full
            # page that already accounts for every declared row; asking for
            # the next would answer HTTP 500 rather than an empty page.
            total = datos.get("tamanio") or 0
            if len(lote) < TAMANIO_PAGINA_REFORMAS or len(filas) >= total:
                return filas
            pagina += 1

    def articulos_of_reforma(
        self, id_ordenamiento: str | int, id_reforma: int | str
    ) -> list[Articulo]:
        """The consolidated text right after that reform, article by article.

        Raises `ScjnApiError` on a reform the SCJN cannot serve. That is not
        hypothetical: `idOrdenamiento=693&idReforma=8` (`lfd`, 21/05/1982)
        answers HTTP 500 on every attempt and at every page size, while
        reforms 7 and 9 of the same law answer fine (issue #173, question 5).
        A crawl logs it and skips that one reform; it never aborts the
        instrument over it."""
        filas: list[Articulo] = []
        pagina = 1
        while True:
            datos = self._pide(
                "GET",
                "/api/SCOW/Articulos",
                params={
                    "idOrdenamiento": id_ordenamiento,
                    "idReforma": id_reforma,
                    "numeroPagina": pagina,
                    "tamanioPagina": TAMANIO_PAGINA_ARTICULOS,
                },
            )
            lote = datos.get("articulos") or []
            filas += [
                Articulo(
                    numero=a.get("numero"),
                    orden=a.get("orden"),
                    referencia=(a.get("referencia") or "").strip(),
                    contenido=a.get("contenido") or "",
                )
                for a in lote
            ]
            # See TAMANIO_PAGINA_ARTICULOS: a short page is the last one, and
            # so is a full page that already covers every declared article.
            total = datos.get("tamanio") or 0
            if len(lote) < TAMANIO_PAGINA_ARTICULOS or len(filas) >= total:
                return filas
            pagina += 1


# --- Markdown writer (issue #175) ----------------------------------------
#
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
# `referencia`/`orden` are not used to shape the file. They are reliable
# (issue #173: never empty over ~13 000 articles, `orden` always contiguous)
# but their vocabulary is open — besides `ENCABEZADO`/`TÍTULO PRIMERO`/
# `ARTÍCULO 1` it includes chapter names and the editorial `D.O.F. <fecha>`
# rows, which are precisely what `quita_notas_editoriales` removes. An
# article whose `referencia` is unexpected has to come out as the same
# paragraph it does today, not as a differently-shaped file.

from nota2md.scjn import (  # noqa: E402  (deliberately below the client)
    quita_notas_editoriales,
    ratio_similitud,
)


# --- paragraph classification ------------------------------------------
#
# The SCJN's text carries no formatting of its own: "TEXTO ORIGINAL.",
# "Artículo N.-" leads and "TRANSITORIOS" captions are plain text, told apart
# only by their own wording/casing — the same situation
# nota2md.texto_vigente's Diputados PDFs are in. This lived in `nota2md.scjn`
# while the source was the reform row's .docx (one docx paragraph already is
# one clean block, so only this per-paragraph classification was ever needed
# on top of it); it moved here with issue #179, when the .docx path went away
# and `articulos_a_markdown` became its only caller. The API's `contenido` is
# in exactly the same shape, which is why the classifier is reused byte for
# byte rather than rewritten — that reuse is what makes a snapshot written
# from the API diffable against the one the WebForms crawler wrote for the
# same law and the same date.
#
# Kept independent of texto_vigente's own patterns rather than imported, for
# the same reason that module gives for staying independent of this package's
# DOF-derived output: the two are meant to be compared, not to share a
# source.
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


def articulos_a_markdown(articulos: list[Articulo]) -> str:
    """The reform's consolidated text as the same light Markdown the
    retired `scjn.docx_a_markdown` produced from the .docx: one
    blank-line-separated
    paragraph per source paragraph, editorial asides removed, a heading for
    "Al margen un sello"/"Transitorios", a bolded caption for an ALL-CAPS
    line and a bolded lead for an "Artículo N"/ordinal/list-marker one."""
    parrafos: list[str] = []
    for articulo in articulos:
        contenido = _ETIQUETA_HTML.sub("", _SALTO_HTML.sub("\n", articulo.contenido))
        for parrafo in _PARRAFOS.split(contenido):
            parrafo = parrafo.strip()
            if parrafo:
                parrafos.append(parrafo)
    limpios = [quita_notas_editoriales(p) for p in parrafos]
    bloques = [_formatea_parrafo(p) for p in limpios if p]
    return "\n\n".join(bloques) + "\n"


def cabecera(ordenamiento: Ordenamiento, reforma: Reforma, nombre_buscado: str) -> str:
    """The same provenance header the retired `scjn._cabecera` wrote, key for
    key and in the same order, with what the API gives for free appended after it —
    never renaming or reordering one that is already there.

    The added keys are spelled snake_case (`seccion_publicacion`,
    `id_ordenamiento`, `reforma_id`) rather than in the API's own camelCase
    because `scjn.lee_cabecera` only recognizes `^[a-z_]+:` — a camelCase key
    would be written and then silently not read back, which is worse than not
    writing it. `id_ordenamiento` is the one worth having: it makes the
    instrument addressable by a stable id instead of a session URL, so a
    later run can skip the search step entirely (issue #177)."""
    lineas = [
        "---",
        "fuente: scjn",
    ]
    if ratio_similitud(ordenamiento.ordenamiento, nombre_buscado) < 1.0:
        lineas.append(f"nombre_buscado: {nombre_buscado}")
    lineas += [
        f"ordenamiento: {ordenamiento.ordenamiento}",
        f"fecha_publicacion: {reforma.fecha_publicacion}",
    ]
    if reforma.fecha_expedicion:
        lineas.append(f"fecha_expedicion: {reforma.fecha_expedicion}")
    if reforma.categoria:
        lineas.append(f"categoria: {reforma.categoria}")
    if ordenamiento.ratio is not None:
        lineas.append(f"ratio_similitud: {ordenamiento.ratio:.3f}")
        lineas.append(f"sospechoso: {'true' if ordenamiento.sospechoso else 'false'}")
    if reforma.seccionPublicacion:
        lineas.append(f"seccion_publicacion: {reforma.seccionPublicacion}")
    if ordenamiento.materia:
        lineas.append(f"materia: {ordenamiento.materia}")
    lineas.append(f"id_ordenamiento: {ordenamiento.idOrdenamiento}")
    lineas.append(f"reforma_id: {reforma.reformaId}")
    lineas.append("---")
    return "\n".join(lineas)


def snapshot(
    ordenamiento: Ordenamiento,
    reforma: Reforma,
    articulos: list[Articulo],
    nombre_buscado: str,
) -> str:
    """A whole `<fecha_publicacion>.md` — header, blank line, body — exactly
    as `descarga_ordenamiento` writes one."""
    return f"{cabecera(ordenamiento, reforma, nombre_buscado)}\n\n{articulos_a_markdown(articulos)}"


# --- Candidate selection (issue #176) ------------------------------------
#
# This is where a hurried migration can make things *worse*. Issue #115
# found 5 instruments where the old search returned a different document
# and the crawler saved it as if it were the right one, and the "file
# already on disk" skip has no notion of a snapshot being *wrong*. So the
# thresholds (`UMBRAL_MINIMO_SIMILITUD`, `UMBRAL_CONFIANZA_SIMILITUD`) and
# the two hard exclusions (`es_acuerdo_interno`, `grupo_instrumento`) are
# carried over unchanged from the retired `scjn.elige_candidato`; only signals the old
# results page did not have are added, each measured against those 5 cases
# plus `lfca` and `lisipl` (the numbers are the comment on issue #176).

from nota2md.scjn import (  # noqa: E402
    UMBRAL_CONFIANZA_SIMILITUD,
    UMBRAL_MINIMO_SIMILITUD,
    es_acuerdo_interno,
    grupo_instrumento,
)

# `categoriaOrdenamiento` is the API's own classification of the document
# (`LEY`, `CODIGO`, `CONSTITUCION`, `REGLAMENTO`, `ACUERDO`, `TRATADO`, ...),
# which the WebForms results page never showed. Mapping it onto
# `grupo_instrumento`'s two groups turns a guess made by reading the title
# into the SCJN's own answer for the same question — it is what rules out
# `lopgjdf`'s reglamento without depending on how the title happens to read.
_CATEGORIA_GRUPO = {
    "LEY": "ley",
    "CODIGO": "ley",
    "CÓDIGO": "ley",
    "CONSTITUCION": "ley",
    "CONSTITUCIÓN": "ley",
    "REGLAMENTO": "reglamento",
}


def grupo_de_categoria(categoria: str | None) -> str | None:
    """`grupo_instrumento`'s answer, read off `categoriaOrdenamiento`
    instead of off the title. None for a category that maps to neither
    group (`ACUERDO`, `TRATADO`, `DECRETO`, ...), which never excludes
    anyone — same posture as a title starting with neither word."""
    return _CATEGORIA_GRUPO.get((categoria or "").strip().upper())


def elige_ordenamiento(
    candidatos: list[Ordenamiento], nombre: str
) -> Ordenamiento | None:
    """The candidate that best matches `nombre` among `BusquedaFrase`'s
    results, or None when none of them plausibly is it — the same contract
    as the retired `scjn.elige_candidato` did, so a batch crawl logs the miss and moves on.

    Order of the filters, and why each one is where it is:

    1. `es_acuerdo_interno` — an SCJN Pleno acuerdo is never a legitimate
       match (`lisr`/`lsint`'s failure mode: the search returned no law at
       all, only an acuerdo whose long title mentions the searched name).
    2. `grupo_instrumento` **and** `grupo_de_categoria` — a ley/código is
       never the reglamento of itself (`lopgjdf`). The second is new: the
       API classifies the document itself, so a reglamento whose title
       happens not to start with "reglamento" is still caught.
    3. `ambito == "FEDERAL"` — `download_legal_provisions_provenance_ids`
       only ever covers federal instruments. New here only in that it costs
       nothing: the old crawler had to have opened the results page to read
       it, and it is now a field of the hit.
    4. `vigencia == "VIGENTE"`.

    Filters 1–2 never fall back to "keep everyone": a document of the wrong
    kind is worse than no document at all. Filters 3–4 always do — an
    abrogated law is still worth crawling its own history, and `lopgjdf`
    (ESTATAL, ABROGADO) would otherwise be dropped outright.

    The winner is the highest `ratio_similitud`, with `iweight` — the API's
    own relevance ranking — breaking a tie, and must still clear
    `UMBRAL_MINIMO_SIMILITUD`; it comes back flagged `sospechoso` when it
    clears that floor but not `UMBRAL_CONFIANZA_SIMILITUD`, for a caller to
    route to manual review rather than trust outright."""
    restantes = [c for c in candidatos if not es_acuerdo_interno(c.ordenamiento)]

    grupo_objetivo = grupo_instrumento(nombre)
    if grupo_objetivo is not None:
        restantes = [
            c
            for c in restantes
            if grupo_instrumento(c.ordenamiento) in (None, grupo_objetivo)
            and grupo_de_categoria(c.categoriaOrdenamiento) in (None, grupo_objetivo)
        ]
    if not restantes:
        return None

    federales = [c for c in restantes if c.ambito == "FEDERAL"] or restantes
    vigentes = [c for c in federales if c.vigencia == "VIGENTE"] or federales

    elegido = max(
        vigentes,
        key=lambda c: (ratio_similitud(c.ordenamiento, nombre), c.iweight or 0),
    )
    ratio = ratio_similitud(elegido.ordenamiento, nombre)
    if ratio < UMBRAL_MINIMO_SIMILITUD:
        return None
    elegido.ratio = ratio
    elegido.sospechoso = ratio < UMBRAL_CONFIANZA_SIMILITUD
    return elegido


# --- Whole-instrument crawl (issue #177) ---------------------------------

from collections.abc import Callable  # noqa: E402
from pathlib import Path  # noqa: E402


@dataclass
class ResultadoCrawl:
    """What one instrument's crawl produced: the snapshot paths (oldest
    first, as the retired `scjn.descarga_ordenamiento` returned them), the ordenamiento
    that was picked, and the reforms the SCJN could not serve."""

    escritos: list[Path]
    ordenamiento: Ordenamiento | None
    reformas_fallidas: list[str] = None  # type: ignore[assignment]
    #: Reforms the API itself marks `tieneArticulos=False` — it holds no
    #: consolidated text for them (mostly FE DE ERRATAS and ACLARACION rows).
    #: Kept apart from `reformas_fallidas`: this is a known absence, not a
    #: failure, and it is why an instrument's snapshot count can legitimately
    #: fall short of the reform count its own SCJN detail page shows.
    reformas_sin_articulos: list[str] = None  # type: ignore[assignment]
    #: How many rows `Reforma` returned, so a caller can check the crawl
    #: against the SCJN's own count without asking again.
    total_reformas: int = 0

    def __post_init__(self) -> None:
        if self.reformas_fallidas is None:
            self.reformas_fallidas = []
        if self.reformas_sin_articulos is None:
            self.reformas_sin_articulos = []


def descarga_ordenamiento(
    api: ScjnApi,
    nombre: str,
    outdir: Path,
    *,
    on_progreso: Callable[[str], None] | None = None,
    id_ordenamiento: str | int | None = None,
) -> ResultadoCrawl:
    """Every reform-dated snapshot the SCJN has for `nombre`, written as
    ``outdir/<fecha_publicacion>.md`` — the same contract as
    the retired `scjn.descarga_ordenamiento`, so `fetch_scjn_legislacion.py`
    keeps every
    one of its resumption mechanisms:

    - a file already on disk is left untouched and its reform not fetched,
      which is what makes a crawl over hundreds of instruments resumable;
    - two rows sharing a `fecha_publicacion` (39 dates on the CPEUM alone,
      up to 4 decrees on one day) get `-2.md`, `-3.md`, … appended in the
      SCJN's own most-recent-first row order;
    - nothing found is an empty result, not an exception, so a batch crawl
      logs the miss and keeps going;
    - `on_progreso` narrates a large instrument (issue #140).

    `id_ordenamiento`, when given, skips the search step entirely — the
    structural win of the whole migration (issue #172): an instrument is
    addressable by a stable id instead of a session-scoped URL, so a second
    run costs one request less per law and can be audited against exactly
    the document the first one read.

    A reform the API marks `tieneArticulos=False` is not requested at all
    (it answers HTTP 500 if asked) and is recorded in
    `reformas_sin_articulos`; one that fails for any other reason goes to
    `reformas_fallidas`. Neither ever aborts the instrument, and the two are
    kept apart because only the first is an expected absence — which is what
    lets a caller check `len(escritos) + len(reformas_sin_articulos)`
    against `total_reformas`, the count the SCJN's own detail page shows.
    """
    if id_ordenamiento is not None:
        elegido = Ordenamiento(
            idOrdenamiento=str(id_ordenamiento),
            ordenamiento=nombre,
            ratio=1.0,
            sospechoso=False,
        )
    else:
        elegido = elige_ordenamiento(api.search_ordenamiento(nombre), nombre)
        if elegido is None:
            return ResultadoCrawl([], None)

    reformas = api.reformas_of_ordenamiento(elegido.idOrdenamiento)
    outdir.mkdir(parents=True, exist_ok=True)

    escritos: list[Path] = []
    fallidas: list[str] = []
    sin_articulos: list[str] = []
    repeticiones: dict[str, int] = {}
    total = len(reformas)
    for indice, reforma in enumerate(reformas, 1):
        if on_progreso is not None and total > 1:
            on_progreso(f"reforma {indice}/{total}")
        repeticiones[reforma.fecha_publicacion] = (
            repeticiones.get(reforma.fecha_publicacion, 0) + 1
        )
        orden = repeticiones[reforma.fecha_publicacion]
        sufijo = f"-{orden}" if orden > 1 else ""
        destino = outdir / f"{reforma.fecha_publicacion}{sufijo}.md"
        if destino.exists():
            escritos.append(destino)
            continue
        if not reforma.tieneArticulos:
            # The API says up front it holds no consolidated text for this
            # row; asking anyway answers HTTP 500.
            sin_articulos.append(
                f"{reforma.fecha_publicacion} (reformaId {reforma.reformaId}, "
                f"{reforma.categoria or 'sin categoria'})"
            )
            continue
        try:
            articulos = api.articulos_of_reforma(elegido.idOrdenamiento, reforma.reformaId)
        except ScjnApiError as exc:
            fallidas.append(f"{reforma.fecha_publicacion} (reformaId {reforma.reformaId}): {exc}")
            if on_progreso is not None:
                on_progreso(f"aviso: la SCJN no sirve la reforma {reforma.fecha_publicacion}")
            continue
        destino.write_text(snapshot(elegido, reforma, articulos, nombre), encoding="utf-8")
        escritos.append(destino)
    return ResultadoCrawl(
        list(reversed(escritos)), elegido, fallidas, sin_articulos, total
    )

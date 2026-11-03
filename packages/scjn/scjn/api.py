"""Client for the SCJN's SCOW JSON API, the backend of
https://legislacion.scjn.gob.mx/consulta/buscador.

This is the transport that replaced this project's own legacy WebForms
crawler (issue #172, retired in #179). Where that one round-tripped `__VIEWSTATE`,
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
either — no more than any other unofficial site this project reads — so the
rate limiting and the retries stay.

Measured live against the corpus the old crawler wrote (issue #173, whose
numbers are the comment on #172); three of those findings are load-bearing
here and each is marked at the code that depends on it.

**What this module is, and is not (issue #303).** It owns the transport and its
paging, the completeness guard, the raw store (`guarda_crudo`/`articulos_de_crudo`)
and the crawl (`descarga_ordenamiento`, `snapshot`, `cabecera`). The
plain-text-to-Markdown writer moved to `scjn.markdown`; every name it had here is
imported back, so `scjn.api.articulos_a_markdown` and `@patch("scjn.api._...")`
keep working.

**What the guard refuses.** `ScjnApi.articulos_of_reforma` returns an `Articulos`
answer only when `completeness_defects` finds nothing wrong with it: `orden` is
exactly `1..max` and there are at least `tamanio` rows. Otherwise the whole reform
is asked again and finally refused with `ScjnApiIncompleteError`, and a crawl
writes no snapshot for it. An *identical* repeated row is dropped from an answer;
two different blocks sharing an `orden` are real and both stay. The predicate is a
pure function so `scripts/verify_scjn_outline.py --guard` audits every raw answer
kept on disk with the very code the client uses (a raw file does not record
`tamanio`, so only the `orden` condition is checkable from it).
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
# the last page" a safe stop condition -- and that condition is the only safe
# one here, because two things are true of this API at once (issue #178, found
# while auditing why `lfd` had 92 snapshots against the 98 reforms its own
# detail page lists):
#
#   - Asking for a page past the end answers HTTP 500, not an empty page.
#   - `tamanio` can exceed the rows one answer carries: `lfd` reforma 99
#     reported `tamanio` 995 and served 991 articles spanning `orden`
#     1..995, with 4 `orden` values missing.
#
# Together those made a `len(filas) >= tamanio` stop condition fail closed: it
# never came true for such an answer, so a second page was always requested,
# it answered 500, and the whole reform was discarded as unavailable. That is
# what produced ~106 of the 231 "the SCJN cannot serve this" reports of the
# first full crawl. Above 500 the endpoint also stops honouring the requested
# size (a ~1.7 MB payload cap), which would make a short page ambiguous
# between "last page" and "truncated"; at 500 it does not.
#
# So *both* stop conditions are needed to stop paging, and neither alone is
# enough:
#
#   - a page shorter than requested is the last one (covers a `tamanio` above
#     what the endpoint serves, e.g. `lfd` reforma 99);
#   - having collected `tamanio` rows is also the end, even on a full page
#     (covers a count that is an exact multiple of the page size -- `lss`
#     reforma 42 declares exactly 500 and serves exactly 500, and asking for
#     its page 2 answers 500 too).
#
# Issue #178 then drew the wrong conclusion from `lfd` reforma 99: it called
# the 991-row answer "complete" and `tamanio` an overcount. It was the other
# way round (issue #300). `tamanio` was right and the answer was *incomplete*:
# the 4 missing `orden` values (934, 937, 940, 942) are `CONTENIDO` blocks the
# API serves today (reform 99 now answers all 995), and the API had left them
# out while still reserving their `orden` numbers. The same happened to
# `tratados/24031`, whose four `CONTENIDO` blocks held the whole Radio
# Regulations revision (254 k characters) and were missing from the published
# snapshot. 367 snapshots of 240 instruments had such a gap on disk. Neither
# stop condition detects it -- they only decide when to stop *asking* -- so
# `articulos_of_reforma` also checks the answer: `orden` must be exactly
# `1..N` and the rows must reach `tamanio`, else the whole reform is asked
# again and finally refused (`ScjnApiIncompleteError`).
TAMANIO_PAGINA_ARTICULOS = 500
TAMANIO_PAGINA_REFORMAS = 500

_EM = re.compile(r"</?em>")


class ScjnApiError(RuntimeError):
    """The API did not answer usefully — an HTTP status, a `codigo` other
    than 200 in the body, or a non-JSON answer (a WAF challenge page). Kept
    distinct from an empty result, which is a legitimate answer."""


class ScjnApiIncompleteError(ScjnApiError):
    """The API answered, but the answer is known to be partial: its `orden`
    values are not exactly `1..N`, or it carries fewer rows than the `tamanio`
    it declares (issue #300). A `ScjnApiError` on purpose, so a crawl treats
    the reform like any other it cannot get -- logged, skipped, never written
    as a snapshot that is known to miss text."""


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
    #: The SCJN's own one-paragraph abstract of the instrument (issue #215).
    #: A field of the *instrument*, like `materia` and `vigencia` and unlike
    #: anything on a `Reforma` row -- which is what makes the three of them
    #: one record per law rather than one per snapshot.
    resumen: str | None = None
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
        rather than a lookup: `scripts/discover_federal_laws.py` pages
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
                resumen=(r.get("resumen") or "").strip() or None,
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
        for intento in range(self.reintentos + 1):
            filas, total = self._articulos_una_vez(id_ordenamiento, id_reforma)
            faltan = _orden_faltante(filas, total)
            if faltan is None:
                return _sin_filas_repetidas(filas)
            if intento < self.reintentos:
                time.sleep(self.espera)
        raise ScjnApiIncompleteError(
            f"idOrdenamiento={id_ordenamiento} idReforma={id_reforma}: "
            f"incomplete answer after {self.reintentos + 1} attempt(s), "
            f"{len(filas)} rows for tamanio {total}; {faltan}"
        )

    def _articulos_una_vez(
        self, id_ordenamiento: str | int, id_reforma: int | str
    ) -> tuple[list[Articulo], int]:
        """Every page of one reform's `Articulos`, and the `tamanio` the API
        declared for it. Completeness is `articulos_of_reforma`'s to judge."""
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
                return filas, total
            pagina += 1


def _sin_filas_repetidas(filas: list["Articulo"]) -> list["Articulo"]:
    """`filas` without the rows the API sent twice, the first of each kept.

    Seen on 2026-10-26 while re-crawling for issue #300: an `Articulos` answer
    can repeat a row *exactly* (same `orden`, `numero`, `referencia` and
    `contenido` -- `cpf` 08-05-2023 came back with 1,007 rows over 893
    distinct blocks, `pef-2026` 21-11-2025 with 8 more than it has), and
    `tamanio` counts the repeats. Written as they came, the snapshot would
    print an article twice. Only an *identical* row is dropped: two different
    blocks sharing an `orden` are real (`lissste` reform 27-05-2011 gives
    `orden` 322 to a `D.O.F.` line and to a `CONTENIDO` block;
    reglamento 166758 too) and both are kept."""
    vistas: set[tuple] = set()
    unicas = []
    for fila in filas:
        llave = (fila.orden, fila.numero, fila.referencia, fila.contenido)
        if llave not in vistas:
            vistas.add(llave)
            unicas.append(fila)
    return unicas


def completeness_defects(filas: list["Articulo"], total: int) -> list[str]:
    """Why an `Articulos` answer is incomplete: one string per defect, empty
    when it is not (issue #300). Complete means both: `orden` is exactly
    `1..max` with no gap, and there are at least `tamanio` (`total`) rows.
    Either alone misses a case: a gap in the middle leaves the count short
    only when `tamanio` counts the reserved numbers, and a block dropped at
    the *end* leaves no gap at all. Rows with no `orden` cannot be checked for
    contiguity; the count still is.

    A pure function of the rows and the declared count, shared by
    `articulos_of_reforma` (the client) and `scripts/verify_scjn_outline.py
    --guard` (the audit of every raw answer kept on disk), so the two cannot
    disagree about what the guard refuses. Pass ``total=0`` when the declared
    `tamanio` is unknown (a kept raw answer does not record it): only the
    `orden` check can fire then.

    >>> from scjn.api import Articulo, completeness_defects
    >>> completeness_defects([Articulo(0, 1, "A", ""), Articulo(0, 3, "B", "")], 2)
    ['missing orden [2]']
    >>> completeness_defects([Articulo(0, 1, "A", "")], 2)
    ['1 rows is fewer than tamanio 2']
    >>> completeness_defects([Articulo(0, 1, "A", "")], 1)
    []
    """
    problemas = []
    ordenes = {f.orden for f in filas if f.orden is not None}
    if ordenes:
        faltan = sorted(set(range(1, max(ordenes) + 1)) - ordenes)
        if faltan:
            problemas.append(f"missing orden {faltan[:20]}{' ...' if len(faltan) > 20 else ''}")
    if len(filas) < total:
        problemas.append(f"{len(filas)} rows is fewer than tamanio {total}")
    return problemas


def _orden_faltante(filas: list["Articulo"], total: int) -> str | None:
    """`completeness_defects` as the one-line reason `ScjnApiIncompleteError`
    carries, or None when the answer is complete."""
    return "; ".join(completeness_defects(filas, total)) or None


# --- The Markdown writer lives in `scjn.markdown` (issue #303) ------------
#
# Moved out of this module with no behaviour change. Every name is imported
# back so `scjn.api.articulos_a_markdown`, `@patch("scjn.api._...")` and the
# tests that reach the private helpers through `scjn.api` keep working.
from scjn.markdown import (  # noqa: E402,F401
    _BLOCK_ANNOTATION,
    _BLOCK_SEPARATOR,
    _COMPONENTE,
    _ETIQUETA_HTML,
    _LATINO,
    _LATINO_UNIDAD,
    _LEAD_ARTICULO,
    _LEAD_LISTA,
    _LEAD_ORDINAL,
    _MARGEN,
    _NUM_ARTICULO,
    _ORDINAL,
    _ORDINAL_DECENA,
    _ORDINAL_PALABRA,
    _ORDINAL_UNIDAD,
    _PARRAFOS,
    _ROMANO,
    _SALTO_HTML,
    _TRANSITORIOS_PARRAFO,
    _es_titular,
    _formatea_parrafo,
    _merge_row,
    _normalize_cell,
    _paragraphs_from_tabbed_block,
    articulos_a_markdown,
)
from scjn.text import ratio_similitud  # noqa: E402


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
    return (
        f"{cabecera(ordenamiento, reforma, nombre_buscado)}\n\n"
        f"{articulos_a_markdown(articulos, outline=True)}"
    )


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

from scjn.text import (  # noqa: E402
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
    3. `ambito == "FEDERAL"` — the catalogue only ever covers federal
       instruments. New here only in that it costs
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


# --- Per-instrument metadata (issue #215) --------------------------------
#
# `materia`, `vigencia` and `resumen` are fields of the *instrument*: they
# ride on every `BusquedaFrase` hit and appear nowhere on a `Reforma` row, so
# one value describes a whole law rather than one of its snapshots (issue
# #203's first open question, answered by measurement).
#
# Reading them therefore costs no new endpoint -- only a search. Which is
# exactly where issue #115's Hallazgo C lives: the SCJN's search can return a
# completely wrong document for an instrument. So this lookup never ranks and
# never picks a best candidate; it keeps only the hit whose `idOrdenamiento`
# is the one the corpus already recorded for that law, and answers None
# otherwise. A law we cannot address by id is a law this function refuses to
# describe.

#: How many result pages `instrument_metadata` will read before giving up.
#: Searching with the SCJN's own title for a law puts it on the first page
#: essentially always; the extra pages are for a title shared by many
#: instruments (the 40 095 state-level ones the same index carries), not for
#: a lookup that is going badly.
PAGINAS_METADATOS = 3


def instrument_metadata(
    api: ScjnApi,
    nombre: str,
    id_ordenamiento: str | int,
    *,
    paginas: int = PAGINAS_METADATOS,
    tamanio_pagina: int = 50,
) -> Ordenamiento | None:
    """The search hit for the law already addressed by `id_ordenamiento` —
    the whole `Ordenamiento`, so `materia`, `vigencia` and `resumen` come
    with it — or None when `nombre` does not turn it up.

    `nombre` is only a way to reach the index: the answer is chosen by
    `idOrdenamiento`, never by similarity or rank, so a wrong document
    cannot be returned as if it were the right one (issue #115, Hallazgo C).
    Callers pass the SCJN's own title for the law — the `ordenamiento` field
    of any snapshot's provenance header — which is the string most likely to
    surface it.

    Returns None rather than raising for "not found": a law the SCJN has not
    indexed under that title is a fact to report, not a crawl to abort
    (`lfca`, issue #124). Transport failures still raise `ScjnApiError`.
    """
    objetivo = str(id_ordenamiento)
    for pagina in range(1, paginas + 1):
        resultados = api.search_ordenamiento(
            nombre, tamanio_pagina=tamanio_pagina, pagina=pagina
        )
        for candidato in resultados:
            if str(candidato.idOrdenamiento) == objetivo:
                return candidato
        if len(resultados) < tamanio_pagina:
            # A short page is the last one -- same stop condition every other
            # paged endpoint here uses, and asking past the end answers 500.
            break
    return None


# --- Whole-instrument crawl (issue #177) ---------------------------------

import gzip  # noqa: E402
import json  # noqa: E402


def guarda_crudo(destino: "Path", articulos: list[Articulo]) -> None:
    """What the API answered for one reform, kept as gzipped JSON -- the four
    fields of every block that `articulos_a_markdown` reads."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    filas = [
        {"numero": a.numero, "orden": a.orden, "referencia": a.referencia, "contenido": a.contenido}
        for a in articulos
    ]
    with gzip.open(destino, "wt", encoding="utf-8") as f:
        json.dump(filas, f, ensure_ascii=False)


def articulos_de_crudo(origen: "Path") -> list[Articulo]:
    """The inverse of `guarda_crudo`: the blocks a reform answered with, ready
    for `snapshot`."""
    with gzip.open(origen, "rt", encoding="utf-8") as f:
        filas = json.load(f)
    return [
        Articulo(
            numero=a.get("numero"), orden=a.get("orden"),
            referencia=a.get("referencia") or "", contenido=a.get("contenido") or "",
        )
        for a in filas
    ]

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
    raw_dir: Path | None = None,
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

    `raw_dir`, when given, also keeps what the API answered for each reform it
    fetched, as ``raw_dir/<snapshot name>.json.gz`` -- the four fields the
    writer reads (`numero`, `orden`, `referencia`, `contenido`) of every
    block. It is what lets the writer change again without asking the SCJN
    for a whole corpus a second time (review fix-2 of #291): see
    `articulos_de_crudo` and `scripts/rewrite_scjn_from_raw.py`.
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
        if raw_dir is not None:
            guarda_crudo(raw_dir / f"{destino.stem}.json.gz", articulos)
        destino.write_text(snapshot(elegido, reforma, articulos, nombre), encoding="utf-8")
        escritos.append(destino)
    return ResultadoCrawl(
        list(reversed(escritos)), elegido, fallidas, sin_articulos, total
    )

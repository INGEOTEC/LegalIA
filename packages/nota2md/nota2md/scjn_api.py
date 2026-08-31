"""Client for the SCJN's SCOW JSON API, the backend of
https://legislacion.scjn.gob.mx/consulta/buscador.

This is the transport `nota2md.scjn`'s WebForms crawler is being migrated
off (issue #172). Where that one round-trips `__VIEWSTATE`, holds a
session-scoped `q` token, scrapes a paged HTML grid and downloads one
`.docx` per reform, this asks three unauthenticated JSON endpoints:

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

# Issue #173, question 5: `Articulos` served a 3 473-article reform whole in
# one 2.1 MB / 1.8 s response at this size, with no ceiling observed. The
# readers below still paginate, since a ceiling appearing later must not
# silently truncate a law.
TAMANIO_PAGINA_ARTICULOS = 5000
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
    # Filled in by candidate selection, the same way `scjn.Candidato` does.
    ratio: float | None = None
    sospechoso: bool | None = None


@dataclass
class Reforma:
    """One row of an instrument's reform table — every field `scjn._cabecera`
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
    ) -> list[Ordenamiento]:
        """Every ordenamiento `BusquedaFrase` returns for `name`.

        Issue #173: the optional filters must be sent, as empty strings —
        a body carrying only `q`/`tipoBusqueda`/`tipoPublicacion` gets an
        HTTP 500, not a validation error."""
        cuerpo = {
            "q": name,
            "tipoBusqueda": tipo_busqueda,
            "tipoPublicacion": tipo_publicacion,
            "ambitoF": "",
            "categoriaF": "",
            "vigenciaF": "",
            "entidadFederativaF": "",
            "materiaF": "",
            "municipioF": "",
            "fechaPublicacionInicio": "",
            "fechaPublicacionFin": "",
            "numeroPagina": 1,
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
            total = datos.get("tamanio") or 0
            if not lote or len(filas) >= total:
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
            total = datos.get("tamanio") or 0
            if not lote or len(filas) >= total:
                return filas
            pagina += 1

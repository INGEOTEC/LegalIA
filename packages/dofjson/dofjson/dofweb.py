"""Fallback source for the DOF's contents: the DOF's own website.

SIDOF (`sidof.py`) is the primary source, but its dataset has holes. For
some dates it does not answer 404 — it answers **200 OK with every note list
empty**, and lists those dates under `FechasSinPublicacion` in
`GET /diarios/{year}`, as if the gazette had not been published at all. For a
few of them it did publish: on 08-03-1999, for instance, SIDOF reports
nothing while the DOF ran the decree amending articles 16, 19, 22 and 123 of
the Constitution. The note is unreachable from SIDOF by any route — its
codNota returns `{"Nota": []}` and its codDiario 404s.

The DOF's public website, `dof.gob.mx`, is a separate system built on a
separate database, and it does have those days. This module reads it and
returns what it finds in the shapes `sidof` uses, so a caller can substitute
one for the other:

* `get_notas()` mirrors `sidof.get_notas()` — a day's index.
* `get_nota()` mirrors `sidof.get_nota()` — one note, with the HTML of its
  text in `cadenaContenido`. This is the only way to read the text of a note
  on a day SIDOF lost: those notes have no SIDOF record at all, so the whole
  HTML → Markdown path (nota2md) would otherwise be unavailable for them.

What the fallback does and does not carry
-----------------------------------------
The website's daily index lists the substantive part of the gazette — the
notes issued by `PE` (Poder Ejecutivo), `PJ`, `PL`, `OA` and `OD`. It leaves
out the three bulk-announcement sections, which are reachable on the site only
through its POST search form:

    CV  convocatorias for public-sector procurement
    VG  convocatorias for civil-service vacancies
    AV  avisos judiciales y generales

On a day both sources have, the recovered set matches SIDOF's exactly once
those three are excluded (verified on days sampled from 1999 through 2026).
So a recovered day is complete with respect to what the gazette *enacted*,
and short of what it *announced* — `notasIncompletas` is set on the result to
say so, rather than letting a partial day pass for a whole one.

Editions with no digital index
------------------------------
The website's per-note index starts in **January 1999**. Before that it holds
only scanned images, so a day returns an edition — a codDiario and its section
list — with no per-note links. Those come back in `edicionesSinIndice`: proof
that the gazette *was* published, which is what a caller needs to avoid
recording the day as empty, even though no titles can be listed. Every day
confirmed lost from SIDOF so far falls in 1999 or later, inside the range
where titles can actually be recovered.

TLS
---
The DOF's certificate covers `dof.gob.mx` only — no SAN for the `www`
subdomain — so requesting `https://www.dof.gob.mx` fails hostname
verification outright, on any client, regardless of trust store. BASE_URL
therefore points at the bare domain, whose redirects (e.g. off
`nota_detalle.php`) stay on it too.

Separately, `dof.gob.mx` serves its leaf certificate without the
intermediate that signs it, so verification can still fail with "unable to
get local issuer certificate" on a client that does not chase the issuer
itself. The missing GoDaddy intermediate ships in
`certs/dof-gob-mx-chain.pem`. The system trust store is tried first, and the
bundled chain is used only if that fails — certificate verification is never
turned off.
"""

import datetime as dt
import html
import re
from pathlib import Path

import requests

BASE_URL = "https://dof.gob.mx"
FUENTE = "dof.gob.mx"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; DOF-JSON-Client/1.0)"}

# The daily index is served as cp1252 without declaring it. Note pages do
# declare their charset, and it is a different one — see _get_nota_pagina().
_ENCODING = "cp1252"

_CADENA = Path(__file__).parent / "certs" / "dof-gob-mx-chain.pem"

# The site says this, in place of a section list, when it has no such day.
_SIN_DATOS = "No hay datos para la fecha seleccionada"

_EDICIONES = {"MAT": "NotasMatutinas", "VES": "NotasVespertinas", "EXT": "NotasExtraordinarias"}

# Headings the daily index uses for its top-level groups, and the codOrgaUno
# SIDOF gives the same group. CV/VG/AV are absent from the index (see above).
_CODIGO_ORGA = {
    "PODER EJECUTIVO": "PE",
    "PODER LEGISLATIVO": "PL",
    "PODER JUDICIAL": "PJ",
    "ORGANISMOS AUTONOMOS": "OA",
    "ORGANISMOS DESCONCENTRADOS O DESCENTRALIZADOS": "OD",
    "GOBIERNO DEL DISTRITO FEDERAL": "GDF",
    "OTROS": "OTROS",
}

# Groups the website's daily index never lists.
ORGA_NO_LISTADOS = ("CV", "VG", "AV")

# The index prints the date it is actually serving. It has been seen answering
# with a different day's page — reproducibly enough that a sweep once counted
# a Monday holiday as published, its codDiario belonging to a day four months
# later. Since the parser stamps each note with the date that was *asked for*,
# an unnoticed mix-up would file real notes under the wrong day, so every page
# is checked against what it claims to be.
_ENCABEZADO = re.compile(r"Fecha:\s*(\d{2}/\d{2}/\d{4})")

_TAG = re.compile(r"<[^>]+>")
# The index leaves commented-out markup inline, and the headings sit inside it,
# so comment delimiters have to go before tags do or their "-->" survives.
_COMENTARIO = re.compile(r"<!--|-->")
_COD_DIARIO = re.compile(r"cod_diario=(\d+)")

# The index is a flat table: section banners, top-level group banners,
# issuing-body subheadings and note links, in document order. Walking the
# matches in order is what assigns each note to the headings above it.
_TOKEN = re.compile(
    r'class="txt_blanco">(?P<seccion>[^<]*)'
    r'|class="txt_blanco2">(?P<orga>[^<]*)'
    r'|class="subtitle_azul">(?P<orgados>.*?)</td>'
    r'|nota_detalle\.php\?codigo=(?P<cod>\d+)[^>]*>(?P<titulo>.*?)</a>',
    re.S,
)


def _texto(bruto: str) -> str:
    """Collapse a chunk of the index's markup down to its visible text."""
    return " ".join(html.unescape(_TAG.sub(" ", _COMENTARIO.sub(" ", bruto))).split())


def _solicita(url: str, timeout: int):
    """Fetch a page, completing the server's certificate chain if needed.

    Tries the system trust store first, so a fixed server (or a platform that
    chases the issuer on its own) needs nothing extra, and falls back to the
    bundled GoDaddy chain only on a TLS failure.
    """
    try:
        response = requests.get(url, headers=_HEADERS, timeout=timeout)
    except requests.exceptions.SSLError:
        response = requests.get(url, headers=_HEADERS, timeout=timeout, verify=str(_CADENA))
    response.raise_for_status()
    return response


def _get(url: str, timeout: int) -> str:
    """Fetch a page of the daily index and decode it (see _ENCODING)."""
    return _solicita(url, timeout).content.decode(_ENCODING, errors="replace")


class PaginaDeOtroDia(Exception):
    """The index answered with a page for a date other than the one asked for.

    Transient, as far as anyone can tell — the same date fetched again comes
    back correct. Raised rather than swallowed so a caller retries the day
    instead of either trusting the wrong notes or writing the day off as
    unpublished.
    """


def _parse_edicion(pagina: str, fecha: dt.date, edicion: str) -> tuple[list[dict], int | None]:
    """Pull one edition's notes, and its codDiario, out of the index page.

    Raises PaginaDeOtroDia if the page is not the one that was requested.
    """
    if _SIN_DATOS in pagina:
        return [], None

    cod_diario = _COD_DIARIO.search(pagina)
    cod_diario = int(cod_diario.group(1)) if cod_diario else None

    seccion = orga = orga_dos = None
    notas = []
    for m in _TOKEN.finditer(pagina):
        if m.group("seccion") is not None:
            seccion = _texto(m.group("seccion")) or None
            orga = orga_dos = None
        elif m.group("orga") is not None:
            orga = _texto(m.group("orga")) or None
            orga_dos = None
        elif m.group("orgados") is not None:
            orga_dos = _texto(m.group("orgados")) or None
        else:
            notas.append({
                "codNota": int(m.group("cod")),
                "titulo": _texto(m.group("titulo")),
                "fecha": f"{fecha:%d-%m-%Y}",
                "codDiario": cod_diario,
                "codEdicion": edicion,
                # "PRIMERA SECCION" -> "PRIMERA", as SIDOF writes it.
                "codSeccion": seccion.replace(" SECCION", "") if seccion else None,
                "codOrgaUno": _CODIGO_ORGA.get(orga),
                "nombreCodOrgaUno": orga,
                "codOrgaDos": orga_dos,
                "orden": float(len(notas) + 1),
                "fuente": FUENTE,
                # A note only gets a link into the index when the site has its
                # digital text (verified against codNota 2124037 — see
                # get_nota()); "S" here is as reliable as SIDOF's own field.
                # Image/PDF are edition-wide resources this index carries no
                # per-note page number for, so neither can actually be sliced
                # out for a single note (nota2md.legal_provisions() refuses the
                # image/pdf paths for a fuente="dof.gob.mx" note for the same
                # reason) — "N" here says so, instead of leaving the field
                # out and reading, inconsistently with SIDOF, as unknown.
                "existeHtml": "S",
                "existeImagen": "N",
                "existePdf": "N",
            })

    if not notas and cod_diario is None:
        # Nothing to take. An edition the gazette did not run comes back
        # either with the "no data" banner or, for some dates, as a bare page
        # with neither banner nor date — both mean the same thing, and neither
        # can be mistaken for another day's content.
        return [], None

    # Only content needs vouching for: this is what stops another day's notes
    # from being filed under the date that was asked for.
    encabezado = _ENCABEZADO.search(pagina)
    esperado = f"{fecha:%d/%m/%Y}"
    if encabezado is None or encabezado.group(1) != esperado:
        raise PaginaDeOtroDia(
            f"se pidió {esperado} ({edicion}) y la página dice "
            f"{encabezado.group(1) if encabezado else 'nada'}"
        )
    return notas, cod_diario


def get_notas(date: dt.date, timeout: int = 60) -> dict:
    """A day's notes index from the DOF website, shaped like sidof.get_notas().

    Adds three keys the SIDOF response does not have:

    `fuente`
        Always `"dof.gob.mx"`, so a stored day says where it came from.
    `notasIncompletas`
        The codOrgaUno groups the website's index never lists (CV/VG/AV) —
        present whenever any note was recovered, because the day is complete
        only with respect to the rest.
    `edicionesSinIndice`
        Editions that exist but have no per-note index, as
        `{"codEdicion", "codDiario"}`. A pre-digital day comes back with no
        notes and a populated list here: the gazette was published, only its
        contents are images.

    Every note also carries `existeHtml`/`existeImagen`/`existePdf`, exactly
    as SIDOF's own notes do, rather than leaving them out: `existeHtml` is
    always `"S"` (a note is only linked into this index once the site has
    its digital text) and `existeImagen`/`existePdf` are always `"N"` (this
    index carries no per-note page number, so neither can be sliced out for
    a single note — see nota2md.legal_provisions(), which refuses those
    paths for a `fuente="dof.gob.mx"` note for the same reason).

    Raises PaginaDeOtroDia if the site answers with a different day's page.
    """
    resultado = {
        "messageCode": 200,
        "response": "OK",
        "fuente": FUENTE,
        "NotasMatutinas": [],
        "NotasVespertinas": [],
        "NotasExtraordinarias": [],
        "edicionesSinIndice": [],
    }

    for edicion, clave in _EDICIONES.items():
        url = (
            f"{BASE_URL}/index.php?year={date:%Y}&month={date:%m}"
            f"&day={date:%d}&edicion={edicion}"
        )
        try:
            pagina = _get(url, timeout)
        except requests.exceptions.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                continue
            raise
        notas, cod_diario = _parse_edicion(pagina, date, edicion)
        resultado[clave] = notas
        if cod_diario is not None and not notas:
            resultado["edicionesSinIndice"].append(
                {"codEdicion": edicion, "codDiario": cod_diario}
            )

    if any(resultado[clave] for clave in _EDICIONES.values()):
        resultado["notasIncompletas"] = list(ORGA_NO_LISTADOS)
    return resultado


# --- one note's text -------------------------------------------------------
#
# `nota_detalle.php?codigo=N` renders a note inside `<div id="DivDetalleNota">`.
# The note's own markup is wrapped in `<HTML>…</HTML>` there — byte for byte
# the string SIDOF serves as `cadenaContenido`, save for the site escaping its
# accents as entities (which parses back the same). The site then appends its
# own disclaimer table *after* that wrapper, still inside the div, so the
# wrapper — not the div — is the note's boundary.

_DIV_NOTA = re.compile(r"""<div[^>]*\bid=['"]?DivDetalleNota['"]?""", re.I)
_DIV = re.compile(r"<\s*(/?)div\b", re.I)
_CUERPO_NOTA = re.compile(r"<HTML>.*?</HTML>", re.I | re.S)
_PARRAFO = re.compile(r"<p\b[^>]*>(.*?)</p>", re.I | re.S)
_FECHA_NOTA = re.compile(r"DOF:\s*(\d{2})/(\d{2})/(\d{4})")

# Some notes (seen on codes from 1999-2000) carry no <HTML>…</HTML> wrapper at
# all — their markup sits directly in the div, as bare <p>/<font> tags. The
# site's own disclaimer about HTML-conversion quality still follows right
# after, inside the same div, with no tag boundary marking where the note
# ends and the disclaimer begins — so that sentence is the only landmark
# available to cut it off. Its absence means the div has no note content at
# all (e.g. a PDF-only note, whose disclaimer says something else entirely).
_AVISO_CALIDAD = re.compile(r"no se muestren correctamente debido a la conversi")

# The date the page shows for a codigo it does not have: the Unix epoch, one
# day off — a formatted zero rather than a real publication date.
_FECHA_INEXISTENTE = ("31", "12", "1969")


def _get_nota_pagina(cod_nota: int, timeout: int, fecha: dt.date | None) -> str:
    """Fetch a note's page, decoded by the charset it declares.

    Unlike the daily index (see _ENCODING), these pages are served as UTF-8
    and say so, so the declared charset is honoured and cp1252 is only the
    fallback for a response that declares nothing.
    """
    url = f"{BASE_URL}/nota_detalle.php?codigo={cod_nota}"
    if fecha is not None:
        url += f"&fecha={fecha:%d/%m/%Y}"
    response = _solicita(url, timeout)
    return response.content.decode(response.encoding or _ENCODING, errors="replace")


def _detalle(pagina: str) -> str | None:
    """The contents of the page's DivDetalleNota, or None if it has none."""
    inicio = _DIV_NOTA.search(pagina)
    if inicio is None:
        return None
    abre = pagina.find(">", inicio.end())
    if abre < 0:
        return None

    profundidad = 1
    for m in _DIV.finditer(pagina, abre + 1):
        profundidad += -1 if m.group(1) else 1
        if profundidad == 0:
            return pagina[abre + 1 : m.start()]
    return pagina[abre + 1 :]


def get_nota(cod_nota: int, timeout: int = 60, fecha: dt.date | None = None) -> dict:
    """One note from the DOF website, shaped like sidof.get_nota().

    Returns `{"messageCode", "response", "fuente", "Nota"}`, with `Nota` an
    empty list — as SIDOF answers for a codNota it does not have — when the
    site has no such note.

    The note carries the fields the page can actually support:

    `cadenaContenido`
        The HTML of the note's text, ready for nota2md, or None when the site
        offers only the scanned edition (`existeHtml` "N").
    `titulo`
        The note's own first line. The daily index (get_notas()) words it
        slightly differently; this is what the document itself says.
    `fecha`, `codNota`, `fuente`, `existeHtml`
        As SIDOF writes them.

    The page carries no codDiario, codEdicion or pagina, so those SIDOF fields
    are absent — the image/PDF paths need them and remain SIDOF-only.

    `fecha`, the argument
        The page normally resolves a bare codigo to its date on its own, but
        for some codes (seen from 1999-2000) that lookup fails and the page
        answers as if the codigo did not exist at all, even though it does —
        while `codigo` *and* its real date together resolve it correctly (see
        issue #109). Pass the date when it is already known — from
        get_notas(), say — to get those notes too; a wrong date is rejected
        the same way a missing note is, so this never risks a mismatched note.
    """
    pagina = _get_nota_pagina(cod_nota, timeout, fecha)

    fecha_pagina = _FECHA_NOTA.search(pagina)
    if fecha_pagina is None or fecha_pagina.groups() == _FECHA_INEXISTENTE:
        return {"messageCode": 200, "response": "OK", "fuente": FUENTE, "Nota": []}

    detalle = _detalle(pagina) or ""
    cuerpo = _CUERPO_NOTA.search(detalle)
    if cuerpo:
        contenido = cuerpo.group(0)
    else:
        # No <HTML> wrapper to bound the note — cut the disclaimer off instead.
        aviso = _AVISO_CALIDAD.search(detalle)
        if aviso is None:
            contenido = None
        else:
            corte = detalle.lower().rfind("<table", 0, aviso.start())
            contenido = detalle[: corte if corte >= 0 else aviso.start()].strip() or None

    titulo = None
    if contenido:
        primero = _PARRAFO.search(contenido)
        titulo = _texto(primero.group(1)) if primero else None

    dia, mes, anio = fecha_pagina.groups()
    return {
        "messageCode": 200,
        "response": "OK",
        "fuente": FUENTE,
        "Nota": {
            "codNota": cod_nota,
            "fecha": f"{dia}-{mes}-{anio}",
            "titulo": titulo,
            "cadenaContenido": contenido,
            "existeHtml": "S" if contenido else "N",
            "fuente": FUENTE,
        },
    }


def hay_publicacion(respuesta: dict) -> bool:
    """Whether the DOF published that day, per a get_notas() response.

    True when notes were recovered *or* an edition exists with no digital
    index — both mean the gazette came out, which is the question a caller
    asks before recording a day as empty.
    """
    return bool(
        respuesta.get("edicionesSinIndice")
        or any(respuesta.get(clave) for clave in _EDICIONES.values())
    )


def cuenta_notas(respuesta: dict) -> int:
    """How many notes a get_notas() response carries, across every edition."""
    return sum(len(respuesta.get(clave, [])) for clave in _EDICIONES.values())

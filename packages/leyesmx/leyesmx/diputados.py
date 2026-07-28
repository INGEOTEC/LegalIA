"""Read the reform history that the Cámara de Diputados publishes per law.

LeyesBiblio (https://www.diputados.gob.mx/LeyesBiblio/) keeps, for every
federal law, a table of the decrees that have reformed it together with the
date each one was published in the Diario Oficial de la Federación. That
table is the curated, authoritative account of a law's evolution: the DOF
publishes the decrees, but only Diputados says which ones amend which law.

The Constitution has its own chronological page (`ref/cpeum_crono.htm`) whose
rows are numbered, so a reform can be cited as "reforma 284". Ordinary laws
use `ref/<abbr>.htm`.
"""

import re
import time
from dataclasses import dataclass
from pathlib import Path

import requests

BASE = "https://www.diputados.gob.mx/LeyesBiblio"
# LeyesBiblio serves Windows-1252, not UTF-8, and does not always say so.
ENCODING = "cp1252"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; LegalIA-leyesmx/0.1)"}

# The Constitution's chronological table is numbered; ordinary laws are not.
PAGINAS = {"cpeum": "ref/cpeum_crono.htm"}

_FECHA = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")
# Ordinary laws write the publication date inside the cell, either as
# "DOF DD-MM-YYYY" or bare as "DD-MM-YYYY" — both spellings are in use, often
# on the same page. The markup splits the date across spans often enough that
# the separators need slack.
_FECHA_DOF = re.compile(r"(?:DOF\s*)?(\d{2})\s*-\s*(\d{2})\s*-\s*(\d{4})")
# A row's date lives in the paragraph carrying the links to the decree, which
# is what keeps a date written into the decree's own prose from being read as
# its publication date.
_ENLACE_DECRETO = re.compile(r'href="[^"]+\.(?:pdf|doc)"', re.I)
_TR = re.compile(r"<tr[^>]*>.*?</tr>", re.S)
_TD = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_P = re.compile(r"<p\b.*?</p>", re.S)
_TAG = re.compile(r"<[^>]+>")
_HREF = re.compile(r'href="([^"]+)"', re.I)
# Diputados names each decree's file <LEY>_ref<N>_<ddmmmyy>. That number is
# more dependable than the table's own numbering column, which several pages
# leave out of the row (see _numero_de_reforma).
_ARCHIVO_REF = re.compile(r"_ref_?(\d+)_", re.I)
# Any other kind of instrument Diputados files in the same table.
_ARCHIVO_OTRO_TIPO = re.compile(
    r"_(?:cant|fe|sent|voto|decla|acuerdo|tarifa|acla|aclara|abro|engrose|"
    r"notifica|lista|mod|prev|aviso)_?\d*_\d{2}[a-z]{3}\d{2}", re.I)
# Where the reform table starts, and the label the original publication sits
# under in the header above it.
_ENCABEZADO_REFORMAS = "Decretos de Reforma"
_PUBLICACION_ORIGINAL = "Publicación Original"
# The reform table is not only reforms. Diputados files a dozen other kinds of
# instrument in it, each named after its kind and each numbered from 1 in the
# same column: `_cant` (restatements of peso amounts), `_fe` (errata), `_sent`
# and `_voto` (SCJN rulings), `_decla` (entry-into-force declarations),
# `_acuerdo`, `_tarifa`, `_acla`, `_abro`, `_notifica` and more. Two series
# sharing the numbering column is why `cnpp` and `ligie_2022` appeared to have
# duplicate reform numbers. A reform is a row linking a `_refNN_` file.
_NO_ES_REFORMA = re.compile(r"Actualización de cantidades|Sentencia de la SCJN", re.I)

# Month abbreviations Diputados uses in file names, e.g. LGPGIR_ref08_04jun14.
_MESES = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
          "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12}
_FECHA_ARCHIVO = re.compile(r"_(\d{2})(" + "|".join(_MESES) + r")(\d{2})\b", re.I)
# Diputados mirrors each decree as published in the DOF; the "_ima" variant is
# the scan of the gazette page, the plain one carries extractable text.
_ES_PDF = re.compile(r"\.pdf$", re.I)
# Diputados appends an editorial summary of what the decree did; it is not
# part of the decree's title.
_RESUMEN = re.compile(r"\bNota\s*:|\bNuevo\b")
_ENTIDADES = {
    "&nbsp;": " ", "&quot;": '"', "&amp;": "&", "&oacute;": "ó", "&aacute;": "á",
    "&eacute;": "é", "&iacute;": "í", "&uacute;": "ú", "&ntilde;": "ñ",
    "&Oacute;": "Ó", "&Aacute;": "Á", "&Eacute;": "É", "&Iacute;": "Í",
    "&Uacute;": "Ú", "&Ntilde;": "Ñ", "&deg;": "°", "&ordm;": "º",
}


@dataclass
class Reforma:
    """One decree that reformed a law, as Diputados records it.

    `no` is None for the row that is not a reform but the law's original
    publication, which the Constitution's table carries at the top.
    """

    no: int | None          # Diputados' own numbering (Constitution only)
    fecha: str              # DOF publication date, DD-MM-YYYY (as in dofjson)
    decreto: str            # decree title, editorial summary stripped
    ley: str = ""
    pdf: str = ""           # Diputados' own copy of the DOF publication


def pagina_de_reformas(ley: str) -> str:
    """URL of `ley`'s reform table (`ley` is its LeyesBiblio abbreviation)."""
    return f"{BASE}/{PAGINAS.get(ley, f'ref/{ley}.htm')}"


def _pide(url: str, timeout: int, intentos: int = 4) -> str:
    """Fetch a LeyesBiblio page, decoded out of its cp1252, retrying briefly.

    Reading all 316 laws is 316 requests, and the server resets a connection
    now and then; dropping a whole law over a transient failure would silently
    leave a hole in the catalogue.
    """
    for intento in range(intentos):
        try:
            r = requests.get(url, headers=_HEADERS, timeout=timeout)
            r.raise_for_status()
            return r.content.decode(ENCODING, errors="replace")
        except requests.exceptions.RequestException:
            if intento == intentos - 1:
                raise
            time.sleep(2 ** intento)


def descarga(ley: str, timeout: int = 60) -> str:
    """The reform page's HTML, decoded out of LeyesBiblio's cp1252."""
    return _pide(pagina_de_reformas(ley), timeout)


def _texto(celda: str) -> str:
    t = _TAG.sub(" ", celda)
    for k, v in _ENTIDADES.items():
        t = t.replace(k, v)
    return re.sub(r"\s+", " ", t).strip()


def _pdf_del_decreto(fila: str, ley: str) -> str:
    """URL of Diputados' copy of the decree, preferring the text-bearing PDF
    over the `_ima` scan of the gazette page."""
    pdfs = [h for h in _HREF.findall(fila) if _ES_PDF.search(h)]
    if not pdfs:
        return ""
    texto = [h for h in pdfs if "_ima" not in h.lower()]
    elegido = (texto or pdfs)[0]
    if elegido.startswith("http"):
        return elegido
    return f"{BASE}/{PAGINAS.get(ley, f'ref/{ley}.htm').rsplit('/', 1)[0]}/{elegido}"


def _ordena(reformas: list[Reforma]) -> list[Reforma]:
    """Oldest first, ties broken by Diputados' own numbering."""
    reformas.sort(key=lambda r: (r.fecha[-4:], r.fecha[3:5], r.fecha[:2], r.no or -1))
    return reformas


def _parse_crono(html: str, ley: str) -> list[Reforma]:
    """The Constitution's chronological table: `No. | Decreto | DD/MM/YYYY | …`,
    each field in its own cell."""
    reformas = []
    for fila in _TR.findall(html):
        celdas = [_texto(c) for c in _TD.findall(fila)]
        fecha = next((m for m in (_FECHA.match(c) for c in celdas) if m), None)
        if fecha is None:
            continue
        d, mo, y = fecha.groups()
        no = int(celdas[0]) if celdas and celdas[0].isdigit() else None
        decreto = next((c for c in celdas if len(c) > 25), "")
        reformas.append(
            Reforma(no=no, fecha=f"{d}-{mo}-{y}",
                    decreto=_RESUMEN.split(decreto)[0].strip(), ley=ley,
                    pdf=_pdf_del_decreto(fila, ley))
        )
    return _ordena(reformas)


def _fecha_de_publicacion(celda: str) -> str | None:
    """The DOF date a reform row publishes on, as DD-MM-YYYY.

    Read from the paragraph holding the links to the decree, since that is
    where the date belongs; a date written into the decree's own title would
    otherwise be mistaken for it. Falls back to the whole cell for rows whose
    paragraphs the markup does not delimit cleanly.
    """
    for parrafo in _P.findall(celda):
        if not _ENLACE_DECRETO.search(parrafo):
            continue
        m = _FECHA_DOF.search(_texto(parrafo))
        if m:
            d, mo, y = m.groups()
            return f"{d}-{mo}-{y}"
    m = _FECHA_DOF.search(_texto(celda))
    if m:
        d, mo, y = m.groups()
        return f"{d}-{mo}-{y}"
    return _fecha_del_archivo(celda)


def _fecha_del_archivo(celda: str, hoy: int = 2026) -> str | None:
    """The date encoded in the decree's file name, e.g. `_ref08_04jun14`.

    A fallback for the occasional typo on Diputados' side: `lgpgir`'s reform 8
    is written "DOF 04-06-214", a year short a digit, while the file it links
    says `04jun14`. The two-digit year is read as this century when that would
    not put the reform in the future, and as the last one otherwise — a
    gazette cannot publish a decree that has not happened yet.
    """
    for href in _HREF.findall(celda):
        m = _FECHA_ARCHIVO.search(href)
        if not m:
            continue
        d, mes, yy = m.group(1), m.group(2).lower(), int(m.group(3))
        año = 2000 + yy if 2000 + yy <= hoy else 1900 + yy
        return f"{d}-{_MESES[mes]:02d}-{año}"
    return None


def _numero_de_reforma(fila: str, celda_no: str) -> int | None:
    """Diputados' number for a reform row, or None if the row is not a reform.

    The two sources of the number answer different questions, so each is used
    for what it is good at.

    Whether the row is a reform is decided by the file it links: the table also
    carries a dozen other kinds of instrument (see _NO_ES_REFORMA), each
    numbered from 1 in the same column, and reading those as reforms is what
    made `cnpp` and `ligie_2022` look like they had duplicate numbers.

    Which reform it is comes from the numbering column, which is the more
    accurate of the two where they disagree — both cases in LeyesBiblio are a
    row linking the wrong file: `lgpsedmtp`'s reform 4 links reform 3's PDF,
    and `loapf`'s reform 47 links a file belonging to another law entirely.
    The file name is the fallback, and a needed one: plenty of rows leave the
    column empty though the row is plainly a decree — `reg_senado` does it for
    reforms 23-29, `lft` for 35-36.
    """
    del_archivo = next(
        (m for m in (_ARCHIVO_REF.search(h) for h in _HREF.findall(fila)) if m), None
    )
    if del_archivo is None and _ARCHIVO_OTRO_TIPO.search(fila):
        return None
    if celda_no.isdigit():
        return int(celda_no)
    return int(del_archivo.group(1)) if del_archivo else None


def _parse_ref(html: str, ley: str) -> list[Reforma]:
    """An ordinary law's page: the original publication, then a table of
    decrees whose row holds the title and the date together in one cell.

    Only numbered rows are reforms. The unnumbered ones restate peso amounts
    rather than amend the law, and Diputados does not number them either.
    "Fe de erratas" entries are corrections, not reforms, and are left out.
    """
    corte = html.find(_ENCABEZADO_REFORMAS)
    cabeza, cuerpo = (html[:corte], html[corte:]) if corte > 0 else (html, "")

    reformas = []
    # The original publication is index 0, as the Constitution's table has it.
    # It is located by its label: on pages that lack one, the first date in the
    # header belongs to something else (`ccom`'s is a peso-amount update).
    etiqueta = cabeza.find(_PUBLICACION_ORIGINAL)
    if etiqueta >= 0:
        original = _FECHA_DOF.search(_texto(cabeza[etiqueta:]))
        if original:
            d, mo, y = original.groups()
            reformas.append(Reforma(no=None, fecha=f"{d}-{mo}-{y}",
                                    decreto="Publicación original", ley=ley,
                                    pdf=_pdf_del_decreto(cabeza[etiqueta:], ley)))

    for fila in _TR.findall(cuerpo):
        celdas = _TD.findall(fila)
        if len(celdas) < 2:
            continue
        texto_fila = _texto(celdas[1])
        if _NO_ES_REFORMA.search(texto_fila):
            continue
        fecha = _fecha_de_publicacion(celdas[1])
        if fecha is None:
            continue
        no = _numero_de_reforma(fila, _texto(celdas[0]))
        if no is None:
            continue
        # The row's paragraphs are the decree's title and then its links; the
        # title is the first that is prose rather than a date line.
        parrafos = [_texto(p) for p in _P.findall(celdas[1])]
        decreto = next(
            (p for p in parrafos if len(p) > 25 and not _FECHA_DOF.search(p)),
            texto_fila,
        )
        reformas.append(
            Reforma(no=no, fecha=fecha,
                    decreto=_RESUMEN.split(decreto)[0].strip(), ley=ley,
                    pdf=_pdf_del_decreto(fila, ley))
        )
    return _ordena(reformas)


def parse_reformas(html: str, ley: str = "") -> list[Reforma]:
    """Every reform in a LeyesBiblio page, oldest first.

    Dates are rewritten to DD-MM-YYYY so they join directly against
    `dofjson`'s `fecha`. Two layouts exist and are told apart by content: the
    Constitution's chronological table puts the date in its own cell, while
    every ordinary law writes it as "DOF DD-MM-YYYY" inside the same cell as
    the decree's title.
    """
    if _ENCABEZADO_REFORMAS in html:
        return _parse_ref(html, ley)
    return _parse_crono(html, ley)


@dataclass
class Ley:
    """One law in LeyesBiblio's catalogue."""

    no: int                 # Diputados' position in the index (1 = the Constitution)
    abrev: str              # LeyesBiblio abbreviation; `pagina_de_reformas` takes it
    nombre: str


_INDICE = "index.htm"
_ENLACE_REF = re.compile(r'href="ref/([A-Za-z0-9_]+)\.htm"', re.I)
_SOLO_DIGITOS = re.compile(r"^\d{1,3}$")


def descarga_indice(timeout: int = 60) -> str:
    """LeyesBiblio's index page, decoded out of its cp1252."""
    return _pide(f"{BASE}/{_INDICE}", timeout)


def lista_leyes(html: str) -> list[Ley]:
    """Every law the index lists, in its own order.

    A catalogue row is numbered and links to the law's reform page; rows
    without both are layout. The Constitution comes first, as number 1, and
    the abbreviation is what `pagina_de_reformas()` resolves — for the
    Constitution that is remapped to its chronological table (see PAGINAS).
    """
    leyes, vistos = [], set()
    for fila in _TR.findall(html):
        celdas = _TD.findall(fila)
        if len(celdas) < 2:
            continue
        no = _texto(celdas[0])
        enlace = _ENLACE_REF.search(fila)
        if enlace is None or not _SOLO_DIGITOS.match(no):
            continue
        abrev = enlace.group(1)
        if abrev in vistos:
            continue
        vistos.add(abrev)
        # The cell holds the law's name and then its publication dates; the
        # name is the linked text.
        nombre = _texto(re.sub(r"</a>.*", "", celdas[1], flags=re.S))
        leyes.append(Ley(no=int(no), abrev=abrev, nombre=nombre))
    return leyes


def descarga_decreto(reforma: Reforma, dest: Path, timeout: int = 120) -> Path:
    """Download Diputados' copy of a reform's decree, as published in the DOF.

    A second, independent route to the primary source: Diputados mirrors every
    decree it lists, so a reform stays reachable even when the DOF's own
    service cannot serve the day it was published — as happens with the
    Constitution's reform 139 of 08-03-1999.
    """
    if not reforma.pdf:
        raise ValueError(f"la reforma {reforma.no} no trae PDF en LeyesBiblio")
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = requests.get(reforma.pdf, headers=_HEADERS, timeout=timeout)
    r.raise_for_status()
    dest.write_bytes(r.content)
    return dest

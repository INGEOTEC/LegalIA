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
_TR = re.compile(r"<tr[^>]*>.*?</tr>", re.S)
_TD = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_TAG = re.compile(r"<[^>]+>")
_HREF = re.compile(r'href="([^"]+)"', re.I)
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


def descarga(ley: str, timeout: int = 60) -> str:
    """The reform page's HTML, decoded out of LeyesBiblio's cp1252."""
    r = requests.get(pagina_de_reformas(ley), headers=_HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.content.decode(ENCODING, errors="replace")


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


def parse_reformas(html: str, ley: str = "") -> list[Reforma]:
    """Every reform in a LeyesBiblio table, oldest first.

    Rows are `No. | Decreto | DD/MM/YYYY | PDF Word`; the header and any
    layout row lacking a date are skipped. Dates are rewritten to DD-MM-YYYY
    so they join directly against `dofjson`'s `fecha`.
    """
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
    reformas.sort(key=lambda r: (r.fecha[-4:], r.fecha[3:5], r.fecha[:2], r.no or -1))
    return reformas


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

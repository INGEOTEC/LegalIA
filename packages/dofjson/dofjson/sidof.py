"""SIDOF's own raw JSON/PDF/image endpoints — nothing else.

This module only ever talks to SIDOF and never calls anything else in this
package: no dofjson.api, no dofjson.notas, no dofjson.dofweb. Anything that
needs to resolve a bare codNota with a dofweb fallback, or work with a
notas-shaped dict once it is in hand (infer_paginas(), quita_notas_sin_titulo(),
_detectar_offset_paginacion() — see dofjson.notas), lives in dofjson.api
instead, which is the only module allowed to depend on this one *and* on
dofweb. Keeping the dependency one-directional (api -> sidof, never the
reverse) is what makes dofjson.sidof safe to call directly for the one thing
it is for: the raw SIDOF REST calls themselves.
"""
import datetime as dt
from pathlib import Path

import requests

BASE_URL = "https://sidof.segob.gob.mx/dof/sidof"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; DOF-JSON-Client/1.0)"}


def _get(path: str, timeout: int = 30) -> dict:
    response = requests.get(f"{BASE_URL}/{path}", headers=_HEADERS, timeout=timeout)
    response.raise_for_status()
    return response.json()


def get_diario(date: dt.date) -> dict:
    """Edition metadata (Matutina/Vespertina/Extraordinaria) for a given date."""
    return _get(f"diarios/porFecha/{date:%d-%m-%Y}")


def get_notas(date: dt.date) -> dict:
    """List of notes/documents published on a given date."""
    return _get(f"notas/{date:%d-%m-%Y}")


def get_nota(cod_nota: int) -> dict:
    """Full detail of a single note, including its HTML content."""
    return _get(f"notas/nota/{cod_nota}")


def get_indicadores(date: dt.date) -> dict:
    """Economic indicators (exchange rate, TIIE, UDIS) for a given date."""
    return _get(f"indicadores/{date:%d-%m-%Y}")


def download_pdf(cod_diario: int, dest: Path, timeout: int = 60) -> None:
    """Download the PDF for a whole edition (there is no per-note PDF; use the
    `pagina`/`paginaHasta` fields from get_nota() to locate a note within it)."""
    response = requests.get(
        f"{BASE_URL}/documentos/pdf/{cod_diario}", headers=_HEADERS, timeout=timeout
    )
    response.raise_for_status()
    if not response.content.startswith(b"%PDF"):
        raise ValueError(f"Response is not a valid PDF for codDiario={cod_diario}")
    dest.write_bytes(response.content)


def get_imagenes(cod_diario: int) -> dict:
    """Per-page scanned image listing for a whole edition (codImagen, pagina,
    nombreArchivo). Match `pagina` against a note's own `pagina` field to find
    its page, then pass `nombreArchivo` and the note's `codEdicion` to
    download_imagen()."""
    return _get(f"imagenesFsRecurso/obtieneImagenesFS/{cod_diario}")


def download_imagen(nombre_archivo: str, edicion: str, dest: Path, timeout: int = 60) -> None:
    """Download a single scanned page as JPEG (a 300dpi certified copy)."""
    response = requests.get(
        f"{BASE_URL}/copiaCertificada/{edicion}/{nombre_archivo}.jpg",
        headers=_HEADERS,
        timeout=timeout,
    )
    response.raise_for_status()
    if not response.content.startswith(b"\xff\xd8\xff"):
        raise ValueError(f"Response is not a valid JPEG for {nombre_archivo}")
    dest.write_bytes(response.content)

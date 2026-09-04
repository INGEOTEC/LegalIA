"""The provenance header `scjn.api.cabecera` writes at the top of every
snapshot, and reading back what a previous crawl already wrote to disk.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


def _fecha(cadena: str) -> datetime:
    return datetime.strptime(cadena, "%d-%m-%Y")


_CABECERA_CAMPO = re.compile(r"^([a-z_]+):\s*(.*)$")


def lee_cabecera(archivo: Path) -> dict:
    """The provenance header `scjn_api.cabecera` writes at the top of
    `archivo`, back
    as a dict (`fuente`, `nombre_buscado`, `ordenamiento`,
    `fecha_publicacion`, and whichever of `fecha_expedicion`/`categoria` that
    snapshot's row had) — reading back a file a previous crawl run already
    wrote, without re-fetching it. `nombre_buscado` is absent on a file a
    crawl wrote before issue #124 added it, same as `ratio_similitud`/
    `sospechoso` for issue #115 — and, since issue #132, also absent on a
    file whose `ordenamiento` was already identical to what was searched
    for, which is not a missing field but `scjn_api.cabecera` declining to write a
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
    file `scjn_api.descarga_ordenamiento` wrote it to."""

    fecha_publicacion: str
    archivo: Path


def _orden_repeticion(version: "VersionInstrumento") -> int:
    """The `-N` suffix `scjn_api.descarga_ordenamiento` appends to the 2nd+
    file of a
    repeated `fecha_publicacion` (see its own docstring), as a sort key: 1
    for a plain `<fecha>.md` (no suffix), N for `<fecha>-N.md`. `fecha` is
    known already (the header, not the filename, is the source of truth),
    so only the part of the stem after it is a repetition suffix — the
    date's own dashes never get mistaken for one."""
    resto = version.archivo.stem[len(version.fecha_publicacion) :]
    return int(resto[1:]) if resto else 1


def versiones_de_directorio(outdir: Path) -> list[VersionInstrumento]:
    """Every snapshot `scjn_api.descarga_ordenamiento` has already written to
    `outdir`, oldest first — read back from each file's own header rather
    than re-crawling, so a later Fase 2 pass can run over a crawl's output
    independently of the crawl itself."""
    versiones = [
        VersionInstrumento(lee_cabecera(archivo)["fecha_publicacion"], archivo)
        for archivo in outdir.glob("*.md")
    ]
    return sorted(versiones, key=lambda v: (_fecha(v.fecha_publicacion), _orden_repeticion(v)))


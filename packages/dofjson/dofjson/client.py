import datetime as dt
import json
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


_EDICION_LISTAS = {
    "MAT": "NotasMatutinas",
    "VES": "NotasVespertinas",
    "EXT": "NotasExtraordinarias",
}


def infer_paginas(nota: dict, notas_del_dia: dict) -> list[int]:
    """Infer which page(s) a note occupies, using the fact that notes are
    published one after another: if the next note (in publication order)
    starts on the same page, this note is confined to a single page; if it
    starts on a later page, this note is assumed to span through that page
    too.

    Publication order is reconstructed by sorting on `codNota`, which is a
    per-edition sequential id — verified empirically to track page order
    (e.g. on 02-01-1980, codNota ascending gives pagina 2,2,2,3,4,6,...,26,
    never decreasing). The `orden` field is NOT reliable for this: it does
    not correlate with page order at all in that same sample.
    """
    lista = notas_del_dia[_EDICION_LISTAS[nota["codEdicion"]]]
    ordenada = sorted(lista, key=lambda n: n["codNota"])
    idx = next(i for i, n in enumerate(ordenada) if n["codNota"] == nota["codNota"])

    pagina_inicio = nota["pagina"]
    pagina_fin = pagina_inicio
    if idx + 1 < len(ordenada):
        siguiente_pagina = ordenada[idx + 1]["pagina"]
        if siguiente_pagina > pagina_inicio:
            pagina_fin = siguiente_pagina

    return list(range(pagina_inicio, pagina_fin + 1))


def download_nota(cod_nota: int, outdir: Path) -> list[Path]:
    """Download a note's content by codNota alone: saves its metadata (incl.
    cadenaContenido) as JSON when the HTML content exists; otherwise falls
    back to downloading the scanned page image(s) for that note, inferring
    whether it spans more than one page (see infer_paginas())."""
    nota = get_nota(cod_nota)["Nota"]
    outdir.mkdir(parents=True, exist_ok=True)

    if nota.get("cadenaContenido"):
        dest = outdir / f"nota-{cod_nota}.json"
        dest.write_text(json.dumps({"Nota": nota}, ensure_ascii=False, indent=2))
        return [dest]

    fecha = dt.datetime.strptime(nota["fecha"], "%d-%m-%Y").date()
    paginas = infer_paginas(nota, get_notas(fecha))
    imagenes_por_pagina = {img["pagina"]: img for img in get_imagenes(nota["codDiario"])["imagenesFS"]}

    dests = []
    for pagina in paginas:
        imagen = imagenes_por_pagina.get(pagina)
        if imagen is None:
            raise ValueError(
                f"nota {cod_nota} has no cadenaContenido and no matching page image "
                f"(codDiario={nota['codDiario']}, pagina={pagina})"
            )
        dest = outdir / f"nota-{cod_nota}-{imagen['nombreArchivo']}.jpg"
        download_imagen(imagen["nombreArchivo"], nota["codEdicion"], dest)
        dests.append(dest)
    return dests

import datetime as dt
import json
import re
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
    """
    lista = notas_del_dia[_EDICION_LISTAS[nota["codEdicion"]]]
    ordenada = sorted(lista, key=lambda n: n["codNota"])
    idx = next(i for i, n in enumerate(ordenada) if n["codNota"] == nota["codNota"])

    pagina_inicio = nota["pagina"]
    if len(ordenada) == idx + 1:
        return [pagina_inicio]
    
    pagina_sig = ordenada[idx + 1]["pagina"]
    if pagina_inicio == pagina_sig:
        return [pagina_inicio]
    return list(range(pagina_inicio, pagina_sig + 1))


def quita_notas_sin_titulo(notas_del_dia: dict) -> dict:
    """Drop notes with no `titulo` from a get_notas() response, for building
    a clean per-day note index. Most are stub duplicates of an adjacent,
    same-page note (existeHtml "S" but existeDoc "N" — see infer_paginas());
    the rest are genuine image-only notes (existeHtml "N") with no digital
    text at all. Do NOT use this on the notas_del_dia passed into
    infer_paginas()/download_nota(): those rely on stub entries being
    present to compute page spans."""
    filtrado = dict(notas_del_dia)
    for clave in _EDICION_LISTAS.values():
        if clave in filtrado:
            filtrado[clave] = [n for n in filtrado[clave] if n.get("titulo")]
    return filtrado


def download_nota_imagenes(
    cod_nota: int, outdir: Path, nota: dict | None = None
) -> list[Path]:
    """Download the scanned page image(s) for a note by codNota, inferring
    whether it spans more than one page (see infer_paginas()).

    Unlike download_nota(), this ALWAYS fetches the page images, even for a
    note that also has digital HTML content (cadenaContenido / existeHtml
    "S"). That is what makes the image→OCR path (dof2md) available for every
    note, not only the image-only ones — the scanned page is the certified
    original, and OCR'ing it is a way to get a note's Markdown that does not
    depend on the HTML being present or well-formed.

    Pass an already-fetched `nota` (the value under the "Nota" key of a
    get_nota() response) to avoid an extra request when the caller already
    has it.

    A page already present in `outdir` from an earlier call (same codNota,
    same outdir) is not re-downloaded — only checked for by name, so the
    day's notes/imagenes metadata is still fetched to work out which page(s)
    this note occupies and their file names."""
    if nota is None:
        nota = get_nota(cod_nota)["Nota"]
    outdir.mkdir(parents=True, exist_ok=True)

    fecha = dt.datetime.strptime(nota["fecha"], "%d-%m-%Y").date()
    paginas = infer_paginas(nota, get_notas(fecha))
    imagenes_por_pagina = {img["pagina"]: img for img in get_imagenes(nota["codDiario"])["imagenesFS"]}

    dests = []
    for pagina in paginas:
        imagen = imagenes_por_pagina.get(pagina)
        if imagen is None:
            raise ValueError(
                f"nota {cod_nota} has no matching page image "
                f"(codDiario={nota['codDiario']}, pagina={pagina})"
            )
        dest = outdir / f"nota-{cod_nota}-{imagen['nombreArchivo']}.jpg"
        if not dest.exists():
            download_imagen(imagen["nombreArchivo"], nota["codEdicion"], dest)
        dests.append(dest)
    return dests


_PAGINA_HEADER_WINDOW = 120


def _detectar_offset_paginacion(reader, paginas_conocidas: set[int]) -> int | None:
    """Best-effort: work out the (printed page number - physical index)
    offset of an edition PDF, by looking for one of the day's known printed
    `pagina` numbers near the top of each physical page's extracted text.

    Modern editions restart their own printed numbering at 1 on the
    edition's cover, so `pagina - 1` is already a valid physical index
    (offset 1). But old digitized volumes often carry a running page count
    from a bound "tomo" spanning many editions (issue #95): their first
    physical page prints no visible number at all, and later pages resume a
    much larger count. Matching the day's actual page numbers against what
    each physical page prints works for both, instead of assuming offset 1.
    """
    votos: dict[int, int] = {}
    for indice, page in enumerate(reader.pages):
        texto = (page.extract_text() or "")[:_PAGINA_HEADER_WINDOW]
        for numero in re.findall(r"\d{1,5}", texto):
            numero = int(numero)
            if numero in paginas_conocidas:
                offset = numero - indice
                votos[offset] = votos.get(offset, 0) + 1
    if not votos:
        return None
    return max(votos, key=votos.get)


def _edicion_pdf_cacheada(cod_diario: int, outdir: Path, timeout: int = 60) -> Path:
    """The whole edition's PDF, cached in `outdir` as `edicion-{cod_diario}.pdf`
    instead of being downloaded into a throwaway tempdir — a second note from
    the same edition (same day, same codDiario) reuses the file already on
    disk instead of fetching the whole edition again."""
    dest = outdir / f"edicion-{cod_diario}.pdf"
    if not dest.exists():
        download_pdf(cod_diario, dest, timeout=timeout)
    return dest


def download_nota_pdf(
    cod_nota: int, outdir: Path, nota: dict | None = None
) -> Path:
    """Download a note as its OWN PDF: fetches the whole edition's PDF and
    slices out only the page(s) the note occupies (see infer_paginas()),
    writing them to `outdir/nota-{cod_nota}.pdf`.

    There is no per-note PDF endpoint — the DOF only serves the full edition
    (download_pdf) — so this is the note-scoped counterpart of
    download_nota_imagenes(): a PDF holding just the note's pages, ready to
    hand to dof2md. Works for any note, with or without HTML content.

    The edition PDF itself is cached in `outdir` (see
    _edicion_pdf_cacheada()) rather than downloaded-and-discarded per note,
    so slicing out another note from the same edition later does not
    re-fetch it. If `outdir/nota-{cod_nota}.pdf` already exists, this
    returns it right away without any network call at all — not even
    get_nota() for `nota` — which is what makes it safe to call again on a
    directory a previous run (or download_nota_imagen_o_pdf()) already
    populated.

    Note: the note's printed `pagina` numbers are matched against the
    edition PDF's own printed page numbers (see
    _detectar_offset_paginacion()) to work out the physical PDF page index,
    rather than assuming `pagina - 1` always is one (see issue #95).

    Pass an already-fetched `nota` to skip an extra get_nota() request."""
    from pypdf import PdfReader, PdfWriter

    outdir.mkdir(parents=True, exist_ok=True)
    dest = outdir / f"nota-{cod_nota}.pdf"
    if dest.exists():
        return dest

    if nota is None:
        nota = get_nota(cod_nota)["Nota"]

    fecha = dt.datetime.strptime(nota["fecha"], "%d-%m-%Y").date()
    notas_del_dia = get_notas(fecha)
    paginas = infer_paginas(nota, notas_del_dia)
    paginas_conocidas = {
        n["pagina"] for n in notas_del_dia[_EDICION_LISTAS[nota["codEdicion"]]]
    }

    edicion_pdf = _edicion_pdf_cacheada(nota["codDiario"], outdir)
    reader = PdfReader(str(edicion_pdf))
    offset = _detectar_offset_paginacion(reader, paginas_conocidas)
    if offset is None:
        # No page's own printed text corroborated an offset at all — some
        # scanned editions carry no extractable text layer whatsoever, so
        # _detectar_offset_paginacion never gets a vote to work with.
        # Assuming offset 1 (modern-edition numbering) is a worse guess than
        # the smallest pagina the day's own notes/imagenes index reports:
        # that page is, by construction, the edition's own first physical
        # page — see get_notas()'s own `pagina` field, sorted.
        offset = min(paginas_conocidas) if paginas_conocidas else 1
    writer = PdfWriter()
    for pagina in paginas:
        indice = pagina - offset
        if indice < 0 or indice >= len(reader.pages):
            raise ValueError(
                f"nota {cod_nota}: página {pagina} fuera del PDF de la edición "
                f"(codDiario={nota['codDiario']}, {len(reader.pages)} páginas)"
            )
        writer.add_page(reader.pages[indice])
    with dest.open("wb") as f:
        writer.write(f)
    return dest


def download_nota_imagen_o_pdf(
    cod_nota: int, outdir: Path, nota: dict | None = None
) -> list[Path]:
    """Download whatever it takes to OCR a note beyond its HTML: the scanned
    page image(s) (download_nota_imagenes()) when SIDOF has one for the
    note's page, or — when it does not (the ValueError download_nota_imagenes
    raises for a page with no matching image) — the *whole edition's* PDF,
    cached in `outdir` as `edicion-{codDiario}.pdf` (see
    _edicion_pdf_cacheada()), left uncut. Returns the resulting path(s) as a
    list either way (one edition path, wrapped, in the fallback case) so a
    caller does not need to know which of the two happened.

    This deliberately does NOT slice the edition down to just this note's
    page(s) the way download_nota_pdf() does — that needs the note's
    physical page position worked out (_detectar_offset_paginacion(), or a
    get_notas()-based equivalent), which is OCR/cutting work, not
    downloading: this function is meant for bulk-downloading everything a
    batch of notes without HTML needs, before any OCR happens at all.
    Working out a note's page position from a *running, multi-edition*
    pagina count (issue #95) can itself fail before there is even a PDF
    reader in the picture — e.g. a note's own pagina can fall outside the
    single day's image listing entirely — which is one more reason that
    work has no business happening at this stage. Call download_nota_pdf()
    directly (e.g. from nota2md.legal_provisions(..., source="pdf")) when a
    per-note, pre-cut PDF is actually needed for OCR.

    Meant for bulk-downloading every note a collection's historial needs
    that has no usable HTML, into one `outdir` per run: the edition PDF
    this function (or download_nota_pdf(), later) fetches is cached there,
    so later notes from the same day reuse it instead of re-downloading it.

    This function itself also skips straight to whatever is already on
    disk with NO network call — not even get_nota() for `nota` — when it
    can: it checks for any `nota-{cod_nota}-*.jpg` download_nota_imagenes()
    may have left behind from a previous run. There is no equivalent
    shortcut for the PDF fallback here, since which edition a note belongs
    to is only known once `nota` itself has been fetched.
    """
    imagenes_existentes = sorted(outdir.glob(f"nota-{cod_nota}-*.jpg"))
    if imagenes_existentes:
        return imagenes_existentes

    if nota is None:
        nota = get_nota(cod_nota)["Nota"]
    try:
        return download_nota_imagenes(cod_nota, outdir, nota=nota)
    except ValueError:
        outdir.mkdir(parents=True, exist_ok=True)
        return [_edicion_pdf_cacheada(nota["codDiario"], outdir)]


def download_nota(cod_nota: int, outdir: Path) -> list[Path]:
    """Download a note's content by codNota alone: saves its metadata (incl.
    cadenaContenido) as JSON when the HTML content exists; otherwise falls
    back to downloading the scanned page image(s) for that note (see
    download_nota_imagenes()). To always get the page images regardless of
    whether HTML content exists, call download_nota_imagenes() directly."""
    nota = get_nota(cod_nota)["Nota"]
    outdir.mkdir(parents=True, exist_ok=True)

    if nota.get("cadenaContenido"):
        dest = outdir / f"nota-{cod_nota}.json"
        dest.write_text(json.dumps({"Nota": nota}, ensure_ascii=False, indent=2))
        return [dest]

    return download_nota_imagenes(cod_nota, outdir, nota=nota)

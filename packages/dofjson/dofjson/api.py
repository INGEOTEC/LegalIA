"""Unified entry point for reading and downloading DOF content — and the one
place that is allowed to know dofjson.sidof (SIDOF) and dofjson.dofweb
(the DOF's own website) both exist. Every other function, in this package
(dofjson.archivo, dofjson.cli) or another one (nota2md, leyesmx...), calls
just this instead of juggling sidof/dofweb itself, which is the bug this
module exists to close: a caller that only ever calls ``sidof`` and never
considers that the day/note could be sitting in ``dofweb`` instead.

get_notas() also carries the day-level policy dofjson.archivo needs for its
whole-archive download (``respaldo``: whether an empty SIDOF answer is even
worth double-checking) and tags its result with `fuente` — FUENTE_SIDOF or
FUENTE_WEB — so a caller like archivo.procesar_dia() can tell which source
answered (and, from the shape of what comes back, whether it actually had
anything) without reaching for dofweb-specific knowledge of its own. Every
other name below (RESPALDO_OPCIONES, tiene_notas, consultar_respaldo,
cuenta_notas) is re-exported for exactly that: so archivo
and cli need nothing from dofjson.sidof/dofjson.dofweb directly either.

download_nota_imagenes()/download_nota_pdf()/download_nota_imagen_o_pdf()/
download_nota() live here too, not in dofjson.sidof, even though what they
download is always SIDOF's (a dofweb-recovered note carries no codDiario/
pagina to locate a scanned page or PDF from): resolving a bare `cod_nota`
into its record still has to go through get_nota() above to give a clear
error for a dofweb-only note instead of crashing later on a missing field,
and dofjson.sidof has no business calling back into this module to do that
-- dofjson.sidof stays a plain, dependency-free SIDOF REST client, only
ever depended on, never depending on anything else in this package.

get_notas() also accepts a `cache_dir`: when given, and that date's day
index is already sitting in one of the notas-archivo assets download_dof_assets()
put there, it is read straight off disk (dofjson.titulos.nota_del_dia_en_cache())
instead of asking SIDOF (or dofweb) at all -- this is where "reduce calls to
SIDOF when the answer is already in a directory" is decided. get_nota() has
no matching `cache_dir` parameter: the archive only ever holds the daily
INDEX (dofjson.archivo saves it after quita_notas_sin_titulo, exactly what a
cache hit here returns), never a note's own cadenaContenido -- that always
requires the one-note SIDOF/dofweb request get_nota() already makes.
download_dof_assets() itself is re-exported below so a caller that only
imports dofjson.api (the intended single entry point) can build/refresh that
cache_dir without reaching into dofjson.titulos directly."""

import datetime as dt
import json
from pathlib import Path

import requests

from dofjson import dofweb, sidof, titulos
from dofjson.notas import (
    EDICION_LISTAS,
    _detectar_offset_paginacion,
    infer_paginas,
    quita_notas_sin_titulo
)
from dofjson.titulos import download_dof_assets, nota_del_dia_en_cache

FUENTE_SIDOF = "sidof"
FUENTE_WEB = dofweb.FUENTE

#: How eagerly get_notas() double-checks an empty SIDOF answer against the
#: DOF website. "todos" (get_notas()'s own default) always checks. "habiles"
#: only checks Mon-Fri — dofjson.archivo's own default for its ~40,000-day
#: range, where a check is a real extra request and every confirmed loss so
#: far falls on a weekday anyway. "nunca" trusts SIDOF alone.
RESPALDO_OPCIONES = ("habiles", "todos", "nunca")

#: How many notes a get_notas()-shaped response carries, across every
#: edition — re-exported as-is; dofweb.py's own docstring covers it.
cuenta_notas = dofweb.cuenta_notas


def tiene_notas(notas: dict) -> bool:
    """Whether a get_notas()-shaped response carries any note at all."""
    return any(notas.get(clave) for clave in EDICION_LISTAS.values())


def _validar_respaldo(respaldo: str) -> None:
    if respaldo not in RESPALDO_OPCIONES:
        raise ValueError(f"respaldo debe ser uno de {RESPALDO_OPCIONES}, no {respaldo!r}")


def consultar_respaldo(fecha: dt.date, respaldo: str) -> bool:
    """Whether an empty SIDOF answer for this date is worth double-checking
    against the DOF website, per `respaldo` (see RESPALDO_OPCIONES)."""
    _validar_respaldo(respaldo)
    if respaldo == "nunca":
        return False
    if respaldo == "todos":
        return True
    return fecha.weekday() < 5


def get_nota(cod_nota: int) -> dict:
    """A note by its codNota, from SIDOF or — when SIDOF has no record of it
    at all — the DOF website (see dofjson.dofweb).

    Tagged with `fuente` (FUENTE_SIDOF or FUENTE_WEB), naming which source
    the returned record actually is — same convention as get_notas().

    SIDOF answers ``{"Nota": []}``, not an error, for a codNota it lacks —
    that empty answer is what sends the lookup to the website. Raises
    ValueError if neither source has the note.
    """
    nota = sidof.get_nota(cod_nota).get("Nota")
    if isinstance(nota, dict) and nota:
        nota["fuente"] = FUENTE_SIDOF
        return nota

    nota = dofweb.get_nota(cod_nota).get("Nota")
    if isinstance(nota, dict) and nota:
        return nota

    raise ValueError(f"nota {cod_nota} does not exist in SIDOF nor in {FUENTE_WEB}")


def get_notas(
    date: dt.date, *, respaldo: str = "todos", cache_dir: Path | None = titulos.SIN_CACHE_DIR
) -> dict:
    """A day's notes index, always tagged with `fuente` (FUENTE_SIDOF or
    FUENTE_WEB) naming which source the returned data actually is — not
    just which one has notes: a day with no edition (a weekend, a holiday,
    or one dofweb also reports nothing for) still comes back tagged
    FUENTE_SIDOF, since that is whose (empty) answer is being returned.

    `respaldo` controls when an empty SIDOF answer is worth double-checking
    against the DOF website (see RESPALDO_OPCIONES, and the days SIDOF
    loses, in dofjson's README) — "todos" (the default here) always checks;
    dofjson.archivo passes "habiles"/"nunca" for its own batch download.

    `cache_dir` is checked first (dofjson.titulos.nota_del_dia_en_cache()):
    when `date`'s day index is already sitting in a notas-archivo asset there
    (see download_dof_assets()), it is returned straight off disk and neither
    SIDOF nor dofweb is ever asked. A miss (asset not downloaded, or `date`
    not archived yet) falls through to the ordinary SIDOF/dofweb lookup below.
    Left unset, it defaults to the package-wide `dofjson.titulos.CACHE_DIR` —
    every caller gets the cache for free without naming a directory; pass
    `cache_dir=None` explicitly to skip the cache for just this call.

    SIDOF's own 404 for a date outside its coverage is treated exactly like
    its ordinary 200-with-nothing answer — both mean "the fallback is worth
    a look" — instead of raising.

    Title-less stub entries (dofjson.notas.quita_notas_sin_titulo) are
    dropped either way, so what is left is real, browsable notes.
    """
    _validar_respaldo(respaldo)

    if cache_dir is titulos.SIN_CACHE_DIR:
        cache_dir = titulos.CACHE_DIR
    if cache_dir is not None:
        cacheada = nota_del_dia_en_cache(date, cache_dir)
        if cacheada is not None:
            return cacheada

    try:
        notas = quita_notas_sin_titulo(sidof.get_notas(date))
    except requests.exceptions.HTTPError as exc:
        if exc.response is None or exc.response.status_code != 404:
            raise
        notas = {clave: [] for clave in EDICION_LISTAS.values()}

    if tiene_notas(notas):
        notas["fuente"] = FUENTE_SIDOF
        return notas

    if consultar_respaldo(date, respaldo):
        alterno = dofweb.get_notas(date)
        if dofweb.hay_publicacion(alterno):
            return quita_notas_sin_titulo(alterno)

    notas["fuente"] = FUENTE_SIDOF
    return notas


def _resolver_nota(cod_nota: int) -> dict:
    """Resolve `cod_nota` through get_nota() above (SIDOF, falling back to
    dofweb) rather than dofjson.sidof.get_nota() directly, so a codNota
    SIDOF has no record of at all (recovered only from dofweb) fails
    clearly here instead of crashing confusingly later on a codDiario/
    pagina a dofweb-recovered note never carries.

    Every function below that accepts an already-fetched `nota` treats this
    as their own fallback for when the caller has not already resolved (and
    vetted) the note itself.
    """
    nota = get_nota(cod_nota)
    if nota.get("fuente") == FUENTE_WEB:
        raise ValueError(
            f"nota {cod_nota} was recovered from {FUENTE_WEB}; SIDOF has no "
            f"record of it, so it carries no codDiario/pagina to locate a scanned "
            f"page or PDF from"
        )
    return nota


def download_nota_imagenes(
    cod_nota: int, outdir: Path, nota: dict | None = None
) -> list[Path]:
    """Download the scanned page image(s) for a note by codNota, inferring
    whether it spans more than one page (see dofjson.notas.infer_paginas()).

    Unlike download_nota(), this ALWAYS fetches the page images, even for a
    note that also has digital HTML content (cadenaContenido / existeHtml
    "S"). That is what makes the image→OCR path (dof2md) available for every
    note, not only the image-only ones — the scanned page is the certified
    original, and OCR'ing it is a way to get a note's Markdown that does not
    depend on the HTML being present or well-formed.

    Pass an already-fetched `nota` (e.g. get_nota()'s own return value) to
    avoid an extra request when the caller already has it.

    A page already present in `outdir` from an earlier call (same codNota,
    same outdir) is not re-downloaded — only checked for by name, so the
    day's notes/imagenes metadata is still fetched to work out which page(s)
    this note occupies and their file names."""
    if nota is None:
        nota = _resolver_nota(cod_nota)
    outdir.mkdir(parents=True, exist_ok=True)

    fecha = dt.datetime.strptime(nota["fecha"], "%d-%m-%Y").date()
    paginas = infer_paginas(nota, sidof.get_notas(fecha))
    imagenes_por_pagina = {
        img["pagina"]: img for img in sidof.get_imagenes(nota["codDiario"])["imagenesFS"]
    }

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
            sidof.download_imagen(imagen["nombreArchivo"], nota["codEdicion"], dest)
        dests.append(dest)
    return dests


def _edicion_pdf_cacheada(cod_diario: int, outdir: Path, timeout: int = 60) -> Path:
    """The whole edition's PDF, cached in `outdir` as `edicion-{cod_diario}.pdf`
    instead of being downloaded into a throwaway tempdir — a second note from
    the same edition (same day, same codDiario) reuses the file already on
    disk instead of fetching the whole edition again."""
    dest = outdir / f"edicion-{cod_diario}.pdf"
    if not dest.exists():
        sidof.download_pdf(cod_diario, dest, timeout=timeout)
    return dest


def download_nota_pdf(cod_nota: int, outdir: Path, nota: dict | None = None) -> Path:
    """Download a note as its OWN PDF: fetches the whole edition's PDF and
    slices out only the page(s) the note occupies (see
    dofjson.notas.infer_paginas()), writing them to `outdir/nota-{cod_nota}.pdf`.

    There is no per-note PDF endpoint — the DOF only serves the full edition
    (dofjson.sidof.download_pdf) — so this is the note-scoped counterpart of
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
    dofjson.notas._detectar_offset_paginacion()) to work out the physical
    PDF page index, rather than assuming `pagina - 1` always is one (see
    issue #95).

    Pass an already-fetched `nota` to skip an extra get_nota() request."""
    from pypdf import PdfReader, PdfWriter

    outdir.mkdir(parents=True, exist_ok=True)
    dest = outdir / f"nota-{cod_nota}.pdf"
    if dest.exists():
        return dest

    if nota is None:
        nota = _resolver_nota(cod_nota)

    fecha = dt.datetime.strptime(nota["fecha"], "%d-%m-%Y").date()
    notas_del_dia = sidof.get_notas(fecha)
    paginas = infer_paginas(nota, notas_del_dia)
    paginas_conocidas = {
        n["pagina"] for n in notas_del_dia[EDICION_LISTAS[nota["codEdicion"]]]
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
    note's page, or — when it does not — the *whole edition's* PDF, cached
    in `outdir` as `edicion-{codDiario}.pdf` (see _edicion_pdf_cacheada()),
    left uncut. Returns the resulting path(s) as a list either way (one
    edition path, wrapped, in the fallback case) so a caller does not need
    to know which of the two happened.

    The image path can be unavailable in two different ways, both treated
    as the same fallback signal: download_nota_imagenes() raises ValueError
    itself for a page with no matching image, and SIDOF's image-listing
    endpoint (dofjson.sidof.get_imagenes()) can 404 outright for a codDiario
    it has no listing for at all, which surfaces as requests.HTTPError.

    This deliberately does NOT slice the edition down to just this note's
    page(s) the way download_nota_pdf() does — that needs the note's
    physical page position worked out (dofjson.notas._detectar_offset_paginacion(),
    or a get_notas()-based equivalent), which is OCR/cutting work, not
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

    This function skips straight to whatever is already on disk before
    doing any downloading: first any `nota-{cod_nota}-*.jpg`
    download_nota_imagenes() may have left behind from a previous run (no
    network call at all, not even get_nota() for `nota`), and — once `nota`
    is known, to read its codDiario — any already-cached
    `edicion-{codDiario}.pdf`, so a previous PDF-fallback run is not retried
    (and re-failed) on every call.
    """
    imagenes_existentes = sorted(outdir.glob(f"nota-{cod_nota}-*.jpg"))
    if imagenes_existentes:
        return imagenes_existentes

    if nota is None:
        nota = _resolver_nota(cod_nota)

    edicion_existente = outdir / f"edicion-{nota['codDiario']}.pdf"
    if edicion_existente.exists():
        return [edicion_existente]

    try:
        return download_nota_imagenes(cod_nota, outdir, nota=nota)
    except (ValueError, requests.HTTPError):
        outdir.mkdir(parents=True, exist_ok=True)
        return [_edicion_pdf_cacheada(nota["codDiario"], outdir)]


def download_nota(cod_nota: int, outdir: Path) -> list[Path]:
    """Download a note's content by codNota alone: saves its metadata (incl.
    cadenaContenido) as JSON when the HTML content exists; otherwise falls
    back to downloading the scanned page image(s) for that note (see
    download_nota_imagenes()). To always get the page images regardless of
    whether HTML content exists, call download_nota_imagenes() directly."""
    nota = _resolver_nota(cod_nota)
    outdir.mkdir(parents=True, exist_ok=True)

    if nota.get("cadenaContenido"):
        dest = outdir / f"nota-{cod_nota}.json"
        dest.write_text(json.dumps({"Nota": nota}, ensure_ascii=False, indent=2))
        return [dest]

    return download_nota_imagenes(cod_nota, outdir, nota=nota)

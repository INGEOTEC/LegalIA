"""Build the Markdown of a single DOF note, identified by its codNota.

Three sources feed the same output, and legal_provisions() picks between them:

* **HTML** — when the note carries digital text (``cadenaContenido``), it is
  converted directly with html_converter.html_to_markdown(). This is the
  preferred path: clean, already scoped to the one note, and needs no OCR.
  A note SIDOF does not have at all (see dofjson.dofweb: whole days are
  missing from its dataset) is looked up on the DOF's website instead, which
  serves the same HTML — so the HTML path covers those notes too, and it is
  the only path that does: the OCR paths start from SIDOF metadata the lost
  notes have no record in.
* **Image** — the note's scanned page image(s) are downloaded
  (dofjson.download_nota_imagenes), OCR'd to Markdown (dof2md).
* **PDF** — the note's own PDF (the edition PDF sliced to the note's pages, via
  dofjson.download_nota_pdf) is OCR'd to Markdown (dof2md).

Both OCR paths then slice the result down to the single note with
dof2md.cutter.cut_markdown_by_titles(), using the note's own title and the
next note's title from the per-day index as boundaries (a page/PDF usually
holds more than one note). They are available for every note — including
those that also have HTML — which is why dofjson downloads images/PDF
regardless of ``existeHtml``. dof2md.cutter is only imported once an OCR path
actually runs (see _load_converter()), same as the rest of dof2md — it is an
optional dependency, so the HTML-only path stays lightweight.

Passing `converter` (a dof2md.BatchConverter already `__enter__`'d by the
caller) lets a batch of legal_provisions() calls share one already-warm
mineru-api server instead of each OCR path starting and stopping its own —
see BatchConverter's own docstring.
"""
import datetime as dt
from pathlib import Path

from dofjson import client, dofweb

from nota2md.html_converter import html_to_markdown

# Which per-edition list in a get_notas() response holds a note, keyed by its
# codEdicion. Mirrors dofjson.client._EDICION_LISTAS (kept local so nota2md
# doesn't reach into dofjson's private names).
_EDICION_LISTAS = {
    "MAT": "NotasMatutinas",
    "VES": "NotasVespertinas",
    "EXT": "NotasExtraordinarias",
}


def titulo_siguiente(nota: dict, notas_del_dia: dict) -> str | None:
    """The title of the note published right after `nota` (in codNota order),
    skipping title-less stub/twin entries. This is the boundary at which
    `nota` ends on its shared page — see cut_markdown_by_titles()."""
    lista = notas_del_dia.get(_EDICION_LISTAS[nota["codEdicion"]], [])
    ordenada = sorted(lista, key=lambda n: n["codNota"])
    idx = next(
        (i for i, n in enumerate(ordenada) if n["codNota"] == nota["codNota"]), None
    )
    if idx is None:
        return None
    for siguiente in ordenada[idx + 1 :]:
        if siguiente.get("titulo"):
            return siguiente["titulo"]
    return None


def fetch_nota(cod_nota: int) -> dict:
    """A note's get_nota() record, from SIDOF when it has it and from the DOF
    website when it does not.

    SIDOF answers `{"Nota": []}` for a codNota it lacks — not an error — so an
    empty record is what sends the lookup to dofweb.get_nota(). A note found
    there says so in its `fuente`, and carries no codDiario/codEdicion/pagina:
    only the HTML path can build it (see the module docstring)."""
    nota = client.get_nota(cod_nota).get("Nota")
    if isinstance(nota, dict) and nota:
        return nota

    nota = dofweb.get_nota(cod_nota).get("Nota")
    if isinstance(nota, dict) and nota:
        return nota

    raise ValueError(
        f"nota {cod_nota} does not exist in SIDOF nor in {dofweb.FUENTE}"
    )


def fetch_daily_legal_provisions(date: dt.date) -> dict:
    """`date`'s notes index — title, codNota, codEdicion, pagina... one entry
    per note, split into NotasMatutinas/NotasVespertinas/NotasExtraordinarias
    — from SIDOF, falling back to the DOF website when SIDOF has nothing for
    that day. Title-less stub entries (see dofjson.client.quita_notas_sin_titulo)
    are dropped either way, so what is left is real, browsable notes.

    SIDOF answers with every list empty both for a day with no edition
    (weekends, holidays) and for a handful of days it has simply lost outright
    — the two look identical on their own (see dofjson.archivo's module
    docstring), so an empty SIDOF answer is always checked against the DOF
    website before being taken to mean nothing was published.
    """
    notas = client.quita_notas_sin_titulo(client.get_notas(date))
    if any(notas.get(clave) for clave in _EDICION_LISTAS.values()):
        return notas

    alterno = dofweb.get_notas(date)
    if dofweb.hay_publicacion(alterno):
        return client.quita_notas_sin_titulo(alterno)
    return notas


def legal_provisions(
    cod_nota: int,
    outdir: str | Path,
    source: str = "auto",
    *,
    nota: dict | None = None,
    notas_del_dia: dict | None = None,
    min_confidence: float = 0.6,
    keep_pages: bool = False,
    keep_mineru_output: bool = False,
    converter=None,
) -> Path:
    """Build the Markdown for `cod_nota` and write it to
    ``outdir/nota-{cod_nota}.md``; return that path.

    `source` picks how the note becomes Markdown:

    * "auto"  — HTML when the note has it (``cadenaContenido``), otherwise the
      scanned-image OCR path.
    * "html"  — force the HTML path.
    * "image" — force OCR of the note's scanned page image(s).
    * "pdf"   — force OCR of the note's own PDF (the edition PDF sliced to the
      note's pages, via dofjson.download_nota_pdf).

    "image" and "pdf" both OCR with dof2md and then slice the result to the one
    note; "auto" never selects "pdf" — it is opt-in. Pass `nota` to reuse an
    already-fetched get_nota() note, and `notas_del_dia` to supply the per-day
    index (e.g. a saved notas JSON) instead of fetching it — the OCR paths need
    it to find the next note's title (the cut boundary). `keep_pages` also
    writes the uncut, full OCR output next to the result as
    ``nota-{cod_nota}.full.md``. `keep_mineru_output` (OCR paths only) keeps
    mineru's own raw output — otherwise thrown away with its temp dir — under
    ``nota-{cod_nota}_mineru/``, for inspecting an OCR result that looks wrong.

    Pass an already-`__enter__`'d `dof2md.BatchConverter` as `converter` (OCR
    paths only) to have this call reuse its already-warm mineru-api server
    instead of starting/stopping its own — the way to build a batch of notes
    without paying the OCR server's startup cost once per note:

        with dof2md.BatchConverter() as ins:
            for cod_nota in codigos_sin_html:
                legal_provisions(cod_nota, outdir, source="image", converter=ins)

    Left as None (the default), a call still works exactly as before —
    dof2md's own convert_images_to_markdown()/convert_to_markdown() manage
    whatever server they individually need.
    """
    if source not in ("auto", "html", "image", "pdf"):
        raise ValueError(
            f"source must be 'auto', 'html', 'image' or 'pdf', got {source!r}"
        )

    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    md_path = outdir / f"nota-{cod_nota}.md"

    if nota is None:
        nota = fetch_nota(cod_nota)

    if source == "html" or (source == "auto" and nota.get("cadenaContenido")):
        if not nota.get("cadenaContenido"):
            raise ValueError(
                f"nota {cod_nota} has no cadenaContenido; use source='image' or "
                f"'pdf' to OCR its scanned page(s) instead"
            )
        md_path.write_text(html_to_markdown(nota["cadenaContenido"]) + "\n", encoding="utf-8")
        return md_path

    if nota.get("fuente") == dofweb.FUENTE:
        raise ValueError(
            f"nota {cod_nota} was recovered from {dofweb.FUENTE} because SIDOF "
            f"does not have it; the {source!r} path needs SIDOF's codDiario and "
            f"page numbers, so only source='html' can build this note"
        )

    if source == "pdf":
        path_or_paths = client.download_nota_pdf(cod_nota, outdir, nota=nota)
    else:
        path_or_paths = client.download_nota_imagenes(cod_nota, outdir, nota=nota)

    if notas_del_dia is None:
        fecha = dt.datetime.strptime(nota["fecha"], "%d-%m-%Y").date()
        notas_del_dia = client.get_notas(fecha)
    titulo = nota.get("titulo", "")
    titulo_sig = titulo_siguiente(nota, notas_del_dia)

    if converter is not None:
        # The caller already __enter__'d this BatchConverter — its mineru-api
        # server (if any) is already up; reuse it as-is, no new one to manage.
        return converter(
            path_or_paths, outdir, md_path.name, titulo, titulo_sig,
            min_confidence=min_confidence, keep_pages=keep_pages,
            keep_mineru_output=keep_mineru_output,
        )

    # No shared converter given: behave exactly as before BatchConverter
    # existed — convert_images_to_markdown()/convert_to_markdown() manage
    # whatever server a single call individually needs (none, for a single
    # PDF or page; their own short-lived one for a multi-page note), instead
    # of this call starting a BatchConverter's server for just itself.
    convert = _load_converter(
        "convert_to_markdown" if source == "pdf" else "convert_images_to_markdown"
    )
    convert(path_or_paths, md_path, keep_mineru_output=keep_mineru_output)
    return _cut_and_write(md_path, outdir, titulo, titulo_sig, min_confidence, keep_pages, cod_nota)


def _load_converter(name: str):
    # dof2md (and mineru) are only needed for the OCR paths — import lazily so
    # the HTML path works without them installed.
    try:
        from dof2md import converter
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise RuntimeError(
            "the image/pdf path needs dof2md (and mineru) installed; "
            "install it from packages/dof2md, or use source='html'"
        ) from exc
    return getattr(converter, name)


def _cut_and_write(md_path, outdir, titulo, titulo_sig, min_confidence, keep_pages, cod_nota):
    """Shared tail of the OCR paths (no shared `converter`): read the full
    OCR Markdown at `md_path`, optionally keep it, then slice it down to
    just this note and overwrite."""
    from dof2md.cutter import cut_markdown_by_titles

    full_markdown = md_path.read_text(encoding="utf-8")
    if keep_pages:
        (outdir / f"nota-{cod_nota}.full.md").write_text(full_markdown, encoding="utf-8")

    cut = cut_markdown_by_titles(
        full_markdown, titulo, titulo_sig, min_confidence=min_confidence
    )
    md_path.write_text(cut + "\n", encoding="utf-8")
    return md_path

"""Build the Markdown of a single DOF note, identified by its codNota.

Three sources feed the same output, and build_nota_markdown() picks between them:

* **HTML** — when the note carries digital text (``cadenaContenido``), it is
  converted directly with html_converter.html_to_markdown(). This is the
  preferred path: clean, already scoped to the one note, and needs no OCR.
* **Image** — the note's scanned page image(s) are downloaded
  (dofjson.download_nota_imagenes), OCR'd to Markdown (dof2md).
* **PDF** — the note's own PDF (the edition PDF sliced to the note's pages, via
  dofjson.download_nota_pdf) is OCR'd to Markdown (dof2md).

Both OCR paths then slice the result down to the single note with
cutter.cut_markdown_by_titles(), using the note's own title and the next note's
title from the per-day index as boundaries (a page/PDF usually holds more than
one note). They are available for every note — including those that also have
HTML — which is why dofjson downloads images/PDF regardless of ``existeHtml``.
"""
import datetime as dt
from pathlib import Path

from dofjson import client

from nota2md.cutter import cut_markdown_by_titles
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


def build_nota_markdown(
    cod_nota: int,
    outdir: Path,
    source: str = "auto",
    *,
    nota: dict | None = None,
    notas_del_dia: dict | None = None,
    min_confidence: float = 0.6,
    keep_pages: bool = False,
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
    ``nota-{cod_nota}.full.md``.
    """
    if source not in ("auto", "html", "image", "pdf"):
        raise ValueError(
            f"source must be 'auto', 'html', 'image' or 'pdf', got {source!r}"
        )

    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    md_path = outdir / f"nota-{cod_nota}.md"

    if nota is None:
        nota = client.get_nota(cod_nota)["Nota"]

    if source == "html" or (source == "auto" and nota.get("cadenaContenido")):
        if not nota.get("cadenaContenido"):
            raise ValueError(
                f"nota {cod_nota} has no cadenaContenido; use source='image' or "
                f"'pdf' to OCR its scanned page(s) instead"
            )
        md_path.write_text(html_to_markdown(nota["cadenaContenido"]) + "\n", encoding="utf-8")
        return md_path

    if source == "pdf":
        return _build_from_pdf(
            cod_nota, nota, outdir, md_path, notas_del_dia, min_confidence, keep_pages
        )

    return _build_from_images(
        cod_nota, nota, outdir, md_path, notas_del_dia, min_confidence, keep_pages
    )


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


def _build_from_images(cod_nota, nota, outdir, md_path, notas_del_dia, min_confidence, keep_pages):
    convert_images_to_markdown = _load_converter("convert_images_to_markdown")
    image_paths = client.download_nota_imagenes(cod_nota, outdir, nota=nota)
    # dof2md OCRs the pages and already rewrites mineru's HTML tables to
    # Markdown tables, so the read-back is Markdown all the way through.
    convert_images_to_markdown(image_paths, md_path)
    return _cut_and_write(cod_nota, nota, outdir, md_path, notas_del_dia, min_confidence, keep_pages)


def _build_from_pdf(cod_nota, nota, outdir, md_path, notas_del_dia, min_confidence, keep_pages):
    convert_to_markdown = _load_converter("convert_to_markdown")
    pdf_path = client.download_nota_pdf(cod_nota, outdir, nota=nota)
    convert_to_markdown(pdf_path, md_path)
    return _cut_and_write(cod_nota, nota, outdir, md_path, notas_del_dia, min_confidence, keep_pages)


def _cut_and_write(cod_nota, nota, outdir, md_path, notas_del_dia, min_confidence, keep_pages):
    """Shared tail of the OCR paths: read the full OCR Markdown at `md_path`,
    optionally keep it, then slice it down to just this note and overwrite."""
    full_markdown = md_path.read_text(encoding="utf-8")

    if keep_pages:
        (outdir / f"nota-{cod_nota}.full.md").write_text(full_markdown, encoding="utf-8")

    if notas_del_dia is None:
        fecha = dt.datetime.strptime(nota["fecha"], "%d-%m-%Y").date()
        notas_del_dia = client.get_notas(fecha)

    cut = cut_markdown_by_titles(
        full_markdown,
        nota.get("titulo", ""),
        titulo_siguiente(nota, notas_del_dia),
        min_confidence=min_confidence,
    )
    md_path.write_text(cut + "\n", encoding="utf-8")
    return md_path

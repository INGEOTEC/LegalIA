"""Build the Markdown of a single legal provision, identified by its codNota.

Four sources feed the same output, and legal_provisions() picks between them:

* **SCJN** — the default, and the only one that is not the DOF's own text:
  when the `scjn-leyes` release (issue #128) covers this codNota with a link
  we are certain of, the result is the SCJN's consolidated text of the *whole
  law* as it read right after this reform, instead of the reform decree the
  DOF published (issue #117). It is read from a per-law tarball — no SIDOF
  request at all — and written with its ``fuente: scjn`` header intact,
  because the SCJN is not an official source of legal text. ``source="dof"``
  turns this path off and goes to the original source.
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
import warnings
from pathlib import Path

import dofjson
import requests

from nota2md.cache import SIN_CACHE_DIR
from nota2md.html_converter import html_to_markdown

# Which per-edition list in a get_notas() response holds a note, keyed by its
# codEdicion. Mirrors dofjson.notas.EDICION_LISTAS (kept local rather than
# imported, since dofjson.notas isn't part of dofjson's own unified surface).
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


def fetch_nota(cod_nota: int, fecha: dt.date | None = None) -> dict:
    """A note's get_nota() record, from SIDOF when it has it and from the DOF
    website when it does not — see dofjson.get_nota(), the package's unified
    entry point for both sources. A note found there says so in its
    `fuente`, and carries no codDiario/codEdicion/pagina: only the HTML path
    can build it (see the module docstring).

    `fecha` is forwarded to dofjson.get_nota() as-is, for the codigos
    (1999-2000) the website only resolves alongside their own date (issue
    #109/#111) — pass it when it is already known."""
    return dofjson.get_nota(cod_nota, fecha=fecha)


def fetch_daily_legal_provisions(date: dt.date) -> dict:
    """`date`'s notes index — title, codNota, codEdicion, pagina... one entry
    per note, split into NotasMatutinas/NotasVespertinas/NotasExtraordinarias
    — from SIDOF, falling back to the DOF website when SIDOF has nothing for
    that day. See dofjson.get_notas(), the package's unified entry point for
    both sources."""
    return dofjson.get_notas(date)


def _snapshot_scjn(cod_nota, instrumento, cache_dir, refrescar):
    """The SCJN's consolidated law text for `cod_nota`, or None when there is
    none to be had — including when the release itself cannot be reached.

    The SCJN path is an improvement over reconstructing the law from the DOF,
    not a hard dependency of building a note: an asset not published yet
    (`KeyError`) or a network failure while reading the index must not turn a
    `legal_provisions` call that would otherwise have succeeded into a
    traceback. Both fall back to the DOF path, but with `warnings.warn` so the
    fallback is never silent. A `ValueError` — the ambiguous codNota of issue
    #117's D4 — does propagate: that one is answerable, by passing
    `instrumento`, and guessing on the caller's behalf is exactly what it is
    there to prevent."""
    from nota2md.scjn import snapshot_de_codNota

    try:
        return snapshot_de_codNota(
            cod_nota, instrumento=instrumento, cache_dir=cache_dir, refrescar=refrescar
        )
    except KeyError as exc:
        warnings.warn(
            f"el release 'scjn-leyes' no responde por el codNota {cod_nota} "
            f"({exc}); se usa el DOF como fuente",
            stacklevel=3,
        )
    except requests.RequestException as exc:
        warnings.warn(
            f"no se pudo leer el corpus de la SCJN para el codNota {cod_nota} "
            f"({exc}); se usa el DOF como fuente",
            stacklevel=3,
        )
    return None


def legal_provisions(
    cod_nota: int,
    outdir: str | Path,
    source: str = "auto",
    *,
    fecha: dt.date | None = None,
    nota: dict | None = None,
    notas_del_dia: dict | None = None,
    min_confidence: float = 0.6,
    keep_pages: bool = False,
    keep_mineru_output: bool = False,
    converter=None,
    instrumento: str | None = None,
    cache_dir=SIN_CACHE_DIR,
    refrescar: bool = False,
) -> Path:
    """Build the Markdown for `cod_nota` and write it into `outdir`; return
    that path.

    `source` picks where the Markdown comes from:

    * "auto"  — the SCJN's consolidated text of the whole law as it read right
      after this reform, when the `scjn-leyes` release covers `cod_nota` with a
      link we are certain of (issue #117); otherwise the DOF: HTML when the
      note has it (``cadenaContenido``), else the scanned-image OCR path.
    * "dof"   — skip the SCJN entirely and build from the original source (the
      DOF/SIDOF), picking HTML-or-image the way "auto" used to.
    * "html"  — force the HTML path (a DOF path, so the SCJN is skipped too).
    * "image" — force OCR of the note's scanned page image(s).
    * "pdf"   — force OCR of the note's own PDF (the edition PDF sliced to the
      note's pages, via dofjson.download_nota_pdf).

    A note built from the SCJN is written as ``outdir/{slug}-{fecha}.md``,
    with its ``fuente: scjn`` provenance header intact — the SCJN is not an
    official source of legal text, and whoever reads the result has to be able
    to tell that from the file alone. Every DOF path keeps writing
    ``outdir/nota-{cod_nota}.md``, unchanged.

    `instrumento` (a law's slug) picks which law is meant when one decree
    reformed several at once; without it that case raises `ValueError` listing
    the candidates. `cache_dir`/`refrescar` control the on-disk cache of the
    release assets the SCJN path reads — see `nota2md.cache`; `cache_dir=None`
    skips the cache entirely. All three are ignored by the DOF paths.

    "image" and "pdf" both OCR with dof2md and then slice the result to the one
    note; "auto" never selects "pdf" — it is opt-in. Pass `nota` to reuse an
    already-fetched get_nota() note, and `notas_del_dia` to supply the per-day
    index (e.g. a saved notas JSON) instead of fetching it — the OCR paths need
    it to find the next note's title (the cut boundary). `fecha` is forwarded
    to fetch_nota() when `nota` is not already given — needed for the
    codigos (1999-2000) the DOF website only resolves alongside their own
    date (issue #109/#111). `keep_pages` also
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
    if source not in ("auto", "dof", "html", "image", "pdf"):
        raise ValueError(
            f"source must be 'auto', 'dof', 'html', 'image' or 'pdf', got {source!r}"
        )

    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    if source == "auto":
        snapshot = _snapshot_scjn(cod_nota, instrumento, cache_dir, refrescar)
        if snapshot is not None:
            slug, archivo, markdown = snapshot
            # `archivo` already carries issue #113's `-N` suffix for a law
            # reformed twice on one date, so `<slug>-<fecha>.md` is unique.
            destino = outdir / f"{slug}-{archivo}"
            destino.write_text(markdown, encoding="utf-8")
            return destino

    # Every remaining path is the DOF's own. "dof" only ever meant "not the
    # SCJN": from here on it picks HTML-or-image exactly as "auto" does.
    if source == "dof":
        source = "auto"

    md_path = outdir / f"nota-{cod_nota}.md"

    if nota is None:
        nota = fetch_nota(cod_nota, fecha=fecha)

    if source == "html" or (source == "auto" and nota.get("cadenaContenido")):
        if not nota.get("cadenaContenido"):
            raise ValueError(
                f"nota {cod_nota} has no cadenaContenido; use source='image' or "
                f"'pdf' to OCR its scanned page(s) instead"
            )
        md_path.write_text(html_to_markdown(nota["cadenaContenido"]) + "\n", encoding="utf-8")
        return md_path

    if nota.get("fuente") == dofjson.FUENTE_WEB:
        raise ValueError(
            f"nota {cod_nota} was recovered from {dofjson.FUENTE_WEB} because SIDOF "
            f"does not have it; the {source!r} path needs SIDOF's codDiario and "
            f"page numbers, so only source='html' can build this note"
        )

    if source == "pdf":
        path_or_paths = dofjson.download_nota_pdf(cod_nota, outdir, nota=nota)
    else:
        path_or_paths = dofjson.download_nota_imagenes(cod_nota, outdir, nota=nota)

    if notas_del_dia is None:
        fecha = dt.datetime.strptime(nota["fecha"], "%d-%m-%Y").date()
        notas_del_dia = dofjson.get_notas(fecha)
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

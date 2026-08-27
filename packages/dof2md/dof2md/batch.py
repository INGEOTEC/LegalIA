"""Batch document-to-Markdown conversion, keeping one mineru-api server warm
across many jobs instead of paying its startup (and model-loading) cost once
per document:

    with BatchConverter() as convert:
        for pdf_path, outdir, filename in jobs:
            convert(pdf_path, outdir, filename)

dof2md itself has no notion of what a "note" is or where a document came
from — a job is just a PDF (a single path) or a set of scanned page images (a
list of paths), an output directory, and an output filename. Whatever calls
this decides what those mean (a DOF legal provision, or anything else).
"""
from pathlib import Path

from dof2md import converter as _converter
from dof2md.converter import DEFAULT_TIMEOUT_SECONDS
from dof2md.cutter import cut_markdown_by_titles
from dof2md.mineru_server import ENV_VAR as _MINERU_API_URL_ENV_VAR
from dof2md.mineru_server import MineruServer


class BatchConverter:
    """Context manager: `__enter__` starts a persistent `mineru-api` server
    (skipped if a caller further up already has one running via
    MINERU_API_URL) and returns `self`, callable once per document;
    `__exit__` stops it. Calling it converts one document — a single PDF
    path, or a list of image paths for a document spanning several scanned
    pages — to Markdown, written to `outdir/filename`.

    `titulo`/`titulo_siguiente`, when given, slice the OCR'd Markdown down
    to the text between their two boundaries (see
    dof2md.cutter.cut_markdown_by_titles) — e.g. a DOF legal provision's own
    title and the next one's, to cut a page shared with the notes before and
    after it down to just this one. Left out (the default), the whole
    conversion is kept as-is, on the assumption that the whole document is
    what was asked for.
    """

    def __init__(self):
        """Create an unstarted converter; call `__enter__` (or use as a
        context manager) before calling it."""
        self._server: MineruServer | None = None

    def __enter__(self) -> "BatchConverter":
        """Start a persistent `mineru-api` server, unless one is already
        reachable via MINERU_API_URL, and return `self`."""
        import os

        if _MINERU_API_URL_ENV_VAR not in os.environ:
            self._server = MineruServer()
            self._server.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        """Stop the `mineru-api` server started by `__enter__`, if any."""
        if self._server is not None:
            self._server.stop()
            self._server = None

    def __call__(
        self,
        path_or_paths: str | Path | list[str | Path],
        outdir: str | Path,
        filename: str,
        titulo: str | None = None,
        titulo_siguiente: str | None = None,
        *,
        min_confidence: float = 0.6,
        keep_pages: bool = False,
        keep_mineru_output: bool = False,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> Path:
        """Convert one document — `path_or_paths` a single PDF path, or a
        list of image paths for a document spanning several scanned pages —
        to Markdown, written to `outdir/filename`, and return that path.

        `titulo`/`titulo_siguiente`, `min_confidence` and `keep_pages` are
        forwarded to `cutter.cut_markdown_by_titles` to crop the result down
        to a single note; left as `None` (the default), the whole conversion
        is kept as-is. `keep_mineru_output` and `timeout` are forwarded to
        `converter.convert_to_markdown`/`convert_images_to_markdown`."""
        outdir = Path(outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        dest = outdir / filename

        if isinstance(path_or_paths, (list, tuple)):
            _converter.convert_images_to_markdown(
                [Path(p) for p in path_or_paths], dest,
                timeout=timeout, keep_mineru_output=keep_mineru_output,
            )
        else:
            _converter.convert_to_markdown(
                Path(path_or_paths), dest,
                timeout=timeout, keep_mineru_output=keep_mineru_output,
            )

        if titulo is None:
            return dest

        full_markdown = dest.read_text(encoding="utf-8")
        if keep_pages:
            (outdir / f"{dest.stem}.full.md").write_text(full_markdown, encoding="utf-8")
        cut = cut_markdown_by_titles(
            full_markdown, titulo, titulo_siguiente, min_confidence=min_confidence
        )
        dest.write_text(cut + "\n", encoding="utf-8")
        return dest

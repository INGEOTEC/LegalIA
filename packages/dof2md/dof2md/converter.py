import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from dof2md.mineru_server import ENV_VAR as MINERU_API_URL_ENV_VAR
from dof2md.tables import html_tables_to_markdown

# Some real DOF editions run to hundreds of pages with heavy table content,
# and mineru has been observed to stall indefinitely on a single page/table
# in such cases rather than just running slowly. This bounds how long any
# one conversion is allowed to run before we give up on it.
DEFAULT_TIMEOUT_SECONDS = 3600


def _require_mineru() -> None:
    if shutil.which("mineru") is None:
        raise RuntimeError(
            "'mineru' is required to convert documents but isn't installed. "
            "Install dof2md's dependencies: pip install dof2md"
        )


def _run_mineru(input_path: Path, tmp_out: str, timeout: float) -> tuple[str, Path | None]:
    """Run mineru's pipeline backend on one input file (PDF or image) inside
    the given temp directory. Returns the raw Markdown text and the directory
    of any figures mineru extracted (or None if there were none).

    Reuses the MINERU_API_URL server if set (see mineru_server.MineruServer),
    so batch conversions don't reload models once per document."""
    api_url = os.environ.get(MINERU_API_URL_ENV_VAR)
    cmd = ["mineru", "-o", tmp_out, "-p", str(input_path), "-b", "pipeline"]
    if api_url:
        cmd += ["--api-url", api_url]
    subprocess.run(cmd, check=True, timeout=timeout)

    auto_dir = Path(tmp_out) / input_path.stem / "auto"
    md_text = (auto_dir / f"{input_path.stem}.md").read_text(encoding="utf-8")
    images_src = auto_dir / "images"
    if images_src.is_dir() and any(images_src.iterdir()):
        return md_text, images_src
    return md_text, None


def convert_to_markdown(
    pdf_path: Path, md_path: Path, timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> None:
    """Convert a PDF to Markdown using mineru's pipeline backend.

    mineru auto-detects which parts of the document need OCR internally, so
    this handles both born-digital and scanned DOF editions, and produces
    much cleaner Markdown (real paragraphs, accurate headings) than plain
    text-layer extraction — at the cost of being far slower (mineru runs
    real layout/OCR models even on already-digital text).

    Complex tables, which mineru emits as raw HTML, are rewritten to Markdown
    tables (see tables.html_tables_to_markdown) so the output is Markdown all
    the way through.

    If MINERU_API_URL is set (see mineru_server.MineruServer), reuses that
    already-running server instead of letting mineru spin up (and tear down)
    its own temporary one for this single call — this is what makes batch
    conversions in archive.py / scripts/archive_year.py avoid reloading
    models once per document.

    Raises subprocess.TimeoutExpired if mineru doesn't finish within
    `timeout` seconds — callers doing batch work should catch this and skip
    the offending document rather than let one stuck conversion block an
    entire run."""
    _require_mineru()

    with tempfile.TemporaryDirectory() as tmp_out:
        md_text, images_src = _run_mineru(pdf_path, tmp_out, timeout)
        if images_src is not None:
            images_dirname = f"{pdf_path.stem}_images"
            shutil.copytree(
                images_src, md_path.parent / images_dirname, dirs_exist_ok=True
            )
            md_text = md_text.replace("](images/", f"]({images_dirname}/")

        md_path.write_text(html_tables_to_markdown(md_text), encoding="utf-8")


def convert_images_to_markdown(
    image_paths: list[Path], md_path: Path, timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> None:
    """Convert one or more scanned page images into a single Markdown document.

    This is the OCR path for notes that come as scanned pages rather than
    digital text — e.g. the JPEGs downloaded by dofjson's
    download_nota_imagenes(). Each image is OCR'd with the same mineru
    pipeline backend as convert_to_markdown() and the results are
    concatenated in the order given (page order), so a note spanning several
    pages becomes one continuous Markdown file.

    Complex tables mineru emits as raw HTML are rewritten to Markdown tables
    (see tables.html_tables_to_markdown), like convert_to_markdown().

    The page images typically hold more than the note of interest (a page can
    start or end mid-note); callers wanting only one note should slice the
    result afterwards — see nota2md.cutter.cut_markdown_by_titles().

    Figures mineru extracts from a page are copied next to the output under
    `<md stem>_images/<image stem>/` (namespaced per page so figures from
    different pages can't collide), and their Markdown references rewritten to
    match. Raises subprocess.TimeoutExpired like convert_to_markdown()."""
    _require_mineru()
    image_paths = [Path(p) for p in image_paths]
    if not image_paths:
        raise ValueError("convert_images_to_markdown requires at least one image path")

    images_dirname = f"{md_path.stem}_images"
    parts = []
    with tempfile.TemporaryDirectory() as tmp_out:
        for image_path in image_paths:
            md_text, images_src = _run_mineru(image_path, tmp_out, timeout)
            if images_src is not None:
                page_dest = md_path.parent / images_dirname / image_path.stem
                shutil.copytree(images_src, page_dest, dirs_exist_ok=True)
                md_text = md_text.replace(
                    "](images/", f"]({images_dirname}/{image_path.stem}/"
                )
            parts.append(md_text.strip())

    md_path.write_text(
        html_tables_to_markdown("\n\n".join(parts)) + "\n", encoding="utf-8"
    )

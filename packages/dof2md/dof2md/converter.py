import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from dof2md.mineru_server import ENV_VAR as MINERU_API_URL_ENV_VAR

# Some real DOF editions run to hundreds of pages with heavy table content,
# and mineru has been observed to stall indefinitely on a single page/table
# in such cases rather than just running slowly. This bounds how long any
# one conversion is allowed to run before we give up on it.
DEFAULT_TIMEOUT_SECONDS = 3600


def convert_to_markdown(
    pdf_path: Path, md_path: Path, timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> None:
    """Convert a PDF to Markdown using mineru's pipeline backend.

    mineru auto-detects which parts of the document need OCR internally, so
    this handles both born-digital and scanned DOF editions, and produces
    much cleaner Markdown (real paragraphs, accurate headings) than plain
    text-layer extraction — at the cost of being far slower (mineru runs
    real layout/OCR models even on already-digital text).

    If MINERU_API_URL is set (see mineru_server.MineruServer), reuses that
    already-running server instead of letting mineru spin up (and tear down)
    its own temporary one for this single call — this is what makes batch
    conversions in archive.py / scripts/archive_year.py avoid reloading
    models once per document.

    Raises subprocess.TimeoutExpired if mineru doesn't finish within
    `timeout` seconds — callers doing batch work should catch this and skip
    the offending document rather than let one stuck conversion block an
    entire run."""
    if shutil.which("mineru") is None:
        raise RuntimeError(
            "'mineru' is required to convert PDFs but isn't installed. "
            "Install dof2md's dependencies: pip install dof2md"
        )

    api_url = os.environ.get(MINERU_API_URL_ENV_VAR)

    with tempfile.TemporaryDirectory() as tmp_out:
        cmd = ["mineru", "-o", tmp_out, "-p", str(pdf_path), "-b", "pipeline"]
        if api_url:
            cmd += ["--api-url", api_url]
        subprocess.run(cmd, check=True, timeout=timeout)
        auto_dir = Path(tmp_out) / pdf_path.stem / "auto"
        md_text = (auto_dir / f"{pdf_path.stem}.md").read_text(encoding="utf-8")

        images_src = auto_dir / "images"
        if images_src.is_dir() and any(images_src.iterdir()):
            images_dirname = f"{pdf_path.stem}_images"
            shutil.copytree(
                images_src, md_path.parent / images_dirname, dirs_exist_ok=True
            )
            md_text = md_text.replace("](images/", f"]({images_dirname}/")

        md_path.write_text(md_text, encoding="utf-8")

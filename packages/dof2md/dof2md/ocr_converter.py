import shutil
import subprocess
import tempfile
from pathlib import Path

MINERU_NOT_INSTALLED_MSG = (
    "This document looks like a scan and needs OCR, but 'mineru' isn't installed. "
    "Install the optional extra: pip install 'dof2md[ocr]'"
)


def convert_scanned_to_markdown(pdf_path: Path, md_path: Path) -> None:
    """Convert a scanned PDF to Markdown using mineru's OCR pipeline.

    This is much slower than the plain-text extraction path (minutes per
    document, even on CPU) since it runs real layout/OCR models — reserved
    for documents that fail the born-digital fast path."""
    if shutil.which("mineru") is None:
        raise RuntimeError(MINERU_NOT_INSTALLED_MSG)

    with tempfile.TemporaryDirectory() as tmp_out:
        subprocess.run(
            ["mineru", "-o", tmp_out, "-p", str(pdf_path), "-b", "pipeline"],
            check=True,
        )
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

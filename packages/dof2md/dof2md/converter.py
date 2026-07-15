from collections import Counter
from pathlib import Path

import fitz  # PyMuPDF


def body_font_size(doc: fitz.Document, sample_pages: int = 5) -> float:
    """Most common font size, used as the baseline to detect headings."""
    sizes = Counter()
    for page in doc[:sample_pages]:
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    sizes[round(span["size"])] += len(span["text"])
    return sizes.most_common(1)[0][0] if sizes else 10.0


def doc_to_markdown(doc: fitz.Document) -> str:
    """Extract the PDF's text as Markdown, using font size to infer
    headings (DOF documents are dense legal text and don't need table
    detection)."""
    body_size = body_font_size(doc)
    lines_out = []

    for page_num, page in enumerate(doc, start=1):
        lines_out.append(f"\n\n#### Page {page_num}\n")
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                spans = line["spans"]
                if not spans:
                    continue
                text = "".join(span["text"] for span in spans).strip()
                if not text:
                    continue
                max_size = max(span["size"] for span in spans)
                is_bold = any("Bold" in span["font"] for span in spans)
                if max_size > body_size * 1.15:
                    lines_out.append(f"\n## {text}\n")
                elif is_bold:
                    lines_out.append(f"**{text}**\n")
                else:
                    lines_out.append(text + "\n")

    return "".join(lines_out)


def convert_to_markdown(pdf_path: Path, md_path: Path) -> None:
    doc = fitz.open(str(pdf_path))
    md_path.write_text(doc_to_markdown(doc), encoding="utf-8")


def _is_scanned_page(page: fitz.Page, area_ratio_threshold: float = 0.85) -> bool:
    """A page is considered scanned if a single image covers most of it —
    the pattern old DOF editions use (a full-page scan plus a baked-in,
    often low-quality, text layer)."""
    page_area = page.rect.width * page.rect.height
    if page_area <= 0:
        return False
    for img in page.get_images(full=True):
        xref = img[0]
        for bbox in page.get_image_rects(xref):
            if (bbox.width * bbox.height) / page_area >= area_ratio_threshold:
                return True
    return False


def is_scanned_document(doc: fitz.Document, sample_pages: int = 5) -> bool:
    """Whether most of the first `sample_pages` pages are full-page scans."""
    pages = doc[: min(sample_pages, len(doc))]
    if not pages:
        return False
    scanned_count = sum(1 for page in pages if _is_scanned_page(page))
    return scanned_count / len(pages) > 0.5

"""Convert the HTML of a single DOF note (the ``cadenaContenido`` field of a
dofjson get_nota() response) to Markdown.

DOF note HTML is regular, and drives the whole mapping:

* Block structure comes from CSS classes, not tag names — every paragraph is
  a ``<div>`` carrying a class (``Texto``, ``ROMANOS``, ``INCISO``, ``Normal``,
  ``Fechas`` …). The document title is an ``<h1 class="Titulo_1">``, the
  "Al margen un sello…" subtitle an ``<h2 class="Titulo_2">``, and inner
  section headers (ANTECEDENTES, DECLARACIONES, CLÁUSULAS) are
  ``<div class="ANOTACION">``.
* Emphasis is inline CSS on ``<span>`` runs: ``font-weight:bold`` and
  ``font-style:italic``. Text is fragmented into many adjacent spans (one per
  run, plus standalone spans holding a single space).

The mapping is chosen so the output resembles what dof2md/mineru produce from
the scanned page images of the same note (``#``/``##`` headings, ``**bold**``,
``*italic*``, GitHub tables), so that a note's Markdown looks the same whether
it came from the HTML or the OCR path.
"""
import re

from bs4 import BeautifulSoup, NavigableString, Tag

_BLOCK_TAGS = {"div", "p", "table", "h1", "h2", "h3", "h4", "ul", "ol"}
_H1_CLASSES = {"Titulo_1"}
_H2_CLASSES = {"Titulo_2", "ANOTACION"}


def html_to_markdown(html: str) -> str:
    """Convert a DOF note's HTML content to Markdown."""
    soup = BeautifulSoup(html or "", "html.parser")
    for junk in soup(["style", "script"]):
        junk.decompose()

    root = soup.body or soup
    blocks: list[str] = []
    _emit_blocks(root, blocks)
    return "\n\n".join(blocks)


def _emit_blocks(element: Tag, blocks: list[str]) -> None:
    for child in element.children:
        if isinstance(child, NavigableString):
            text = _clean(str(child))
            if text:
                blocks.append(text)
            continue
        if not isinstance(child, Tag):
            continue

        name = child.name.lower()
        classes = set(child.get("class") or [])

        if name == "table":
            _append(blocks, _render_table(child))
        elif name == "h1" or classes & _H1_CLASSES:
            _append_heading(blocks, "#", child)
        elif name == "h2" or classes & _H2_CLASSES:
            _append_heading(blocks, "##", child)
        elif name in ("h3", "h4"):
            _append_heading(blocks, "###", child)
        elif name in ("div", "p") and _has_block_child(child):
            _emit_blocks(child, blocks)
        else:
            _append(blocks, _render_inline(child))


def _has_block_child(element: Tag) -> bool:
    return any(
        isinstance(c, Tag) and c.name and c.name.lower() in _BLOCK_TAGS
        for c in element.children
    )


def _append(blocks: list[str], text: str) -> None:
    if text and text.strip():
        blocks.append(text)


def _append_heading(blocks: list[str], marker: str, element: Tag) -> None:
    text = _render_inline(element, inline_breaks=False)
    if text:
        blocks.append(f"{marker} {text}")


# --- inline runs -----------------------------------------------------------

def _is_bold(node: Tag) -> bool:
    style = (node.get("style") or "").replace(" ", "").lower()
    return "font-weight:bold" in style or node.name in ("b", "strong")


def _is_italic(node: Tag) -> bool:
    style = (node.get("style") or "").replace(" ", "").lower()
    return "font-style:italic" in style or node.name in ("i", "em")


def _collect_runs(node, bold, italic, runs, inline_breaks):
    for child in node.children:
        if isinstance(child, NavigableString):
            runs.append([re.sub(r"\s+", " ", str(child)), bold, italic])
        elif isinstance(child, Tag) and child.name == "br":
            runs.append(["\n" if inline_breaks else " ", bold, italic])
        elif isinstance(child, Tag):
            _collect_runs(
                child,
                bold or _is_bold(child),
                italic or _is_italic(child),
                runs,
                inline_breaks,
            )


def _render_inline(element: Tag, inline_breaks: bool = True) -> str:
    runs: list[list] = []
    _collect_runs(element, False, False, runs, inline_breaks)

    merged: list[list] = []
    for text, bold, italic in runs:
        if merged and merged[-1][1] == bold and merged[-1][2] == italic:
            merged[-1][0] += text
        else:
            merged.append([text, bold, italic])

    out = []
    for text, bold, italic in merged:
        if not text:
            continue
        if (bold or italic) and text.strip(" "):
            stripped = text.strip(" ")
            lead = text[: len(text) - len(text.lstrip(" "))]
            trail = text[len(text.rstrip(" ")) :]
            marker = ("**" if bold else "") + ("*" if italic else "")
            out.append(f"{lead}{marker}{stripped}{marker}{trail}")
        else:
            out.append(text)

    return _clean("".join(out))


def _clean(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return text.strip()


# --- tables ----------------------------------------------------------------

def _table_grid(table: Tag) -> list[list[str]]:
    """Flatten an HTML table (honouring rowspan/colspan) into a rectangular
    grid of cell strings. A spanned cell's text goes in its top-left slot; the
    slots it covers are filled with empty strings, since Markdown tables can't
    merge cells."""
    filled: dict[tuple[int, int], str] = {}
    n_cols = 0
    rows = table.find_all("tr")
    for r, tr in enumerate(rows):
        c = 0
        for td in tr.find_all(["td", "th"], recursive=False):
            while (r, c) in filled:
                c += 1
            text = _cell(td)
            row_span = _span(td, "rowspan")
            col_span = _span(td, "colspan")
            for dr in range(row_span):
                for dc in range(col_span):
                    filled[(r + dr, c + dc)] = text if (dr == 0 and dc == 0) else ""
            c += col_span
            n_cols = max(n_cols, c)
    return [[filled.get((r, c), "") for c in range(n_cols)] for r in range(len(rows))]


def _span(td: Tag, attr: str) -> int:
    try:
        return max(1, int(td.get(attr, 1)))
    except (TypeError, ValueError):
        return 1


def _render_table(table: Tag) -> str:
    grid = _table_grid(table)
    if not grid or not grid[0]:
        return ""

    n_cols = max(len(row) for row in grid)
    grid = [row + [""] * (n_cols - len(row)) for row in grid]

    lines = [
        "| " + " | ".join(grid[0]) + " |",
        "|" + "---|" * n_cols,
    ]
    for row in grid[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _cell(td: Tag) -> str:
    text = _render_inline(td, inline_breaks=False)
    return text.replace("\n", " ").replace("|", r"\|").strip()

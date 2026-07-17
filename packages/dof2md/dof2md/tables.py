"""Turn the raw HTML tables mineru emits into Markdown tables.

mineru renders simple tables as Markdown but falls back to raw HTML
(``<table>…</table>`` with rowspan/colspan) for anything complex. This module
rewrites those HTML tables into GitHub Markdown tables so dof2md's output is
Markdown all the way through — no leftover HTML.

Implemented with the standard library's ``html.parser`` only (no BeautifulSoup)
to keep dof2md's dependencies to just ``requests`` and ``mineru``.
"""
import re
from html.parser import HTMLParser

_TABLE_RE = re.compile(r"<table\b[^>]*>.*?</table>", re.DOTALL | re.IGNORECASE)


def html_tables_to_markdown(text: str) -> str:
    """Replace every ``<table>…</table>`` block in `text` with a GitHub
    Markdown table, leaving everything else untouched. Blank lines are ensured
    around each converted table so it renders as a table."""

    def _replace(match: "re.Match") -> str:
        rows = _parse_rows(match.group(0))
        rendered = _render(rows)
        return f"\n\n{rendered}\n\n" if rendered else match.group(0)

    return re.sub(r"\n{3,}", "\n\n", _TABLE_RE.sub(_replace, text))


class _TableParser(HTMLParser):
    """Collect a single HTML table's cells as rows of (text, attrs)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows: list[list[tuple[str, dict]]] = []
        self._row: list[tuple[str, dict]] | None = None
        self._cell: list[str] | None = None
        self._cell_attrs: dict = {}

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._cell = []
            self._cell_attrs = {k.lower(): v for k, v in attrs}
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(("".join(self._cell), self._cell_attrs))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def _parse_rows(fragment: str) -> list[list[tuple[str, dict]]]:
    parser = _TableParser()
    parser.feed(fragment)
    parser.close()
    return parser.rows


def _span(attrs: dict, name: str) -> int:
    try:
        return max(1, int(attrs.get(name, 1)))
    except (TypeError, ValueError):
        return 1


def _clean_cell(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().replace("|", r"\|")


def _grid(rows: list[list[tuple[str, dict]]]) -> list[list[str]]:
    """Flatten rows into a rectangular grid honouring rowspan/colspan. A
    spanned cell's text goes in its top-left slot; the covered slots are filled
    with empty strings, since Markdown tables can't merge cells."""
    filled: dict[tuple[int, int], str] = {}
    n_cols = 0
    for r, row in enumerate(rows):
        c = 0
        for text, attrs in row:
            while (r, c) in filled:
                c += 1
            row_span = _span(attrs, "rowspan")
            col_span = _span(attrs, "colspan")
            cell = _clean_cell(text)
            for dr in range(row_span):
                for dc in range(col_span):
                    filled[(r + dr, c + dc)] = cell if (dr == 0 and dc == 0) else ""
            c += col_span
            n_cols = max(n_cols, c)
    return [[filled.get((r, c), "") for c in range(n_cols)] for r in range(len(rows))]


def _render(rows: list[list[tuple[str, dict]]]) -> str:
    grid = _grid(rows)
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

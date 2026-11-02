"""Helpers the `md2akn` test files share (issue #305): one definition each, imported with
``from .conftest import ...``. They build and read small documents written with the SCJN's
outline markers (`<!-- scjn:block ... -->`); they are not fixtures and not part of the package."""

from pathlib import Path

from md2akn import parse_markdown, text_units
from md2akn.patterns import ARTICULO

FIXTURES = Path(__file__).parent / "fixtures"
UNIT_FIXTURES = Path(__file__).parent / "fixtures_units"


def walk(node):
    """`node` and every descendant, in document order."""
    yield node
    for child in node.children:
        yield from walk(child)


def marker(orden, label, kind="other", extra="", numero=0):
    """One outline marker line; `extra` is appended after `kind` (` type=annex num="4"`)."""
    return f'<!-- scjn:block orden={orden} numero={numero} kind={kind}{extra} label="{label}" -->'


def document_of(*blocks):
    """Blocks (markers and texts) joined into one document, blank-line separated."""
    return "\n\n".join(blocks) + "\n"


def read_unit_fixture(name: str) -> str:
    return (UNIT_FIXTURES / name).read_text(encoding="utf-8")


def article_opener(line: str):
    """`(number, separator)` of the article opener `line` is, or `None`.

    `clasifica` matches against the line with its bold markers removed."""
    m = ARTICULO.match(line.replace("**", ""))
    return None if m is None else (m.group("num"), m.group("sep").strip())


def units_of(markdown: str):
    """The retrieval units of `markdown`, every article whole (`split_articles=False`)."""
    return text_units(parse_markdown(markdown), split_articles=False)

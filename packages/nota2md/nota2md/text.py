"""Fold away formatting differences before comparing two pieces of a law's
text -- the one normalization step both `nota2md.leyes` (matching a reform
instruction's own text against the law it names) and `nota2md.linking` (the
`codNota` content-diff confirmation) need.

A leaf on purpose: this module imports nothing of its own, so both callers
can depend on it without creating a cycle between them (issue #208).
"""

import re
import unicodedata

# Markdown syntax (headings, emphasis, code spans, table pipes) to strip
# before comparing two texts, so formatting differences don't count as content
# differences — a table cell's leading "|" is left alone, only a separator
# "|" with another one somewhere later on the same line counts as syntax.
_MARKDOWN_SYNTAX = re.compile(r"[#*_`]|\|(?=[^|]*\|)")


def normaliza_para_comparar(texto: str) -> str:
    """Fold away formatting differences (Markdown syntax, accents, case,
    whitespace) that don't reflect a real difference in the law's content, so
    similarity is measured on words, not on typesetting."""
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = _MARKDOWN_SYNTAX.sub("", texto)
    texto = texto.lower()
    return re.sub(r"\s+", " ", texto).strip()

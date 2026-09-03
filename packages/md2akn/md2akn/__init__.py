"""Segment a Mexican federal law's Markdown into a navigable hierarchy —
articles inside their chapter, fracciones inside their article, incisos
inside their fracción — labelled with Akoma Ntoso's vocabulary.

`parse_legal_provisions(path)` is the whole package in one call: hand it a
Markdown file and get back the root node, which `walk()` iterates in document
order and `find(eId)` addresses by identifier ("article 27, fracción VII").

**No Akoma Ntoso XML is produced anywhere in this project** — an earlier
converter that lived in `nota2md` was removed (issue #168). What this package
takes from the standard (an OASIS Standard since 2018, reviewed in issue #91)
is its *vocabulary* — the element names in `AKN_TYPES`, the eId naming
convention, and `refers_to` for the two Mexican structures the standard has no
element for. Not emitting XML is also what lets the tree stay faithful to a
Mexican article, which normally has both a flat introduction and hierarchical
children — a combination the standard's `hierarchy` content model forbids,
requiring a strict either/or that an XML conversion would have to flatten.

The spaCy layer is there for callers who want it: the segmenter is a pipeline
component (registered as soon as `md2akn` is imported), so it composes with
anything else a caller wants to run over the same `Doc`.

>>> import spacy
>>> import md2akn
>>> nlp = spacy.blank("es")
>>> _ = nlp.add_pipe("akn_segmenter")
>>> doc = nlp("**ARTICULO 1o.-** Son obligaciones de los patrones.\\n")
>>> doc._.akn_tree                 # the root AknNode
AknNode(akn_type='act', eId='act', children=1)
>>> doc._.akn_meta                 # the frontmatter (empty: this note has none)
{}
>>> len(doc.spans["akn"])          # every node's Span, in document order
4

Everything public is reachable off the package itself — nobody imports
`md2akn.segmenter` from outside.
"""

from md2akn.model import (
    AKN_TYPES,
    REFERS_TO_APARTADO,
    REFERS_TO_TRANSITORIOS,
    AknNode,
    Annotation,
)
from md2akn.pipeline import parse_legal_provisions, parse_markdown
from md2akn.validate import Report, Violation, validate

__version__ = "0.1.0"

__all__ = [
    "parse_legal_provisions",
    "parse_markdown",
    "AknNode",
    "Annotation",
    "AKN_TYPES",
    "REFERS_TO_TRANSITORIOS",
    "REFERS_TO_APARTADO",
    "validate",
    "Report",
    "Violation",
]

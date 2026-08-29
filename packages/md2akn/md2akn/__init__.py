"""Segment a Mexican federal law's Markdown into a navigable hierarchy —
articles inside their chapter, fracciones inside their article, incisos
inside their fracción — labelled with Akoma Ntoso's vocabulary.

`parse_legal_provisions(path)` is the whole package in one call: hand it a
Markdown file and get back the root node, which `walk()` iterates in document
order and `find(eId)` addresses by identifier ("article 27, fracción VII").

**No XML is produced here.** `nota2md.akoma_ntoso` already converts to Akoma
Ntoso XML; what this package takes from the standard (an OASIS Standard since
2018, reviewed in issue #91) is its *vocabulary* — the element names in
`AKN_TYPES`, the eId naming convention, and `refers_to` for the two Mexican
structures the standard has no element for. Not emitting XML is what lets the
tree stay faithful to a Mexican article, which normally has both a flat
introduction and hierarchical children — a combination the standard's content
model forbids and the XML converter therefore has to flatten.

The spaCy layer is there for callers who want it: the segmenter is a pipeline
component, so it composes with anything else a caller wants to run over the
same `Doc`.

    import spacy
    nlp = spacy.blank("es")
    nlp.add_pipe("akn_segmenter")
    doc = nlp(markdown_text)
    doc._.akn_tree           # the root AknNode
    doc._.akn_meta           # the frontmatter
    doc.spans["akn"]         # every node's Span, in document order

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

"""The spaCy layer: the `akn_segmenter` pipeline component, the custom
extensions the tree is reachable through, and `parse_legal_provisions`.

Three efficiency decisions, all of them settled here rather than left to be
discovered later (issue #158):

1. **`spacy.blank("es")` — the tokenizer and nothing else.** The segmentation
   is rule-based and deterministic; loading `es_core_news_*` would buy a
   tagger and a parser that no rule consults, at a large cost per document.

2. **`nlp.max_length` is raised explicitly.** spaCy's default is 1,000,000
   characters and the corpus' largest law, `ligie-2022`, is 1.89 MB (`lfd`
   1.36 MB, `lgipe` 1.28 MB), so the default fails on the real worst case.
   The default exists to stop a parser/NER pipeline from exhausting memory
   (roughly 1 GB per 100,000 characters); with a tokenizer-only pipeline that
   cost is not incurred, so raising it is safe rather than merely convenient.

3. **The scan never iterates tokens** — see `md2akn.segmenter`. Tokenization
   happens once, when the `Doc` is built, and the rules work on the raw
   string.
"""

from pathlib import Path
from typing import Optional

import spacy
from spacy.language import Language
from spacy.tokens import Doc, Span, SpanGroup

from md2akn.model import AknNode
from md2akn.segmenter import segment

#: Where the flat, document-ordered list of nodes is put on the `Doc`.
SPAN_GROUP = "akn"

#: `nlp.max_length` for the pipeline `parse_legal_provisions` builds. Ten
#: million characters is a little over five times the corpus' worst case, so
#: it is a ceiling on runaway input rather than a limit a real law can reach.
MAX_LENGTH = 10_000_000


def _register_extensions() -> None:
    """Register the `Doc`/`Span` extensions, tolerating a second call.

    Importing this module twice — or a test module and the package both
    reaching it — must not raise `ValueError: extension already exists`, so
    every registration is guarded rather than forced. `force=True` would work
    too but would silently clobber an extension another library owns.
    """
    if not Doc.has_extension("akn_tree"):
        Doc.set_extension("akn_tree", default=None)
    if not Doc.has_extension("akn_meta"):
        Doc.set_extension("akn_meta", default=None)
    for nombre in ("akn_type", "num", "eId", "node"):
        if not Span.has_extension(nombre):
            Span.set_extension(nombre, default=None)


_register_extensions()


# No `from __future__ import annotations` in this module, unlike the rest of
# the package: spaCy resolves a factory's signature through pydantic when the
# component is built, and PEP 563 would leave `nlp: Language` an unresolved
# ForwardRef, failing with "field 'nlp' not yet prepared".
@Language.factory("akn_segmenter")
def create_akn_segmenter(nlp: Language, name: str):
    """The pipeline component factory, so `nlp.add_pipe("akn_segmenter")`
    works on any `Language` — including one a caller assembled themselves."""
    return AknSegmenter()


class AknSegmenter:
    """Segments a `Doc`'s Markdown and hangs the result off the `Doc`.

    Stateless: one instance can process any number of documents, and two
    documents never share a tree.

    **The tree is the authority; the `Span` extensions are a convenience with
    one documented rule.** spaCy stores a `Span`'s custom extensions on the
    `Doc` keyed by ``("._.", name, start_char, end_char)`` — the span's label
    is not part of that key — so two nodes covering the same characters
    necessarily share one set of extension values. That is common rather than
    exotic here: `act` and `body` coincide in any law with no preamble, and a
    container coincides with its only child.

    The rule adopted: **the innermost (most specific) node wins**, which is
    what `_.node` on a shared range answers. It is the more useful of the two
    — asked what a range *is*, "article 5" beats "the body that contains it"
    — and it falls out of writing the extensions in preorder, outermost
    first. `doc.spans["akn"]` is unaffected: it is a list, so it holds one
    entry per node, in document order, each labelled with its own eId.
    """

    def __call__(self, doc: Doc) -> Doc:
        tree, meta = segment(doc)
        doc._.akn_tree = tree
        doc._.akn_meta = meta

        nodos = list(tree.walk())
        for nodo in nodos:
            # Preorder, so that where two nodes share a character range the
            # innermost one is written last and therefore wins -- see the
            # class docstring.
            nodo.span._.akn_type = nodo.akn_type
            nodo.span._.num = nodo.num
            nodo.span._.eId = nodo.eId
            nodo.span._.node = nodo
        doc.spans[SPAN_GROUP] = SpanGroup(doc, spans=[n.span for n in nodos])
        return doc


_NLP: Optional[Language] = None


def get_nlp() -> Language:
    """The module-wide pipeline, built on first use.

    Cached because building it costs the tokenizer's own setup, and
    `parse_legal_provisions` is expected to be called once per law over a
    corpus of hundreds.
    """
    global _NLP
    if _NLP is None:
        nlp = spacy.blank("es")
        nlp.max_length = MAX_LENGTH
        nlp.add_pipe("akn_segmenter")
        _NLP = nlp
    return _NLP


def parse_markdown(text: str) -> AknNode:
    """`text`'s tree — the same thing `parse_legal_provisions` returns, for a
    string already in memory."""
    return get_nlp()(text)._.akn_tree


def parse_legal_provisions(path) -> AknNode:
    """The tree of the law in the Markdown file at `path`.

    The shortcut the whole package exists for: it reads the file, runs it
    through the cached pipeline, and returns the root `AknNode` (`akn_type ==
    "act"`), whose frontmatter is on `tree.meta` and whose structure is
    walked with `walk()` or addressed with `find(eId)`.

    Which file to hand it is the caller's problem, deliberately. The SCJN
    corpus' own convention — "the most recent date without a `-2`/`-3`
    suffix" — is a fact about that corpus, not about Markdown, and lives in
    `scripts/`, not here.
    """
    return parse_markdown(Path(path).read_text(encoding="utf-8"))

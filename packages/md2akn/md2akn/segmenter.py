"""Frontmatter, block scanning, and the tree built out of the blocks.

Two design decisions of issue #158 live here, and both are about cost.

**The scan is over lines of the raw text, never over tokens.** Markdown is a
block format: what makes a line an article heading is how the line begins,
not what any word in it is. So the recognition is a single O(lines) pass over
the string, and only at the end is each character range turned into a spaCy
`Span`. spaCy's tokenizer therefore runs exactly once per document, and no
rule ever iterates tokens — which matters at the corpus' real worst case,
`ligie-2022` at 1.89 MB.

**Frontmatter is split off, not parsed with a YAML library.** The corpus'
headers are flat `key: value` lines (`fuente`, `ordenamiento`,
`fecha_publicacion`, ...); taking a YAML dependency to read them would double
this package's dependency list to save a five-line loop. A file with no
frontmatter reads exactly the same, with `meta == {}`.
"""

from __future__ import annotations

from dataclasses import dataclass

from spacy.tokens import Span

from md2akn.model import AknNode, EIdAllocator
from md2akn.patterns import FRONTMATTER_ENTRY, FRONTMATTER_FENCE


@dataclass(frozen=True)
class Block:
    """One Markdown block — the text between two blank lines — with its
    character offsets into the whole document, frontmatter included, so a
    span built from them lands on the right characters of the `Doc`."""

    text: str
    start: int
    end: int

    @property
    def stripped(self) -> str:
        return self.text.strip()


def split_frontmatter(text: str) -> tuple[dict[str, str], int]:
    """The document's frontmatter as a dict, and the offset the body starts
    at.

    Returns ``({}, 0)`` for a document that does not open with a `---` fence
    — which is not an error: the corpus writes the header, but this package
    takes any Markdown file.

    An unterminated fence is treated the same way. A document whose first
    line happens to be `---` and which never closes it is far more likely to
    be a body that opens with a horizontal rule than a truncated header, and
    swallowing the whole file as metadata would be the worse failure.
    """
    if not FRONTMATTER_FENCE.match(text):
        return {}, 0

    inicio = text.index("\n") + 1
    fin = text.find("\n---", inicio - 1)
    if fin == -1:
        return {}, 0
    # Past the closing fence's own line, plus the blank line that follows it
    # when there is one, so the body starts at real content.
    cierre = text.find("\n", fin + 1)
    cuerpo = len(text) if cierre == -1 else cierre + 1

    meta: dict[str, str] = {}
    for linea in text[inicio:fin].splitlines():
        coincidencia = FRONTMATTER_ENTRY.match(linea)
        if coincidencia:
            meta[coincidencia.group(1)] = coincidencia.group(2).strip()
    return meta, cuerpo


def iter_blocks(text: str, offset: int = 0):
    """Yield every non-empty block of `text[offset:]`, in document order,
    with offsets relative to the whole of `text`.

    A block is a run of consecutive non-blank lines. Leading and trailing
    blank lines are not blocks and are not yielded — which is why the tree's
    leaves do not, on their own, reproduce the document character for
    character: they reproduce it once the separators between them are put
    back (the invariant #158's tests state).
    """
    n = len(text)
    i = offset
    while i < n:
        # Skip blank lines.
        while i < n:
            fin_linea = text.find("\n", i)
            fin_linea = n if fin_linea == -1 else fin_linea
            if text[i:fin_linea].strip():
                break
            i = fin_linea + 1
        if i >= n:
            return
        inicio = i
        # Consume until the next blank line.
        while i < n:
            fin_linea = text.find("\n", i)
            fin_linea = n if fin_linea == -1 else fin_linea
            if not text[i:fin_linea].strip():
                break
            i = fin_linea + 1
        yield Block(text[inicio:i].rstrip("\n"), inicio, inicio + len(text[inicio:i].rstrip("\n")))
        i = fin_linea + 1 if i < n else n


def node_span(doc, start: int, end: int, label: str) -> Span:
    """`doc`'s span over ``[start, end)``, labelled with the node's eId.

    The label carries the node's identity *into the `SpanGroup`*, which is
    the only place it can be carried: two nodes routinely cover exactly the
    same characters — `act` and `body` do in any law with no preamble, and so
    do a container and its only child — so within `doc.spans["akn"]` the
    label is what tells them apart.

    It does **not** disambiguate the custom extensions, and no label could:
    spaCy keys those on the `Doc` by ``("._.", name, start_char, end_char)``
    alone, label excluded (verified against spaCy 3.8). See `AknSegmenter`
    for the rule that follows from it.

    `alignment_mode="expand"` because a Markdown block boundary need not fall
    on a token boundary; `char_span` still answers None for a range no token
    touches, which an empty document is the only real way to produce, and an
    empty labelled span is the right answer there.
    """
    span = doc.char_span(start, end, label=label, alignment_mode="expand")
    return span if span is not None else Span(doc, 0, 0, label=label)


def build_tree(doc, meta_end: int, blocks: list[Block], meta: dict[str, str] | None = None) -> AknNode:
    """The placeholder tree of issue #158: `act` → `body` → one `content`
    per block.

    This is deliberately trivial. #159/#160/#161 replace the body's flat run
    of `content` nodes with the real hierarchy; what this layer fixes is the
    *shape* — that there is an `act` spanning the document minus its
    frontmatter, that structure hangs off it, and that every leaf carries a
    real `Span` — so the three that follow only have to supply rules.
    """
    eids = EIdAllocator()

    fin_acto = blocks[-1].end if blocks else len(doc.text)
    inicio_acto = blocks[0].start if blocks else meta_end
    act = AknNode(
        "act",
        node_span(doc, inicio_acto, fin_acto, "act"),
        eId=eids.allocate("act"),
        meta=dict(meta or {}),
    )
    # `body` covers exactly what `act` does whenever there is no preamble and
    # no conclusions -- which, at this layer, is always. See `node_span` for
    # why that makes the label mandatory rather than decorative.
    act.add(
        AknNode("body", node_span(doc, inicio_acto, fin_acto, "body"), eId=eids.allocate("body"))
    )
    body = act.children[0]
    for i, bloque in enumerate(blocks, 1):
        eid = eids.allocate(f"content_{i}")
        body.add(AknNode("content", node_span(doc, bloque.start, bloque.end, eid), eId=eid))
    return act


def segment(doc) -> tuple[AknNode, dict[str, str]]:
    """`doc`'s tree and its frontmatter — the whole of what the spaCy
    pipeline component does, kept here so it can be exercised without one."""
    # Imported here rather than at module scope: `md2akn.structure` reaches
    # back for `node_span`, and a top-level import either way would be a
    # cycle.
    from md2akn.structure import build

    meta, meta_end = split_frontmatter(doc.text)
    blocks = list(iter_blocks(doc.text, meta_end))
    return build(doc, meta, blocks), meta

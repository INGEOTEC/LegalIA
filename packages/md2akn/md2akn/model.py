"""The tree `md2akn` produces: `AknNode`, `Annotation`, and the vocabulary
their `akn_type` is drawn from.

The vocabulary is Akoma Ntoso's (OASIS LegalDocML, the standard issue #91
reviewed and settled on), but **this package never emits Akoma Ntoso XML** —
no converter to it exists anywhere in this project (an earlier one that lived
in `nota2md` was removed in issue #168). What is borrowed is the naming: the
element names in `AknType`, the `eId` convention, and the `refersTo` escape
hatch for the two Mexican structures the standard has no element for.

That distinction is what makes the tree here more faithful to the source than
an XML conversion could be. Akoma Ntoso's `hierarchy` content model is a
strict either/or — an element has a flat `content` **or** hierarchical
children, never both — and a Mexican article routinely has both: an
introductory paragraph, then its fracciones, then one or more closing
paragraphs. Since this package emits no XML it does not have to choose, and
keeps the article as it actually reads. See #160 for the `is_chapeau`/
`is_tail` flags that would let a future XML conversion make that choice
knowingly instead of by position.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from spacy.tokens import Span

#: Every `akn_type` a node can carry, in the rough order they nest. These are
#: Akoma Ntoso element names spelled exactly as the standard spells them, so
#: that a reader who knows the standard needs no translation table — and so
#: that whoever writes the XML conversion later has nothing to map.
#:
#: Two Mexican structures have no element here at all, because the standard
#: has none for them: "transitorios" and "apartado". Both are expressed as an
#: existing element plus `refers_to` (`section` + `#transitorios`, `level` +
#: `#apartado`) instead. See #159 and #160.
AKN_TYPES = (
    "act",          # the whole document (the root)
    "preamble",     # "Al margen un sello...", the enacting formula
    "body",         # the normative body
    "book",         # LIBRO
    "title",        # TÍTULO
    "chapter",      # CAPÍTULO
    "section",      # SECCIÓN, and TRANSITORIOS via refers_to
    "level",        # APARTADO A/B, via refers_to
    "article",      # Artículo N
    "paragraph",    # fracción
    "point",        # inciso
    "subpoint",     # subinciso / numeral
    "content",      # a plain paragraph
    "conclusions",  # the closing signatures
)

#: The `refers_to` values this package uses, for the structures Akoma Ntoso
#: has no element for. Kept as a named constant so #159/#160 spell them the
#: same way and a consumer can match on them.
REFERS_TO_TRANSITORIOS = "#transitorios"
REFERS_TO_APARTADO = "#apartado"

#: eId path separator, as Akoma Ntoso names them: an inciso a) of fracción II
#: of article 27 is `art_27__para_II__point_a`.
EID_SEPARATOR = "__"

_ESPACIOS = re.compile(r"\s+")


def eid_component(prefix: str, num: str) -> str:
    """One segment of an eId: an AKN prefix and the number as it appeared,
    with whitespace folded to `_` (`art_27`, `para_VII_Bis`).

    The number is kept as the document wrote it rather than normalized: it is
    what a reader would cite, and normalizing `1o.` to `1` would make two
    different articles collide in a law that has both.
    """
    return prefix + "_" + _ESPACIOS.sub("_", num.strip().rstrip("."))


class EIdAllocator:
    """Hands out eIds that are unique across one `act`.

    Uniqueness is not a formality: a Mexican article can legally restate the
    same fracción label under two separate "I. a X." lists — one for a body's
    composition, a later one for its members' eligibility — and Akoma Ntoso
    requires eId to be unique across the whole document. The disambiguation is
    open-addressing over a suffix: append `_2`, `_3`, ... to the second and
    later claimants.
    """

    def __init__(self):
        self.used: set[str] = set()

    def allocate(self, proposed: str) -> str:
        candidate = proposed
        i = 2
        while candidate in self.used:
            candidate = f"{proposed}_{i}"
            i += 1
        self.used.add(candidate)
        return candidate

    def child(self, parent_eid: str, prefix: str, num: str) -> str:
        """A child's eId, scoped under `parent_eid` — the same allocation,
        with the parent's path prepended (skipped for the structural roots,
        which carry no number and would only add noise to every path)."""
        segment = eid_component(prefix, num)
        base = f"{parent_eid}{EID_SEPARATOR}{segment}" if parent_eid else segment
        return self.allocate(base)


@dataclass
class Annotation:
    """One `(REFORMADO, D.O.F. 10 DE JUNIO DE 2011)` note, attached to the
    node it describes.

    Defined here and left empty of logic on purpose (issue #158): #161 fills
    it in. In Akoma Ntoso terms this is the *passive* side of a modification
    — `lifecycle`/`temporalData`/`passiveModifications` — recorded here with
    enough granularity that someone can emit that XML later without going
    back to the text. The *active* side, a DOF decree saying what it changed,
    is issue #163.

    `raw` is never lost. An annotation whose action cannot be recognized is
    still recorded, with `action=None`, rather than dropped — #162's sweep
    counts those, and a silently discarded annotation would make that count
    a lie.
    """

    raw: str
    action: str | None = None
    scope: str | None = None
    date: dt.date | None = None
    source: str | None = None


@dataclass(eq=False)
class AknNode:
    """One node of the segmented document.

    Compared and hashed by identity (`eq=False`): a node holds a reference to
    its parent, so structural equality would recurse forever, and two nodes
    that happen to carry the same text are still two different nodes.
    """

    akn_type: str
    span: "Span"
    eId: str = ""
    num: str | None = None
    heading: str | None = None
    refers_to: str | None = None
    #: The document's frontmatter, set on the root `act` only (empty on
    #: every other node, and on an `act` whose file had none).
    meta: dict[str, str] = field(default_factory=dict, repr=False)
    #: Set only on a `content` child of an `article` that also has fracciones:
    #: `is_chapeau` for the introduction that precedes them ("Artículo 4. Son
    #: obligaciones:"), `is_tail` for a paragraph that follows the last one.
    #:
    #: They exist because Akoma Ntoso's `hierarchy` content model forbids
    #: exactly this shape — an element has a flat `content` **or**
    #: hierarchical children, never both — and a Mexican article routinely
    #: has both. Since this package emits no XML it does not have to choose,
    #: and keeps the article as it reads; the flags are what would let a
    #: future XML conversion make that choice knowingly rather than by
    #: position (see #160). An article of nothing but paragraphs still has
    #: them as `content` children (issue #181) — it simply has no chapeau and
    #: no tail, because there is nothing to interleave and nothing to choose.
    is_chapeau: bool = False
    is_tail: bool = False
    parent: "AknNode | None" = field(default=None, repr=False)
    children: list["AknNode"] = field(default_factory=list, repr=False)
    notes: list[Annotation] = field(default_factory=list, repr=False)

    def add(self, child: "AknNode") -> "AknNode":
        """Append `child` and set its `parent` — the only way children are
        attached, so `node.parent.children` always contains `node` (one of
        the invariants #162 checks)."""
        child.parent = self
        self.children.append(child)
        return child

    def walk(self) -> Iterator["AknNode"]:
        """This node and every descendant, in preorder — which, since
        children are appended in document order, is document order.

        The tree and this iterator are the same structure seen two ways, not
        two implementations: `walk` reads `children`, it does not maintain a
        parallel list.
        """
        yield self
        for child in self.children:
            yield from child.walk()

    def find(self, eId: str) -> "AknNode | None":
        """The node with this eId, anywhere below (and including) this one —
        `tree.find("art_27__para_VII")` is "give me article 27, fracción
        VII". None when nothing carries that eId."""
        for node in self.walk():
            if node.eId == eId:
                return node
        return None

    @property
    def text(self) -> str:
        """The node's own raw Markdown, exactly as the file wrote it."""
        return self.span.text

    @property
    def start_char(self) -> int:
        return self.span.start_char

    @property
    def end_char(self) -> int:
        return self.span.end_char

    def __repr__(self) -> str:
        partes = [f"akn_type={self.akn_type!r}", f"eId={self.eId!r}"]
        if self.num is not None:
            partes.append(f"num={self.num!r}")
        if self.heading is not None:
            partes.append(f"heading={self.heading!r}")
        if self.refers_to is not None:
            partes.append(f"refers_to={self.refers_to!r}")
        partes.append(f"children={len(self.children)}")
        return f"AknNode({', '.join(partes)})"

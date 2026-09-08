"""Every text of a law, ready to embed (issue #218's Fase 1, the plan for
#217).

#217 fixed the article as *the* unit to embed. Measured over the cached
`scjn-leyes` release, article-only units leave real legal text with no vector
at all: container epigraphs (`TÍTULO PRIMERO`), tariff rows hanging loose
under a chapter, the transitorios (where *vigencia* lives), the enacting
formula every law opens with, and the signature block some of them close
with. So this module extends #217's rule rather than replacing it: articles
are still a unit, and every other character of the law becomes a unit too —
`coverage()` turns "the whole law is represented" into an assertion a test
can fail on.

This is a pure function of an already-parsed tree: it adds no dependency
(`spacy` stays the only one `md2akn` needs), and the GPU job Fase 2 builds
never imports it — it reads `units.parquet` and nothing else.

Two things are metadata, not text, and neither is ever embedded: the
frontmatter (`tree.meta`) and a reform annotation (`Annotation`, on some
node's `notes`). An annotation has no character offset of its own — only its
`raw` text — so its position is recovered by searching `raw` in the document
text, walking the tree in document order (`AknNode.walk` — a node's own
notes, then its children's) and advancing a cursor past each match. That is
exact rather than approximate: annotations are attached to nodes in the same
left-to-right pass that reads the document, so the order `walk()` visits
them in is the order they occur in, with one exception — a trailing
annotation with no node after it hangs off the root `act` (see
`md2akn.structure`), which is why it is visited *last* here rather than
first, ahead of the walk order.
"""

from __future__ import annotations

import bisect
import hashlib
import re
from dataclasses import dataclass

from md2akn.model import REFERS_TO_TRANSITORIOS, AknNode
from md2akn.pipeline import parse_markdown

#: The split threshold `text_units` uses when no `cap` is given: measured
#: over the cached `scjn-leyes` release as the point past which an article's
#: own text stops reading like the rest of the corpus (#218's own
#: measurement table: median article length is in the low hundreds).
DEFAULT_SPLIT_CAP = 2000

_WHITESPACE = re.compile(r"\s+")

#: The Akoma Ntoso container types that carry a `.heading` and therefore get
#: their own `heading` unit (rule 4): `book`/`title`/`chapter`/`section`, and
#: `level` (an apartado) -- but only in the one shape that ever reaches
#: `_walk_container` at all, an apartado holding whole articles of its own
#: (`SECCIÓN ... > APARTADO I > Artículo N`, the appellate-procedure shape
#: `cmpp`/`cnpp`/`lacp` and others use -- measured missing entirely from
#: `coverage()` before this entry existed, issue #218). An apartado nested
#: *inside* an article instead (holding that article's own fracciones, e.g.
#: constitutional article 123) never reaches this function: `_article_units`
#: absorbs the whole article -- apartado heading included -- into the
#: article's own text before `_walk_container` ever sees it, so the two
#: shapes cannot collide here. `paragraph` (a fracción) never carries a
#: heading unit either way -- the tree never nests one directly under a
#: book/title/chapter/section/level without an article between them, so it
#: never reaches this function as `node`.
_CONTAINER_LABEL = {
    "book": "LIBRO",
    "title": "TÍTULO",
    "chapter": "CAPÍTULO",
    "section": "SECCIÓN",
    "level": "APARTADO",
}
_CONTAINER_TYPES = frozenset(_CONTAINER_LABEL)

#: `unit_type`s a `TextUnit` may carry -- the vocabulary #218's issue body
#: names, kept here as one place that spells them all.
UNIT_TYPES = (
    "article",
    "article_piece",
    "preamble",
    "heading",
    "loose",
    "conclusions",
)

#: The two templates `text_units` ships (issue #218 Fase 1, rule 7): whether
#: an `article`/`article_piece` unit's text is prefixed with the law's name
#: and the article's own number. `heading`/`loose` units always carry their
#: ancestor path regardless of which template is chosen -- only an article's
#: own prefix is what the template decides. Which one a published vector set
#: used is a manifest field, never a code edit (#217 §1's open question).
TEMPLATES = ("bare", "contextual")


@dataclass(frozen=True)
class TextUnit:
    """One span of a law's text, exactly as it is handed to the embedding
    model — `text` is already normalized, so it is what a caller would hash
    or embed with no further processing."""

    #: `article | article_piece | preamble | heading | loose | conclusions`.
    unit_type: str
    #: The anchor node's eId — the article's own, even for one of its pieces.
    eId: str
    #: 0 for a whole unit; 1..n for a split article's pieces, in order.
    piece: int
    #: The direct child's eId a piece corresponds to (`None` when `piece == 0`).
    piece_eId: str | None
    #: The anchor node's own `akn_type` (`"article"` for every article piece).
    akn_type: str
    num: str | None
    #: Ancestor container labels, outermost first — the law's own name (from
    #: the frontmatter) leads when it is known, then one entry per enclosing
    #: `book`/`title`/`chapter`/`section`. Does not include the unit's own
    #: label when the unit is itself a container's heading.
    path: tuple[str, ...]
    #: The unit's raw span into the document, annotations and all — the
    #: range `coverage()` accounts for. Not the same as `len(text)`: an
    #: annotation inside the range is stripped from `text` but still counted
    #: here, against the same characters `coverage()` calls "annotation".
    start_char: int
    end_char: int
    #: Normalized — exactly what gets embedded.
    text: str
    text_sha1: str


@dataclass(frozen=True)
class LeafRef:
    """One leaf node's `eId`, and the unit that carries its text.

    A "leaf" is any node with no children of its own — the genuine bottom of
    the tree, not merely a node with no unit of its own (a container with
    children has none, but is not a leaf). Recovered from `units` by
    character-range containment rather than stored while building them, so a
    caller who already has `units` (read back from `units.parquet`, say)
    never needs anything else to reconstruct it.
    """

    eId: str
    akn_type: str
    unit_eId: str
    unit_piece: int


@dataclass(frozen=True)
class Coverage:
    """The coverage invariant, as data (issue #218 Fase 1).

    Every non-whitespace character of the document falls into exactly one
    bucket: `frontmatter_chars` (the `--- ... ---` header, never a node),
    `annotation_chars` (a reform annotation, metadata not text) or
    `covered_chars` (embedded in some unit's `text`). A well-formed law has
    `uncovered_chars == 0` — the property the coverage test asserts on every
    fixture, and the cached `scjn-leyes` release sweep measures at scale.
    """

    total_chars: int
    frontmatter_chars: int
    annotation_chars: int
    covered_chars: int
    uncovered_chars: int


def normalize(text: str) -> str:
    """`text`, whitespace-folded — exactly what gets embedded and exactly
    what gets hashed, so a query normalized the same way lands on the same
    text a corpus unit did.

    Folds runs of whitespace to one space and strips the ends; nothing else.
    No case folding, no accent stripping, no punctuation removal — the
    tokenizer is the only thing that gets to interpret the text.

    >>> import md2akn
    >>> md2akn.normalize("  Artículo  1o.-\\n\\nEn los Estados Unidos Mexicanos.  ")
    'Artículo 1o.- En los Estados Unidos Mexicanos.'
    """
    return _WHITESPACE.sub(" ", text).strip()


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _iter_notes_in_document_order(tree: AknNode):
    """Every `Annotation` in the tree, in the order it occurs in the raw
    text — `walk()`'s order for every node but the root, whose own `notes`
    (a trailing annotation with nothing left to attach to) are visited last
    instead of first."""
    for node in tree.walk():
        if node is tree:
            continue
        yield from node.notes
    yield from tree.notes


def _annotation_ranges(tree: AknNode, doc_text: str) -> list[tuple[int, int]]:
    """`[start, end)` for every annotation's raw text, in document order.

    An `Annotation` carries no offset of its own, only `raw` — its exact
    original substring — so its position is recovered with a left-to-right
    search, advancing a cursor past each match in turn. That is exact, not a
    heuristic: `_iter_notes_in_document_order` visits annotations in the same
    order they occur in the text (see the module docstring), so the next
    annotation's `raw` is never searched for before the position the
    previous one ended at.

    `doc_text` is taken as a parameter rather than read off `tree.span.doc`
    here: it is `AknNode.span.doc.text`, which spaCy rebuilds from the token
    array on every access rather than caching — every caller in this module
    reads it exactly once and passes the string around instead (see
    `_RangeIndex`).
    """
    ranges: list[tuple[int, int]] = []
    cursor = 0
    for note in _iter_notes_in_document_order(tree):
        idx = doc_text.find(note.raw, cursor)
        if idx == -1:
            idx = doc_text.find(note.raw)
        if idx == -1:  # pragma: no cover - would mean raw was never written
            continue
        ranges.append((idx, idx + len(note.raw)))
        cursor = idx + len(note.raw)
    return ranges


class _RangeIndex:
    """`_annotation_ranges`'s output, indexed for a lookup that does not cost
    the whole list per call — and the document's own text, fetched here
    exactly once.

    Two unrelated costs, bundled into one object because both have to be
    threaded through the same recursive walk and both are wrong to pay more
    than once per law:

    - `text_units` calls `_strip_ranges` once per node — thousands of times
      for a heavily reformed law like `lft` — so a plain "scan the list from
      the front" (the correct but naive reading of "sorted, so break early")
      is actually `O(nodes x annotations)`. Ranges are non-overlapping and
      sorted by both ends (they cannot cross), so `bisect` finds the first
      range that could still overlap `[start, end)` in `O(log annotations)`.
    - `AknNode.span.doc.text` is **not** a cached attribute: spaCy rebuilds a
      `Doc`'s text from its token array on every access, an `O(document
      length)` string join. Reading it once per node the same way turned a
      1.2 MB law's ~1,100-child article alone into minutes of wall clock
      (measured while building this module) — fetched here once instead.
    """

    __slots__ = ("ranges", "doc_text", "_ends")

    def __init__(self, ranges: list[tuple[int, int]], doc_text: str):
        self.ranges = ranges
        self.doc_text = doc_text
        self._ends = [r[1] for r in ranges]

    def overlapping(self, start: int, end: int):
        i = bisect.bisect_right(self._ends, start)
        ranges = self.ranges
        while i < len(ranges) and ranges[i][0] < end:
            yield ranges[i]
            i += 1


def _strip_ranges(doc_text: str, start: int, end: int, ranges: "_RangeIndex") -> str:
    """`doc_text[start:end]` with every annotation range inside it removed —
    the text a unit spanning `[start, end)` actually embeds."""
    parts = []
    cursor = start
    for r_start, r_end in ranges.overlapping(start, end):
        if r_end <= cursor:
            continue
        if r_start > cursor:
            parts.append(doc_text[cursor:r_start])
        cursor = max(cursor, min(r_end, end))
    parts.append(doc_text[cursor:end])
    return "".join(parts)


def _node_text(node: AknNode, ann_ranges: "_RangeIndex") -> str:
    return _strip_ranges(ann_ranges.doc_text, node.start_char, node.end_char, ann_ranges)


def _group_text(nodes: list[AknNode], ann_ranges: "_RangeIndex") -> str:
    return _strip_ranges(ann_ranges.doc_text, nodes[0].start_char, nodes[-1].end_char, ann_ranges)


def _container_label(node: AknNode) -> str:
    if node.refers_to == REFERS_TO_TRANSITORIOS:
        label = "TRANSITORIOS"
        if node.num and node.num != "transitorios":
            label = f"{label} {node.num}"
        return label
    label = _CONTAINER_LABEL.get(node.akn_type, node.akn_type.upper())
    if node.num:
        label = f"{label} {node.num}"
    if node.heading:
        label = f"{label} {node.heading}"
    return label


def _with_context(raw_text: str, path: tuple[str, ...]) -> str:
    """`raw_text`, prefixed with its ancestor `path` — rule 7's context
    prefix, unconditional for `heading`/`loose` units."""
    if not path:
        return normalize(raw_text)
    prefix = " > ".join(path)
    return normalize(f"{prefix}\n\n{raw_text}")


def _with_article_template(raw_text: str, template: str, article: AknNode, law_name: str | None) -> str:
    if template == "contextual":
        prefix_parts = []
        if law_name:
            prefix_parts.append(law_name)
        if article.num:
            prefix_parts.append(f"Artículo {article.num}")
        if prefix_parts:
            return normalize(f"{', '.join(prefix_parts)}. {raw_text}")
    return normalize(raw_text)


def _leaf_unit(unit_type: str, node: AknNode, ann_ranges) -> TextUnit:
    text = normalize(_node_text(node, ann_ranges))
    return TextUnit(
        unit_type=unit_type,
        eId=node.eId,
        piece=0,
        piece_eId=None,
        akn_type=node.akn_type,
        num=node.num,
        path=(),
        start_char=node.start_char,
        end_char=node.end_char,
        text=text,
        text_sha1=_sha1(text),
    )


def _article_units(
    article: AknNode,
    path: tuple[str, ...],
    ann_ranges,
    cap: int,
    template: str,
    law_name: str | None,
) -> list[TextUnit]:
    whole_raw = _node_text(article, ann_ranges)
    if len(normalize(whole_raw)) <= cap or not article.children:
        text = _with_article_template(whole_raw, template, article, law_name)
        return [
            TextUnit(
                unit_type="article",
                eId=article.eId,
                piece=0,
                piece_eId=None,
                akn_type="article",
                num=article.num,
                path=path,
                start_char=article.start_char,
                end_char=article.end_char,
                text=text,
                text_sha1=_sha1(text),
            )
        ]

    chapeau = [c for c in article.children if c.is_chapeau]
    remaining = [c for c in article.children if not c.is_chapeau]
    units: list[TextUnit] = []
    piece = 0
    chapeau_raw = ""
    if chapeau:
        piece = 1
        chapeau_raw = _group_text(chapeau, ann_ranges)
        text = _with_article_template(chapeau_raw, template, article, law_name)
        units.append(
            TextUnit(
                unit_type="article_piece",
                eId=article.eId,
                piece=piece,
                piece_eId=chapeau[0].eId,
                akn_type="article",
                num=article.num,
                path=path,
                start_char=chapeau[0].start_char,
                end_char=chapeau[-1].end_char,
                text=text,
                text_sha1=_sha1(text),
            )
        )
    for child in remaining:
        piece += 1
        child_raw = _node_text(child, ann_ranges)
        combined = f"{chapeau_raw}\n\n{child_raw}" if chapeau_raw else child_raw
        text = _with_article_template(combined, template, article, law_name)
        units.append(
            TextUnit(
                unit_type="article_piece",
                eId=article.eId,
                piece=piece,
                piece_eId=child.eId,
                akn_type="article",
                num=article.num,
                path=path,
                start_char=child.start_char,
                end_char=child.end_char,
                text=text,
                text_sha1=_sha1(text),
            )
        )
    return units


def _is_loose_leaf(node: AknNode) -> bool:
    return not node.children and node.akn_type != "article"


def _loose_groups(leaves: list[AknNode], ann_ranges, cap: int) -> list[list[AknNode]]:
    groups: list[list[AknNode]] = []
    current: list[AknNode] = []
    current_len = 0
    for leaf in leaves:
        leaf_len = len(normalize(_node_text(leaf, ann_ranges)))
        if current and current_len + leaf_len > cap:
            groups.append(current)
            current = []
            current_len = 0
        current.append(leaf)
        current_len += leaf_len
    if current:
        groups.append(current)
    return groups


def _loose_units(leaves: list[AknNode], path: tuple[str, ...], ann_ranges, cap: int) -> list[TextUnit]:
    units = []
    for group in _loose_groups(leaves, ann_ranges, cap):
        raw = _group_text(group, ann_ranges)
        text = _with_context(raw, path)
        units.append(
            TextUnit(
                unit_type="loose",
                eId=group[0].eId,
                piece=0,
                piece_eId=None,
                akn_type=group[0].akn_type,
                num=None,
                path=path,
                start_char=group[0].start_char,
                end_char=group[-1].end_char,
                text=text,
                text_sha1=_sha1(text),
            )
        )
    return units


def _walk_container(
    node: AknNode,
    path: tuple[str, ...],
    ann_ranges,
    cap: int,
    template: str,
    law_name: str | None,
    units: list[TextUnit],
) -> None:
    children = node.children
    first_start = children[0].start_char if children else node.end_char
    if node.akn_type in _CONTAINER_TYPES:
        heading_raw = _strip_ranges(ann_ranges.doc_text, node.start_char, first_start, ann_ranges)
        if normalize(heading_raw):
            text = _with_context(heading_raw, path)
            units.append(
                TextUnit(
                    unit_type="heading",
                    eId=node.eId,
                    piece=0,
                    piece_eId=None,
                    akn_type=node.akn_type,
                    num=node.num,
                    path=path,
                    start_char=node.start_char,
                    end_char=first_start,
                    text=text,
                    text_sha1=_sha1(text),
                )
            )
        child_path = path + (_container_label(node),)
    else:
        child_path = path

    i, n = 0, len(children)
    while i < n:
        child = children[i]
        if child.akn_type == "article":
            units.extend(_article_units(child, child_path, ann_ranges, cap, template, law_name))
            i += 1
        elif _is_loose_leaf(child):
            j = i + 1
            while j < n and _is_loose_leaf(children[j]):
                j += 1
            units.extend(_loose_units(children[i:j], child_path, ann_ranges, cap))
            i = j
        else:
            _walk_container(child, child_path, ann_ranges, cap, template, law_name, units)
            i += 1


def text_units(source, *, cap: int = DEFAULT_SPLIT_CAP, template: str = "bare") -> list[TextUnit]:
    """Every text of a law, ready to embed — articles, container epigraphs,
    loose content, the preamble and the closing signatures, in document
    order.

    `source` is either Markdown as a `str` or an already-parsed `AknNode`
    (`md2akn.parse_markdown`'s return), so a caller who parsed for another
    reason does not pay to parse twice.

    The seven rules (issue #218 Fase 1):

    1. Frontmatter and reform annotations are metadata, never embedded.
    2. An article no longer than `cap` (normalized) is one unit, whole.
    3. A longer article splits at its direct children: the chapeau alone,
       then one piece per remaining child, each prefixed with the chapeau.
    4. A container's epigraph — the text before its first child — is its own
       `heading` unit.
    5. Consecutive leaf children of a non-article node are grouped into one
       `loose` unit each, up to `cap`.
    6. `preamble` and `conclusions` are one unit each.
    7. `heading`/`loose` units always carry their ancestor `path` in the
       embedded text; whether an article carries its own number and the
       law's name is `template`'s call (`"bare"` or `"contextual"`).

    >>> import md2akn
    >>> text = (
    ...     "**CAPITULO I**\\n\\n**Del objeto**\\n\\n"
    ...     "**Artículo 1o.** Esta ley regula el objeto.\\n"
    ... )
    >>> units = md2akn.text_units(text)
    >>> [(u.unit_type, u.text) for u in units]
    [('heading', '**CAPITULO I** **Del objeto**'), ('article', '**Artículo 1o.** Esta ley regula el objeto.')]
    """
    if template not in TEMPLATES:
        raise ValueError(f"unknown template: {template!r} (expected one of {TEMPLATES})")
    tree = parse_markdown(source) if isinstance(source, str) else source
    doc_text = tree.span.doc.text
    ann_ranges = _RangeIndex(_annotation_ranges(tree, doc_text), doc_text)
    law_name = tree.meta.get("nombre_buscado") or None

    units: list[TextUnit] = []
    preamble = next((c for c in tree.children if c.akn_type == "preamble"), None)
    if preamble is not None:
        units.append(_leaf_unit("preamble", preamble, ann_ranges))

    body = next((c for c in tree.children if c.akn_type == "body"), None)
    if body is not None:
        root_path = (law_name,) if law_name else ()
        _walk_container(body, root_path, ann_ranges, cap, template, law_name, units)

    conclusions = next((c for c in tree.children if c.akn_type == "conclusions"), None)
    if conclusions is not None:
        units.append(_leaf_unit("conclusions", conclusions, ann_ranges))

    return units


def leaf_map(tree: AknNode, units: list[TextUnit]) -> list[LeafRef]:
    """Every leaf `eId` in `tree`, mapped to the unit that carries it.

    Recovered by character-range containment against each unit's
    `(start_char, end_char)` rather than stored while `text_units` builds
    them: `units` is everything a reader has once it comes back off
    `units.parquet`, and this has to be reconstructible from that alone.

    >>> import md2akn
    >>> text = "**Artículo 1o.** Uno.\\n\\n**Artículo 2o.** Dos.\\n"
    >>> tree = md2akn.parse_markdown(text)
    >>> units = md2akn.text_units(text)
    >>> [(ref.eId, ref.unit_eId) for ref in md2akn.leaf_map(tree, units)]
    [('art_1o__p_1', 'art_1o'), ('art_2o__p_1', 'art_2o')]
    """
    starts = sorted(units, key=lambda u: u.start_char)
    boundaries = [u.start_char for u in starts]
    refs = []
    for node in tree.walk():
        if node.children or node.akn_type in ("act", "body"):
            continue
        i = bisect.bisect_right(boundaries, node.start_char) - 1
        if i < 0:
            continue
        owner = starts[i]
        if not (owner.start_char <= node.start_char < owner.end_char):
            continue
        refs.append(
            LeafRef(eId=node.eId, akn_type=node.akn_type, unit_eId=owner.eId, unit_piece=owner.piece)
        )
    return refs


def coverage(tree: AknNode, units: list[TextUnit]) -> Coverage:
    """The coverage invariant, as data: how many non-whitespace characters of
    `tree`'s document fall into each of frontmatter, an annotation, some
    unit's text, or none of those.

    A well-formed law has `uncovered_chars == 0` — see `Coverage`.

    >>> import md2akn
    >>> text = "**Artículo 1o.** Uno.\\n"
    >>> tree = md2akn.parse_markdown(text)
    >>> cov = md2akn.coverage(tree, md2akn.text_units(text))
    >>> cov.uncovered_chars
    0
    """
    doc_text = tree.span.doc.text
    n = len(doc_text)
    owner = bytearray(n)  # 0 uncovered, 1 frontmatter, 2 annotation, 3 unit

    def mark(start: int, end: int, code: int) -> None:
        start, end = max(0, start), min(n, end)
        if end > start:
            owner[start:end] = bytes([code]) * (end - start)

    mark(0, tree.start_char, 1)
    for unit in units:
        mark(unit.start_char, unit.end_char, 3)
    for start, end in _annotation_ranges(tree, doc_text):
        mark(start, end, 2)

    total = frontmatter = annotation = covered = uncovered = 0
    for i, ch in enumerate(doc_text):
        if ch.isspace():
            continue
        total += 1
        code = owner[i]
        if code == 1:
            frontmatter += 1
        elif code == 2:
            annotation += 1
        elif code == 3:
            covered += 1
        else:
            uncovered += 1

    return Coverage(
        total_chars=total,
        frontmatter_chars=frontmatter,
        annotation_chars=annotation,
        covered_chars=covered,
        uncovered_chars=uncovered,
    )

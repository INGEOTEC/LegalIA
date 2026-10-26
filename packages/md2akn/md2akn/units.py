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

from md2akn.model import REFERS_TO_ANEXO, REFERS_TO_TRANSITORIOS, AknNode
from md2akn.outline import MARCADORES_EN_TEXTO
from md2akn.pipeline import parse_markdown

#: The split threshold `text_units` uses when no `cap` is given: measured
#: over the cached `scjn-leyes` release as the point past which an article's
#: own text stops reading like the rest of the corpus (#218's own
#: measurement table: median article length is in the low hundreds).
DEFAULT_SPLIT_CAP = 2000

_WHITESPACE = re.compile(r"\s+")

#: A blank line — the paragraph boundary rule 9 splits an over-cap unit at,
#: and exactly the boundary `md2akn.segmenter.iter_blocks` reads the document
#: with: a Markdown block *is* a paragraph here.
_PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n")

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
    # Review fix-1 of #291 (rule 16): a `PARTE`, and an `ANEXO`/`APÉNDICE` (its
    # word is `refers_to`'s, see `_container_label`).
    "attachment": "ANEXO",
    "part": "PARTE",
    "book": "LIBRO",
    "title": "TÍTULO",
    "chapter": "CAPÍTULO",
    "section": "SECCIÓN",
    "level": "APARTADO",
    # Review fix-4 of #291 (rules 29 to 31): a labelled block or a division. Its
    # label *is* its `num` (see `_container_label`), so this word is only the
    # fallback for a division with no heading.
    "hcontainer": "DIVISIÓN",
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
    # Issue #291's rules 12 and 13: a container's introductory text, and a
    # table (cut at row boundaries).
    "intro",
    "table",
    # Review fix-1 of #291's rule 15: a table of contents.
    "toc",
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

    #: `article | article_piece | preamble | heading | loose | conclusions |
    #: intro | table | toc` (see `UNIT_TYPES`).
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


@dataclass(frozen=True)
class CapReport:
    """The cap invariant, as data (issue #227's rule 9) — `coverage()`'s
    sibling: `coverage()` turned "nothing is lost" into something a test can
    fail on, this turns "the units are usable" into the same.

    `splittable == 0` is the invariant. An over-cap unit is only admissible
    when rule 9 has nothing left to cut, which happens for two reasons and is
    counted in `unsplittable` — the residue #218's own 665 over-cap units
    were counted as:

    - the unit's own span is a **single paragraph** — rule 9 never cuts
      mid-sentence, so a 52,578-character paragraph stays one unit;
    - or what pushes it over the cap is a prefix rather than its own text:
      rule 3 prefixes every piece of a split article with that article's
      chapeau, and `cff`'s article 20 has a 3,500-character chapeau, so each
      of its 14 pieces is over the cap no matter how finely the piece itself
      is cut. Splitting the chapeau would leave a piece carrying half of its
      own context, which is worse than a long unit.
    """

    cap: int
    units: int
    #: The longest unit's embedded text, in characters.
    max_chars: int
    #: Units whose text is longer than `cap`.
    over_cap: int
    #: Of those, the ones rule 9 has nothing left to cut — see above.
    unsplittable: int
    #: Of those, the ones that could still have been cut — the invariant.
    splittable: int
    #: `article` units over `cap` that rule 3/9 never had a chance to split
    #: because `split_articles=False` — counted separately from `over_cap`
    #: (issue #256), since leaving an article whole is the point rather than
    #: a residue. Always 0 when `split_articles=True` (the default).
    articles_over_cap: int = 0


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
    # An outline marker (rule 19) is metadata exactly like an annotation: it
    # is no part of what gets embedded, and `coverage()` counts its
    # characters as annotation.
    if "<!-- scjn:block " in doc_text:
        ranges.extend((m.start(), m.end()) for m in MARCADORES_EN_TEXTO.finditer(doc_text))
        ranges.sort()
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
    if node.akn_type == "hcontainer":
        return node.num or node.heading or _CONTAINER_LABEL["hcontainer"]
    if node.refers_to == REFERS_TO_TRANSITORIOS:
        label = "TRANSITORIOS"
        if node.num and node.num != "transitorios":
            label = f"{label} {node.num}"
        return label
    label = _CONTAINER_LABEL.get(node.akn_type, node.akn_type.upper())
    if node.akn_type == "attachment" and node.refers_to != REFERS_TO_ANEXO:
        label = "APÉNDICE"
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


def _unpipe_label_rows(raw_text: str) -> str:
    """`raw_text` with every line written as a table row (`| CAPÍTULO II |  |`)
    reduced to its cells joined by a space. The SCJN's own text puts a tab after
    some container labels, which `scjn.api` reads as a two-cell row; in a
    *heading* that row is the label, not a table (review fix-2 of #291: the
    marker says it is the container's own heading block)."""
    lines = []
    for line in raw_text.split("\n"):
        stripped = line.strip()
        if len(stripped) > 1 and stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            line = " ".join(c for c in cells if c)
        lines.append(line)
    return "\n".join(lines)


def _with_article_template(raw_text: str, template: str, num: str | None, law_name: str | None) -> str:
    """`raw_text`, prefixed as `template` says — rule 7's article prefix.

    Takes the article's `num` rather than the node itself so that rule 9 can
    re-wrap one of its pieces with the same prefix without carrying the tree
    around (see `_capped_pieces`).
    """
    if template == "contextual":
        prefix_parts = []
        if law_name:
            prefix_parts.append(law_name)
        if num:
            prefix_parts.append(f"Artículo {num}")
        if prefix_parts:
            return normalize(f"{', '.join(prefix_parts)}. {raw_text}")
    return normalize(raw_text)


def _paragraph_spans(doc_text: str, start: int, end: int) -> list[tuple[int, int]]:
    """`[start, end)` cut at blank lines, one span per paragraph, in order.

    The separator stays with the paragraph it follows, so the spans partition
    the range exactly — no character falls between two of them, which is what
    keeps `coverage()` true of a unit rule 9 has split.
    """
    spans: list[tuple[int, int]] = []
    cursor = start
    for m in _PARAGRAPH_BREAK.finditer(doc_text, start, end):
        if m.end() <= cursor:
            continue
        spans.append((cursor, m.end()))
        cursor = m.end()
    if cursor < end:
        spans.append((cursor, end))
    return spans


def _capped_pieces(
    start: int,
    end: int,
    ann_ranges: "_RangeIndex",
    cap: int,
    wrap,
    split_over_cap: bool,
) -> list[tuple[int, int, str]]:
    """Rule 9 (issue #227): `[start, end)` as consecutive
    `(start, end, text)` pieces, each fitting `cap`.

    One piece — the whole range — whenever the text already fits, whenever
    `split_over_cap` is off, or whenever the range is a **single paragraph**
    that does not fit: an over-cap paragraph is left whole rather than cut
    mid-sentence, and `max_unit_chars` counts what is left that way. Otherwise
    paragraphs are packed greedily, in document order, into the longest run
    whose own embedded text still fits.

    `wrap` is the unit's own text-building step — `normalize`, the ancestor
    path prefix, or the article template — applied to every candidate piece,
    so the cap is measured against what actually gets embedded rather than
    against the raw slice.
    """
    doc_text = ann_ranges.doc_text
    whole = wrap(_strip_ranges(doc_text, start, end, ann_ranges))
    if not split_over_cap or len(whole) <= cap:
        return [(start, end, whole)]
    spans = _paragraph_spans(doc_text, start, end)
    if len(spans) <= 1:
        return [(start, end, whole)]

    pieces: list[tuple[int, int, str]] = []
    grupo: tuple[int, int, str] | None = None
    for s, e in spans:
        if grupo is None:
            grupo = (s, e, wrap(_strip_ranges(doc_text, s, e, ann_ranges)))
            continue
        candidato = wrap(_strip_ranges(doc_text, grupo[0], e, ann_ranges))
        if len(candidato) > cap:
            pieces.append(grupo)
            grupo = (s, e, wrap(_strip_ranges(doc_text, s, e, ann_ranges)))
        else:
            grupo = (grupo[0], e, candidato)
    pieces.append(grupo)

    # A piece whose text came out empty -- a paragraph that was nothing but a
    # reform annotation, stripped as metadata -- is not a unit. Its characters
    # still have to belong to one, or `coverage()` would report them
    # uncovered, so they are folded into the neighbouring piece's span.
    fusionadas: list[tuple[int, int, str]] = []
    arrastre = None
    for pieza in pieces:
        if not pieza[2]:
            if fusionadas:
                anterior = fusionadas[-1]
                fusionadas[-1] = (anterior[0], pieza[1], anterior[2])
            else:
                arrastre = pieza[0] if arrastre is None else arrastre
            continue
        if arrastre is not None:
            pieza = (arrastre, pieza[1], pieza[2])
            arrastre = None
        fusionadas.append(pieza)
    return fusionadas or [(start, end, whole)]


def _units_from_pieces(
    pieces: list[tuple[int, int, str]],
    *,
    unit_type: str,
    eId: str,
    akn_type: str,
    num: str | None,
    path: tuple[str, ...],
    piece_eId: str | None,
    first_piece: int = 0,
    piece_type: str | None = None,
    whole_if_single: bool = True,
) -> list[TextUnit]:
    """`_capped_pieces`' output as `TextUnit`s.

    A single piece is the unit whole, `piece` 0 — exactly what every unit
    looked like before rule 9. Several are consecutive pieces of it, numbered
    from `first_piece + 1`, all carrying the same `piece_eId` (the node the
    text came from, when there is one: paragraph pieces of the same child of
    an article share it) and `piece_type` as their `unit_type` when the split
    changes what they are — a split `article` becomes `article_piece`, while a
    split `preamble` stays a `preamble`, since the vocabulary has no other
    name for it and rule 9 adds none (issue #227).

    `whole_if_single` is off for the pieces rule 3 already numbers — one of
    an article's children is piece `n` of that article whether or not rule 9
    then cuts it in two, and renumbering it 0 would claim it is the whole
    article.
    """
    if whole_if_single and len(pieces) == 1:
        start, end, text = pieces[0]
        return [
            TextUnit(
                unit_type=unit_type, eId=eId, piece=first_piece,
                piece_eId=piece_eId if first_piece else None,
                akn_type=akn_type, num=num, path=path,
                start_char=start, end_char=end, text=text, text_sha1=_sha1(text),
            )
        ]
    return [
        TextUnit(
            unit_type=piece_type or unit_type, eId=eId, piece=first_piece + i,
            piece_eId=piece_eId, akn_type=akn_type, num=num, path=path,
            start_char=start, end_char=end, text=text, text_sha1=_sha1(text),
        )
        for i, (start, end, text) in enumerate(pieces, 1)
    ]


def _leaf_units(
    unit_type: str, node: AknNode, ann_ranges, cap: int, split_over_cap: bool
) -> list[TextUnit]:
    pieces = _capped_pieces(
        node.start_char, node.end_char, ann_ranges, cap, normalize, split_over_cap
    )
    return _units_from_pieces(
        pieces, unit_type=unit_type, eId=node.eId, akn_type=node.akn_type,
        num=node.num, path=(), piece_eId=None,
    )


def _article_units(
    article: AknNode,
    path: tuple[str, ...],
    ann_ranges,
    cap: int,
    template: str,
    law_name: str | None,
    split_over_cap: bool = True,
    split_articles: bool = True,
) -> list[TextUnit]:
    def wrap(raw_text: str, chapeau_raw: str = "") -> str:
        combined = f"{chapeau_raw}\n\n{raw_text}" if chapeau_raw else raw_text
        return _with_article_template(combined, template, article.num, law_name)

    whole_raw = _node_text(article, ann_ranges)
    if not split_articles:
        # split_articles=False: rules 3 and 9 never apply to an article --
        # whatever its length, it is one `article` unit, `piece` 0.
        text = wrap(whole_raw)
        return [
            TextUnit(
                unit_type="article", eId=article.eId, piece=0, piece_eId=None,
                akn_type="article", num=article.num, path=path,
                start_char=article.start_char, end_char=article.end_char,
                text=text, text_sha1=_sha1(text),
            )
        ]
    if len(normalize(whole_raw)) <= cap or not article.children:
        # Rule 2, unchanged — and rule 9 behind it, for the one shape rule 3
        # cannot reach: an article with no children at all whose own text is
        # over the cap (a 25,000-character transitorio, say).
        pieces = _capped_pieces(
            article.start_char, article.end_char, ann_ranges, cap, wrap, split_over_cap
        )
        return _units_from_pieces(
            pieces, unit_type="article", eId=article.eId, akn_type="article",
            num=article.num, path=path, piece_eId=None, piece_type="article_piece",
        )

    chapeau = [c for c in article.children if c.is_chapeau]
    remaining = [c for c in article.children if not c.is_chapeau]
    units: list[TextUnit] = []
    piece = 0
    chapeau_raw = ""
    if chapeau:
        chapeau_raw = _group_text(chapeau, ann_ranges)
        pieces = _capped_pieces(
            chapeau[0].start_char, chapeau[-1].end_char, ann_ranges, cap, wrap,
            split_over_cap,
        )
        units.extend(_units_from_pieces(
            pieces, unit_type="article_piece", eId=article.eId, akn_type="article",
            num=article.num, path=path, piece_eId=chapeau[0].eId, first_piece=piece,
            whole_if_single=False,
        ))
        piece = units[-1].piece
    for child in remaining:
        pieces = _capped_pieces(
            child.start_char, child.end_char, ann_ranges, cap,
            lambda raw: wrap(raw, chapeau_raw), split_over_cap,
        )
        units.extend(_units_from_pieces(
            pieces, unit_type="article_piece", eId=article.eId, akn_type="article",
            num=article.num, path=path, piece_eId=child.eId, first_piece=piece,
            whole_if_single=False,
        ))
        piece = units[-1].piece
    return units


def _is_loose_leaf(node: AknNode) -> bool:
    # Rule 35 (review fix-6 of #291): a container with nothing under it -- an
    # annex, a part, a chapter whose text is only its own heading line -- is a
    # heading, not loose text; `_walk_container` emits it as one.
    return (not node.children
            and node.akn_type not in ("article", "table", "toc") + tuple(_CONTAINER_TYPES))


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


def _loose_units(
    leaves: list[AknNode],
    path: tuple[str, ...],
    ann_ranges,
    cap: int,
    split_over_cap: bool = True,
) -> list[TextUnit]:
    units = []
    for group in _loose_groups(leaves, ann_ranges, cap):
        # The grouping above packs by raw length; the cap rule 9 enforces is
        # on the embedded text, which also carries the ancestor path.
        pieces = _capped_pieces(
            group[0].start_char, group[-1].end_char, ann_ranges, cap,
            lambda raw: _with_context(raw, path), split_over_cap,
        )
        units.extend(_units_from_pieces(
            pieces, unit_type="loose", eId=group[0].eId, akn_type=group[0].akn_type,
            num=None, path=path, piece_eId=None,
        ))
    return units


def _table_units(
    table: AknNode,
    path: tuple[str, ...],
    ann_ranges,
    cap: int,
    split_over_cap: bool = True,
) -> list[TextUnit]:
    """Rule 13 (issue #291): one `table` node as `table` units, cut at row
    boundaries.

    A row is one paragraph of the table's span (`scjn.api` writes one row per
    Markdown paragraph, issue #253). Rows are packed in order into the longest
    run whose embedded text still fits `cap`, and **every piece after the
    first is prefixed with the table's first row** -- its column header --
    chapeau-style, the way rule 3 repeats an article's chapeau: without it the
    pieces of a tariff would be rows of numbers with no column names. The
    prefix is part of the embedded text only; a piece's `[start_char,
    end_char)` is its own rows, so the pieces partition the table's span and
    `coverage()` stays true. Like every unit it carries its ancestor `path`.

    A single row over the cap is left whole -- rule 9 never cuts inside a
    paragraph -- and so is a row whose header prefix pushes it over:
    `max_unit_chars` counts both as unsplittable. `split_over_cap=False`
    turns the cutting off and gives one unit per table.
    """
    doc_text = ann_ranges.doc_text
    rows = _paragraph_spans(doc_text, table.start_char, table.end_char)
    header_raw = _strip_ranges(doc_text, rows[0][0], rows[0][1], ann_ranges)

    def text_of(start: int, end: int, first: bool) -> str:
        raw = _strip_ranges(doc_text, start, end, ann_ranges)
        return _with_context(raw if first else f"{header_raw}\n\n{raw}", path)

    whole = text_of(table.start_char, table.end_char, True)
    if not split_over_cap or len(whole) <= cap or len(rows) <= 1:
        pieces = [(table.start_char, table.end_char, whole)]
    else:
        pieces = []
        start, end = rows[0]
        text = text_of(start, end, True)
        for row_start, row_end in rows[1:]:
            candidate = text_of(start, row_end, not pieces)
            if len(candidate) > cap:
                pieces.append((start, end, text))
                start, end = row_start, row_end
                text = text_of(start, end, False)
            else:
                end, text = row_end, candidate
        pieces.append((start, end, text))
    return _units_from_pieces(
        pieces, unit_type="table", eId=table.eId, akn_type="table", num=None,
        path=path, piece_eId=None,
    )


def _intro_units(
    intro: AknNode,
    path: tuple[str, ...],
    ann_ranges,
    cap: int,
    split_over_cap: bool = True,
) -> list[TextUnit]:
    """Rules 12 and 13 (issue #291) for one container's `intro`: its plain
    paragraphs as `intro` units, its tables as `table` units, in document
    order.

    The paragraphs between two tables (or before the first, or after the last)
    are packed like `loose` leaves -- up to `cap`, then cut at paragraph
    boundaries by rule 9 -- and all carry the intro's own `eId`: one piece is
    the unit whole (`piece` 0), several are numbered `1..n` across the whole
    intro, in document order. A table is never part of an `intro` unit: a
    LIGIE chapter's *Nota.* paragraphs are one `intro` unit and its tariff is
    one or more `table` units.
    """
    pieces: list[tuple[int, int, str]] = []
    tables: list[TextUnit] = []
    run: list[AknNode] = []

    def flush() -> None:
        for group in _loose_groups(run, ann_ranges, cap):
            pieces.extend(_capped_pieces(
                group[0].start_char, group[-1].end_char, ann_ranges, cap,
                lambda raw: _with_context(raw, path), split_over_cap,
            ))
        run.clear()

    for child in intro.children:
        if child.akn_type == "table":
            flush()
            tables.extend(_table_units(child, path, ann_ranges, cap, split_over_cap))
        else:
            run.append(child)
    flush()
    units = _units_from_pieces(
        pieces, unit_type="intro", eId=intro.eId, akn_type="intro", num=None,
        path=path, piece_eId=None,
    )
    return sorted(units + tables, key=lambda unit: unit.start_char)


def _toc_units(
    toc: AknNode,
    path: tuple[str, ...],
    ann_ranges,
    cap: int,
    split_over_cap: bool = True,
) -> list[TextUnit]:
    """Rule 15 (review fix-1 of #291): one `toc` node as `toc` units.

    A table of contents is text, and `coverage()` has to account for it, but it
    is not law: it lists headings the document goes on to write. It is cut like
    `loose` text (paragraph boundaries, up to `cap`) and carries its ancestor
    `path`, and the matrix treats it like a heading -- neither a source nor a
    candidate -- because what an index says is *where* a text is, not what it
    says.
    """
    pieces = _capped_pieces(
        toc.start_char, toc.end_char, ann_ranges, cap,
        lambda raw: _with_context(raw, path), split_over_cap,
    )
    return _units_from_pieces(
        pieces, unit_type="toc", eId=toc.eId, akn_type="toc", num=None, path=path,
        piece_eId=None,
    )


def _walk_container(
    node: AknNode,
    path: tuple[str, ...],
    ann_ranges,
    cap: int,
    template: str,
    law_name: str | None,
    units: list[TextUnit],
    split_over_cap: bool = True,
    split_articles: bool = True,
) -> None:
    children = node.children
    first_start = children[0].start_char if children else node.end_char
    if node.akn_type in _CONTAINER_TYPES:
        heading_raw = _strip_ranges(ann_ranges.doc_text, node.start_char, first_start, ann_ranges)
        if normalize(heading_raw):
            pieces = _capped_pieces(
                node.start_char, first_start, ann_ranges, cap,
                lambda raw: _with_context(_unpipe_label_rows(raw), path), split_over_cap,
            )
            units.extend(_units_from_pieces(
                pieces, unit_type="heading", eId=node.eId, akn_type=node.akn_type,
                num=node.num, path=path, piece_eId=None,
            ))
        child_path = path + (_container_label(node),)
    else:
        child_path = path

    i, n = 0, len(children)
    while i < n:
        child = children[i]
        if child.akn_type == "article":
            units.extend(_article_units(
                child, child_path, ann_ranges, cap, template, law_name, split_over_cap,
                split_articles,
            ))
            i += 1
        elif child.akn_type == "intro":
            units.extend(_intro_units(child, child_path, ann_ranges, cap, split_over_cap))
            i += 1
        elif child.akn_type == "table":
            units.extend(_table_units(child, child_path, ann_ranges, cap, split_over_cap))
            i += 1
        elif child.akn_type == "toc":
            units.extend(_toc_units(child, child_path, ann_ranges, cap, split_over_cap))
            i += 1
        elif _is_loose_leaf(child) and child.akn_type != "hcontainer":
            j = i + 1
            while j < n and _is_loose_leaf(children[j]):
                j += 1
            units.extend(_loose_units(children[i:j], child_path, ann_ranges, cap, split_over_cap))
            i = j
        else:
            _walk_container(
                child, child_path, ann_ranges, cap, template, law_name, units, split_over_cap,
                split_articles,
            )
            i += 1


def text_units(
    source,
    *,
    cap: int = DEFAULT_SPLIT_CAP,
    template: str = "bare",
    split_over_cap: bool = True,
    split_articles: bool = True,
) -> list[TextUnit]:
    """Every text of a law, ready to embed — articles, container epigraphs,
    loose content, the preamble and the closing signatures, in document
    order.

    `source` is either Markdown as a `str` or an already-parsed `AknNode`
    (`md2akn.parse_markdown`'s return), so a caller who parsed for another
    reason does not pay to parse twice.

    The twenty-four rules — seven from issue #218's Fase 1, two added by #227, one
    by #279, three by #291 and eleven by its review fixes, numbered into the same list rather than
    replacing any of them:

    1. Frontmatter and reform annotations are metadata, never embedded.
    2. An article no longer than `cap` (normalized) is one unit, whole. With
       `split_articles=False`, every article is one unit whatever its
       length — this rule then applies unconditionally, and rule 3 never
       fires.
    3. A longer article splits at its direct children: the chapeau alone,
       then one piece per remaining child, each prefixed with the chapeau.
       Never applied when `split_articles=False` (see rule 2).
    4. A container's epigraph — the text before its first child — is its own
       `heading` unit.
    5. Consecutive leaf children of a non-article node are grouped into one
       `loose` unit each, up to `cap`.
    6. `preamble` and `conclusions` are one unit each.
    7. `heading`/`loose` units always carry their ancestor `path` in the
       embedded text; whether an article carries its own number and the
       law's name is `template`'s call (`"bare"` or `"contextual"`).
    8. An instrument that never writes `Artículo N` numbers its provisions
       some other way, and that is decided once per document, in
       `md2akn.structure.modo_sin_articulos` — bold ordinals
       (`**PRIMERO.-**`), or failing those decimal numerals (`1.`, `2.1`),
       open articles in its body too. Gated on the *document*, so no law and
       no article-numbered reglamento can change.
    9. No unit exceeds `cap`: one still over it after rules 2-6 is cut again
       at **paragraph** boundaries into consecutive pieces, reusing `piece` /
       `piece_eId` and the same context prefix. A single paragraph longer
       than `cap` is left whole — never cut mid-sentence — and
       `max_unit_chars` counts what is left that way. `split_over_cap=False`
       turns this rule off, which is how a pre-#227 vector set is reproduced
       byte for byte. Never applied to an article when `split_articles=False`
       (see rule 2) — an over-cap article is then left whole, counted by
       `max_unit_chars`' own `articles_over_cap` rather than `over_cap`.
    10. A roman-numbered article opener (`ARTÍCULO I`, `**ARTÍCULO IV.-**`) is
       an article — how an international treaty numbers its own articles
       (issue #279). Unconditional: a roman alternative in
       `patterns.NUM_ARTICULO`, so the gate of rule 8 sees it too and a
       roman-numbered treaty is never read as an *acuerdo*.
    11. A decimal article number is read whole: `Artículo 1.1:` is article
       `1.1` (eId `art_1.1`), not article `1`. How the T-MEC and six other
       treaties number their articles (issue #291). Unconditional, and only
       after the article word.
    12. A container's introductory text -- every block between its heading
       (epigraph included) and its first child container or article -- is
       that container's `intro` node and a `intro` unit, not a `loose` one:
       the *Notas.* of a LIGIE section or chapter (issue #291). Capped and cut
       like `loose`, carrying the same ancestor `path`; text before any
       container keeps its `preamble`/`loose` treatment.
    13. Consecutive table rows (blocks opening and closing with `|`, as
       `scjn.api` writes them since #253) are one `table` node, and a table
       that is not inside an article yields `table` units cut at row
       boundaries up to `cap`, each piece after the first prefixed with the
       table's first row -- its column header. A table inside an article is
       part of that article's own text. A table is never part of an `intro`
       unit.
    14. An article's number is read whole (review fix-1 of #291): `1o.-A BIS`,
       `3o.- Bis`, `228 a`, `228 a BIS`, `Artículo 5 Bis 1` are each their own
       number, not the base article's again. Unconditional, a widening of
       `patterns.NUM_ARTICULO`; `scjn.api`'s writer bolds the same shapes.
    15. A table of contents -- a run of short blocks after a marker (`Índice`,
       `Contenido`) that the document then repeats as its own headings -- is
       one `toc` node and `toc` units, never containers or articles. A marker
       with no repeat after it is left alone.
    16. `PARTE` is a container above the books and titles, and
       `ANEXO`/`APÉNDICE` an `attachment` closing a container of its own: the
       chapter `1.1` of Part 1 is not the chapter `1.1` of Part 2, and an
       annex is its own text.
    17. Recitals are not provisions: under `CONSIDERANDO`, numbered paragraphs
       stay in the preamble, in an instrument whose provisions are numbered
       the same way (rule 8); they end at a container, at the heading that
       opens what is ordered, at transitorios or when their numbering starts
       over. A `D.O.F.`-headed decree quoted in the text opens its own
       transitorios instead of continuing the host's.
    18. In rule 8's numeral mode a whole numeral that starts over (`1.` after
       `3.`), or carries on a list that did, is a list item of the provision
       above, not a further article. A dotted numeral (`2.1`) always opens one.
    19. (review fix-2 of #291, again after the fact.) A snapshot written by
       `scjn.api` since that fix carries the outline the SCJN's API states, one
       `<!-- scjn:block ... -->` marker line before each block (`kind`:
       header, recitals, preamble, toc, transitorios, decree, container,
       article, provision, other). The markers are metadata, like rule 1's
       frontmatter: never embedded, counted as annotation by `coverage()`.
       Where one is present it decides the class of the block's heading (a
       container with its type and number, an article with its number, the
       index as a `toc`) and the text heuristics are used only inside a
       block. A document with no marker is read exactly as before.
    20. A reform decree the API marks (`kind=decree`, `D.O.F.` plus a date)
       holds everything after it -- its own transitorios, an `ANEXO ÚNICO`
       reprinting the instrument -- as one section, so none of it is the host
       instrument's again.
    21. A container found in the *text* of a container's block nests below
       that container instead of beside it (`AknNode`'s floor): the `PARTE 1`
       an annex's block contains is inside that annex.
    22. An identifier is never silently reused: a second claimant of an eId
       takes a `_2` suffix and `AknNode.repeated` is true for **both**
       claimants, which is what the Instruments page's *repeated number* flag
       counts. A number that restarts inside another container is a
       different identifier and no repeat.
    23. A block the API marks `other` (or a second `preamble`) that holds
       articles in its own text, or is followed by article blocks, opens an
       implicit `section` until the next non-article marker: a code's
       `1 ÁMBITO` whose articles are `1.1`, `1.2`.
    24. Inside a block the API marks `article`, `header`, `preamble` or
       `recitals`, no line opens structure: an amendment article quoting
       `ARTÍCULO 5.-` is that article's own text. An article's identity is
       the number of its text when it equals or extends the API's label, the
       label when it extends the text's, else the one consistent with the
       API's own `numero`.
    25. An annex the API numbers after a chapter (`numero` 2, `num` `2-A`:
       the `N-` prefix is the fallback) is a child of the innermost open
       container carrying that number -- `cap_2__att_2-A` -- and the next
       container of the chapter's level or higher closes it, so a chapter is
       never inside an annex. An annex with no owner stays at the top. Its
       title is the text of its own label (`ANEXO 2-A:` is not annex `2`
       titled `A`).
    26. An appendix is an `attachment` whose `refersTo` is `#apendice` and
       whose eId prefix is `app` (`app_1`), never `att`. One the API marks
       with no owner nests in the annex that is open, after that annex's
       chapters, or else in the instrument.
    27. A bare `**APÉNDICE**` heading, with no number, directly under the
       unnumbered appendix it repeats is that appendix's own running page
       header (the T-MEC's tariff appendix of annex 2-C, over seven pages), not
       a further appendix.
    28. A block the API marks `other` that opens an implicit section (rule
       23) is a text of its own -- an amending `RESOLUCIÓN MSC.411(97)` --
       and hangs from the body whatever was open: never inside the appendix,
       annex or chapter before it.
    29. Every block the API marks `other` is a container of its own: an
       `hcontainer` whose `num` is its label and whose eId is derived from it
       (`hc_NOTA_EXPLICATIVA`). An explicit role table (`md2akn.outline.
       ROLES_DE_ETIQUETA`) gives some a `refersTo`: `PARTE FINAL ...` is
       `#conclusions`, `NOTA EXPLICATIVA` and `NOTAS` are `#note`, the steps of
       the Mexican promulgation decree (`FIRMA`, `APROBACIÓN`, `RESERVA(S)`,
       `RATIFICACIÓN Y DEPÓSITO`, `PROMULGACIÓN`, `CERTIFICACIÓN`,
       `DECLARACIÓN ...`) are kept together under one `#promulgation`
       container, and `RESOLUCIÓN ...` is the amending-resolution container of
       rule 28. A label that only *starts* with one of those words
       (`RESERVAS AL REGLAMENTO DE RADIOCOMUNICACIONES`) has no role. What the
       block holds before its first article is its `intro` (rule 12). An API
       `provision` block (`PRIMERO.-` of an acuerdo) is an article.
    30. A block the API split in two -- a title (`ARTÍCULOS`) and a second
       block labelled `CONTENIDO ARTICULOS` -- is one container.
    31. A numbering that starts over *inside* one block opens a division
       (`hcontainer`, `refersTo="#division"`; `#form` for a model certificate),
       so an identifier is the same article or it is not repeated: the
       `MODELO DE CERTIFICADO ...` of an appendix and the `SE CERTIFICA`
       placeholder of the certificates that print no title, a heading such as
       `A.II - VENTANAS` or `Artículos que se refieren al Comité Ejecutivo`.
       The boxes of a form are not articles, a numeral followed by a lower-case
       word (`1.1. cuando ...`) is a value and not an opener, and a numeral
       equal to the number of the article just above is that article's
       paragraph. A division needs a heading to open at: with none the repeat
       stays, flagged on both claimants, and nothing is merged or renumbered.
    32. The article-number grammar of the marker path reads `S5.43`/`5.43A`
       (a radio-regulations provision, under its `ARTÍCULO S5` section and
       `APÉNDICE S30` appendix), `Artículo Primero.-` with a mixed-case ordinal
       inside transitorios, and the caption line the SCJN prints *before* an
       article's heading (`Objeto`) as the start of that article.
    33. An API block is the root of its hierarchy (review fix-5 of #291).
       Every `RESOLUCIÓN ...` block is a `section`, whether or not its own text
       has articles (it used to be an `hcontainer` when it had none). The
       headings a block's text prints keep their order among themselves: an
       `ANEXO` holds the chapters printed after it, a chapter its parts
       (`PARTE A`, `B`, ...: the next chapter's `PARTE A` is its own, never
       `part_A_2`), a bare `APÉNDICE` after the annex of the same block is
       inside it, and the next marker closes the whole stack the block opened.
       The one annex that leaves its block is the one with no container or
       article after its heading in the block's text: the structure of the
       code a resolution amends, or of the instrument an `ANEXO ÚNICO`
       approves, is what the API serves in the blocks that follow, and they
       belong to that annex.
    34. An API block is the root of *all* its text (review fix-6 of #291).
       What a block's text holds hangs from the container the block opened,
       whatever transitorios section is open around it (the text of an `other`
       block after a reform decree used to hang from the decree: the block's
       container held only its heading line); an index belongs to the API block
       it starts in and ends at the next marker, never swallowing the heading
       the next block opens with; the lines between a marker and the heading it
       opens a container with (`[...]` before `Transitorios`, an institution's
       name above `SEGUNDA PARTE`) are that heading's; and the text a
       container (or the body) holds after its index, or after a captioned
       table, is its `intro` too. `md2akn.validate._block_escapes` checks it.
    35. A container with nothing under it -- an annex, a part, a chapter whose
       text is only its heading line -- is a `heading` unit, never `loose`.
    36. A table's caption belongs to it: one or two bold-led lines
       (`**A.** Tarifa para ...`, `**TARIFA**`) straight before its rows are the
       first lines of the `table` node (its `heading` is the first one), and the
       plain paragraphs right after the rows are its notes, up to the next
       caption. A table inside an article stays article text.
    37. `**Artículo 8o. **fracción II primer párrafo: $179,017.26`, a line that
       names an article and goes on in lower case, is a reference, not an
       article opener, unless the API says the block is an article.
    38. An API `article` block whose text is only its heading and a caption,
       followed by `provision` blocks, opens a section of those provisions
       (the numbered provisions of a regulation run on across its headed
       articles): when a document does it three times or more.
    39. A numbered provision the API could not classify because the source
       misprints its number (`kind=other`, label `98 (SIC)`) is an article.
    40. A bare `CONTENIDO` block (`kind=other`, no label after the word) that follows a
       block holding only its own title -- `ANEXO` and `CONTENIDO`, `PROTOCOLO`
       and `CONTENIDO` -- is that block's content, as rule 30 reads `ARTÍCULOS` and
       `CONTENIDO ARTICULOS` (issue #300). One that follows a block with text of
       its own (an index after a `CONSIDERANDO`) is read by the other rules.
    41. A heading line that is only `Art. 8`, straight before the block `**ARTICULO 8**`
       with the same number, is a running head and not an article (the Radio
       Regulations a treaty reprints); and an article number may end with a capital
       letter that closes the line (`Artículo 11 A`, issue #300).
    Rules 25 to 41 apply to a document with outline markers; one without
    them is read, and its eIds written, as before.

    >>> import md2akn
    >>> text = (
    ...     "**CAPITULO I**\\n\\n**Del objeto**\\n\\n"
    ...     "**Artículo 1o.** Esta ley regula el objeto.\\n"
    ... )
    >>> units = md2akn.text_units(text)
    >>> [(u.unit_type, u.text) for u in units]
    [('heading', '**CAPITULO I** **Del objeto**'), ('article', '**Artículo 1o.** Esta ley regula el objeto.')]

    Rule 9, on a document with one over-cap unit and three paragraphs to cut
    it at:

    >>> acuerdo = "\\n\\n".join(["Párrafo de %d." % n for n in range(1, 4)])
    >>> [(u.unit_type, u.piece, u.text) for u in md2akn.text_units(acuerdo, cap=20)]
    [('preamble', 1, 'Párrafo de 1.'), ('preamble', 2, 'Párrafo de 2.'), ('preamble', 3, 'Párrafo de 3.')]
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
        units.extend(_leaf_units("preamble", preamble, ann_ranges, cap, split_over_cap))

    body = next((c for c in tree.children if c.akn_type == "body"), None)
    if body is not None:
        root_path = (law_name,) if law_name else ()
        _walk_container(
            body, root_path, ann_ranges, cap, template, law_name, units, split_over_cap,
            split_articles,
        )

    conclusions = next((c for c in tree.children if c.akn_type == "conclusions"), None)
    if conclusions is not None:
        units.extend(_leaf_units("conclusions", conclusions, ann_ranges, cap, split_over_cap))

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


def max_unit_chars(
    tree: AknNode, units: list[TextUnit], *, cap: int = DEFAULT_SPLIT_CAP, split_articles: bool = True
) -> CapReport:
    """The cap invariant, as data: how many of `units` are longer than `cap`,
    and how many of those rule 9 could still have cut.

    `coverage()`'s sibling (issue #227). A corpus built with `split_over_cap`
    on has `splittable == 0`; whatever is left in `unsplittable` is a unit
    rule 9 has nothing left to cut — see `CapReport`.

    Counted against the *embedded* text — a `heading`/`loose` unit's ancestor
    path, an article piece's chapeau and an article's `contextual` prefix are
    all part of what the model sees, so they are part of what the cap is
    measured on. "Could still have been cut" is counted against the unit's
    own span instead, and only its paragraphs that carry text: a paragraph
    that is nothing but a reform annotation is stripped from the embedded
    text (rule 1), so cutting there would produce an empty unit.

    `split_articles` must match the flag `units` was built with (issue #256):
    when it is `False`, an over-cap `article` unit was never offered to rule 9
    at all, so it is counted in `articles_over_cap` instead of `over_cap` —
    `splittable == 0` stays the invariant either way.

    >>> import md2akn
    >>> text = "\\n\\n".join("Párrafo de %d." % n for n in range(1, 4)) + "\\n"
    >>> report = md2akn.max_unit_chars(
    ...     md2akn.parse_markdown(text), md2akn.text_units(text, cap=20), cap=20)
    >>> (report.units, report.max_chars, report.over_cap, report.splittable)
    (3, 13, 0, 0)
    """
    doc_text = tree.span.doc.text
    ann_ranges = _RangeIndex(_annotation_ranges(tree, doc_text), doc_text)
    max_chars = 0
    over_cap = unsplittable = splittable = articles_over_cap = 0
    for unit in units:
        n = len(unit.text)
        max_chars = max(max_chars, n)
        if n <= cap:
            continue
        if not split_articles and unit.unit_type == "article":
            articles_over_cap += 1
            continue
        over_cap += 1
        con_texto = [
            (start, end)
            for start, end in _paragraph_spans(doc_text, unit.start_char, unit.end_char)
            if normalize(_strip_ranges(doc_text, start, end, ann_ranges))
        ]
        # Rule 9's own decision, restated rather than re-derived: the greedy
        # packer emits more than one piece exactly when the span has two or
        # more paragraphs that carry text. A piece whose chapeau prefix alone
        # is over the cap therefore lands here as unsplittable, since its own
        # span is that one child paragraph.
        if len(con_texto) <= 1:
            unsplittable += 1
        else:
            splittable += 1
    return CapReport(
        cap=cap,
        units=len(units),
        max_chars=max_chars,
        over_cap=over_cap,
        unsplittable=unsplittable,
        splittable=splittable,
        articles_over_cap=articles_over_cap,
    )

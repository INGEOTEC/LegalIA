"""Slice the Markdown OCR'd from a note's scanned page images down to just the
note of interest.

The page images downloaded for a note (dofjson.download_nota_imagenes) hold
whole pages: the first page usually begins with the tail of the previous note,
and the last page often ends with the start of the next one. But the per-day
note index gives us the title of every note in order, so we can locate two
boundaries in the OCR'd text — where THIS note's title appears (start) and
where the NEXT note's title appears (end) — and keep only what lies between.

Matching is fuzzy on purpose: OCR text differs from the index title in case,
accents, line breaks and the odd misread character, so titles are compared on
an accent-folded, marker-stripped, whitespace-collapsed form, and located with
difflib's longest-matching-block alignment rather than an exact search.
"""
import difflib
import re
import unicodedata

_SKIP_CHARS = set("#*|\\>_`~[]")
_HEADING_LINE = re.compile(r"^#{1,6}\s")


def _normalize_with_map(text: str) -> tuple[str, list[int]]:
    """Return an accent-folded, lowercased, marker-stripped, single-spaced copy
    of `text`, alongside a map from each normalized-character position back to
    its index in the original `text` (so a match can be sliced from the
    original)."""
    norm_chars: list[str] = []
    idx_map: list[int] = []
    prev_space = True
    for i, ch in enumerate(text):
        base = unicodedata.normalize("NFKD", ch)
        base = "".join(c for c in base if not unicodedata.combining(c)).lower()
        if not base or base in _SKIP_CHARS:
            continue
        if base.isspace():
            if not prev_space:
                norm_chars.append(" ")
                idx_map.append(i)
                prev_space = True
            continue
        for c in base:
            if c in _SKIP_CHARS:
                continue
            norm_chars.append(c)
            idx_map.append(i)
        prev_space = False
    return "".join(norm_chars), idx_map


def _locate(
    norm_text: str, idx_map: list[int], title: str, from_norm: int = 0
) -> tuple[int | None, int | None, float]:
    """Best-effort match for `title` within `norm_text[from_norm:]`. Returns
    (normalized offset, original-text offset, 0..1 confidence), or
    (None, None, 0.0) if nothing matches.

    `from_norm` lets the caller skip past an earlier region — used to find the
    NEXT note's title after the current one, which matters when consecutive
    titles are near-duplicates (e.g. two deslinde avisos differing only in a
    number) and a whole-text search would otherwise anchor on the first."""
    norm_title, _ = _normalize_with_map(title)
    norm_title = norm_title.strip()
    if not norm_title:
        return None, None, 0.0

    haystack = norm_text[from_norm:]
    matcher = difflib.SequenceMatcher(None, haystack, norm_title, autojunk=False)
    blocks = matcher.get_matching_blocks()
    anchor = max(blocks, key=lambda b: b.size)
    if anchor.size == 0:
        return None, None, 0.0

    matched = sum(b.size for b in blocks)
    confidence = matched / len(norm_title)
    start_local = max(0, anchor.a - anchor.b)
    start_norm = min(from_norm + start_local, len(idx_map) - 1)
    return start_norm, idx_map[start_norm], confidence


def locate_titles(
    markdown: str, titulo: str, titulo_siguiente: str | None = None
) -> dict:
    """Locate the start (this note's title) and end (next note's title)
    boundaries in `markdown`. Exposed mainly for inspection/testing;
    cut_markdown_by_titles() is the usual entry point.

    Returns a dict with `start`, `start_confidence`, `end`, `end_confidence`
    (offsets are into `markdown`; `end` is None when there is no next title or
    it wasn't found after the start)."""
    norm_text, idx_map = _normalize_with_map(markdown)

    if titulo:
        start_norm, start, start_conf = _locate(norm_text, idx_map, titulo)
    else:
        start_norm, start, start_conf = None, None, 0.0
    if start is None:
        start_norm, start = 0, 0

    end, end_conf = None, 0.0
    if titulo_siguiente:
        # Skip past the current note's own title before searching for the next
        # one, so near-duplicate titles don't anchor the end back on the start.
        norm_titulo, _ = _normalize_with_map(titulo or "")
        from_norm = min(start_norm + len(norm_titulo.strip()), len(norm_text))
        _, candidate, end_conf = _locate(
            norm_text, idx_map, titulo_siguiente, from_norm=from_norm
        )
        if candidate is not None and candidate > start:
            end = candidate

    return {
        "start": start,
        "start_confidence": start_conf,
        "end": end,
        "end_confidence": end_conf,
    }


def cut_markdown_by_titles(
    markdown: str,
    titulo: str,
    titulo_siguiente: str | None = None,
    min_confidence: float = 0.6,
) -> str:
    """Return the slice of `markdown` that belongs to the note titled `titulo`.

    `titulo` and `titulo_siguiente` are the note's own title and the next
    note's title (in publication order) from the per-day index — the next
    title marks where this note ends.

    A boundary is only applied when its match confidence clears
    `min_confidence`: a start below the threshold falls back to the beginning
    of the text, and a weak/absent next-title match falls back to the end, so
    a poor match degrades to keeping more text rather than dropping the note's
    content."""
    if not markdown.strip():
        return ""

    located = locate_titles(markdown, titulo, titulo_siguiente)

    start = located["start"] if located["start_confidence"] >= min_confidence else 0
    boundary_applied = (
        located["end"] is not None and located["end_confidence"] >= min_confidence
    )

    # Snap the start to the beginning of its line, so the note's own heading
    # marker (`## `) travels with its title instead of being clipped off.
    start = markdown.rfind("\n", 0, start) + 1

    if boundary_applied:
        # Snap the end to the start of the next note's title line, then walk
        # back over the blank and heading lines right before it: the DOF prints
        # the next note's organism headers (e.g. `# SECRETARIA DE ENERGIA`)
        # ABOVE its title, so they'd otherwise be left dangling at the tail of
        # this note.
        end = markdown.rfind("\n", 0, located["end"]) + 1
        end = _trim_preceding_headings(markdown, end)
    else:
        end = len(markdown)
    if end <= start:
        end = len(markdown)

    return markdown[start:end].strip()


def _trim_preceding_headings(markdown: str, end: int) -> int:
    """Move `end` back over any run of blank and heading (`#…`) lines that
    immediately precede it, returning the new offset."""
    while end > 0:
        line_start = markdown.rfind("\n", 0, end - 1) + 1
        line = markdown[line_start:end].strip()
        if line == "" or _HEADING_LINE.match(line):
            end = line_start
        else:
            break
    return end

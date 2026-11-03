"""Rule-hit counters (issue #304): how often each text rule of the builder takes effect.

`md2akn`'s structure rules are listed in `RULES.md` (layers S, B, U). A rule that cannot
fire any more is dead code, and "cannot fire" is only known by counting: the parser calls
`hit` where a rule changes what it would otherwise have done, and
`scripts/embeddings/measure_trees.py --hits` runs it over every snapshot and prints the
counts, per rule, branch and whether the document carries outline markers. Nothing reads
these counters in production and `hit` is one dictionary increment, so leaving it in costs
nothing measurable; `HITS` is process-wide and `reset` empties it.

The key is ``<rule>/<branch>/<marked|unmarked>``: the rule's id (`RULES.md`), the branch
within it, and whether the document has outline markers (a branch that only fires on a
document with none stays, a branch with no hits at all is removed).
"""

from __future__ import annotations

from collections import Counter

#: ``{"S4/heading-in-block/marked": count}`` since the process started or `reset`.
HITS: Counter = Counter()


def hit(rule: str, branch: str, marked: bool = True) -> None:
    """Count one application of `rule`'s `branch`."""
    HITS[f"{rule}/{branch}/{'marked' if marked else 'unmarked'}"] += 1


def reset() -> None:
    HITS.clear()

"""Reproduce #218's own counts table over the whole cached `scjn-leyes`
release: how many `TextUnit`s of each `unit_type`, and their length
distribution.

This is not a test — see `packages/md2akn/tests/test_units_release_sweep.py`
for the one that asserts `coverage()`'s invariant at the same scale. This
script only reports; the release moves over time, so a number printed here
is a measurement of the corpus as it is cached *now*, never an assertion.

Reads the release straight off `scjn`'s own disk-first cache
(`scjn.iter_current_federal_laws`, populated by `nota2md download all` or the
`scjn download` CLI) — no network request of its own, and no file this
repository versions.

    python scripts/md2akn_units_sweep.py
    python scripts/md2akn_units_sweep.py --cap 2000 --template bare
"""

from __future__ import annotations

import argparse
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "md2akn"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "scjn"))

from md2akn import DEFAULT_SPLIT_CAP, coverage, parse_markdown, text_units  # noqa: E402
from scjn import iter_current_federal_laws  # noqa: E402


def _percentile(values: list[int], p: float) -> int:
    if not values:
        return 0
    values = sorted(values)
    k = min(len(values) - 1, int(round(p * (len(values) - 1))))
    return values[k]


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cap", type=int, default=DEFAULT_SPLIT_CAP)
    parser.add_argument("--template", choices=("bare", "contextual"), default="bare")
    args = parser.parse_args(argv)

    lengths: dict[str, list[int]] = defaultdict(list)
    laws = 0
    total_annotation_chars = 0
    total_uncovered_chars = 0
    distinct_hashes: set[str] = set()

    for ley in iter_current_federal_laws():
        laws += 1
        tree = parse_markdown(ley["markdown"])
        units = text_units(tree, cap=args.cap, template=args.template)
        cov = coverage(tree, units)
        total_annotation_chars += cov.annotation_chars
        total_uncovered_chars += cov.uncovered_chars
        for unit in units:
            lengths[unit.unit_type].append(len(unit.text))
            distinct_hashes.add(unit.text_sha1)

    print(f"{laws} laws, cap={args.cap}, template={args.template!r}\n")
    header = f"{'unit_type':<16}{'n':>10}{'median':>10}{'mean':>10}{'p95':>10}{'max':>10}"
    print(header)
    print("-" * len(header))
    total_units = 0
    for unit_type in ("article_piece", "article", "heading", "loose", "preamble", "conclusions"):
        values = lengths.get(unit_type, [])
        if not values:
            continue
        total_units += len(values)
        print(
            f"{unit_type:<16}{len(values):>10}"
            f"{int(statistics.median(values)):>10}"
            f"{int(statistics.mean(values)):>10}"
            f"{_percentile(values, 0.95):>10}"
            f"{max(values):>10}"
        )
    print("-" * len(header))
    print(f"{'total':<16}{total_units:>10}")
    print(f"\ndistinct texts after dedup by text_sha1: {len(distinct_hashes)}")
    print(f"annotation characters (never embedded), summed over every law: {total_annotation_chars}")
    print(f"uncovered characters, summed over every law (0 on a well-formed corpus): {total_uncovered_chars}")


if __name__ == "__main__":
    main()

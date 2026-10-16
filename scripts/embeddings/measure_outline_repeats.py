#!/usr/bin/env python3
"""Repeated article numbers before and after `md2akn` reads the SCJN's outline
(review fix-2 of #291), per instrument and per collection.

A number is **repeated** only when it is the same identifier: two articles that
`md2akn` could only keep apart with a `_2`-style suffix (`AknNode.repeated`).
`1.1` of Part 1 and `1.1` of Part 2 have different eIds and are not a repeat.
This counts, for the instruments of the Atlas (`instruments.json`), the
`duplicate_number` excerpts `export_instrument_files.excerpt_flags` flags on the
newest snapshot of each instrument under ``--scjn-dir`` (what a rebuild would
embed), twice:

    before   the snapshot with its outline markers stripped -- the Markdown the
             writer produced before the fix, parsed by the same `md2akn`
    after    the snapshot as it is now, markers and all

so the difference is what reading the outline contributes, with the parser held
fixed. Read-only; no network.

    uv run --group viz python scripts/embeddings/measure_outline_repeats.py \
        --json /tmp/fix2/repeats.json
"""

import argparse
import collections
import json
import sys
from datetime import datetime
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages" / "md2akn"))
sys.path.insert(0, str(ROOT / "packages" / "scjn"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import export_instrument_files as X  # noqa: E402
from md2akn import parse_markdown, text_units  # noqa: E402
from scjn.outline import strip_markers  # noqa: E402


def newest(directory: Path) -> Path:
    """The snapshot a rebuild embeds: the newest `fecha_publicacion`, a same-day
    `-N` repeat after the plain one (`scjn.release.iter_current_*`'s rule)."""
    def key(path: Path):
        return datetime.strptime(path.stem[:10], "%d-%m-%Y"), path.name

    return max(directory.glob("*.md"), key=key)


def repeats(markdown: str):
    """``(duplicate_number excerpts, eIds that collide)`` of one document."""
    tree = parse_markdown(markdown)
    units = text_units(tree, split_articles=False)
    collisions = {n.eId for n in tree.walk() if n.repeated}
    flags = X.excerpt_flags(units, set(), collisions)
    return (
        sum("duplicate_number" in names for names in flags),
        sorted(u.eId for u, names in zip(units, flags) if "duplicate_number" in names),
    )


def one(job):
    collection, key, path = job
    markdown = Path(path).read_text(encoding="utf-8")
    before, _ = repeats(strip_markers(markdown))
    after, eids = repeats(markdown)
    return {"c": collection, "k": key, "archivo": Path(path).name, "before": before,
            "after": after, "eids": eids[:12]}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--scjn-dir", type=Path, default=ROOT / "scripts" / "scjn")
    p.add_argument("--index", type=Path,
                   default=ROOT / "website" / "pages" / "instruments" / "instruments.json")
    p.add_argument("--json", type=Path, default=None)
    p.add_argument("--jobs", type=int, default=6)
    args = p.parse_args(argv)

    atlas = json.loads(args.index.read_text(encoding="utf-8"))["instruments"]
    jobs = [(e["c"], e["k"], str(newest(args.scjn_dir / e["c"] / e["k"]))) for e in atlas]
    with Pool(args.jobs) as pool:
        rows = pool.map(one, jobs, chunksize=8)
    summary = collections.defaultdict(lambda: collections.Counter())
    for row in rows:
        s = summary[row["c"]]
        s["instruments"] += 1
        s["before_instruments"] += row["before"] > 0
        s["after_instruments"] += row["after"] > 0
        s["before_excerpts"] += row["before"]
        s["after_excerpts"] += row["after"]
    for c, s in summary.items():
        print(c, dict(s))
    total = collections.Counter()
    for s in summary.values():
        total.update(s)
    print("all", dict(total))
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps({"summary": {c: dict(s) for c, s in summary.items()},
                                          "instruments": rows}, ensure_ascii=False, indent=1),
                             encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

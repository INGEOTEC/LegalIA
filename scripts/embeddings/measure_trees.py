#!/usr/bin/env python3
"""Which instruments' `md2akn` trees and units change between two versions of
the parser (review fix-4 of #291).

``--json PATH`` writes, for the newest snapshot of every instrument of the Atlas,
a digest of its tree (`akn_type`, `eId`, `refers_to`, `repeated` of every node, in
document order) and of its units (`unit_type`, `eId`, `text_sha1`), plus the
node and unit counts. Run it with the parser before a change and again after,
then ``--compare BEFORE AFTER`` lists what moved, per collection and with the
instruments named, so a rule that was meant to reach 7 instruments cannot
silently reach 700. Read-only; no network.
"""

import argparse
import collections
import hashlib
import json
import sys
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages" / "md2akn"))
sys.path.insert(0, str(ROOT / "packages" / "scjn"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from measure_outline_repeats import newest  # noqa: E402
from md2akn import parse_markdown, text_units  # noqa: E402


def digest(markdown: str) -> dict:
    tree = parse_markdown(markdown)
    nodes = [f"{n.akn_type} {n.eId} {n.refers_to} {int(n.repeated)}" for n in tree.walk()]
    units = text_units(tree, split_articles=False)
    unit_rows = [f"{u.unit_type} {u.eId} {u.text_sha1}" for u in units]
    return {
        "tree": hashlib.sha1("\n".join(nodes).encode()).hexdigest(),
        "units": hashlib.sha1("\n".join(unit_rows).encode()).hexdigest(),
        "nodes": len(nodes),
        "n_units": len(units),
        "types": dict(collections.Counter(n.split(" ")[0] for n in nodes)),
    }


def one(job):
    collection, key, path = job
    return {"c": collection, "k": key, "archivo": Path(path).name,
            **digest(Path(path).read_text(encoding="utf-8"))}


def compare(before: Path, after: Path) -> None:
    a = {(r["c"], r["k"]): r for r in json.loads(before.read_text(encoding="utf-8"))}
    b = {(r["c"], r["k"]): r for r in json.loads(after.read_text(encoding="utf-8"))}
    per = collections.defaultdict(lambda: collections.Counter())
    changed = []
    for key, x in a.items():
        y = b[key]
        s = per[key[0]]
        s["instruments"] += 1
        s["tree_changed"] += x["tree"] != y["tree"]
        s["units_changed"] += x["units"] != y["units"]
        if x["tree"] != y["tree"]:
            changed.append((key, x["nodes"], y["nodes"], x["n_units"], y["n_units"]))
    for c, s in per.items():
        print(c, dict(s))
    print("changed trees (collection key: nodes before->after, units before->after):")
    for key, nb, na, ub, ua in changed:
        print(f"  {key[0]:12} {key[1]:>8}: {nb}->{na}   {ub}->{ua}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--scjn-dir", type=Path, default=ROOT / "scripts" / "scjn")
    p.add_argument("--index", type=Path,
                   default=ROOT / "website" / "pages" / "instruments" / "instruments.json")
    p.add_argument("--json", type=Path)
    p.add_argument("--jobs", type=int, default=6)
    p.add_argument("--compare", nargs=2, type=Path, metavar=("BEFORE", "AFTER"))
    args = p.parse_args(argv)
    if args.compare:
        compare(*args.compare)
        return 0
    atlas = json.loads(args.index.read_text(encoding="utf-8"))["instruments"]
    jobs = [(e["c"], e["k"], str(newest(args.scjn_dir / e["c"] / e["k"]))) for e in atlas]
    with Pool(args.jobs) as pool:
        rows = pool.map(one, jobs, chunksize=8)
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(rows), encoding="utf-8")
    print(len(rows), "instruments")
    return 0


if __name__ == "__main__":
    sys.exit(main())

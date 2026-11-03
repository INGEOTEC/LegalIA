#!/usr/bin/env python3
"""Which instruments' `md2akn` trees and units change between two versions of
the parser (review fix-4 of #291).

``--all-snapshots`` (issue #304) digests *every* snapshot of the four corpora
(``scripts/scjn/<c>/<key>/*.md``, not only the newest of each Atlas instrument) with a
stricter digest: every node's type, eId, number, heading, `refers_to`, offsets,
chapeau/tail flags, `repeated`, notes and depth, and every unit's type, eId, piece, hash
and offsets, for both `split_articles` settings. It is what proves a refactor of the
builder changed nothing: `nota2md.legal_provisions` reads any reform's snapshot, so the
newest one alone is not enough. The default behaviour is unchanged.

``--json PATH`` writes, for the newest snapshot of every instrument of the Atlas,
a digest of its tree (`akn_type`, `eId`, `refers_to`, `repeated` of every node, in
document order) and of its units (`unit_type`, `eId`, `text_sha1`), plus the
node and unit counts. With ``--all-snapshots --hits`` every row also carries the rule-hit
counters of that parse (`md2akn.hits`), and ``--report-hits JSON`` sums them per rule and
branch: how a rule that can no longer fire is found, by counting rather than by reading. Run it with the parser before a change and again after,
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
from md2akn import hits, parse_markdown, text_units  # noqa: E402


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


def strict_digest(markdown: str) -> dict:
    """Everything `parse_markdown` and `text_units` return that a consumer can see."""
    tree = parse_markdown(markdown)
    nodes = []

    def walk(node, depth):
        notes = "|".join(f"{n.raw}~{n.action}~{n.scope}~{n.date}~{n.source}" for n in node.notes)
        nodes.append(
            f"{depth} {node.akn_type} {node.eId} {node.num} {node.heading} {node.refers_to} "
            f"{node.start_char} {node.end_char} {int(node.is_chapeau)} {int(node.is_tail)} "
            f"{int(node.repeated)} {node.modo} {notes}"
        )
        for child in node.children:
            walk(child, depth + 1)

    walk(tree, 0)
    out = {"nodes": len(nodes), "tree": hashlib.sha1("\n".join(nodes).encode()).hexdigest()}
    for name, split in (("units", False), ("units_split", True)):
        units = text_units(tree, split_articles=split)
        rows = [
            f"{u.unit_type} {u.eId} {u.piece} {u.piece_eId} {u.text_sha1} {u.start_char} "
            f"{u.end_char} {int(u.envelope)} {'/'.join(u.path)}"
            for u in units
        ]
        out[name] = hashlib.sha1("\n".join(rows).encode()).hexdigest()
        out["n_" + name] = len(units)
    return out


def one(job):
    collection, key, path, strict, with_hits = job
    text = Path(path).read_text(encoding="utf-8")
    if with_hits:
        hits.reset()
    row = {"c": collection, "k": key, "archivo": Path(path).name,
           **(strict_digest(text) if strict else digest(text))}
    if with_hits:
        row["hits"] = dict(hits.HITS)
    return row


def report_hits(path: Path) -> None:
    """Sum the per-snapshot counters of a ``--hits`` run: per ``rule/branch/marked``, the
    number of applications, of snapshots and of instruments it reached."""
    rows = json.loads(path.read_text(encoding="utf-8"))
    total = collections.Counter()
    snapshots = collections.Counter()
    instruments = collections.defaultdict(set)
    for r in rows:
        for key, n in r.get("hits", {}).items():
            total[key] += n
            snapshots[key] += 1
            instruments[key].add((r["c"], r["k"]))
    print(f"{'rule/branch/marked':58}{'hits':>12}{'snapshots':>11}{'instruments':>13}")
    for key in sorted(total):
        print(f"{key:58}{total[key]:>12}{snapshots[key]:>11}{len(instruments[key]):>13}")


def compare(before: Path, after: Path) -> None:
    a = {(r["c"], r["k"], r["archivo"]): r for r in json.loads(before.read_text(encoding="utf-8"))}
    b = {(r["c"], r["k"], r["archivo"]): r for r in json.loads(after.read_text(encoding="utf-8"))}
    per = collections.defaultdict(lambda: collections.Counter())
    changed = []
    # Every field two digests share is compared (the strict digest has more of them).
    fields = [f for f in next(iter(a.values())) if f not in ("c", "k", "archivo", "types", "hits")]
    for key, x in a.items():
        y = b.get(key)
        s = per[key[0]]
        s["snapshots"] += 1
        if y is None:
            s["missing_after"] += 1
            changed.append((key, "missing after"))
            continue
        s["tree_changed"] += x["tree"] != y["tree"]
        s["units_changed"] += x["units"] != y["units"]
        moved = [f for f in fields if x.get(f) != y.get(f)]
        if moved:
            s["moved"] += 1
            changed.append((key, ", ".join(f"{f} {x.get(f)}->{y.get(f)}" if f.startswith(("n", "nodes")) else f
                                            for f in moved)))
    s_extra = set(b) - set(a)
    for c, s in per.items():
        print(c, dict(s))
    if s_extra:
        print(f"{len(s_extra)} snapshots only in the second file")
    print("moved snapshots (collection key archivo: fields):")
    for key, what in changed:
        print(f"  {key[0]:12} {key[1]:>8} {key[2]}: {what}")
    print(f"{len(changed)} moved of {len(a)}")
    return len(changed) + len(s_extra)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--scjn-dir", type=Path, default=ROOT / "scripts" / "scjn")
    p.add_argument("--index", type=Path,
                   default=ROOT / "website" / "pages" / "instruments" / "instruments.json")
    p.add_argument("--json", type=Path)
    p.add_argument("--jobs", type=int, default=6)
    p.add_argument("--all-snapshots", action="store_true",
                   help="every snapshot of the four corpora, with the strict digest (issue #304)")
    p.add_argument("--every", type=int, default=1, metavar="N",
                   help="with --all-snapshots: only every N-th snapshot (a quick check while a "
                        "refactor is under way; the proof is the full run)")
    p.add_argument("--max-bytes", type=int, default=None,
                   help="with --all-snapshots: skip snapshots larger than this")
    p.add_argument("--hits", action="store_true",
                   help="with --all-snapshots: also record the rule-hit counters of every parse")
    p.add_argument("--report-hits", type=Path, metavar="JSON",
                   help="sum the counters of a --hits run per rule and branch")
    p.add_argument("--compare", nargs=2, type=Path, metavar=("BEFORE", "AFTER"))
    args = p.parse_args(argv)
    if args.compare:
        return 1 if compare(*args.compare) else 0
    if args.report_hits:
        report_hits(args.report_hits)
        return 0
    if args.all_snapshots:
        # Largest files first, so the pool is not left waiting on the last ligie snapshot.
        paths = sorted(
            ((c, p.parent.name, p) for c in ("leyes", "reglamentos", "lineamientos", "tratados")
             for p in (args.scjn_dir / c).glob("*/*.md")),
            key=lambda t: -t[2].stat().st_size,
        )
        paths = [t for t in paths if args.max_bytes is None or t[2].stat().st_size <= args.max_bytes]
        jobs = [(c, k, str(p), True, args.hits) for c, k, p in paths[:: args.every]]
        unit = "snapshots"
    else:
        atlas = json.loads(args.index.read_text(encoding="utf-8"))["instruments"]
        jobs = [(e["c"], e["k"], str(newest(args.scjn_dir / e["c"] / e["k"])), False, False) for e in atlas]
        unit = "instruments"
    with Pool(args.jobs) as pool:
        rows = pool.map(one, jobs, chunksize=1 if args.all_snapshots else 8)
    rows.sort(key=lambda r: (r["c"], r["k"], r["archivo"]))
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(rows), encoding="utf-8")
    print(len(rows), unit)
    return 0


if __name__ == "__main__":
    sys.exit(main())

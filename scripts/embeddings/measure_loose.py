#!/usr/bin/env python3
"""`loose` excerpts and repeated article numbers, per instrument and per
collection (review fix-4 of #291).

For the instruments of the Atlas (`instruments.json`), parses the newest snapshot
of each one under ``--scjn-dir`` (what a rebuild embeds) with the `md2akn` that is
importable now, and records the excerpts `export_instrument_files.excerpt_flags`
flags as `loose` and as `duplicate_number`, plus how many excerpts of each unit
type there are. Run it before a change to `md2akn` and again after, and compare
the two files with ``--compare``:

    uv run --group viz python scripts/embeddings/measure_loose.py --json /tmp/before.json
    uv run --group viz python scripts/embeddings/measure_loose.py --json /tmp/after.json
    uv run --group viz python scripts/embeddings/measure_loose.py --compare /tmp/before.json /tmp/after.json

``--other-labels`` tabulates, instead, the labels of the API blocks the SCJN
marks `kind=other` (the vocabulary the container roles of rule 29 are drawn
from). Read-only; no network.
"""

import argparse
import collections
import json
import re
import sys
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages" / "md2akn"))
sys.path.insert(0, str(ROOT / "packages" / "scjn"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import export_instrument_files as X  # noqa: E402
from measure_outline_repeats import newest  # noqa: E402
from md2akn import parse_markdown, text_units  # noqa: E402
from md2akn.outline import es_continuacion, rol_de_etiqueta  # noqa: E402
from scjn.outline import parse_marker  # noqa: E402


def block_kinds(markdown: str):
    """Sorted ``(offset, kind)`` of every marker line of a snapshot, to tell which
    kind of API block a given excerpt starts in."""
    out = []
    for m in re.finditer(r"^<!-- scjn:block .*? -->[ \t]*$", markdown, re.MULTILINE):
        marker = parse_marker(m.group(0))
        out.append((m.start(), marker.get("kind", "?") if marker else "?"))
    return out


def kind_at(kinds, offset: int) -> str:
    import bisect
    i = bisect.bisect_right([k[0] for k in kinds], offset) - 1
    return kinds[i][1] if i >= 0 else "before-any-marker"


def measure(markdown: str) -> dict:
    tree = parse_markdown(markdown)
    units = text_units(tree, split_articles=False)
    collisions = {n.eId for n in tree.walk() if n.repeated}
    flags = X.excerpt_flags(units, set(), collisions)
    types = collections.Counter(u.unit_type for u in units)
    kinds = block_kinds(markdown)
    by_kind = collections.Counter(
        kind_at(kinds, u.start_char) for u in units if u.unit_type == "loose"
    )
    return {
        "loose_by_block_kind": dict(by_kind),
        "loose": sum("loose" in names for names in flags),
        "duplicate_number": sum("duplicate_number" in names for names in flags),
        "types": dict(types),
        "loose_eids": sorted({u.eId for u, names in zip(units, flags) if "loose" in names})[:6],
    }


def one(job):
    collection, key, path = job
    row = measure(Path(path).read_text(encoding="utf-8"))
    return {"c": collection, "k": key, "archivo": Path(path).name, **row}


def other_labels(job):
    """The labels of an instrument's `other` blocks (digits folded), and how many of
    them rule 29 gives each role, or `continuation` when rule 30 folds the block
    into the one before it."""
    collection, key, path = job
    out = collections.Counter()
    roles = collections.Counter()
    previous = ""
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.startswith("<!-- scjn:block "):
            marker = parse_marker(line)
            if marker and marker.get("kind") == "other":
                label = marker["label"]
                out[re.sub(r"\d+", "N", label).strip()] += 1
                if es_continuacion(label, previous):
                    roles["continuation"] += 1
                else:
                    roles[rol_de_etiqueta(label) or "generic"] += 1
            previous = marker.get("label", "") if marker else ""
    out["\0roles"] = 0
    return collection, key, (out, roles)


def summarise(rows):
    summary = collections.defaultdict(collections.Counter)
    for row in rows:
        s = summary[row["c"]]
        s["instruments"] += 1
        s["loose_instruments"] += row["loose"] > 0
        s["loose_excerpts"] += row["loose"]
        s["repeat_instruments"] += row["duplicate_number"] > 0
        s["repeat_excerpts"] += row["duplicate_number"]
        for kind, n in row.get("loose_by_block_kind", {}).items():
            s["loose_in_" + kind] += n
    return {c: dict(s) for c, s in summary.items()}


def compare(before: Path, after: Path) -> None:
    a = json.loads(before.read_text(encoding="utf-8"))
    b = json.loads(after.read_text(encoding="utf-8"))
    print("collection             before(instr/excerpts)   after(instr/excerpts)")
    for field in ("loose", "repeat"):
        print(f"-- {field}")
        for c in sorted(set(a["summary"]) | set(b["summary"])):
            x, y = a["summary"].get(c, {}), b["summary"].get(c, {})
            print(f"{c:14} {x.get(field + '_instruments', 0):6} / {x.get(field + '_excerpts', 0):7}"
                  f"   {y.get(field + '_instruments', 0):6} / {y.get(field + '_excerpts', 0):7}")
    after_by = {(r["c"], r["k"]): r for r in b["instruments"]}
    print("-- largest loose groups after (before -> after)")
    ranked = sorted(a["instruments"], key=lambda r: -r["loose"])[:25]
    for r in ranked:
        n = after_by[(r["c"], r["k"])]
        print(f"{r['c']:13} {r['k']:>8} {r['loose']:5} -> {n['loose']:5}   repeats {r['duplicate_number']} -> {n['duplicate_number']}")
    still = [r for r in b["instruments"] if r["duplicate_number"]]
    print(f"-- instruments that still repeat after: {len(still)}")
    for r in still:
        print(r["c"], r["k"], r["duplicate_number"])


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--scjn-dir", type=Path, default=ROOT / "scripts" / "scjn")
    p.add_argument("--index", type=Path,
                   default=ROOT / "website" / "pages" / "instruments" / "instruments.json")
    p.add_argument("--json", type=Path, default=None)
    p.add_argument("--jobs", type=int, default=6)
    p.add_argument("--compare", nargs=2, type=Path, metavar=("BEFORE", "AFTER"))
    p.add_argument("--other-labels", action="store_true",
                   help="tabulate the labels of the `kind=other` blocks instead")
    args = p.parse_args(argv)
    if args.compare:
        compare(*args.compare)
        return 0

    atlas = json.loads(args.index.read_text(encoding="utf-8"))["instruments"]
    jobs = [(e["c"], e["k"], str(newest(args.scjn_dir / e["c"] / e["k"]))) for e in atlas]
    with Pool(args.jobs) as pool:
        if args.other_labels:
            table = collections.defaultdict(collections.Counter)
            where = collections.defaultdict(set)
            by_role = collections.defaultdict(collections.Counter)
            for c, k, (counter, roles) in pool.imap_unordered(other_labels, jobs, chunksize=8):
                counter.pop("\0roles", None)
                for label, n in counter.items():
                    table[(c, label)][k] += n
                    where[(c, label)].add(k)
                for role, n in roles.items():
                    by_role[c][role] += n
            ranked = sorted(table.items(), key=lambda item: -len(item[1]))
            print(f"{len(ranked)} distinct (collection, label) shapes, "
                  f"{sum(sum(v.values()) for v in table.values())} blocks")
            print("-- blocks by role of rule 29 (continuation = rule 30)")
            for c in sorted(by_role):
                print(f"{c:13} " + ", ".join(f"{r} {n}" for r, n in by_role[c].most_common()))
            for (c, label), per in ranked[: 120]:
                print(f"{sum(per.values()):6} blocks {len(per):5} instruments  {c:12} {label[:80]}")
            return 0
        rows = pool.map(one, jobs, chunksize=8)
    summary = summarise(rows)
    for c, s in summary.items():
        print(c, s)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps({"summary": summary, "instruments": rows},
                                        ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

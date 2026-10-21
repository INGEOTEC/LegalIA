#!/usr/bin/env python3
"""The four invariants of review fix-6 of #291, measured per instrument.

For the instruments of the Atlas (`instruments.json`), parses the newest snapshot
of each one under ``--scjn-dir`` with the `md2akn` that is importable now and
records, per instrument:

* **I1** -- `md2akn.validate._block_escapes`: nodes built from the text of an API
  block that do not hang from the node that block opened;
* **I2** -- `loose` excerpts outside a transitorios section (the ones the Atlas
  matrix compares), with their eId, path and first 80 characters;
* **I3** -- `duplicate_number` excerpts (`export_instrument_files.excerpt_flags`);
* **I4** -- `numbering_gap` excerpts, with the container, the number and the
  integer number before it.

    uv run --group viz python scripts/embeddings/measure_invariants.py --json /tmp/inv.json
    uv run --group viz python scripts/embeddings/measure_invariants.py --compare A.json B.json

``--detail`` prints every offending item, not only the totals. Read-only; no
network.
"""

import argparse
import collections
import json
import sys
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages" / "md2akn"))
sys.path.insert(0, str(ROOT / "packages" / "scjn"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import export_instrument_files as X  # noqa: E402
import instrument_matrix  # noqa: E402
from measure_outline_repeats import newest  # noqa: E402
from md2akn import parse_markdown, text_units  # noqa: E402
from md2akn.validate import _block_escapes  # noqa: E402


def measure(markdown: str) -> dict:
    """The four invariants of one document, as plain data."""
    tree = parse_markdown(markdown)
    units = text_units(tree, split_articles=False)
    repeated = {n.eId for n in tree.walk() if n.repeated}
    flags = X.excerpt_flags(units, set(), repeated)
    loose, gaps = [], []
    last_integer: dict = {}
    for unit, names in zip(units, flags):
        if unit.unit_type == "article" and not instrument_matrix.is_transitorio_path(unit.path) \
                and unit.num is not None:
            value = X.integer_number(unit.num)
            if value is not None:
                before = last_integer.get(unit.path)
                if "numbering_gap" in names:
                    gaps.append({"eId": unit.eId, "num": unit.num, "after": before,
                                 "path": list(unit.path)})
                last_integer[unit.path] = value
        if "loose" in names and not instrument_matrix.is_transitorio_path(unit.path):
            loose.append({"eId": unit.eId, "path": list(unit.path),
                          "text": markdown[unit.start_char:unit.end_char][:80]})
    escapes = _block_escapes(tree)
    return {
        "i1": [str(v) for v in escapes],
        "i2": loose,
        "i3": sum("duplicate_number" in names for names in flags),
        "i4": gaps,
        "loose_all": sum("loose" in names for names in flags),
    }


def one(job):
    collection, key, path = job
    row = measure(Path(path).read_text(encoding="utf-8"))
    return {"c": collection, "k": key, "archivo": Path(path).name, **row}


def summarise(rows):
    out = collections.defaultdict(collections.Counter)
    for row in rows:
        s = out[row["c"]]
        s["instruments"] += 1
        for name, n in (("i1", len(row["i1"])), ("i2", len(row["i2"])), ("i3", row["i3"]),
                        ("i4", len(row["i4"])), ("loose_all", row["loose_all"])):
            s[name + "_excerpts"] += n
            s[name + "_instruments"] += n > 0
    return {c: dict(s) for c, s in out.items()}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--scjn-dir", type=Path, default=ROOT / "scripts" / "scjn")
    p.add_argument("--index", type=Path,
                   default=ROOT / "website" / "pages" / "instruments" / "instruments.json")
    p.add_argument("--json", type=Path)
    p.add_argument("--jobs", type=int, default=6)
    p.add_argument("--detail", action="store_true")
    p.add_argument("--compare", nargs=2, type=Path, metavar=("BEFORE", "AFTER"))
    args = p.parse_args(argv)
    if args.compare:
        a, b = (json.loads(x.read_text(encoding="utf-8")) for x in args.compare)
        for name, x in (("before", a), ("after", b)):
            print(name)
            for c, s in x["summary"].items():
                print("  ", c, s)
        return 0
    atlas = json.loads(args.index.read_text(encoding="utf-8"))["instruments"]
    jobs = [(e["c"], e["k"], str(newest(args.scjn_dir / e["c"] / e["k"]))) for e in atlas]
    with Pool(args.jobs) as pool:
        rows = pool.map(one, jobs, chunksize=8)
    summary = summarise(rows)
    for c, s in summary.items():
        print(c, s)
    if args.detail:
        for row in rows:
            for key in ("i1", "i2", "i4"):
                for item in row[key][:50]:
                    print(row["c"], row["k"], key, item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)[:300])
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps({"summary": summary, "instruments": rows},
                                        ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

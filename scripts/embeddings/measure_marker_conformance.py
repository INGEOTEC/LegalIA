#!/usr/bin/env python3
"""Where `md2akn`'s tree disagrees with the outline markers of a snapshot (review fix-2
of #300 and #301).

The SCJN's API serves an instrument as a list of blocks, each with a marker (`kind`,
`type`, `num`, `label`), and that outline has to organise the document first: text rules
may only refine *inside* a block. For the newest snapshot of every instrument of the Atlas
this compares each block with the node rooted at it (`md2akn.validate.block_conformance`,
invariant I5) and reports, by collection and by class of disagreement, how many there are
and which instruments they are in, with examples:

* ``no-node``     the block opened no node of its own;
* ``type``        its root is not what its `kind`/`type` say;
* ``number``      its root's number is not the marker's `num`;
* ``label-line``  the block's own label line is content, not the heading of its node;
* ``crossing``    a leaf that starts in the block reaches into the next one;
* ``parent``      a container hangs from a container of the same or a lower rank.

Also reported, because it is what case (2) of the review measured: the compared `intro`
units whose whole text is just the label of the marker before them.

``--json PATH`` keeps the full list; ``--compare BEFORE AFTER`` shows what moved.
Read-only; no network.
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

from measure_outline_repeats import newest  # noqa: E402
from md2akn import parse_markdown, text_units  # noqa: E402
from md2akn.outline import es_linea_de_etiqueta  # noqa: E402
from md2akn.validate import block_conformance  # noqa: E402


def label_intro_units(units) -> int:
    """Compared (non-envelope) `intro` units whose whole text is just the label of the
    container the marker before them opened (the last element of the unit's `path`)."""
    n = 0
    for u in units:
        if u.unit_type != "intro" or u.envelope or not u.path:
            continue
        prefijo = " > ".join(u.path)
        cuerpo = u.text[len(prefijo):] if u.text.startswith(prefijo) else u.text
        if es_linea_de_etiqueta(cuerpo.strip(), u.path[-1]):
            n += 1
    return n


def one(job):
    collection, key, path = job
    markdown = Path(path).read_text(encoding="utf-8")
    tree = parse_markdown(markdown)
    rows = [
        {"klass": d.klass, "orden": d.orden, "kind": d.kind, "label": d.label,
         "eId": d.eId, "detail": d.detail}
        for d in block_conformance(tree)
    ]
    units = text_units(tree, split_articles=False)
    return {"c": collection, "k": key, "archivo": Path(path).name, "rows": rows,
            "label_intro": label_intro_units(units)}


def report(results, examples: int) -> None:
    por = collections.defaultdict(collections.Counter)
    instr = collections.defaultdict(lambda: collections.defaultdict(set))
    ejemplos = collections.defaultdict(list)
    li = collections.Counter()
    li_i = collections.Counter()
    for r in results:
        li[r["c"]] += r["label_intro"]
        li_i[r["c"]] += bool(r["label_intro"])
        for d in r["rows"]:
            por[r["c"]][d["klass"]] += 1
            instr[r["c"]][d["klass"]].add(r["k"])
            if len(ejemplos[(r["c"], d["klass"])]) < examples:
                ejemplos[(r["c"], d["klass"])].append((r["k"], r["archivo"], d))
    print("label-only compared intro units:")
    for c in li:
        print(f"  {c:12} {li[c]:6} units in {li_i[c]} instruments")
    for c in por:
        print(f"\n{c}")
        for klass, n in por[c].most_common():
            print(f"  {klass:11} {n:6} disagreements in {len(instr[c][klass])} instruments")
            for k, archivo, d in ejemplos[(c, klass)]:
                print(f"      {k} {archivo} block {d['orden']} {d['kind']} {d['label'][:40]!r}: {d['detail'][:110]}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--scjn-dir", type=Path, default=ROOT / "scripts" / "scjn")
    p.add_argument("--index", type=Path,
                   default=ROOT / "website" / "pages" / "instruments" / "instruments.json")
    p.add_argument("--json", type=Path)
    p.add_argument("--jobs", type=int, default=6)
    p.add_argument("--examples", type=int, default=3)
    p.add_argument("--from-json", type=Path, help="report a record already written")
    args = p.parse_args(argv)
    if args.from_json:
        report(json.loads(args.from_json.read_text(encoding="utf-8")), args.examples)
        return 0
    atlas = json.loads(args.index.read_text(encoding="utf-8"))["instruments"]
    jobs = [(e["c"], e["k"], str(newest(args.scjn_dir / e["c"] / e["k"]))) for e in atlas]
    with Pool(args.jobs) as pool:
        results = pool.map(one, jobs, chunksize=8)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(results), encoding="utf-8")
    print(len(results), "instruments")
    report(results, args.examples)
    return 0


if __name__ == "__main__":
    sys.exit(main())

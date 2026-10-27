#!/usr/bin/env python3
"""The envelope and the `preamble`/`conclusions` units, before and after a change of
the parser (review fix-1 of #301).

For the newest snapshot of every instrument of the Atlas this records

* how many units are `envelope` (not compared), by collection and by the label of the
  API block they sit in;
* the `preamble` and `conclusions` units that **cross a marker**: a unit whose
  characters hold the start of an API block other than the one it starts in;
* the **leading run**, the blocks after the title block that the envelope reads as the
  decree's opening, listing every one whose label has no role in the decree
  (`md2akn.outline.rol_de_etiqueta`) -- the blocks a positional rule reads as envelope
  by position alone, which a person has to inspect so that an instrument's own text is
  not swallowed.

``--json PATH`` writes the record; ``--compare BEFORE AFTER`` prints the two side by
side. Read-only, no network. Run it with the parser before a change and again after.
"""

import argparse
import bisect
import collections
import json
import sys
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages" / "md2akn"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from measure_outline_repeats import newest  # noqa: E402
from md2akn import parse_markdown, text_units  # noqa: E402
from md2akn.outline import MARCADORES_EN_TEXTO, lee_marcador, rol_de_etiqueta  # noqa: E402


def blocks(text: str):
    """``(start, attrs)`` of every API block, the way `rangos_de_envoltura` reads them."""
    found = list(MARCADORES_EN_TEXTO.finditer(text))
    starts = [len(text[: m.start()].rstrip()) if m.start() else 0 for m in found]
    return starts, [lee_marcador(m.group(0)) or {} for m in found]


def label_of(starts, attrs, position: int) -> str:
    k = bisect.bisect_right(starts, position) - 1
    return attrs[k].get("label", "") if k >= 0 else ""


def digest(markdown: str) -> dict:
    tree = parse_markdown(markdown)
    units = text_units(tree, split_articles=False)
    starts, attrs = blocks(markdown)
    envelope = collections.Counter()
    crossing = []
    for unit in units:
        if unit.envelope:
            envelope[label_of(starts, attrs, unit.start_char)] += 1
        if unit.unit_type in ("preamble", "conclusions") and starts:
            k = bisect.bisect_right(starts, unit.start_char) - 1
            following = starts[k + 1] if k + 1 < len(starts) else None
            if following is not None and unit.end_char > following:
                crossed = [attrs[j].get("label", "") for j in range(k + 1, len(starts))
                           if starts[j] < unit.end_char]
                crossing.append([unit.unit_type, unit.eId, *crossed[:6]])
    leading = []
    seen_header = False
    for start, a in zip(starts, attrs):
        if a.get("kind") == "header":
            seen_header = True
            continue
        if not seen_header and not leading and a.get("kind") != "other":
            break
        if a.get("kind") != "other":
            break
        if rol_de_etiqueta(a.get("label", "")) != "promulgation":
            leading.append(a.get("label", ""))
    return {
        "envelope": dict(envelope),
        "envelope_units": sum(envelope.values()),
        "units": len(units),
        "crossing": crossing,
        "leading_without_role": leading,
    }


def one(job):
    collection, key, path = job
    return {"c": collection, "k": key, **digest(Path(path).read_text(encoding="utf-8"))}


def summarise(rows: list[dict]) -> dict:
    out = {}
    for collection in sorted({r["c"] for r in rows}):
        part = [r for r in rows if r["c"] == collection]
        by_label = collections.Counter()
        for r in part:
            by_label.update(r["envelope"])
        out[collection] = {
            "instruments": len(part),
            "envelope_units": sum(r["envelope_units"] for r in part),
            "instruments_with_envelope": sum(1 for r in part if r["envelope_units"]),
            "crossing_instruments": sum(1 for r in part if r["crossing"]),
            "crossing_units": sum(len(r["crossing"]) for r in part),
            "by_label": by_label,
        }
    return out


def compare(before: Path, after: Path, top: int) -> None:
    a = summarise(json.loads(before.read_text(encoding="utf-8")))
    b = summarise(json.loads(after.read_text(encoding="utf-8")))
    for collection in a:
        x, y = a[collection], b[collection]
        print(f"{collection}: {x['instruments']} instruments")
        for name in ("envelope_units", "instruments_with_envelope",
                     "crossing_instruments", "crossing_units"):
            print(f"  {name:28} {x[name]:>7} -> {y[name]:>7}")
        labels = set(x["by_label"]) | set(y["by_label"])
        moved = sorted(
            ((x["by_label"].get(l, 0), y["by_label"].get(l, 0), l) for l in labels
             if x["by_label"].get(l, 0) != y["by_label"].get(l, 0)),
            key=lambda t: -abs(t[0] - t[1]),
        )
        for before_n, after_n, label in moved[:top]:
            print(f"    {before_n:>6} -> {after_n:>6}  {label[:70]}")


def leading_listing(path: Path) -> None:
    rows = json.loads(path.read_text(encoding="utf-8"))
    per = collections.Counter()
    where = collections.defaultdict(list)
    for r in rows:
        for label in r["leading_without_role"]:
            per[(r["c"], label)] += 1
            where[(r["c"], label)].append(r["k"])
    for (c, label), n in per.most_common():
        print(f"{c:12} {n:>4}  {label[:70]}  e.g. {', '.join(where[(c, label)][:4])}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--scjn-dir", type=Path, default=ROOT / "scripts" / "scjn")
    p.add_argument("--index", type=Path,
                   default=ROOT / "website" / "pages" / "instruments" / "instruments.json")
    p.add_argument("--json", type=Path)
    p.add_argument("--jobs", type=int, default=8)
    p.add_argument("--compare", nargs=2, type=Path, metavar=("BEFORE", "AFTER"))
    p.add_argument("--leading", type=Path, metavar="JSON",
                   help="list the leading-run blocks with no role, from a record")
    p.add_argument("--top", type=int, default=15)
    args = p.parse_args(argv)
    if args.compare:
        compare(*args.compare, args.top)
        return 0
    if args.leading:
        leading_listing(args.leading)
        return 0
    atlas = json.loads(args.index.read_text(encoding="utf-8"))["instruments"]
    jobs = [(e["c"], e["k"], str(newest(args.scjn_dir / e["c"] / e["k"]))) for e in atlas]
    with Pool(args.jobs) as pool:
        rows = pool.map(one, jobs, chunksize=8)
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(rows), encoding="utf-8")
    print(len(rows), "instruments")
    for collection, s in summarise(rows).items():
        print(collection, {k: v for k, v in s.items() if k != "by_label"})
    return 0


if __name__ == "__main__":
    sys.exit(main())

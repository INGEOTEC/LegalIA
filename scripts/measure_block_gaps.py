#!/usr/bin/env python3
"""Count the SCJN snapshots whose API blocks have a gap (issue #300).

A snapshot written with ``outline=True`` carries one ``<!-- scjn:block orden=N ... -->``
marker per API block. The API once served an ``Articulos`` answer that left some
blocks out while still reserving their ``orden`` numbers (the ``CONTENIDO`` blocks
of ``tratados/24031``, four of ``lfd`` 28-12-2025), so the ``orden`` values of such
a snapshot are not ``1..N``. This script measures it, per collection: how many
snapshots and how many instruments have a gap, how many blocks are missing, and
which ones, kept as a record the way ``measure_trees.py`` is.

It reads ``scripts/scjn/<collection>/<key>/*.md`` only (no network). An instrument
the re-crawl ledger lists as ``incomplete`` (or ``error``) is reported by key apart,
since its old files are the ones left on disk.

A marker gap is not always an omission: a block whose whole text is an editorial
aside (``N. DE E. VÉASE ANEXOS ...``, removed by `quita_notas_editoriales`) yields
no text and so no marker, though the API served it. When the raw answer the re-crawl
kept (``--raw-dir``, default ``<outdir>/raw-api``) holds the missing ``orden``
values, the gap is *explained* (the block was served and wrote nothing) and counted
apart; only a gap the raw answer does not explain, or one with no raw answer at all,
is an omission and fails the run.

    ./scripts/measure_block_gaps.py                 # all four collections
    ./scripts/measure_block_gaps.py --coleccion tratados --verbose
    ./scripts/measure_block_gaps.py --json /tmp/gaps-before.json

Exit status 1 when a gap is found outside the instruments the ledger lists.
A block missing at the *end* of an answer leaves no gap, so a count of 0 here is a
lower bound; the re-crawl with the guarded client (``scjn.api.ScjnApiIncompleteError``)
is what rules those out.
"""

import argparse
import gzip
import json
import re
import sys
from pathlib import Path

MARKER = re.compile(r"^<!-- scjn:block orden=(\d+) ", re.MULTILINE)
COLLECTIONS = ("leyes", "reglamentos", "lineamientos", "tratados")


def missing_orden(markdown: str) -> list[int]:
    """The `orden` values absent from ``1..max`` of a snapshot's markers."""
    ordenes = {int(n) for n in MARKER.findall(markdown)}
    if not ordenes:
        return []
    return sorted(set(range(1, max(ordenes) + 1)) - ordenes)


def ledger_status(base: Path, name: str) -> dict[str, dict]:
    """The last ledger row of every key, over the plain ledger and its ``--part`` ones."""
    rows: dict[str, dict] = {}
    for ledger in sorted(base.glob(f"{name}*")):
        for line in ledger.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                rows[row["key"]] = row
    return rows


def raw_orden(raw_dir: Path, collection: str, key: str, snapshot: str) -> set[int] | None:
    """The `orden` values of the raw API answer kept for a snapshot, or None."""
    path = raw_dir / collection / key / f"{Path(snapshot).stem}.json.gz"
    if not path.exists():
        return None
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return {row["orden"] for row in json.load(f) if row.get("orden") is not None}


def measure(outdir: Path, collection: str, ledger_name: str, raw_dir: Path) -> dict:
    base = outdir / collection
    status = ledger_status(base, ledger_name)
    unlisted = {k for k, r in status.items() if r["status"] != "done"}
    snapshots = instruments = blocks = 0
    offenders: dict[str, dict[str, list[int]]] = {}
    total_snapshots = 0
    explained: dict[tuple[str, str], list[int]] = {}
    for directory in sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith(".")):
        gaps = {}
        for path in sorted(directory.glob("*.md")):
            total_snapshots += 1
            faltan = missing_orden(path.read_text(encoding="utf-8"))
            if faltan:
                served = raw_orden(raw_dir, collection, directory.name, path.name)
                if served is not None and set(faltan) <= served:
                    explained[(directory.name, path.name)] = faltan
                    continue
                gaps[path.name] = faltan
        if gaps:
            offenders[directory.name] = gaps
            instruments += 1
            snapshots += len(gaps)
            blocks += sum(len(v) for v in gaps.values())
    in_ledger = {k: v for k, v in offenders.items() if k in unlisted}
    return {
        "collection": collection,
        "snapshots_total": total_snapshots,
        "instruments_with_gap": instruments,
        "snapshots_with_gap": snapshots,
        "missing_blocks": blocks,
        "explained_snapshots": len(explained),
        "explained_blocks": sum(len(v) for v in explained.values()),
        "explained": [f"{k}/{n}: {v}" for (k, n), v in sorted(explained.items())],
        "ledger_rows": len(status),
        "ledger_not_done": sorted(unlisted),
        "offenders": offenders,
        "offenders_outside_ledger": {k: v for k, v in offenders.items() if k not in unlisted},
        "offenders_in_ledger": in_ledger,
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--coleccion", choices=COLLECTIONS, action="append", default=None)
    p.add_argument("--outdir", type=Path, default=Path("scripts/scjn"))
    p.add_argument("--ledger", default=".recrawl-contenido.jsonl",
                   help="re-crawl ledger name whose not-done instruments are reported apart")
    p.add_argument("--raw-dir", type=Path, default=None,
                   help="where the raw API answers are kept (default <outdir>/raw-api)")
    p.add_argument("--json", type=Path, default=None, help="write the full measurement here")
    p.add_argument("--verbose", action="store_true", help="print every offending snapshot")
    args = p.parse_args(argv)

    results = [measure(args.outdir, c, args.ledger, args.raw_dir or args.outdir / "raw-api") for c in (args.coleccion or COLLECTIONS)]
    bad = 0
    for r in results:
        print(
            f"{r['collection']:13} {r['snapshots_total']:6} snapshots | "
            f"{r['snapshots_with_gap']:4} with a gap in {r['instruments_with_gap']:4} instruments, "
            f"{r['missing_blocks']:5} missing blocks | explained by the raw answer: "
            f"{r['explained_snapshots']} snapshots, {r['explained_blocks']} blocks | "
            f"ledger not done: {len(r['ledger_not_done'])}"
        )
        if args.verbose:
            for line in r["explained"]:
                print(f"    explained {r['collection']}/{line}")
        for key, gaps in r["offenders"].items():
            outside = key in r["offenders_outside_ledger"]
            if outside:
                bad += 1
            if args.verbose or outside:
                tag = "" if outside else " (listed in the ledger)"
                for name, faltan in gaps.items():
                    print(f"    {r['collection']}/{key}/{name}: missing orden {faltan[:12]}"
                          f"{' ...' if len(faltan) > 12 else ''}{tag}")
        for key in r["ledger_not_done"]:
            print(f"    ledger: {r['collection']}/{key} not done")
    if args.json:
        args.json.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

"""What a rule change did to a collection's `units.parquet` (issue #291).

Compares the `units.parquet` of a published work directory with the one
`build_units.py` rebuilt in a copy of it, instrument by instrument, and answers
the question "do the vectors have to be recomputed?" with numbers rather than
a guess:

* **instruments whose rows differ** -- the `(unit_type, eId, piece, text_sha1)`
  sequence of the instrument is not the same. A pure relabelling (a text that
  was `loose` and is now `intro`) lands here but needs no vector, because a
  vector belongs to a `text_sha1`, not to a row;
* **instruments with a new text** -- some `text_sha1` of the rebuilt instrument
  is in no published row of the collection, which is what has to be encoded;
* **distinct new texts** of the collection -- the size of the encoding job
  (`plan_shards.py --against` excludes every hash a published run already
  embedded, so it must agree);
* **rows before and after, by `unit_type`**.

    python scripts/embeddings/compare_units.py \\
        --published emb-run-whole-lineamientos --rebuilt emb-run-lineamientos-051
    python scripts/embeddings/compare_units.py ... --json out.json --markdown out.md

Read-only: neither directory is written to.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import pyarrow.parquet as pq

COLUMNS = ["coleccion", "clave", "nombre", "unit_type", "eId", "piece", "text_sha1"]


def _signature(rows: list[tuple]) -> list[tuple]:
    return [(unit_type, eid, piece, sha) for unit_type, eid, piece, sha in rows]


def _by_clave(table) -> tuple[dict[str, list[tuple]], dict[str, str]]:
    """`{clave: [(unit_type, eId, piece, text_sha1), ...]}` in file order, and
    `{clave: nombre}`."""
    columns = {name: table.column(name).to_pylist() for name in COLUMNS}
    rows: dict[str, list[tuple]] = {}
    names: dict[str, str] = {}
    for i, clave in enumerate(columns["clave"]):
        rows.setdefault(clave, []).append(
            (columns["unit_type"][i], columns["eId"][i], columns["piece"][i], columns["text_sha1"][i])
        )
        names.setdefault(clave, columns["nombre"][i])
    return rows, names


def compare(published, rebuilt) -> dict:
    """The comparison of two `units.parquet` tables (`pyarrow.Table`s) of the
    same collection, as plain data (JSON-serialisable)."""
    old_rows, old_names = _by_clave(published)
    new_rows, new_names = _by_clave(rebuilt)
    old_texts = {sha for rows in old_rows.values() for *_, sha in rows}
    new_texts = {sha for rows in new_rows.values() for *_, sha in rows}

    changed = []
    unchanged = 0
    for clave in sorted(set(old_rows) | set(new_rows)):
        before, after = old_rows.get(clave, []), new_rows.get(clave, [])
        if _signature(before) == _signature(after):
            unchanged += 1
            continue
        fresh = {sha for *_, sha in after} - old_texts
        changed.append({
            "clave": clave,
            "nombre": new_names.get(clave) or old_names.get(clave),
            "rows_before": len(before),
            "rows_after": len(after),
            "new_texts": len(fresh),
            "only_relabelled": not fresh and {s for *_, s in before} == {s for *_, s in after},
            "added": clave not in old_rows,
            "removed": clave not in new_rows,
        })

    return {
        "coleccion": (rebuilt.column("coleccion")[0].as_py() if rebuilt.num_rows else None),
        "instruments_published": len(old_rows),
        "instruments_rebuilt": len(new_rows),
        "instruments_changed": len(changed),
        "instruments_unchanged": unchanged,
        "instruments_with_new_text": sum(1 for c in changed if c["new_texts"]),
        "instruments_relabelled_only": sum(1 for c in changed if c["only_relabelled"]),
        "rows_published": published.num_rows,
        "rows_rebuilt": rebuilt.num_rows,
        "distinct_texts_published": len(old_texts),
        "distinct_texts_rebuilt": len(new_texts),
        "distinct_new_texts": len(new_texts - old_texts),
        "distinct_dropped_texts": len(old_texts - new_texts),
        "rows_by_unit_type_before": dict(Counter(published.column("unit_type").to_pylist())),
        "rows_by_unit_type_after": dict(Counter(rebuilt.column("unit_type").to_pylist())),
        "changed": changed,
    }


def markdown(result: dict, *, list_changed: bool) -> str:
    """The comparison as Markdown tables, the shape the README section uses."""
    lines = [f"### {result['coleccion']}", ""]
    lines += [
        "| | published | rebuilt |",
        "|---|---:|---:|",
        f"| instruments | {result['instruments_published']:,} | {result['instruments_rebuilt']:,} |",
        f"| rows | {result['rows_published']:,} | {result['rows_rebuilt']:,} |",
        f"| distinct texts | {result['distinct_texts_published']:,} | {result['distinct_texts_rebuilt']:,} |",
        "",
        f"Instruments whose rows differ: **{result['instruments_changed']}** "
        f"({result['instruments_with_new_text']} with a new text, "
        f"{result['instruments_relabelled_only']} only relabelled); unchanged: "
        f"{result['instruments_unchanged']}. Distinct new texts: "
        f"**{result['distinct_new_texts']:,}** (dropped: {result['distinct_dropped_texts']:,}).",
        "",
        "| unit_type | before | after |",
        "|---|---:|---:|",
    ]
    before, after = result["rows_by_unit_type_before"], result["rows_by_unit_type_after"]
    for unit_type in sorted(set(before) | set(after), key=lambda t: -(after.get(t, 0) + before.get(t, 0))):
        lines.append(f"| {unit_type} | {before.get(unit_type, 0):,} | {after.get(unit_type, 0):,} |")
    if list_changed and result["changed"]:
        lines += ["", "| clave | instrument | rows before | rows after | new texts | only relabelled |",
                  "|---|---|---:|---:|---:|:---:|"]
        for c in result["changed"]:
            name = (c["nombre"] or "").replace("|", "\\|")
            lines.append(
                f"| {c['clave']} | {name} | {c['rows_before']} | {c['rows_after']} | "
                f"{c['new_texts']} | {'yes' if c['only_relabelled'] else ''} |"
            )
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--published", type=Path, required=True,
                        help="the work directory holding the published vectors (read only)")
    parser.add_argument("--rebuilt", type=Path, required=True,
                        help="the copy `build_units.py` rebuilt `units.parquet` in")
    parser.add_argument("--json", type=Path, help="write the comparison as JSON")
    parser.add_argument("--markdown", type=Path, help="write the tables as Markdown")
    parser.add_argument("--list-changed", action="store_true",
                        help="also list every changed instrument in the Markdown")
    args = parser.parse_args(argv)
    result = compare(
        pq.read_table(args.published / "units.parquet", columns=COLUMNS),
        pq.read_table(args.rebuilt / "units.parquet", columns=COLUMNS),
    )
    text = markdown(result, list_changed=args.list_changed)
    if args.json:
        args.json.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if args.markdown:
        args.markdown.write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

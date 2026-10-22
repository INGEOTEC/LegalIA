#!/usr/bin/env python3
"""What each rule of review fix-1 of #291 (rules 14-18 of `md2akn.text_units`
and `scjn.api`'s writer) contributed to the repeated article numbers.

209 of the Atlas' 2,676 instruments carried a `duplicate_number` flag. For each
one this reparses the document the Atlas showed (the Markdown of the old
instrument file) and counts the flagged excerpts under the same definition
`export_instrument_files.excerpt_flags` has, switching one rule off at a time:

    S0  the count the old index recorded (old parser, old Markdown)
    A   the new parser, every rule on, on the old Markdown
    B   the new parser with rules 15-18 off: the number grammar (rule 14) and
        the transitorios markers are what is left of S0 - B
    T P R L   rule 15 (tables of contents), 16 (PARTE/ANEXO), 17 (recitals),
        18 (restarted numerals) off, one at a time: a rule's share is its count
        minus A
    S2  the new parser on the Markdown the fixed `scjn.api` writer produced
        (the snapshot in the local `scjn` cache), i.e. what the Atlas shows now

This is the record of review fix-1 and counts by the number alone (the flag's
definition then); review fix-2 changed the flag to the identifier
(`measure_outline_repeats.py`), and fix-1's own conclusion about what survives
was corrected by it.

Nothing is hidden: what survives in S2 is the SCJN's own repeated numbering
(annex and appendix restarts, one law in several documents, an API row
repeated), reported for the repository owner. Read-only; no network.

    uv run --group viz python scripts/embeddings/measure_repeated_numbers.py \
        --old-files /tmp/fix1/oldfiles --old-index /tmp/fix1/instruments_old.json \
        --json /tmp/fix1/measure.json
"""

import argparse
import collections
import json
import re
import sys
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import md2akn  # noqa: E402
import md2akn.structure as structure  # noqa: E402
import export_instrument_files as X  # noqa: E402
from md2akn.units import text_units  # noqa: E402

NEVER = re.compile(r"(?!x)x")

SWITCHES = {
    # name -> {module attribute: value while off}
    "B": {"rangos_de_indice": lambda *a, **k: {}, "CONTENEDOR_PARTE": NEVER, "CONTENEDOR_ANEXO": NEVER,
          "CONSIDERANDO": NEVER, "REINICIO_ES_ITEM": False},
    "T": {"rangos_de_indice": lambda *a, **k: {}},
    "P": {"CONTENEDOR_PARTE": NEVER, "CONTENEDOR_ANEXO": NEVER},
    "R": {"CONSIDERANDO": NEVER},
    "L": {"REINICIO_ES_ITEM": False},
}


def repeated(markdown: str) -> int:
    """Excerpts whose article number appears more than once outside the
    transitorios -- the definition of `duplicate_number` the instrument files
    had when this script was written (review fix-1). Review fix-2 of #291
    replaced it in `export_instrument_files.excerpt_flags` by "the eId needed a
    `_2` suffix" (`AknNode.repeated`), because a number that restarts inside
    another container is not a repeat; the old definition is kept here, copied,
    so this record of fix-1 still reproduces."""
    units = text_units(markdown, split_articles=False)
    numbered = [
        str(unit.num).strip() for unit in units
        if unit.unit_type == "article" and unit.num is not None
        and not X.instrument_matrix.is_transitorio_path(unit.path)
    ]
    counts = collections.Counter(numbered)
    return sum(counts[number] > 1 for number in numbered)


def counts(job):
    key, old_markdown, new_markdown = job
    out = {"A": repeated(old_markdown), "S2": repeated(new_markdown)}
    for name, patch in SWITCHES.items():
        saved = {attribute: getattr(structure, attribute) for attribute in patch}
        try:
            for attribute, value in patch.items():
                setattr(structure, attribute, value)
            out[name] = repeated(old_markdown)
        finally:
            for attribute, value in saved.items():
                setattr(structure, attribute, value)
    return key, out


def current_markdown(keys):
    """`{(coleccion, clave): the newest snapshot's Markdown}` from the local `scjn` cache."""
    import scjn
    sources = (
        ("leyes", scjn.iter_current_federal_laws(), "slug"),
        ("reglamentos", scjn.iter_current_reglamentos(), "id_ordenamiento"),
        ("lineamientos", scjn.iter_current_lineamientos(), "id_ordenamiento"),
        ("tratados", scjn.iter_current_tratados(), "id_ordenamiento"),
    )
    found = {}
    for coleccion, records, name in sources:
        for record in records:
            key = (coleccion, str(record[name]))
            if key in keys:
                found[key] = record["markdown"]
    return found


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--old-files", type=Path, required=True,
                        help="the instrument files the Atlas showed before the fix: <coleccion>/<clave>.json")
    parser.add_argument("--old-index", type=Path, required=True, help="that Atlas' instruments.json")
    parser.add_argument("--json", type=Path, required=True)
    parser.add_argument("--processes", type=int, default=7)
    args = parser.parse_args(argv)

    index = json.loads(args.old_index.read_text())["instruments"]
    flagged = {(entry["c"], entry["k"]): entry for entry in index if entry["f"].get("duplicate_number")}
    new = current_markdown(set(flagged))
    jobs = []
    for key in sorted(flagged):
        old = json.loads((args.old_files / key[0] / f"{key[1]}.json").read_text())["markdown"]
        jobs.append((key, old, new[key]))
    with Pool(args.processes) as pool:
        measured = dict(pool.imap_unordered(counts, jobs))
    rows = []
    for key in sorted(flagged):
        m = measured[key]
        s0 = flagged[key]["f"]["duplicate_number"]
        rows.append({
            "coleccion": key[0], "clave": key[1], "nombre": flagged[key]["n"], "S0": s0, "A": m["A"], "S2": m["S2"],
            "grammar_and_markers": s0 - m["B"], "rule_15": m["T"] - m["A"], "rule_16": m["P"] - m["A"],
            "rule_17": m["R"] - m["A"], "rule_18": m["L"] - m["A"], "writer": m["A"] - m["S2"],
        })
    args.json.write_text(json.dumps(rows, ensure_ascii=False, indent=1) + "\n")
    totals = collections.Counter()
    for row in rows:
        for name in ("S0", "A", "S2"):
            totals[name] += row[name]
        totals["flagged_before"] += 1
        totals["flagged_after"] += row["S2"] > 0
    print(dict(totals), md2akn.__version__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

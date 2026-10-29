#!/usr/bin/env python3
"""List the disagreements between an API block's marker and the node `md2akn` opened for it
that the parser takes on purpose (invariant I5, review fix-2 of #300 and #301).

`md2akn.validate.block_conformance` compares every block of every current instrument
(`scjn.iter_current_*`, the corpora `test_units_release_sweep.py` sweeps) with the node
rooted at it. A disagreement is either a defect -- the sweep fails on it -- or one of the
shapes in `md2akn.validate.CLASES_DOCUMENTADAS`, each with the reason the SCJN's own data
forces it. This script writes the second kind as the fixture the sweep reads,
`packages/md2akn/tests/fixtures_units/marker_documented.json`: one entry per block, with
the marker's attributes and the first line of the block as evidence. The sweep fails on a
documented case that is not in the fixture and on an entry that no longer is one, so a new
instrument cannot add one without somebody reading its raw block.

    uv run python scripts/embeddings/classify_marker_exceptions.py --write
    uv run python scripts/embeddings/classify_marker_exceptions.py   # summary only

Read-only apart from `--write`; no network.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages" / "md2akn"))
sys.path.insert(0, str(ROOT / "packages" / "scjn"))

from md2akn import parse_markdown  # noqa: E402
from md2akn.outline import MARCADORES_EN_TEXTO  # noqa: E402
from md2akn.validate import CLASES_DOCUMENTADAS, block_conformance  # noqa: E402
from scjn import (  # noqa: E402
    iter_current_federal_laws,
    iter_current_lineamientos,
    iter_current_reglamentos,
    iter_current_tratados,
)

FIXTURE = ROOT / "packages" / "md2akn" / "tests" / "fixtures_units" / "marker_documented.json"
COLLECTIONS = (
    ("leyes", iter_current_federal_laws, "slug"),
    ("reglamentos", iter_current_reglamentos, "id_ordenamiento"),
    ("lineamientos", iter_current_lineamientos, "id_ordenamiento"),
    ("tratados", iter_current_tratados, "id_ordenamiento"),
)


def raw_block(markdown: str, orden: str) -> tuple[str, str]:
    """The marker line of block `orden` and the first line of text after it."""
    found = list(MARCADORES_EN_TEXTO.finditer(markdown))
    for k, m in enumerate(found):
        if f"orden={orden} " in m.group(0):
            end = found[k + 1].start() if k + 1 < len(found) else len(markdown)
            first = next((line.strip() for line in markdown[m.end():end].split("\n")
                          if line.strip()), "")
            return m.group(0), first[:140]
    return "", ""


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--write", action="store_true")
    args = p.parse_args(argv)
    entries, real = [], []
    for collection, iterate, key in COLLECTIONS:
        for instrument in iterate():
            tree = parse_markdown(instrument["markdown"])
            for d in block_conformance(tree):
                if not d.documented:
                    real.append((collection, instrument[key], str(d)))
                    continue
                marker, first = raw_block(instrument["markdown"], d.orden)
                entries.append({
                    "collection": collection, "key": str(instrument[key]), "orden": d.orden,
                    "class": d.klass, "eId": d.eId, "detail": d.detail,
                    "evidence": f"{marker} | first line: {first}",
                })
    for klass, why in CLASES_DOCUMENTADAS.items():
        n = [e for e in entries if e["class"] == klass]
        print(f"{klass}: {len(n)} blocks in {len({(e['collection'], e['key']) for e in n})} instruments")
        print(f"    {why}")
    print(f"{len(real)} disagreement(s) that are not documented")
    for row in real[:20]:
        print("   ", *row)
    if args.write:
        FIXTURE.write_text(json.dumps(entries, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print("wrote", FIXTURE, len(entries), "entries")
    return 1 if real else 0


if __name__ == "__main__":
    sys.exit(main())

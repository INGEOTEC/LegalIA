"""The corpus, as `units.parquet` + `leaves.parquet` + `corpus-manifest.json`
(issue #218 Fase 2).

Walks `scjn.iter_current_federal_laws()` — the *current* text of every
federal law the cached `scjn-leyes` release covers, one snapshot per law —
and calls `md2akn.text_units()` on each. Nothing here talks to the SCJN or
the DOF: the corpus is read off `scjn`'s own disk-first cache, populated
ahead of time with ``nota2md download all`` (316 assets, ~307 MB) or the
`scjn download` CLI.

Row order is deterministic — slug, then document order within it — so the
file is byte-reproducible from the same release, cap and template: run this
twice against an unchanged cache and every byte of `units.parquet` matches.

    python scripts/embeddings/build_units.py --work-dir ~/emb-run
    python scripts/embeddings/build_units.py --work-dir ~/emb-run --slug lft cpeum
    python scripts/embeddings/build_units.py --work-dir ~/emb-run --cap 2000 --template bare
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "packages" / "md2akn"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "packages" / "scjn"))

import pyarrow as pa  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402

from md2akn import DEFAULT_SPLIT_CAP, leaf_map, parse_markdown, text_units  # noqa: E402
from scjn import iter_current_federal_laws  # noqa: E402
from scjn.release import local_slugs  # noqa: E402

from _atomic import atomic_write_bytes, atomic_write_text  # noqa: E402

UNITS_SCHEMA = pa.schema([
    ("slug", pa.string()),
    ("nombre", pa.string()),
    ("fecha_publicacion", pa.string()),
    ("archivo", pa.string()),
    ("codNota", pa.int64()),
    ("unit_type", pa.string()),
    ("eId", pa.string()),
    ("piece", pa.int32()),
    ("piece_eId", pa.string()),
    ("akn_type", pa.string()),
    ("num", pa.string()),
    ("path", pa.list_(pa.string())),
    ("start_char", pa.int64()),
    ("end_char", pa.int64()),
    ("text", pa.string()),
    ("text_sha1", pa.string()),
])

LEAVES_SCHEMA = pa.schema([
    ("slug", pa.string()),
    ("archivo", pa.string()),
    ("eId", pa.string()),
    ("akn_type", pa.string()),
    ("unit_eId", pa.string()),
    ("unit_piece", pa.int32()),
])


def build_rows(slugs=None, *, cap: int, template: str, cache_dir=None):
    """`(unit_rows, leaf_rows, law_count)` over every law `iter_current_federal_laws`
    yields — a generator's worth of work done eagerly here, since both
    Parquet files are written once at the end rather than streamed."""
    unit_rows: list[dict] = []
    leaf_rows: list[dict] = []
    law_count = 0
    for ley in iter_current_federal_laws(slugs, cache_dir=cache_dir):
        law_count += 1
        tree = parse_markdown(ley["markdown"])
        units = text_units(tree, cap=cap, template=template)
        for unit in units:
            unit_rows.append({
                "slug": ley["slug"],
                "nombre": ley["nombre"],
                "fecha_publicacion": ley["fecha_publicacion"],
                "archivo": ley["archivo"],
                "codNota": ley["codNota"],
                "unit_type": unit.unit_type,
                "eId": unit.eId,
                "piece": unit.piece,
                "piece_eId": unit.piece_eId,
                "akn_type": unit.akn_type,
                "num": unit.num,
                "path": list(unit.path),
                "start_char": unit.start_char,
                "end_char": unit.end_char,
                "text": unit.text,
                "text_sha1": unit.text_sha1,
            })
        for ref in leaf_map(tree, units):
            leaf_rows.append({
                "slug": ley["slug"],
                "archivo": ley["archivo"],
                "eId": ref.eId,
                "akn_type": ref.akn_type,
                "unit_eId": ref.unit_eId,
                "unit_piece": ref.unit_piece,
            })
    return unit_rows, leaf_rows, law_count


def _write_parquet(path: Path, rows: list[dict], schema: pa.Schema) -> None:
    table = pa.Table.from_pylist(rows, schema=schema)
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink, compression="zstd")
    atomic_write_bytes(path, sink.getvalue().to_pybytes())


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, default=None,
                         help="Defaults to $LEGALIA_EMB_WORK, then cwd -- always under "
                              "/home, since a Slurm job cannot see /tmp on this cluster.")
    parser.add_argument("--slug", nargs="+", default=None,
                         help="Build only these laws' units instead of every cached one.")
    parser.add_argument("--cap", type=int, default=DEFAULT_SPLIT_CAP)
    parser.add_argument("--template", choices=("bare", "contextual"), default="bare")
    args = parser.parse_args(argv)

    work_dir = args.work_dir or Path(os.environ.get("LEGALIA_EMB_WORK", "."))
    work_dir.mkdir(parents=True, exist_ok=True)

    slugs = args.slug or local_slugs()
    started = time.time()
    unit_rows, leaf_rows, law_count = build_rows(slugs, cap=args.cap, template=args.template)

    _write_parquet(work_dir / "units.parquet", unit_rows, UNITS_SCHEMA)
    _write_parquet(work_dir / "leaves.parquet", leaf_rows, LEAVES_SCHEMA)

    counts: dict[str, int] = {}
    for row in unit_rows:
        counts[row["unit_type"]] = counts.get(row["unit_type"], 0) + 1

    manifest = {
        "laws": law_count,
        "units": len(unit_rows),
        "leaves": len(leaf_rows),
        "distinct_texts": len({row["text_sha1"] for row in unit_rows}),
        "counts_by_unit_type": counts,
        "cap": args.cap,
        "template": args.template,
        "seconds": round(time.time() - started, 1),
    }
    atomic_write_text(
        work_dir / "corpus-manifest.json",
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
    )
    print(json.dumps(manifest, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

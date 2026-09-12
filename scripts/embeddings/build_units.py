"""The corpus, as `units.parquet` + `leaves.parquet` + `corpus-manifest.json`
(issue #218 Fase 2, widened to the three collections by #227 Fase 3).

Walks the *current* text of every instrument of one collection — the cached
`scjn-leyes` (315 laws), `scjn-reglamentos` (1,082 with text) or
`scjn-lineamientos` (126) release — and calls `md2akn.text_units()` on each.
Nothing here talks to the SCJN or the DOF: the corpus is read off `scjn`'s
own disk-first cache, populated ahead of time with ``nota2md download all``
(all three releases since issue #225) or the `scjn download` CLI.

One collection per work directory, and dedup stays inside a collection
(#227's decision 8: cross-collection dedup was measured at 0.9 % of the
vectors and would force a shared file across three corpora that are crawled,
packaged and republished on their own schedules).

`clave` is the column that names the published file, so a reader never
branches: a law's slug, and the `id_ordenamiento` for the other two —
which is the only key the SCJN guarantees stable for them (#220's decision,
`scjn.catalog.instrumento_key`). `slug` and `codNota` stay exactly what they
were and are null for the id-keyed collections, which have no DOF link at
all.

Row order is deterministic — `(coleccion, clave, document order)` — so the
file is byte-reproducible from the same release, cap and template: run this
twice against an unchanged cache and every byte of `units.parquet` matches.

    python scripts/embeddings/build_units.py --work-dir emb-run-leyes
    python scripts/embeddings/build_units.py --work-dir emb-run-leyes --slug lft cpeum
    python scripts/embeddings/build_units.py --work-dir emb-run-reglamentos \
        --coleccion reglamentos
    python scripts/embeddings/build_units.py --work-dir emb-run-lineamientos \
        --coleccion lineamientos --id 31834
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

import md2akn  # noqa: E402
from md2akn import (  # noqa: E402
    DEFAULT_SPLIT_CAP,
    leaf_map,
    max_unit_chars,
    parse_markdown,
    text_units,
)
from md2akn.segmenter import iter_blocks, split_frontmatter  # noqa: E402
from md2akn.structure import modo_sin_articulos  # noqa: E402
from scjn import (  # noqa: E402
    iter_current_federal_laws,
    iter_current_lineamientos,
    iter_current_reglamentos,
)
from scjn.release import (  # noqa: E402
    local_lineamientos_ids,
    local_reglamentos_ids,
    local_slugs,
)

from _atomic import atomic_write_bytes, atomic_write_text  # noqa: E402

#: One entry per collection this can build: the reader that yields its
#: current text, the disk-first lister behind "every instrument this machine
#: has", and the field of a yielded record that is its `clave` (issue #227's
#: decision 6/7). Deliberately not a second `scjn.cache.Coleccion` — that
#: descriptor is about a *release* (tag series, cache subdirectory) and knows
#: nothing about `leyes`, which this table does have to cover.
COLECCIONES = {
    "leyes": {"iter": iter_current_federal_laws, "local": local_slugs, "clave": "slug"},
    "reglamentos": {
        "iter": iter_current_reglamentos, "local": local_reglamentos_ids,
        "clave": "id_ordenamiento",
    },
    "lineamientos": {
        "iter": iter_current_lineamientos, "local": local_lineamientos_ids,
        "clave": "id_ordenamiento",
    },
}

UNITS_SCHEMA = pa.schema([
    ("coleccion", pa.string()),
    ("clave", pa.string()),
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
    ("coleccion", pa.string()),
    ("clave", pa.string()),
    ("slug", pa.string()),
    ("archivo", pa.string()),
    ("eId", pa.string()),
    ("akn_type", pa.string()),
    ("unit_eId", pa.string()),
    ("unit_piece", pa.int32()),
])


def build_rows(
    claves=None,
    *,
    cap: int,
    template: str,
    cache_dir=None,
    split_over_cap: bool = True,
    coleccion: str = "leyes",
):
    """`(unit_rows, leaf_rows, stats)` over every instrument the collection's
    own reader yields — a generator's worth of work done eagerly here, since
    both Parquet files are written once at the end rather than streamed.

    `stats` carries what the manifest reports beyond the row counts: how many
    instruments were read, how rule 8 read each of them, and what rule 9 left
    over the cap (issue #227's decisions 2 and 3 — the two new rules are
    recorded, like the other seven, rather than being invisible in the
    output).
    """
    lector = COLECCIONES[coleccion]
    unit_rows: list[dict] = []
    leaf_rows: list[dict] = []
    instrumentos = 0
    modos: dict[str, int] = {}
    over_cap = residuo = maximo = 0
    for instrumento in lector["iter"](claves, cache_dir=cache_dir):
        instrumentos += 1
        clave = str(instrumento[lector["clave"]])
        markdown = instrumento["markdown"]
        _meta, fin_meta = split_frontmatter(markdown)
        modo = modo_sin_articulos(list(iter_blocks(markdown, fin_meta)))
        modos[modo or "articulo"] = modos.get(modo or "articulo", 0) + 1
        tree = parse_markdown(markdown)
        units = text_units(tree, cap=cap, template=template, split_over_cap=split_over_cap)
        reporte = max_unit_chars(tree, units, cap=cap)
        over_cap += reporte.over_cap
        residuo += reporte.unsplittable
        maximo = max(maximo, reporte.max_chars)
        if reporte.splittable:
            raise SystemExit(
                f"{coleccion}/{clave}: {reporte.splittable} unit(s) over the cap that "
                "rule 9 could still have split -- md2akn's own invariant is broken"
            )
        for unit in units:
            unit_rows.append({
                "coleccion": coleccion,
                "clave": clave,
                "slug": instrumento.get("slug"),
                "nombre": instrumento["nombre"],
                "fecha_publicacion": instrumento["fecha_publicacion"],
                "archivo": instrumento["archivo"],
                "codNota": instrumento.get("codNota"),
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
                "coleccion": coleccion,
                "clave": clave,
                "slug": instrumento.get("slug"),
                "archivo": instrumento["archivo"],
                "eId": ref.eId,
                "akn_type": ref.akn_type,
                "unit_eId": ref.unit_eId,
                "unit_piece": ref.unit_piece,
            })
    stats = {
        "instruments": instrumentos,
        "instruments_by_numbering": modos,
        "units_over_cap": over_cap,
        "units_over_cap_unsplittable": residuo,
        "max_unit_chars": maximo,
    }
    return unit_rows, leaf_rows, stats


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
    parser.add_argument("--coleccion", choices=tuple(COLECCIONES), default="leyes",
                         help="Which corpus to build. One collection per work directory "
                              "(issue #227's decision 8).")
    parser.add_argument("--slug", nargs="+", default=None,
                         help="Build only these laws' units instead of every cached one. "
                              "`leyes` only -- the other two collections are keyed by "
                              "id_ordenamiento and have no slug at all (--id).")
    parser.add_argument("--id", nargs="+", default=None, dest="ids",
                         help="Build only these instruments' units, by id_ordenamiento. "
                              "`reglamentos`/`lineamientos` only.")
    parser.add_argument("--cap", type=int, default=DEFAULT_SPLIT_CAP)
    parser.add_argument("--template", choices=("bare", "contextual"), default="bare")
    parser.add_argument("--no-split-over-cap", dest="split_over_cap", action="store_false",
                         help="Turn off rule 9 (issue #227): leave a unit that is still over "
                              "the cap after the article split whole, as #218 did. Only ever "
                              "used to reproduce a pre-#227 corpus byte for byte.")
    args = parser.parse_args(argv)

    if args.slug and args.coleccion != "leyes":
        raise SystemExit("--slug is `leyes` only; the id-keyed collections take --id")
    if args.ids and args.coleccion == "leyes":
        raise SystemExit("--id is for the id-keyed collections; `leyes` takes --slug")

    work_dir = args.work_dir or Path(os.environ.get("LEGALIA_EMB_WORK", "."))
    work_dir.mkdir(parents=True, exist_ok=True)

    lector = COLECCIONES[args.coleccion]
    claves = args.slug or args.ids or lector["local"]()
    started = time.time()
    unit_rows, leaf_rows, stats = build_rows(
        claves, cap=args.cap, template=args.template,
        split_over_cap=args.split_over_cap, coleccion=args.coleccion,
    )

    _write_parquet(work_dir / "units.parquet", unit_rows, UNITS_SCHEMA)
    _write_parquet(work_dir / "leaves.parquet", leaf_rows, LEAVES_SCHEMA)

    counts: dict[str, int] = {}
    for row in unit_rows:
        counts[row["unit_type"]] = counts.get(row["unit_type"], 0) + 1

    manifest = {
        "coleccion": args.coleccion,
        **stats,
        "units": len(unit_rows),
        "leaves": len(leaf_rows),
        "distinct_texts": len({row["text_sha1"] for row in unit_rows}),
        "counts_by_unit_type": counts,
        "cap": args.cap,
        "template": args.template,
        "split_over_cap": args.split_over_cap,
        "md2akn_version": md2akn.__version__,
        "seconds": round(time.time() - started, 1),
    }
    atomic_write_text(
        work_dir / "corpus-manifest.json",
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
    )
    print(json.dumps(manifest, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

"""Export the instrument map as one compact JSON for the website's Atlas.

Issue #244: `build_instrument_umap_html.py` (issue #242) draws the 1,523
federal instruments as a standalone Vega-Lite page under `output/`, from what
lives in the gitignored `emb-run-umap/`. The website cannot read parquet or
npy, and that page inlines a 1.4 MB dataset with redundant columns, so this
script writes **one JSON** with exactly what the Atlas needs, into the
website's own tree:

    uv run --group viz python scripts/embeddings/export_atlas_data.py
    uv run --group viz python scripts/embeddings/export_atlas_data.py \\
        --output /tmp/atlas.json --n-neighbors 8,16 --decimals 3

It is a pure read of #242's outputs — `instruments.parquet`,
`instrument-matrix/matrix.npy`, `matrix.json`, `umap.parquet` and
`umap.json` — and never refits anything: `project()` is called only to fail
loudly when the matrix is missing, and is a no-op once `umap.parquet` exists
(there is no `--force` here on purpose). No Slurm, no network, seconds.

* **`provisions`, not `units`.** A unit (`md2akn.text_units`) is an article,
  a transitory article or another indivisible piece of an instrument; a
  general reader knows that as a *provision*. The Python side keeps `units`;
  the export never says it.
* **Targets as `[id, weight]` pairs.** `out` is `strongest_targets` over the
  instrument's row (who its provisions point at hardest), `inc` the same
  helper over its column (who points *here* hardest) — the ranking the #242
  page used, imported rather than reimplemented, so the research page and the
  Atlas cannot disagree about who the five are. Weights keep one decimal: a
  weight is a sum of `1/m` fractions.
* **`out`'s total is not exported**: under the `1/m` rule it equals `p` for
  every instrument (`matrix.json`'s `row_sums_equal_units`).
* **Short keys** (`c`, `k`, `n`, `p`, `in`, `out`, `inc`) because there are
  1,523 of each; `meta` spells everything out.

`meta.commit` is provenance for the file, and the page never displays it.
Regenerating the committed file is this script, run by a human — never a
workflow (issue #115, Hallazgo C).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_umap_html as html  # noqa: E402
import instrument_matrix  # noqa: E402
from build_instrument_umap_html import (  # noqa: E402
    DEFAULT_N_NEIGHBORS, DEFAULT_RADIO_VALUE, TOP_TARGETS, project, strongest_targets)
from package_vectors import TAGS as VECTOR_TAGS  # noqa: E402
from prepare_umap_input import DEFAULT_MODEL  # noqa: E402

DEFAULT_OUTPUT = Path("website/pages/atlas/atlas.json")
DEFAULT_DECIMALS = 4

TITLE = "An Atlas of Mexican Federal Law: Laws, Regulations and Guidelines"


def weighted_targets(matrix, index: int, limit: int) -> list[list]:
    """`strongest_targets` as `[id, weight]` pairs, the weight to one decimal.

    Widened to a Python `float` before rounding: a `float32` 11.3 would
    otherwise reach JSON as `11.300000190734863`.
    """
    row = matrix[index]
    return [[j, round(float(row[j]), 1)] for j in strongest_targets(matrix, index, limit)]


def collection_counts(collections) -> dict:
    """Instruments per collection, in the order the collections first appear
    in the table (`prepare_umap_input.py` stacks leyes, reglamentos,
    lineamientos)."""
    counts: dict[str, int] = {}
    for name in collections:
        counts[name] = counts.get(name, 0) + 1
    return counts


def release_names(collections) -> list[str]:
    """The corpus releases the texts came from, then the vector releases the
    embeddings came from, for every collection present."""
    return [f"scjn-{name}" for name in collections] + [VECTOR_TAGS[name] for name in collections]


def model_name(work_dir: Path) -> str:
    """The model `prepare_umap_input.py` recorded in `input.json`, or its
    default when that record is absent."""
    record = Path(work_dir) / "input.json"
    if record.exists():
        return json.loads(record.read_text(encoding="utf-8")).get("model", DEFAULT_MODEL)
    return DEFAULT_MODEL


def atlas(work_dir: Path, *, n_neighbors=DEFAULT_N_NEIGHBORS, top: int = TOP_TARGETS,
          decimals: int = DEFAULT_DECIMALS, now=None, log=print) -> dict:
    """The whole export as one dict: `meta`, `instruments`, `projections`,
    in that order. Raises `SystemExit` on anything that does not add up."""
    from datetime import datetime, timezone

    import numpy as np
    import pyarrow.parquet as pq

    work_dir = Path(work_dir)
    n_neighbors = [int(k) for k in n_neighbors]
    # Only ever a check here: `umap.parquet` is on disk, so this refits nothing,
    # and a missing matrix is a `SystemExit` naming instrument_matrix.py.
    fits = project(work_dir, n_neighbors=n_neighbors, log=log)

    out_dir = instrument_matrix.output_dir(work_dir)
    matrix = np.load(out_dir / "matrix.npy")
    summary = json.loads((out_dir / "matrix.json").read_text(encoding="utf-8"))
    table = pq.read_table(work_dir / "instruments.parquet").to_pandas()
    coordinates = pq.read_table(out_dir / "umap.parquet").to_pandas()

    n = len(table)
    if matrix.ndim != 2 or matrix.shape != (n, n):
        raise SystemExit(f"matrix.npy is {matrix.shape}, instruments.parquet has {n} rows: "
                         "rerun instrument_matrix.py over this work directory")
    if table["i"].astype(int).tolist() != list(range(n)):
        raise SystemExit("instruments.parquet is not in `i` order")
    missing = [k for k in n_neighbors
               if f"x{k}" not in coordinates.columns or f"y{k}" not in coordinates.columns]
    if missing:
        raise SystemExit(f"umap.parquet has no fit for n_neighbors={missing}: "
                         "refit with build_instrument_umap_html.py --force")
    coordinates = coordinates.set_index("i").reindex(range(n))
    if coordinates.isna().any().any():
        raise SystemExit("umap.parquet does not cover every instrument")

    incoming = matrix.sum(axis=0)
    transposed = matrix.T
    instruments = []
    for i, row in enumerate(table.itertuples(index=False)):
        out = weighted_targets(matrix, i, top)
        if not out:
            raise SystemExit(f"instrument {i} ({row.clave}) points nowhere: "
                             "matrix.npy is not the one instrument_matrix.py writes")
        instruments.append({
            "c": row.coleccion,
            "k": row.clave,
            "n": row.nombre,
            "p": int(row.units),
            "in": round(float(incoming[i]), 1),
            "out": out,
            "inc": weighted_targets(transposed, i, top),
        })

    provisions = int(summary["unit_rows"])
    if sum(entry["p"] for entry in instruments) != provisions:
        raise SystemExit(f"the instruments' provisions add up to "
                         f"{sum(entry['p'] for entry in instruments)}, matrix.json says "
                         f"{provisions}")

    projections = {}
    for k in n_neighbors:
        xs = coordinates[f"x{k}"].to_numpy(dtype="float64")
        ys = coordinates[f"y{k}"].to_numpy(dtype="float64")
        projections[str(k)] = [[round(float(x), decimals), round(float(y), decimals)]
                               for x, y in zip(xs, ys)]

    collections = collection_counts(table["coleccion"])
    fit = fits["fits"][0]
    meta = {
        "title": TITLE,
        "generated": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        "commit": html.repository_commit(),
        "model": model_name(work_dir),
        "instruments": n,
        "provisions": provisions,
        "distinct_texts": int(summary["vector_rows"]),
        "collections": collections,
        "n_neighbors": n_neighbors,
        "default_n_neighbors": (DEFAULT_RADIO_VALUE if DEFAULT_RADIO_VALUE in n_neighbors
                                else n_neighbors[len(n_neighbors) // 2]),
        "umap": {key: fit.get(key) for key in ("min_dist", "metric", "random_state",
                                               "umap_version")},
        "weighting": summary.get("weighting", "1/m"),
        "top": top,
        "sources": release_names(collections),
    }
    return {"meta": meta, "instruments": instruments, "projections": projections}


def export(work_dir: Path, output: Path, **kwargs) -> dict:
    """Write `atlas()` to `output` atomically and return what was measured."""
    from _atomic import atomic_write_text

    log = kwargs.get("log", print)
    data = atlas(work_dir, **kwargs)
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n"
    output = Path(output)
    atomic_write_text(output, text)
    measured = {
        "output": str(output),
        "bytes": output.stat().st_size,
        "instruments": data["meta"]["instruments"],
        "provisions": data["meta"]["provisions"],
        "collections": data["meta"]["collections"],
        "n_neighbors": data["meta"]["n_neighbors"],
    }
    log(json.dumps(measured, ensure_ascii=False))
    log(f"{output}: {measured['bytes'] / 1e3:.1f} kB, {measured['instruments']} instruments, "
        f"{measured['provisions']} provisions")
    return measured


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--work-dir", type=Path, default=Path("emb-run-umap"))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--n-neighbors", default=",".join(str(k) for k in DEFAULT_N_NEIGHBORS),
                        help="comma-separated neighbourhood sizes, each already fitted")
    parser.add_argument("--top", type=int, default=TOP_TARGETS,
                        help="targets per direction and instrument")
    parser.add_argument("--decimals", type=int, default=DEFAULT_DECIMALS,
                        help="decimals kept on every coordinate")
    args = parser.parse_args(argv)

    export(args.work_dir, args.output,
           n_neighbors=tuple(int(k) for k in args.n_neighbors.split(",") if k),
           top=args.top, decimals=args.decimals)


if __name__ == "__main__":
    main()

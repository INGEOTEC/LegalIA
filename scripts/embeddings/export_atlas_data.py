"""Export the instrument map as one compact JSON for the website's Atlas.

Issue #244: `build_instrument_umap_html.py` (issue #242) draws the federal
instruments as a standalone Vega-Lite page under `output/`, from what lives in
the gitignored work directory (`emb-run-atlas/`, prepared with `--unique-names`,
issue #259). The website cannot read parquet or
npy, and that page inlines a 1.4 MB dataset with redundant columns, so this
script writes **one JSON** with exactly what the Atlas needs, into the
website's own tree:

    uv run --group viz python scripts/embeddings/export_atlas_data.py
    uv run --group viz python scripts/embeddings/export_atlas_data.py \\
        --output /tmp/atlas.json --n-neighbors 8,16 --decimals 3
    uv run --group viz python scripts/embeddings/export_atlas_data.py \\
        --work-dir emb-run-atlas-4b --output website/pages/atlas/atlas-qwen3-4b.json \\
        --instruments-as website/pages/atlas/atlas.json

It is a pure read of #242's outputs — `instruments.parquet`,
`instrument-matrix/matrix.npy`, `matrix.json`, `umap.parquet` and
`umap.json` — and never refits anything: `project()` is called only to fail
loudly when the matrix is missing, and is a no-op once `umap.parquet` exists
(there is no `--force` here on purpose). No Slurm, no network, seconds.

* **`excerpts`, not `units`** (issue #271). A unit (`md2akn.text_units`) is an
  article, a transitory article or another block of text cut from an
  instrument, whatever its legal role; the reader's word for that is *excerpt*
  (not *provision*, which would suggest a normative rule a heading is not, and
  which already means a DOF note in this project). The Python side keeps
  `units`; the export never says it. The JSON keys keep their old name
  (`meta.provisions`, the pair files' `provisions`): they are code, and the pair
  files live in a release.
* **Targets as `[id, weight]` pairs.** `out` is `strongest_targets` over the
  instrument's row (who its excerpts point at hardest), `inc` the same
  helper over its column (who points *here* hardest) — the ranking the #242
  page used, imported rather than reimplemented, so the research page and the
  Atlas cannot disagree about who the five are. Weights keep one decimal: a
  weight is a sum of `1/m` fractions.
* **`out`'s total is `pc`** (issue #271): under the `1/m` rule the row sum
  equals the instrument's *counted* excerpts (`matrix.json`'s
  `row_sums_equal_counted_rows`) — its `p` minus its headings, its
  transitorios (neither is compared at all, issue #264) and the word-for-word
  matches several instruments share, which are searched but not counted. It is
  exported as the integer `pc`, refused unless the row sum is within 1e-3 of an
  integer, at least 1, and the `pc` add up to `meta.counted_rows`. `pc` is per
  model (BM25 differs from the embeddings in 197 instruments), so unlike `p` it
  is **not** part of the `--instruments-as` comparison. `meta` carries the totals (`heading_rows_excluded`,
  `transitorio_rows_excluded`, `identical_shared_dropped`, `counted_rows`).
* **Unique instruments only** (issue #259). A work directory prepared without
  `prepare_umap_input.py --unique-names` is refused, and `meta` records
  `unique_names` and `duplicates_dropped` per collection.
* **Short keys** (`c`, `k`, `n`, `p`, `pc`, `in`, `out`, `inc`) because there are
  over a thousand of each; `meta` spells everything out.

* **`meta.method` (issue #267).** `"dense"` for the two embedding models, `"bm25"`
  for the lexical baseline, whose `model` is `"bm25"` and whose `meta` also
  carries the index parameters, the tokeniser, the vocabulary size and the
  documents indexed; the weights in `out`/`inc` are the same `1/m` sums either way.

* **A second model's file (issue #261).** `--instruments-as` refuses, with a
  `SystemExit` naming the first differing position, an export whose `c`/`k`/`n`/`p`
  differ from an existing `atlas.json`'s, because the page swaps the two files
  keeping its selection.

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
import scoring  # noqa: E402
from build_instrument_umap_html import (  # noqa: E402
    DEFAULT_N_NEIGHBORS, DEFAULT_RADIO_VALUE, TOP_TARGETS, project, strongest_targets)
from package_vectors import TAGS as VECTOR_TAGS  # noqa: E402
from prepare_umap_input import DEFAULT_MODEL, UNIQUE_REPORT  # noqa: E402
from unique_instruments import GROUPED_COLLECTIONS  # noqa: E402

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


def counted_excerpts(row_sum: float, index: int, clave: str) -> int:
    """An instrument's counted excerpts: its matrix row sum as an integer.

    Under the `1/m` rule a row sums to a whole number of counted unit rows
    (`row_sums_equal_counted_rows`); a sum further than 1e-3 from an integer, or
    below 1, means `matrix.npy` is not the one `instrument_matrix.py` writes.
    """
    value = float(row_sum)
    if abs(value - round(value)) > 1e-3:
        raise SystemExit(f"instrument {index} ({clave}) has a matrix row sum of {value:.4f}, "
                         "not a whole number of counted excerpts: matrix.npy is not the one "
                         "instrument_matrix.py writes")
    if round(value) < 1:
        raise SystemExit(f"instrument {index} ({clave}) has no counted excerpt (row sum "
                         f"{value:.4f}): matrix.npy is not the one instrument_matrix.py writes")
    return int(round(value))


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


def input_record(work_dir: Path) -> dict:
    """`input.json` as the preparing script wrote it, `{}` when absent."""
    record = Path(work_dir) / "input.json"
    return json.loads(record.read_text(encoding="utf-8")) if record.exists() else {}


def duplicates_dropped(work_dir: Path) -> dict:
    """What `prepare_umap_input.py --unique-names` dropped, per id-keyed
    collection (issue #259), after checking the work directory really was
    prepared that way.

    The Atlas draws unique laws, regulations and guidelines, so a work
    directory prepared without `--unique-names` is refused here — with
    `export_atlas_pairs.py` calling this too — rather than letting the website
    be fed a map that counts a reissued regulation several times. Also refuses
    an `instruments.parquet` that still lists an instrument the report says
    was dropped.
    """
    import pyarrow.parquet as pq

    work_dir = Path(work_dir)
    record = work_dir / "input.json"
    if not record.exists() or not json.loads(record.read_text(encoding="utf-8")).get("unique_names"):
        raise SystemExit(f"{work_dir} was not prepared with `prepare_umap_input.py --unique-names` "
                         "(input.json does not record unique_names: true): the Atlas draws "
                         "unique instruments only, rerun the chain with that flag")
    report = json.loads((work_dir / UNIQUE_REPORT).read_text(encoding="utf-8"))
    table = pq.read_table(work_dir / "instruments.parquet").to_pandas()
    present = set(zip(table["coleccion"], table["clave"].astype(str)))
    for coleccion, entry in report.items():
        for dropped in entry["dropped_instruments"]:
            if (coleccion, str(dropped["clave"])) in present:
                raise SystemExit(f"{coleccion} {dropped['clave']} is listed as dropped in "
                                 f"{UNIQUE_REPORT} but is in instruments.parquet")
    return {name: int(entry["dropped"]) for name, entry in report.items()
            if name in GROUPED_COLLECTIONS}


#: The instrument fields whose position-by-position agreement between two
#: model's exports the page relies on: `i` must mean the same instrument in both.
INSTRUMENT_TABLE_KEYS = ("c", "k", "n", "p")


def check_same_instruments(data: dict, reference_path: Path) -> None:
    """Refuse `data` unless its instruments are the ones `reference_path` (an
    existing `atlas.json`) lists, position by position (issue #261).

    The Atlas page fetches a second model's file on demand and keeps its
    selection across the swap, which only makes sense when instrument `i` is
    the same instrument in both files: `c`, `k`, `n` and `p` must all match, and
    the count. A `SystemExit` naming the first position that differs otherwise.
    """
    reference = json.loads(Path(reference_path).read_text(encoding="utf-8"))["instruments"]
    entries = data["instruments"]
    for position, (mine, theirs) in enumerate(zip(entries, reference)):
        for key in INSTRUMENT_TABLE_KEYS:
            if mine[key] != theirs[key]:
                raise SystemExit(
                    f"instrument {position} differs from {reference_path}: `{key}` is "
                    f"{mine[key]!r} here, {theirs[key]!r} there -- the two exports do not "
                    "list the same instruments in the same positions")
    if len(entries) != len(reference):
        raise SystemExit(
            f"{len(entries)} instruments here, {len(reference)} in {reference_path}: they "
            f"differ from position {min(len(entries), len(reference))} on -- the two exports "
            "do not list the same instruments in the same positions")


def atlas(work_dir: Path, *, n_neighbors=DEFAULT_N_NEIGHBORS, top: int = TOP_TARGETS,
          decimals: int = DEFAULT_DECIMALS, now=None, log=print) -> dict:
    """The whole export as one dict: `meta`, `instruments`, `projections`,
    in that order. Raises `SystemExit` on anything that does not add up."""
    from datetime import datetime, timezone

    import numpy as np
    import pyarrow.parquet as pq

    work_dir = Path(work_dir)
    dropped = duplicates_dropped(work_dir)
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
    counted = matrix.sum(axis=1)
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
            "pc": counted_excerpts(counted[i], i, row.clave),
            "in": round(float(incoming[i]), 1),
            "out": out,
            "inc": weighted_targets(transposed, i, top),
        })

    provisions = int(summary["unit_rows"])
    for key in ("heading_rows_excluded", "transitorio_rows_excluded",
                "identical_shared_dropped", "counted_rows"):
        if key not in summary:
            raise SystemExit(f"matrix.json has no `{key}`: it was written by an older "
                             "instrument_matrix.py, rerun it with --force")
    if not summary.get("row_sums_equal_counted_rows"):
        raise SystemExit("matrix.json does not report row_sums_equal_counted_rows: true")
    if abs(float(matrix.sum()) - summary["counted_rows"]) > 1e-3:
        raise SystemExit(f"matrix.npy sums to {float(matrix.sum()):.3f}, matrix.json counted "
                         f"{summary['counted_rows']} rows")
    if sum(entry["pc"] for entry in instruments) != int(summary["counted_rows"]):
        raise SystemExit(f"the instruments' counted excerpts add up to "
                         f"{sum(entry['pc'] for entry in instruments)}, matrix.json says "
                         f"{summary['counted_rows']}")
    if sum(entry["p"] for entry in instruments) != provisions:
        raise SystemExit(f"the instruments' excerpts add up to "
                         f"{sum(entry['p'] for entry in instruments)}, matrix.json says "
                         f"{provisions}")

    projections = {}
    for k in n_neighbors:
        xs = coordinates[f"x{k}"].to_numpy(dtype="float64")
        ys = coordinates[f"y{k}"].to_numpy(dtype="float64")
        projections[str(k)] = [[round(float(x), decimals), round(float(y), decimals)]
                               for x, y in zip(xs, ys)]

    if summary.get("method", scoring.DENSE) != scoring.method_of(work_dir):
        raise SystemExit(f"matrix.json was computed with method "
                         f"{summary.get('method', scoring.DENSE)!r}, input.json says "
                         f"{scoring.method_of(work_dir)!r}: rerun instrument_matrix.py --force")
    collections = collection_counts(table["coleccion"])
    fit = fits["fits"][0]
    meta = {
        "title": TITLE,
        "generated": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        "commit": html.repository_commit(),
        "model": model_name(work_dir),
        "method": summary.get("method", scoring.DENSE),
        "instruments": n,
        "provisions": provisions,
        "heading_rows_excluded": int(summary["heading_rows_excluded"]),
        "transitorio_rows_excluded": int(summary["transitorio_rows_excluded"]),
        "identical_shared_dropped": int(summary["identical_shared_dropped"]),
        "counted_rows": int(summary["counted_rows"]),
        "distinct_texts": int(summary["vector_rows"]),
        "collections": collections,
        "unique_names": True,
        "duplicates_dropped": dropped,
        "n_neighbors": n_neighbors,
        "default_n_neighbors": (DEFAULT_RADIO_VALUE if DEFAULT_RADIO_VALUE in n_neighbors
                                else n_neighbors[len(n_neighbors) // 2]),
        "umap": {key: fit.get(key) for key in ("min_dist", "metric", "random_state",
                                               "umap_version")},
        "weighting": summary.get("weighting", "1/m"),
        "top": top,
        "sources": release_names(collections),
    }
    if meta["method"] == scoring.BM25:
        # What the lexical baseline was built with (issue #267), from the
        # preparing script's own record.
        record = input_record(work_dir)
        meta["bm25"] = record["bm25"]
        meta["tokeniser"] = record["tokeniser"]
        meta["vocabulary"] = record["vocabulary"]
        meta["documents"] = record["documents"]
    return {"meta": meta, "instruments": instruments, "projections": projections}


def export(work_dir: Path, output: Path, instruments_as: Path | None = None, **kwargs) -> dict:
    """Write `atlas()` to `output` atomically and return what was measured.

    `instruments_as` is an existing `atlas.json` whose instrument table this
    export must repeat exactly (`check_same_instruments`), checked before
    anything is written."""
    from _atomic import atomic_write_text

    log = kwargs.get("log", print)
    data = atlas(work_dir, **kwargs)
    if instruments_as is not None:
        check_same_instruments(data, instruments_as)
        log(f"{instruments_as}: the same {len(data['instruments'])} instruments, in order")
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
        f"{measured['provisions']} excerpts")
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
    parser.add_argument("--instruments-as", type=Path, default=None, metavar="ATLAS_JSON",
                        help="an existing atlas.json (e.g. website/pages/atlas/atlas.json) "
                             "whose instruments -- c, k, n, p at every position -- this "
                             "export must list too; refuse otherwise")
    args = parser.parse_args(argv)

    export(args.work_dir, args.output, instruments_as=args.instruments_as,
           n_neighbors=tuple(int(k) for k in args.n_neighbors.split(",") if k),
           top=args.top, decimals=args.decimals)


if __name__ == "__main__":
    main()

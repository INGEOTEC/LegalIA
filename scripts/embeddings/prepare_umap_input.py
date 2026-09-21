"""Read the three collections' vectors once, into one matrix every UMAP job maps.

Issue #241. `legalvec` publishes one vector per distinct text of each corpus,
split into a per-instrument file plus the collection's shared file, so
reading the whole corpus means ~1,500 `load_vectors` calls over ~600 parquet
files. Three cluster jobs each doing that would triple a ten-minute step and,
worse, could disagree about row order; one `vectors.npy` cannot. This is the
login-node step that writes it, along with the id table that maps a row back
to its `(coleccion, text_sha1)` and the per-instrument centroid inputs.

Deduplication is **inside** a collection and never across them, matching
issue #227's own rule: a text two collections happen to share gets two rows
here, because it got a vector in each release.

    uv run --group viz python scripts/embeddings/prepare_umap_input.py
    uv run --group viz python scripts/embeddings/prepare_umap_input.py --force

Outputs, all under `--work-dir` (`emb-run-umap/`, gitignored):

* `vectors.npy` — `(N, K)` `float16`, exactly as published, no normalization.
* `vector_ids.parquet` — `row`, `coleccion`, `text_sha1`.
* `instruments.parquet` — `i`, `coleccion`, `clave`, `nombre`, `units`,
  `distinct_texts`.
* `centroid_input.npy` — `(instruments, K)` `float32`, the mean of an
  instrument's **unit rows'** vectors.
* `input.json` — model, K, N, per-collection counts, `legalvec.__version__`.
* `prepare.done` — written last; re-running without `--force` is a no-op.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import pyarrow as pa

import legalvec

from _atomic import atomic_write_npy, atomic_write_table, atomic_write_text

#: The three corpora, in the order their rows are stacked into `vectors.npy`.
COLLECTIONS = ("leyes", "reglamentos", "lineamientos")

#: The model this issue projects. `--model` exists so the 4B can be run
#: later; issue #241 runs only this one.
DEFAULT_MODEL = "Qwen/Qwen3-Embedding-0.6B"

DONE_MARKER = "prepare.done"


def instruments_of(units: pa.Table) -> list[dict]:
    """One record per instrument of `units`, keyed by `clave` (#227's own
    key: a law's slug, an `id_ordenamiento` otherwise), in first-appearance
    order, carrying its `nombre` and the `text_sha1` of every unit row it has.

    The `text_sha1` list keeps repeats on purpose: a centroid is the mean over
    **unit rows**, so an instrument that repeats a transitorio is pulled
    toward that text as many times as it repeats it.
    """
    claves = units.column("clave").to_pylist()
    nombres = units.column("nombre").to_pylist()
    hashes = units.column("text_sha1").to_pylist()

    by_clave: dict[str, dict] = {}
    for clave, nombre, sha1 in zip(claves, nombres, hashes):
        record = by_clave.get(clave)
        if record is None:
            record = by_clave[clave] = {"clave": clave, "nombre": nombre, "text_sha1": []}
        record["text_sha1"].append(sha1)
    return list(by_clave.values())


def collection_matrix(coleccion: str, records: list[dict], model: str, *, cache_dir=None, log=print):
    """Every distinct vector of one collection, plus the row each
    `text_sha1` landed on.

    `legalvec.load_vectors` is called once per instrument — the published
    per-instrument file unioned with the collection's shared file — and the
    union is deduplicated here by `text_sha1`. Reading the shared file once
    per instrument is redundant work the measurement said is affordable (it
    is 11 MB for the largest collection, against ~600 per-instrument files),
    and it keeps this script on `legalvec`'s public reader rather than on a
    second copy of its union-and-dedup rule. A missing asset therefore
    surfaces as `legalvec.AssetNotCached`, never as a silently skipped
    instrument.
    """
    rows: list[np.ndarray] = []
    row_of: dict[str, int] = {}
    k = None
    for n, record in enumerate(records, start=1):
        vectors = legalvec.load_vectors(coleccion, record["clave"], model, cache_dir=cache_dir)
        k = vectors.k
        for i, sha1 in enumerate(vectors.text_sha1):
            if sha1 in row_of:
                continue
            row_of[sha1] = len(rows)
            # `.copy()` on purpose: a view would keep the whole `VectorSet`
            # matrix (this instrument's file *plus* the collection's shared
            # file, ~11 MB for reglamentos) alive for as long as any one of
            # its rows is kept, which measured at ~7 GB after 300
            # instruments and would have reached ~25 GB over the three
            # collections. Copying the row keeps the peak at the matrix
            # itself (~780 MB).
            rows.append(vectors.vectors[i].copy())
        if n % 200 == 0 or n == len(records):
            log(f"  {coleccion}: {n}/{len(records)} instruments, {len(rows)} distinct texts")
    matrix = (
        np.stack(rows).astype(np.float16)
        if rows
        else np.empty((0, k or 0), dtype=np.float16)
    )
    return matrix, row_of, k


def centroids_of(records: list[dict], matrix: np.ndarray, row_of: dict[str, int]) -> np.ndarray:
    """The mean of each instrument's unit rows' vectors, `float32`.

    `transform`ing this with a fitted UMAP model is what lands an instrument
    where its text mass is (issue #241's Decisions); the mean is taken in the
    embedding space, in `float32`, since `float16` sums lose precision fast
    over a few thousand rows.
    """
    means = np.empty((len(records), matrix.shape[1]), dtype=np.float32)
    for i, record in enumerate(records):
        indices = [row_of[sha1] for sha1 in record["text_sha1"]]
        means[i] = matrix[indices].astype(np.float32).mean(axis=0)
    return means


def prepare(
    work_dir: Path,
    *,
    model: str = DEFAULT_MODEL,
    collections: tuple[str, ...] = COLLECTIONS,
    cache_dir=None,
    force: bool = False,
    log=print,
) -> dict:
    """Write the whole input set, returning what `input.json` records."""
    work_dir = Path(work_dir)
    marker = work_dir / DONE_MARKER
    if marker.exists() and not force:
        log(f"{marker} exists -- nothing to do (pass --force to redo)")
        return json.loads((work_dir / "input.json").read_text(encoding="utf-8"))

    started = time.time()
    matrices: list[np.ndarray] = []
    centroid_rows: list[np.ndarray] = []
    ids_coleccion: list[str] = []
    ids_sha1: list[str] = []
    instruments: list[dict] = []
    per_collection: dict[str, dict] = {}
    k = None

    for coleccion in collections:
        units = legalvec.load_units(coleccion, cache_dir=cache_dir)
        records = instruments_of(units)
        log(f"{coleccion}: {units.num_rows} unit rows, {len(records)} instruments")
        matrix, row_of, k_collection = collection_matrix(
            coleccion, records, model, cache_dir=cache_dir, log=log
        )
        if k is not None and k_collection != k:
            raise SystemExit(
                f"{coleccion} was published at K={k_collection}, the previous "
                f"collection at K={k} -- refusing to stack mismatched vectors"
            )
        k = k_collection

        offset = sum(m.shape[0] for m in matrices)
        for sha1, row in sorted(row_of.items(), key=lambda item: item[1]):
            ids_coleccion.append(coleccion)
            ids_sha1.append(sha1)
        matrices.append(matrix)
        centroid_rows.append(centroids_of(records, matrix, row_of))
        for record in records:
            instruments.append({
                "i": len(instruments),
                "coleccion": coleccion,
                "clave": record["clave"],
                "nombre": record["nombre"],
                "units": len(record["text_sha1"]),
                "distinct_texts": len(set(record["text_sha1"])),
            })
        per_collection[coleccion] = {
            "unit_rows": units.num_rows,
            "instruments": len(records),
            "distinct_texts": matrix.shape[0],
            "first_row": offset,
        }
        log(f"{coleccion}: {matrix.shape[0]} vectors (rows {offset}..{offset + matrix.shape[0] - 1})")

    vectors = np.concatenate(matrices) if matrices else np.empty((0, k or 0), dtype=np.float16)
    centroid_input = (
        np.concatenate(centroid_rows)
        if centroid_rows
        else np.empty((0, k or 0), dtype=np.float32)
    )
    del matrices, centroid_rows

    atomic_write_npy(work_dir / "vectors.npy", vectors)
    atomic_write_table(work_dir / "vector_ids.parquet", pa.table({
        "row": pa.array(range(len(ids_sha1)), type=pa.int32()),
        "coleccion": pa.array(ids_coleccion, type=pa.string()),
        "text_sha1": pa.array(ids_sha1, type=pa.string()),
    }))
    atomic_write_npy(work_dir / "centroid_input.npy", centroid_input)
    atomic_write_table(work_dir / "instruments.parquet", pa.table({
        "i": pa.array([r["i"] for r in instruments], type=pa.int32()),
        "coleccion": pa.array([r["coleccion"] for r in instruments], type=pa.string()),
        "clave": pa.array([r["clave"] for r in instruments], type=pa.string()),
        "nombre": pa.array([r["nombre"] for r in instruments], type=pa.string()),
        "units": pa.array([r["units"] for r in instruments], type=pa.int32()),
        "distinct_texts": pa.array([r["distinct_texts"] for r in instruments], type=pa.int32()),
    }))

    summary = {
        "model": model,
        "model_slug": legalvec.model_slug(model),
        "k": k,
        "n": int(vectors.shape[0]),
        "instruments": len(instruments),
        "collections": per_collection,
        "legalvec_version": legalvec.__version__,
        "seconds": round(time.time() - started, 1),
    }
    atomic_write_text(work_dir / "input.json", json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    atomic_write_text(marker, "")
    log(json.dumps(summary, indent=2, ensure_ascii=False))
    return summary


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--work-dir", type=Path, default=Path("emb-run-umap"))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--cache-dir", type=Path, default=None,
                        help="legalvec cache to read (default: $LEGALVEC_CACHE_DIR)")
    parser.add_argument("--collections", default=",".join(COLLECTIONS),
                        help="comma-separated collections to stack, in order")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    prepare(
        args.work_dir,
        model=args.model,
        collections=tuple(c for c in args.collections.split(",") if c),
        cache_dir=args.cache_dir,
        force=args.force,
    )


if __name__ == "__main__":
    main()

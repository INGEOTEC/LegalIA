"""Fit one UMAP configuration on every vector of the three corpora.

Issue #241, the job body: one `sbatch` job per configuration, run on a
compute node against the single `vectors.npy` `prepare_umap_input.py` wrote.
With ~245 GB per node the fit is on **all** 381,349 vectors (the `float32`
matrix is ~1.6 GB), so there is no sample and no experiment mode: a
configuration that dies is reported, not quietly replaced by a smaller one.

    python project_umap.py --work-dir emb-run-umap --n-neighbors 15 --knn 15
    python project_umap.py --work-dir emb-run-umap --n-neighbors 200

Outputs, under `--work-dir/<name>/` (`name` defaults to `nn{n:03d}`):

* `coordinates.parquet` — `row`, `x`, `y`, min-max scaled to `[0, 1]` per
  axis so every configuration shares one plotting frame; the raw ranges are
  in `projection.json`.
* `centroids.parquet` — `i`, `x`, `y`: `transform` of `centroid_input.npy`
  through the **same** affine map, so an instrument sits in the same frame as
  its units (and may fall slightly outside `[0, 1]`, which is information,
  not an error).
* `projection.json` — parameters, versions, N, threads, node, Slurm job id,
  seconds per phase, peak RSS.
* `.done` — written last.

With `--knn 15` it also writes `--work-dir/neighbors.parquet` (at the work
directory's **root**, not inside the configuration): the neighbours live in
the 1,024-dimension embedding space, not in any 2-D projection, so they are
configuration-independent and every projection shares them. The launcher
passes `--knn` to the cheapest configuration only.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import resource
import time
from pathlib import Path

DEFAULT_MODEL = "Qwen/Qwen3-Embedding-0.6B"


def thread_count(requested: int | None = None) -> int:
    """How many threads this job may use: `--threads`, else Slurm's own
    `$SLURM_CPUS_ON_NODE`, else every core the machine has."""
    if requested:
        return requested
    from_slurm = os.environ.get("SLURM_CPUS_ON_NODE")
    if from_slurm:
        return int(from_slurm)
    return os.cpu_count() or 1


def set_thread_env(threads: int) -> None:
    """Pin every threading layer numba, BLAS and pynndescent read.

    This must run **before** numba or umap is imported: numba reads
    `NUMBA_NUM_THREADS` once, when its threading layer is initialised, and
    ignores a later change. That is why this module imports neither at the
    top level.
    """
    for name in ("NUMBA_NUM_THREADS", "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS",
                 "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[name] = str(threads)


def peak_rss_gb() -> float:
    """Peak resident set size of this process, in GB (`ru_maxrss` is KB on
    Linux) — the number that says whether a configuration fits in a node."""
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 ** 2), 2)


def scale_to_unit(coordinates):
    """Min-max scale each axis into `[0, 1]`, returning the coordinates and
    the raw `(min, max)` per axis.

    UMAP's raw units carry no meaning, and a fixed `[0, 1]` frame is what
    lets the HTML's radio switch projections without rescaling any axis. The
    raw range is recorded so the scaling stays invertible.
    """
    import numpy as np

    coordinates = np.asarray(coordinates, dtype=np.float32)
    low = coordinates.min(axis=0)
    high = coordinates.max(axis=0)
    span = np.where(high > low, high - low, 1.0)
    scaled = (coordinates - low) / span
    return scaled.astype(np.float32), [(float(low[i]), float(high[i])) for i in range(coordinates.shape[1])]


def apply_scale(coordinates, ranges):
    """The same affine map `scale_to_unit` derived, applied to other points
    (the instrument centroids)."""
    import numpy as np

    coordinates = np.asarray(coordinates, dtype=np.float32)
    low = np.array([r[0] for r in ranges], dtype=np.float32)
    high = np.array([r[1] for r in ranges], dtype=np.float32)
    span = np.where(high > low, high - low, 1.0)
    return ((coordinates - low) / span).astype(np.float32)


def nearest_neighbors(vectors, k: int):
    """`k` cosine nearest neighbours of every row, with the row itself
    removed.

    A separate `pynndescent` index rather than UMAP's private
    `_knn_indices`: the neighbours are wanted for every projection, and
    depending on a private attribute of a fitted model would tie them to one.
    `n_neighbors=k + 1` because the first hit of an exact-ish search is the
    point itself; a row where it is not (approximate search, duplicate
    distances) simply drops its last column instead.
    """
    import numpy as np
    import pynndescent

    index = pynndescent.NNDescent(vectors, metric="cosine", n_neighbors=k + 1, n_jobs=-1)
    indices, distances = index.neighbor_graph
    indices = np.asarray(indices)
    distances = np.asarray(distances)

    out_indices = np.empty((indices.shape[0], k), dtype=np.int32)
    out_distances = np.empty((indices.shape[0], k), dtype=np.float32)
    for row in range(indices.shape[0]):
        keep = [j for j in range(indices.shape[1]) if indices[row, j] != row][:k]
        while len(keep) < k:  # pathological: every hit was the row itself
            keep.append(keep[-1] if keep else 0)
        out_indices[row] = indices[row, keep]
        out_distances[row] = distances[row, keep]
    return out_indices, out_distances


def project(
    work_dir: Path,
    *,
    n_neighbors: int,
    min_dist: float = 0.1,
    metric: str = "cosine",
    name: str | None = None,
    knn: int = 0,
    threads: int | None = None,
    model: str = DEFAULT_MODEL,
    log=print,
) -> dict:
    """Fit one configuration and write its outputs. Returns
    `projection.json`'s own content."""
    import numpy as np
    import pyarrow as pa

    from _atomic import atomic_write_table, atomic_write_text

    work_dir = Path(work_dir)
    name = name or f"nn{n_neighbors:03d}"
    out_dir = work_dir / name
    out_dir.mkdir(parents=True, exist_ok=True)

    timings: dict[str, float] = {}
    started = time.time()
    vectors = np.load(work_dir / "vectors.npy").astype(np.float32)
    centroid_input = np.load(work_dir / "centroid_input.npy").astype(np.float32)
    timings["load"] = round(time.time() - started, 1)
    log(f"{name}: {vectors.shape[0]} vectors x {vectors.shape[1]} dims loaded "
        f"in {timings['load']}s")

    import umap

    reducer = umap.UMAP(
        n_components=2,
        metric=metric,
        n_neighbors=n_neighbors,
        min_dist=min_dist,
        n_jobs=-1,
        low_memory=False,
        verbose=True,
    )
    mark = time.time()
    embedding = reducer.fit_transform(vectors)
    timings["fit"] = round(time.time() - mark, 1)
    log(f"{name}: fit in {timings['fit']}s")

    scaled, ranges = scale_to_unit(embedding)

    mark = time.time()
    centroids = apply_scale(reducer.transform(centroid_input), ranges)
    timings["centroids"] = round(time.time() - mark, 1)
    log(f"{name}: {centroids.shape[0]} centroids transformed in {timings['centroids']}s")

    if knn:
        mark = time.time()
        indices, distances = nearest_neighbors(vectors, knn)
        timings["knn"] = round(time.time() - mark, 1)
        atomic_write_table(work_dir / "neighbors.parquet", pa.table({
            "row": pa.array(range(indices.shape[0]), type=pa.int32()),
            "neighbors": pa.array(indices.tolist(), type=pa.list_(pa.int32())),
            "distances": pa.array(distances.tolist(), type=pa.list_(pa.float32())),
        }))
        log(f"{name}: {knn} neighbours per row in {timings['knn']}s")

    mark = time.time()
    atomic_write_table(out_dir / "coordinates.parquet", pa.table({
        "row": pa.array(range(scaled.shape[0]), type=pa.int32()),
        "x": pa.array(scaled[:, 0], type=pa.float32()),
        "y": pa.array(scaled[:, 1], type=pa.float32()),
    }))
    atomic_write_table(out_dir / "centroids.parquet", pa.table({
        "i": pa.array(range(centroids.shape[0]), type=pa.int32()),
        "x": pa.array(centroids[:, 0], type=pa.float32()),
        "y": pa.array(centroids[:, 1], type=pa.float32()),
    }))
    timings["write"] = round(time.time() - mark, 1)

    summary = {
        "name": name,
        "n_neighbors": n_neighbors,
        "min_dist": min_dist,
        "metric": metric,
        "n_components": 2,
        "random_state": None,
        "model": model,
        "n": int(vectors.shape[0]),
        "k": int(vectors.shape[1]),
        "instruments": int(centroids.shape[0]),
        "knn": knn,
        "threads": thread_count(threads),
        "node": platform.node(),
        "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
        "umap_version": getattr(umap, "__version__", None),
        "seconds": timings,
        "seconds_total": round(time.time() - started, 1),
        "peak_rss_gb": peak_rss_gb(),
        "x_range": ranges[0],
        "y_range": ranges[1],
    }
    atomic_write_text(out_dir / "projection.json",
                      json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    atomic_write_text(out_dir / ".done", "")
    log(json.dumps(summary, indent=2, ensure_ascii=False))
    return summary


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--work-dir", type=Path, default=Path("emb-run-umap"))
    parser.add_argument("--n-neighbors", type=int, required=True)
    parser.add_argument("--min-dist", type=float, default=0.1)
    parser.add_argument("--metric", default="cosine")
    parser.add_argument("--name", default=None, help="output directory (default nnNNN)")
    parser.add_argument("--knn", type=int, default=0,
                        help="k cosine neighbours to write at the work-dir root (0 = none)")
    parser.add_argument("--threads", type=int, default=None)
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help="recorded in projection.json; the vectors themselves "
                             "come from prepare_umap_input.py")
    args = parser.parse_args(argv)

    threads = thread_count(args.threads)
    set_thread_env(threads)
    print(f"threads={threads}")

    project(
        args.work_dir,
        n_neighbors=args.n_neighbors,
        min_dist=args.min_dist,
        metric=args.metric,
        name=args.name,
        knn=args.knn,
        threads=threads,
        model=args.model,
    )


if __name__ == "__main__":
    main()

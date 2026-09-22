"""Count, per instrument, where its units' nearest *foreign* neighbours live.

Issue #242. Issue #241 drew the picture of 381,349 texts; this draws the
picture of the 1,523 instruments that own them, by asking one question of
every unit of every federal law, reglamento and lineamiento:

    which instrument owns the text closest to this one, among all the texts
    that are not exclusively mine?

The answer is a square weight matrix `A` (1,523 x 1,523, rows and columns in
`instruments.parquet`'s own `i` order): every unit row of instrument `I`
distributes a total weight of **1** over the `m` instruments owning a winning
text, `A[I, J] += 1/m` for each of them. The diagonal is zero by construction,
`A` is **not** symmetric (a reglamento pointing at its law says nothing about
the law pointing back), and `build_instrument_umap_html.py` is what turns it
into a page.

    python instrument_matrix.py --dry-run     # the sbatch command
    python instrument_matrix.py --submit      # one job on geoint1/geoint2
    python instrument_matrix.py --wait --max-wait-minutes 9   # one chunk
    python instrument_matrix.py --report      # the state, offline
    python instrument_matrix.py --work-dir emb-run-umap       # compute here

The rules, all of them decided in issue #242 and none of them defaults worth
changing quietly:

* **All six `unit_type`s count**, not only `article`: the question is about
  what makes up a document.
* **Ties count, every one of them.** The winners of a row are every column
  within `--tolerance` (1e-6 on float32 cosine) of its best, because identical
  texts across collections are *exact* ties and picking the lowest column
  index would silently prefer `leyes` to everything else.
* **`1/m` to each of the `m` instruments owning a winning text.** A text two
  instruments share is evidence about both, so both are credited — but a unit
  row is one article and weighs one, however many instruments answer for it.
  The first pass added +1 to each instead, and a single winning column can be
  owned by hundreds of instruments (the worst row: 847), because boilerplate
  ("Se deroga.", a standard transitorio) is one vector row shared across a
  whole collection; `A` then counted article-instrument incidences rather
  than articles. Row sums now equal the instrument's unit-row count exactly.
* **Per unit row, not per distinct text**: a boilerplate transitorio repeated
  `m` times inside a code is `m` articles, and counts `m` times — the same
  choice #241's centroids already made.
* **Only columns owned *exclusively* by the source instrument are masked.** A
  text `I` shares with `J` stays a candidate: the nearest foreign neighbour of
  such a unit is that very text, at similarity 1, which is the strongest
  relation there is and the last thing to hide.
* **Exact cosine, by blocked matrix products**, not `neighbors.parquet` and
  not an approximate index: k=15 neighbours of an article of a 3,600-article
  code are all inside that code, so the foreign neighbour is never in them.

Outputs, under `--work-dir/instrument-matrix/`, written atomically with
`.done` last:

* `matrix.npy` — `(1523, 1523)` `float32` (`float64` while accumulating), row
  `i` the source instrument.
* `nearest.parquet` — one row per **unit row** (408,804): `i`, `coleccion`,
  `clave`, `unit_type`, `eId`, `row` (vector row), `similarity`, `n_winners`
  (how many vector *rows* tied — a different number from `m`, since one row
  can have several owners and two tied rows can share one), `targets` (the
  instruments credited), `m` (how many of them) and `weight` (`1/m`).
* `matrix.json` — the weighting rule and the row-sum identity it implies,
  seconds per phase, the tie and `m` histograms, `shared_rows`, the
  tolerance, peak RSS, threads, host.
* `job.json`, `slurm-<jobid>.out` when it ran through Slurm.

`.done` makes a rerun a no-op; `--force` recomputes.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from project_umap import peak_rss_gb, set_thread_env, thread_count  # noqa: E402
from submit_umap import EXIT_STILL_RUNNING, queued_jobs  # noqa: E402

#: Where everything this script writes lives, inside #241's own work
#: directory: one subdirectory, so nothing #241 wrote is ever touched.
SUBDIR = "instrument-matrix"

#: Similarity slack that still counts as a tie. Identical texts give exactly
#: 1.0 in float32 and near-identical ones land within a few units of the last
#: bit; 1e-6 is far above float32's own noise at |cos| <= 1 and far below any
#: difference that could mean two texts are really different.
DEFAULT_TOLERANCE = 1e-6

#: How many of an instrument's rows are multiplied by the whole corpus at
#: once. `ccf` alone has 3,654 distinct texts, and `3654 x 381349` float32 is
#: 5.6 GB; at 1,024 rows a block is ~1.6 GB whatever the instrument.
DEFAULT_BLOCK_ROWS = 1024

#: The generic `exec "$PYTHON" "$@"` wrapper #241 wrote: it pins the
#: repository's own `.venv`, which is the one interpreter the login node and
#: both compute nodes agree on (shared `/home`, no GPU, no module system).
SUBMIT_SH = Path(__file__).with_name("submit_umap.sh")
THIS_SCRIPT = Path(__file__)

#: The login node is saturated by another user's work outside Slurm, so the
#: job never lands there — the same default `submit_umap.py` carries.
DEFAULT_EXCLUDE = "geoint0"

#: The sweep is expected to take 5-30 minutes on one node. Two hours is the
#: ceiling at which it is a finding rather than a slow job, and there is no
#: retry: issue #242 keeps #241's rule that a job which dies is reported.
TIME_LIMIT = "2:00:00"

JOB_JSON = "job.json"


def output_dir(work_dir: Path) -> Path:
    return Path(work_dir) / SUBDIR


def unit_rows(work_dir: Path, *, collections=None, cache_dir=None, log=print):
    """Every unit row of the three corpora, with the vector row and the
    instrument it belongs to.

    This is `build_umap_html.load_frames`' own join — `units.parquet` ->
    `vector_ids.parquet` on `(coleccion, text_sha1)` -> `instruments.parquet`
    on `(coleccion, clave)` — called with no projection, rather than a second
    copy of it: the two scripts must never disagree about which vector row a
    unit got. Its compact codes are decoded back here (`c` -> `coleccion`,
    `u` -> `unit_type`) and `clave`/`nombre` come from `instruments.parquet`,
    which the join already restricts to.
    """
    import pandas as pd
    import pyarrow.parquet as pq

    import build_umap_html as html

    if collections is None:
        collections = tuple(html.COLLECTION_CODES)
    points, _ = html.load_frames(work_dir, [], neighbors=0, collections=collections,
                                 cache_dir=cache_dir, log=log)
    instruments = pq.read_table(Path(work_dir) / "instruments.parquet").to_pandas()
    collections = {code: name for name, code in html.COLLECTION_CODES.items()}
    units = pd.DataFrame({
        "i": points["i"].astype("int32"),
        "coleccion": points["c"].map(collections),
        "unit_type": points["u"].map(dict(enumerate(html.UNIT_TYPES))),
        "eId": points["e"],
        "row": points["v"].astype("int32"),
    })
    units = units.merge(instruments[["i", "clave"]], on="i", how="left")
    return units[["i", "coleccion", "clave", "unit_type", "eId", "row"]]


def owners_of_rows(units, vector_rows: int) -> list[list[int]]:
    """Per vector row, the instruments that own a unit carrying that text.

    Usually one. Several when a text repeats across instruments *inside* a
    collection (a boilerplate transitorio, "Se deroga."), which is exactly the
    case the masking rule below has to keep: such a column is the strongest
    foreign neighbour its co-owner can have.
    """
    owners: list[list[int]] = [[] for _ in range(vector_rows)]
    pairs = units[["row", "i"]].drop_duplicates()
    for row, i in zip(pairs["row"].to_numpy(), pairs["i"].to_numpy()):
        owners[int(row)].append(int(i))
    return owners


def _winner_histogram(counts) -> dict:
    """`n_winners` bucketed the way `matrix.json` reports it."""
    import numpy as np

    counts = np.asarray(counts)
    return {
        "1": int((counts == 1).sum()),
        "2": int((counts == 2).sum()),
        "3-5": int(((counts >= 3) & (counts <= 5)).sum()),
        "6+": int((counts >= 6).sum()),
    }


def _m_histogram(counts) -> dict:
    """`m` bucketed the way `matrix.json` reports it.

    Wider buckets than `_winner_histogram`'s: `m` is what the `1/m` rule
    divides by, and the boilerplate rows this pass is about sit in the last
    two buckets (the worst row of the first run had `m = 847`), where
    `n_winners` never leaves the first.
    """
    import numpy as np

    counts = np.asarray(counts)
    return {
        "1": int((counts == 1).sum()),
        "2": int((counts == 2).sum()),
        "3-5": int(((counts >= 3) & (counts <= 5)).sum()),
        "6-20": int(((counts >= 6) & (counts <= 20)).sum()),
        "21-100": int(((counts >= 21) & (counts <= 100)).sum()),
        "101+": int((counts >= 101).sum()),
    }


def build_matrix(work_dir: Path, *, tolerance: float = DEFAULT_TOLERANCE,
                 block_rows: int = DEFAULT_BLOCK_ROWS, threads: int | None = None,
                 collections=None, cache_dir=None, log=print) -> dict:
    """Compute `A`, the per-unit-row table and `matrix.json`. Returns the
    summary it wrote."""
    import numpy as np
    import pandas as pd
    import pyarrow as pa

    from _atomic import atomic_write_npy, atomic_write_table, atomic_write_text

    work_dir = Path(work_dir)
    out_dir = output_dir(work_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    timings: dict[str, float] = {}
    started = time.time()
    units = unit_rows(work_dir, collections=collections, cache_dir=cache_dir, log=log)
    vectors = np.load(work_dir / "vectors.npy").astype(np.float32)
    timings["load"] = round(time.time() - started, 1)
    log(f"{len(units)} unit rows over {vectors.shape[0]} vectors "
        f"in {timings['load']}s")

    mark = time.time()
    # The published vectors are not normalised (their norms run 92 to 121), so
    # a dot product is not a cosine until this happens. Once, in place.
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    np.divide(vectors, np.where(norms > 0, norms, 1.0), out=vectors)
    timings["normalise"] = round(time.time() - mark, 1)

    n_vectors = vectors.shape[0]
    owners = owners_of_rows(units, n_vectors)
    shared_rows = sum(1 for group in owners if len(group) > 1)
    instruments = int(units["i"].max()) + 1
    # `float64` while accumulating: 408,804 additions of fractions as small as
    # 1/847, summed to an identity the summary asserts at 1e-3. It is written
    # as `float32`, which halves a 9 MB file and verifies at that tolerance.
    matrix = np.zeros((instruments, instruments), dtype=np.float64)

    # One result per (instrument, vector row): every unit row of `I` carrying
    # the same text has the same answer, and multiplying it back afterwards is
    # what makes "a text repeated m times counts m times" a merge rather than
    # m matrix products.
    pairs = units[["i", "row"]].drop_duplicates().sort_values(["i", "row"])
    by_instrument = {i: group["row"].to_numpy() for i, group in pairs.groupby("i")}

    mark = time.time()
    result_i, result_row, result_sim, result_winners, result_targets = [], [], [], [], []
    for position, (i, rows) in enumerate(sorted(by_instrument.items())):
        # Mask what this instrument owns alone: a column no one else owns can
        # only ever be its own text, and "nearest foreign neighbour" is not
        # about those. A column it shares stays, deliberately.
        exclusive = np.array([c for c in rows if len(owners[c]) == 1], dtype=np.int64)
        for start in range(0, len(rows), block_rows):
            block = rows[start:start + block_rows]
            similarity = vectors[block] @ vectors.T
            if exclusive.size:
                similarity[:, exclusive] = -np.inf
            best = similarity.max(axis=1)
            for offset, row in enumerate(block):
                winners = np.flatnonzero(similarity[offset] >= best[offset] - tolerance)
                targets = sorted({j for c in winners.tolist() for j in owners[c]} - {i})
                if not targets:
                    # 1,522 candidate instruments are never all masked, so an
                    # empty target set is a broken join or a broken mask, not a
                    # case with a sensible weight -- and `1/m` would divide by 0.
                    raise SystemExit(
                        f"instrument {i}, vector row {int(row)}: no foreign "
                        "target at all, which cannot happen (only the source's "
                        "exclusively owned columns are masked)")
                result_i.append(i)
                result_row.append(int(row))
                result_sim.append(float(best[offset]))
                result_winners.append(int(winners.size))
                result_targets.append(targets)
            del similarity
        if position % 50 == 0:
            log(f"{position + 1}/{len(by_instrument)} instruments, "
                f"{time.time() - mark:.0f}s elapsed")
    timings["sweep"] = round(time.time() - mark, 1)
    log(f"sweep in {timings['sweep']}s")

    # `m` counts *instruments*, not tied vector rows: an instrument owning two
    # tied winners is one target (`targets` is a set), so no instrument is
    # favoured for repeating a text.
    result_m = np.array([len(targets) for targets in result_targets], dtype="int32")
    answers = pd.DataFrame({
        "i": np.array(result_i, dtype="int32"),
        "row": np.array(result_row, dtype="int32"),
        "similarity": np.array(result_sim, dtype="float32"),
        "n_winners": np.array(result_winners, dtype="int32"),
        "targets": result_targets,
        "m": result_m,
        "weight": (1.0 / result_m).astype("float32"),
    })
    nearest = units.merge(answers, on=["i", "row"], how="left")
    if len(nearest) != len(units):
        raise SystemExit("the per-(instrument, text) answers did not join back "
                         f"one-to-one: {len(nearest)} rows against {len(units)}")

    # One unit row, one unit of weight: `1/m` to each of the `m` instruments
    # owning a winning text. The division is redone here in `float64` rather
    # than read from the `float32` column, so the row sums land on the unit
    # count to far better than the 1e-3 the summary checks them at. Done from
    # the joined table rather than from `answers`, so a text repeated m times
    # inside one instrument really does count m times.
    for source, targets in zip(nearest["i"].to_numpy(), nearest["targets"]):
        weight = 1.0 / len(targets)
        for target in targets:
            matrix[source, target] += weight
    matrix = matrix.astype(np.float32)

    # The row-sum identity is the whole point of the `1/m` rule: every unit row
    # weighs 1, so an instrument's row sums to how many unit rows it has.
    units_per_instrument = (nearest.groupby("i").size()
                            .reindex(range(instruments), fill_value=0).to_numpy())
    row_sums_equal_units = bool(np.allclose(matrix.sum(axis=1), units_per_instrument,
                                            atol=1e-3))

    mark = time.time()
    atomic_write_npy(out_dir / "matrix.npy", matrix)
    atomic_write_table(out_dir / "nearest.parquet", pa.table({
        "i": pa.array(nearest["i"].to_numpy(), type=pa.int32()),
        "coleccion": pa.array(nearest["coleccion"].astype(str)),
        "clave": pa.array(nearest["clave"].astype(str)),
        "unit_type": pa.array(nearest["unit_type"].astype(str)),
        "eId": pa.array(nearest["eId"].astype(str)),
        "row": pa.array(nearest["row"].to_numpy(), type=pa.int32()),
        "similarity": pa.array(nearest["similarity"].to_numpy(), type=pa.float32()),
        "n_winners": pa.array(nearest["n_winners"].to_numpy(), type=pa.int32()),
        "targets": pa.array(list(nearest["targets"]), type=pa.list_(pa.int32())),
        "m": pa.array(nearest["m"].to_numpy(), type=pa.int32()),
        "weight": pa.array(nearest["weight"].to_numpy(), type=pa.float32()),
    }))
    timings["write"] = round(time.time() - mark, 1)

    summary = {
        "unit_rows": int(len(nearest)),
        "vector_rows": int(n_vectors),
        "instruments": instruments,
        "weighting": "1/m",
        "matrix_dtype": str(matrix.dtype),
        "matrix_sum": round(float(matrix.sum()), 3),
        "row_sums_equal_units": row_sums_equal_units,
        "nonzero_cells": int((matrix > 0).sum()),
        "tie_rows": int((nearest["n_winners"] > 1).sum()),
        "n_winners": _winner_histogram(nearest["n_winners"].to_numpy()),
        "max_m": int(nearest["m"].max()),
        "m": _m_histogram(nearest["m"].to_numpy()),
        "shared_rows": shared_rows,
        "tolerance": tolerance,
        "block_rows": block_rows,
        "threads": thread_count(threads),
        "node": platform.node(),
        "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
        "seconds": timings,
        "seconds_total": round(time.time() - started, 1),
        "peak_rss_gb": peak_rss_gb(),
    }
    atomic_write_text(out_dir / "matrix.json",
                      json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    atomic_write_text(out_dir / ".done", "")
    log(json.dumps(summary, indent=2, ensure_ascii=False))
    return summary


def sbatch_command(work_dir: Path, *, exclude: str = DEFAULT_EXCLUDE,
                   tolerance: float = DEFAULT_TOLERANCE,
                   block_rows: int = DEFAULT_BLOCK_ROWS,
                   force: bool = False) -> list[str]:
    """The one `sbatch` line this issue submits. Absolute paths throughout:
    Slurm copies the wrapper into its own spool directory and chdirs, so a
    relative `--work-dir` would point at nothing.

    `--force` is forwarded into the job: the `.done` the previous run left is
    checked inside the job, not here, so recomputing a directory that already
    has one (issue #242's second pass rewrites the whole matrix) needs the flag
    on the other side of `sbatch` too.
    """
    work_dir = Path(work_dir).resolve()
    cmd = ["sbatch", "--parsable", "--job-name=instrument-matrix",
           f"--time={TIME_LIMIT}",
           f"--output={output_dir(work_dir) / 'slurm-%j.out'}"]
    if exclude:
        cmd.append(f"--exclude={exclude}")
    cmd += [str(SUBMIT_SH), str(THIS_SCRIPT),
            "--work-dir", str(work_dir),
            "--tolerance", repr(tolerance),
            "--block-rows", str(block_rows)]
    if force:
        cmd.append("--force")
    return cmd


def submit(work_dir: Path, *, exclude: str = DEFAULT_EXCLUDE,
           tolerance: float = DEFAULT_TOLERANCE, block_rows: int = DEFAULT_BLOCK_ROWS,
           force: bool = False, dry_run: bool = False, log=print) -> dict:
    """Submit the sweep as one Slurm job and record `job.json`."""
    from _atomic import atomic_write_text

    work_dir = Path(work_dir).resolve()
    if not (work_dir / "prepare.done").exists():
        raise SystemExit(f"{work_dir / 'prepare.done'} is missing -- this issue "
                         "reads what prepare_umap_input.py wrote (issue #241)")
    out_dir = output_dir(work_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    done = out_dir / ".done"
    if force and done.exists() and not dry_run:
        # The marker of the *previous* run has to go before the new job starts,
        # or `--wait` reads it and reports a job that has barely been queued as
        # finished. The job writes its own once it has rewritten everything.
        done.unlink()
        log(f"{done} removed: --force means the finished run is being replaced")

    cmd = sbatch_command(work_dir, exclude=exclude, tolerance=tolerance,
                         block_rows=block_rows, force=force)
    log(" ".join(cmd))
    if dry_run:
        return {"dry_run": True, "command": cmd}

    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    job_id = result.stdout.strip()
    log(f"instrument-matrix: job {job_id}")
    state = {"job_id": job_id, "submitted_at": time.time(),
             "argv": " ".join(cmd), "exclude": exclude}
    atomic_write_text(out_dir / JOB_JSON,
                      json.dumps(state, indent=2, ensure_ascii=False) + "\n")
    return state


def load_job(work_dir: Path) -> dict:
    path = output_dir(work_dir) / JOB_JSON
    if not path.exists():
        raise SystemExit(f"{path} is missing -- run instrument_matrix.py --submit first")
    return json.loads(path.read_text(encoding="utf-8"))


def slurm_tail(work_dir: Path, lines: int = 20, log=print) -> None:
    """The tail of whatever Slurm wrote — the only thing that says *why* a
    job left the queue without finishing."""
    for out in sorted(output_dir(work_dir).glob("slurm-*.out")):
        text = out.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
        log(f"--- tail of {out} ---")
        for line in text:
            log(line)


def wait(work_dir: Path, *, poll: int = 60, max_wait_minutes: float = 0.0,
         sleep=time.sleep, log=print) -> str:
    """Poll `squeue` until the job leaves it.

    Returns `"finished"` (`.done` is there), `"still running"` (this chunk's
    `--max-wait-minutes` elapsed — the caller simply calls again, since the
    state lives in `job.json` rather than in a process that has to stay
    alive) or `"failed"` (the job left the queue and wrote no `.done`).
    """
    work_dir = Path(work_dir)
    done = output_dir(work_dir) / ".done"
    state = load_job(work_dir)
    job_id = str(state["job_id"])
    chunk_deadline = time.time() + max_wait_minutes * 60 if max_wait_minutes else None

    while True:
        if done.exists():
            log(f"instrument-matrix: {done} is there")
            return "finished"
        remaining = queued_jobs([job_id])
        elapsed = (time.time() - state["submitted_at"]) / 60
        if not remaining:
            # One more look: a job can leave the queue microseconds before its
            # own `.done` rename lands on a shared filesystem.
            if done.exists():
                return "finished"
            log(f"job {job_id} left the queue after {elapsed:.1f} min without a .done")
            slurm_tail(work_dir, log=log)
            return "failed"
        log(f"job {job_id} still queued or running after {elapsed:.1f} min")
        if chunk_deadline is not None and time.time() > chunk_deadline:
            return "still running"
        sleep(poll)


def report(work_dir: Path, log=print) -> dict:
    """What is on disk, offline: no Slurm, no network."""
    work_dir = Path(work_dir)
    out_dir = output_dir(work_dir)
    done = (out_dir / ".done").exists()
    summary = {}
    if (out_dir / "matrix.json").exists():
        summary = json.loads((out_dir / "matrix.json").read_text(encoding="utf-8"))
    log(f"instrument-matrix: {'done' if done else 'not finished'} ({out_dir})")
    for name in ("matrix.npy", "nearest.parquet", "matrix.json", "umap.parquet"):
        path = out_dir / name
        log(f"  {name}: {'present' if path.exists() else 'MISSING'}"
            + (f" ({path.stat().st_size / 1e6:.1f} MB)" if path.exists() else ""))
    if summary:
        log(f"  {summary['unit_rows']} unit rows, sum {summary['matrix_sum']}, "
            f"{summary['tie_rows']} tied, {summary['seconds_total']}s, "
            f"{summary['peak_rss_gb']} GB peak, node {summary['node']}")
    if not done:
        slurm_tail(work_dir, log=log)
    return {"done": done, "summary": summary}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--work-dir", type=Path, default=Path("emb-run-umap"))
    parser.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE,
                        help="similarity slack that still counts as a tie")
    parser.add_argument("--block-rows", type=int, default=DEFAULT_BLOCK_ROWS,
                        help="rows of one instrument multiplied by the corpus at once")
    parser.add_argument("--threads", type=int, default=None)
    parser.add_argument("--cache-dir", type=Path, default=None)
    parser.add_argument("--exclude", default=DEFAULT_EXCLUDE,
                        help="nodes sbatch must not use (default: the saturated login node)")
    parser.add_argument("--poll", type=int, default=60)
    parser.add_argument("--max-wait-minutes", type=float, default=0.0,
                        help="bound one wait chunk: return exit status 75 after this "
                             "many minutes if the job is still there")
    parser.add_argument("--submit", action="store_true",
                        help="submit the sweep to Slurm instead of computing it here")
    parser.add_argument("--wait", action="store_true",
                        help="poll squeue for the submitted job")
    parser.add_argument("--report", action="store_true",
                        help="print what is on disk and exit (no Slurm)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the sbatch command without submitting it")
    parser.add_argument("--force", action="store_true",
                        help="recompute even if .done is already there; --submit "
                             "forwards it into the job")
    args = parser.parse_args(argv)

    if args.report:
        return 0 if report(args.work_dir)["done"] else 1

    if args.dry_run or args.submit:
        submit(args.work_dir, exclude=args.exclude, tolerance=args.tolerance,
               block_rows=args.block_rows, force=args.force, dry_run=args.dry_run)
        if not args.wait:
            return 0

    if args.wait:
        outcome = wait(args.work_dir, poll=args.poll,
                       max_wait_minutes=args.max_wait_minutes)
        if outcome == "still running":
            return EXIT_STILL_RUNNING
        if outcome == "failed":
            return 1
        report(args.work_dir)
        return 0

    done = output_dir(args.work_dir) / ".done"
    if done.exists() and not args.force:
        print(f"{done} is there -- nothing to do (pass --force to recompute)")
        return 0

    threads = thread_count(args.threads)
    set_thread_env(threads)
    print(f"threads={threads}")
    build_matrix(args.work_dir, tolerance=args.tolerance, block_rows=args.block_rows,
                 threads=threads, cache_dir=args.cache_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())

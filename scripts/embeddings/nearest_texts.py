"""The five closest *foreign texts* of every searched excerpt of every instrument.

Issue #288. `instrument_matrix.py` (issue #242) asks of every searched unit row
"which instrument owns the text closest to this one, among the texts that are
not exclusively mine?" and keeps only the winner and its exact ties. The
Instruments page shows, on every searched excerpt, the **five closest texts of
other instruments** and lets the reader navigate to them, so this script
repeats the very same sweep and keeps the top `TOP_TEXTS` vector rows instead
of the best one.

    python nearest_texts.py --work-dir emb-run-atlas-4b --dry-run
    python nearest_texts.py --work-dir emb-run-atlas-4b --submit
    python nearest_texts.py --work-dir emb-run-atlas-4b --wait --max-wait-minutes 9
    python nearest_texts.py --work-dir emb-run-atlas-4b --report
    python nearest_texts.py --work-dir emb-run-atlas-4b          # compute here

It builds on a finished matrix: `--work-dir/prepare.done` and
`instrument-matrix/.done` (with its `nearest.parquet`) must exist.

The rules are the matrix's, unchanged (issues #242, #259, #264) and imported
rather than restated in code:

* **Sources** are the *searched* unit rows (`instrument_matrix.searched_mask`):
  no heading and nothing inside a transitorios section. Sources are keyed by
  `(i, row)`, an instrument and a vector row: the same text repeated inside one
  instrument has one answer, and the exporter fans it back out to its excerpts.
* **Candidates** are every vector row except the source instrument's
  *exclusively* owned rows (`own_rows_of` + `owners_of_rows`, one owner) and the
  rows nobody owns among the searched rows (a text only headings and
  transitorios carry). A text the source shares with another instrument stays a
  candidate, at similarity 1 when it is the same text.
* **Exact scores**, by blocked products through `scoring.scorer_for(work_dir)`
  (cosine over `vectors.npy` here, BM25 for a BM25 work directory), never
  `neighbors.parquet`.
* **Top K** per source: `np.argpartition` to the K best of a block's rows, then
  sorted by `(-similarity, target_row)`, so a tie at the K-th place is cut
  deterministically by the lower vector row. A tie at rank 1 is **not**
  collapsed: two exact ties are ranks 1 and 2. A candidate that was masked or,
  for a scorer with `may_have_no_match`, scored 0 is never written, so a source
  can have fewer than K rows.
* **Rank 1 is the Atlas' winner, and the script proves it**: before `.done`, the
  rank-1 similarity of every source equals `nearest.parquet`'s within
  `--tolerance` and its `targets` are among the matrix's. Any disagreement is a
  `SystemExit` naming the first `(i, row)`.

Outputs, under `--work-dir/nearest-texts/` (its own directory: the matrix's
`.done` means "the matrix is there"), written atomically with `.done` last:

* `nearest-texts.parquet` — one row per (source, rank), sorted by
  `(i, row, rank)`: `i`, `coleccion`, `clave`, `row` (the source's vector row),
  `rank` (1..K), `target_row`, `similarity`, `targets` (the instruments owning
  `target_row`, minus `i`, sorted).
* `summary.json` — `top`, `sources` (the searched unit rows, as in
  `nearest.parquet`), `source_texts` (distinct `(i, row)`, what the table is
  keyed by), `rows_written`, `sources_short_of_top`, `agrees_with_nearest`, the
  tolerance, the scorer's `describe()`, block size, threads, node, Slurm job,
  seconds per phase and peak RSS.
* `job.json`, `slurm-<jobid>.out` when it ran through Slurm.

`.done` makes a rerun a no-op; `--force` recomputes.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import instrument_matrix  # noqa: E402
import scoring  # noqa: E402
from project_umap import peak_rss_gb, set_thread_env, thread_count  # noqa: E402
from submit_umap import EXIT_STILL_RUNNING  # noqa: E402

#: How many foreign texts each searched source keeps — "to be consistent with
#: the Atlas", which shows five of everything. The exporter imports it.
TOP_TEXTS = 5

SUBDIR = "nearest-texts"
THIS_SCRIPT = Path(__file__)
TABLE = "nearest-texts.parquet"
SUMMARY = "summary.json"
JOB_NAME = "nearest-texts"

#: What the matrix must have written before this script reads it.
REQUIRES = ("prepare.done", "instrument-matrix/.done", "instrument-matrix/nearest.parquet")


def output_dir(work_dir: Path) -> Path:
    return instrument_matrix.output_dir(work_dir, SUBDIR)


def check_prerequisites(work_dir: Path) -> None:
    """A `SystemExit` naming the first thing the matrix should have written."""
    work_dir = Path(work_dir)
    for name in REQUIRES:
        if not (work_dir / name).exists():
            raise SystemExit(f"{work_dir / name} is missing -- nearest_texts.py builds on "
                             "prepare_umap_input.py and instrument_matrix.py")


def top_of_block(similarity, top: int):
    """The `top` best columns of every row of a `(rows, columns)` score block,
    as `(columns, values)` arrays of shape `(rows, min(top, columns))`, each row
    sorted by `(-value, column)`.

    `argpartition` picks *a* set of the best `top`; when several columns tie at
    the K-th value the choice among them would be arbitrary, so the rows where
    more than `top` columns reach the K-th value are redone from every column at
    or above it and cut by the lower column index.
    """
    import numpy as np

    n_columns = similarity.shape[1]
    top = min(top, n_columns)
    picked = np.argpartition(similarity, n_columns - top, axis=1)[:, n_columns - top:]
    values = np.take_along_axis(similarity, picked, axis=1)
    cut = values.min(axis=1)
    over = np.flatnonzero((similarity >= cut[:, None]).sum(axis=1) > top)
    for position in over:
        row = similarity[position]
        candidates = np.flatnonzero(row >= cut[position])
        order = np.lexsort((candidates, -row[candidates]))[:top]
        picked[position] = candidates[order]
        values[position] = row[candidates[order]]
    by_column = np.argsort(picked, axis=1, kind="stable")
    picked = np.take_along_axis(picked, by_column, axis=1)
    values = np.take_along_axis(values, by_column, axis=1)
    by_value = np.argsort(-values, axis=1, kind="stable")
    return (np.take_along_axis(picked, by_value, axis=1),
            np.take_along_axis(values, by_value, axis=1))


def sweep_top(scorer, owners, unowned, pairs, own_rows, *, top: int = TOP_TEXTS,
              block_rows: int = instrument_matrix.DEFAULT_BLOCK_ROWS, log=print):
    """For each pair of `pairs` (a frame with columns `i` and `row`, one per
    distinct source), its `top` best foreign vector rows.

    Returns `(i, row, rank, target_row, similarity)` as parallel `numpy` arrays
    in `(i, row, rank)` order. The mask is `instrument_matrix.sweep_answers`':
    what the source instrument owns alone, and the rows nobody owns.
    """
    import numpy as np

    pairs = pairs.drop_duplicates().sort_values(["i", "row"])
    by_instrument = {i: group["row"].to_numpy() for i, group in pairs.groupby("i")}
    chunks = {"i": [], "row": [], "rank": [], "target": [], "similarity": []}
    mark = time.time()
    for position, (i, rows) in enumerate(sorted(by_instrument.items())):
        exclusive = np.array([c for c in own_rows[i] if len(owners[c]) == 1], dtype=np.int64)
        masked = np.concatenate([exclusive, unowned])
        for start in range(0, len(rows), block_rows):
            block = rows[start:start + block_rows]
            similarity = scorer.scores(block)
            if masked.size:
                similarity[:, masked] = -np.inf
            columns, values = top_of_block(similarity, top)
            del similarity
            # A masked column (-inf), and for a lexical scorer a zero score, is
            # no neighbour at all: never written.
            keep = np.isfinite(values)
            if scorer.may_have_no_match:
                keep &= values > 0
            sources, ranks = np.nonzero(keep)
            chunks["i"].append(np.full(len(sources), i, dtype=np.int32))
            chunks["row"].append(block[sources].astype(np.int32))
            chunks["rank"].append((ranks + 1).astype(np.int32))
            chunks["target"].append(columns[sources, ranks].astype(np.int32))
            chunks["similarity"].append(values[sources, ranks].astype(np.float32))
        if position % 50 == 0:
            log(f"{position + 1}/{len(by_instrument)} instruments, "
                f"{time.time() - mark:.0f}s elapsed")
    empty = {"i": np.int32, "row": np.int32, "rank": np.int32,
             "target": np.int32, "similarity": np.float32}
    return tuple(np.concatenate(chunks[key]) if chunks[key] else np.array([], dtype=empty[key])
                 for key in ("i", "row", "rank", "target", "similarity"))


def check_agreement(table, nearest, *, tolerance: float, relative: bool) -> None:
    """Rank 1 of every source against `nearest.parquet`, or a `SystemExit`
    naming the first `(i, row)` that disagrees.

    `table` is this script's own frame, `nearest` the matrix's (one row per unit
    row, so a repeated text appears several times with the same answer). A
    source with `n_winners > 0` must have a rank-1 row whose similarity is the
    matrix's within `tolerance` and whose `targets` are among the matrix's; a
    source with `n_winners == 0` (a lexical scorer's no-match) must have none;
    and no source here may be missing from `nearest.parquet`.
    """
    import numpy as np

    first = table[table["rank"] == 1][["i", "row", "similarity", "targets"]]
    recorded = (nearest[["i", "row", "similarity", "n_winners", "targets"]]
                .drop_duplicates(["i", "row"]))
    joined = recorded.merge(first, on=["i", "row"], how="outer", suffixes=("", "_got"),
                            indicator=True).sort_values(["i", "row"])

    def fail(row, message):
        raise SystemExit(f"instrument {int(row['i'])}, vector row {int(row['row'])}: {message}")

    extra = joined[joined["_merge"] == "right_only"]
    if len(extra):
        fail(extra.iloc[0], "a source here that nearest.parquet does not have")
    winnerless = (joined["_merge"] == "left_only") & (joined["n_winners"] > 0)
    if winnerless.any():
        fail(joined[winnerless].iloc[0],
             "no rank-1 text although the matrix recorded a winner")
    spurious = (joined["_merge"] == "both") & (joined["n_winners"] == 0)
    if spurious.any():
        fail(joined[spurious].iloc[0],
             "the matrix found no winner but this sweep wrote a rank-1 text")

    both = joined[joined["_merge"] == "both"]
    got = both["similarity_got"].to_numpy(dtype=np.float64)
    want = both["similarity"].to_numpy(dtype=np.float64)
    if relative:
        bad = np.abs(got - want) > tolerance * np.maximum(np.abs(got), np.abs(want))
    else:
        bad = np.abs(got - want) > tolerance
    if bad.any():
        row = both[bad].iloc[0]
        fail(row, f"rank-1 similarity {float(row['similarity_got'])!r} against the "
                  f"matrix's {float(row['similarity'])!r} (tolerance {tolerance})")
    for position, (mine, theirs) in enumerate(zip(both["targets_got"].to_numpy(),
                                                  both["targets"].to_numpy())):
        if not set(map(int, mine)) <= set(map(int, theirs)):
            fail(both.iloc[position],
                 f"rank-1 targets {sorted(map(int, mine))} are not among the matrix's "
                 f"{sorted(map(int, theirs))}")


def build_nearest_texts(work_dir: Path, *, top: int = TOP_TEXTS,
                        tolerance: float = instrument_matrix.DEFAULT_TOLERANCE,
                        block_rows: int = instrument_matrix.DEFAULT_BLOCK_ROWS,
                        threads: int | None = None, collections=None, cache_dir=None,
                        log=print) -> dict:
    """Sweep, check against the matrix, write the table and `summary.json`.
    Returns the summary."""
    import numpy as np
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq

    from _atomic import atomic_write_table, atomic_write_text

    work_dir = Path(work_dir)
    check_prerequisites(work_dir)
    out_dir = output_dir(work_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    timings: dict[str, float] = {}
    started = time.time()
    all_units = instrument_matrix.unit_rows(work_dir, collections=collections,
                                            cache_dir=cache_dir, log=log)
    units = all_units[instrument_matrix.searched_mask(all_units)].reset_index(drop=True)
    scorer = scoring.scorer_for(work_dir)
    n_vectors = scorer.n_rows
    timings["load"] = round(time.time() - started, 1)
    timings.update({key: value for key, value in scorer.timings.items() if key != "load"})
    log(f"{len(units)} searched unit rows of {len(all_units)} over {n_vectors} vectors "
        f"({scorer.method}) in {timings['load']}s")

    owners = instrument_matrix.owners_of_rows(units, n_vectors)
    unowned = np.array([c for c, group in enumerate(owners) if not group], dtype=np.int64)
    own_rows = instrument_matrix.own_rows_of(units)
    pairs = units[["i", "row"]].drop_duplicates()

    mark = time.time()
    sources_i, sources_row, ranks, targets_row, similarity = sweep_top(
        scorer, owners, unowned, pairs, own_rows, top=top, block_rows=block_rows, log=log)
    timings["sweep"] = round(time.time() - mark, 1)
    log(f"sweep in {timings['sweep']}s")

    # `i` -> (coleccion, clave) from the join, so the table is readable alone.
    names = units[["i", "coleccion", "clave"]].drop_duplicates("i").set_index("i")
    table = pd.DataFrame({
        "i": sources_i, "row": sources_row, "rank": ranks,
        "target_row": targets_row, "similarity": similarity,
    })
    # Rows already come in `(i, row)` order; the rank order inside a source is
    # the one `top_of_block` sorted, but a stable sort makes it a guarantee.
    table = table.sort_values(["i", "row", "rank"], kind="stable").reset_index(drop=True)
    table["targets"] = [sorted(set(owners[int(c)]) - {int(i)})
                        for c, i in zip(table["target_row"].to_numpy(), table["i"].to_numpy())]

    mark = time.time()
    nearest = pq.read_table(instrument_matrix.output_dir(work_dir) / "nearest.parquet").to_pandas()
    check_agreement(table, nearest, tolerance=tolerance, relative=scorer.relative)
    timings["check"] = round(time.time() - mark, 1)

    mark = time.time()
    table["coleccion"] = names.loc[table["i"], "coleccion"].to_numpy()
    table["clave"] = names.loc[table["i"], "clave"].to_numpy()
    atomic_write_table(out_dir / TABLE, pa.table({
        "i": pa.array(table["i"].to_numpy(), type=pa.int32()),
        "coleccion": pa.array(table["coleccion"].astype(str)),
        "clave": pa.array(table["clave"].astype(str)),
        "row": pa.array(table["row"].to_numpy(), type=pa.int32()),
        "rank": pa.array(table["rank"].to_numpy(), type=pa.int32()),
        "target_row": pa.array(table["target_row"].to_numpy(), type=pa.int32()),
        "similarity": pa.array(table["similarity"].to_numpy(), type=pa.float32()),
        "targets": pa.array(list(table["targets"]), type=pa.list_(pa.int32())),
    }))
    timings["write"] = round(time.time() - mark, 1)

    per_source = table.groupby(["i", "row"]).size()
    source_texts = len(pairs)
    summary = {
        "top": top,
        "sources": int(len(units)),
        "source_texts": int(source_texts),
        "rows_written": int(len(table)),
        # Sources (distinct `(i, row)`) with fewer than `top` rows, a source with
        # none at all included (a lexical scorer's no-match).
        "sources_short_of_top": int(source_texts - (per_source == top).sum()),
        "sources_without_rows": int(source_texts - len(per_source)),
        "agrees_with_nearest": True,
        "tolerance": tolerance,
        **scorer.describe(),
        "vector_rows": int(n_vectors),
        "block_rows": block_rows,
        "threads": thread_count(threads),
        "node": platform.node(),
        "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
        "seconds": timings,
        "seconds_total": round(time.time() - started, 1),
        "peak_rss_gb": peak_rss_gb(),
    }
    atomic_write_text(out_dir / SUMMARY, json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    atomic_write_text(out_dir / ".done", "")
    log(json.dumps(summary, indent=2, ensure_ascii=False))
    return summary


def report(work_dir: Path, log=print) -> dict:
    """What is on disk, offline: no Slurm, no network."""
    work_dir = Path(work_dir)
    out_dir = output_dir(work_dir)
    done = (out_dir / ".done").exists()
    summary = {}
    if (out_dir / SUMMARY).exists():
        summary = json.loads((out_dir / SUMMARY).read_text(encoding="utf-8"))
    log(f"nearest-texts: {'done' if done else 'not finished'} ({out_dir})")
    for name in (TABLE, SUMMARY):
        path = out_dir / name
        log(f"  {name}: {'present' if path.exists() else 'MISSING'}"
            + (f" ({path.stat().st_size / 1e6:.1f} MB)" if path.exists() else ""))
    if summary:
        log(f"  agrees_with_nearest: {str(summary['agrees_with_nearest']).lower()}")
        log(f"  {summary['rows_written']} rows for {summary['source_texts']} source texts "
            f"({summary['sources']} searched unit rows), top {summary['top']}, "
            f"{summary['sources_short_of_top']} short of it, "
            f"{summary['seconds_total']}s, {summary['peak_rss_gb']} GB peak, "
            f"node {summary['node']}")
    if not done:
        instrument_matrix.slurm_tail(work_dir, log=log, subdir=SUBDIR)
    return {"done": done, "summary": summary}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--work-dir", type=Path, default=Path("emb-run-umap"))
    parser.add_argument("--top", type=int, default=TOP_TEXTS,
                        help="foreign texts kept per searched source")
    parser.add_argument("--tolerance", type=float, default=instrument_matrix.DEFAULT_TOLERANCE,
                        help="slack of the rank-1 agreement check with nearest.parquet")
    parser.add_argument("--block-rows", type=int, default=instrument_matrix.DEFAULT_BLOCK_ROWS,
                        help="rows of one instrument multiplied by the corpus at once")
    parser.add_argument("--threads", type=int, default=None)
    parser.add_argument("--cache-dir", type=Path, default=None)
    instrument_matrix.add_slurm_arguments(parser)
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
        instrument_matrix.submit(
            args.work_dir, exclude=instrument_matrix.resolve_exclude(args),
            tolerance=args.tolerance,
            block_rows=args.block_rows, force=args.force, dry_run=args.dry_run,
            script=THIS_SCRIPT, subdir=SUBDIR, job_name=JOB_NAME,
            extra_args=["--top", args.top], requires=REQUIRES,
            shared_node=args.shared_node, cpus=args.cpus, mem=args.mem,
            cache_dir=args.cache_dir)
        if not args.wait:
            return 0

    if args.wait:
        outcome = instrument_matrix.wait(args.work_dir, poll=args.poll,
                                         max_wait_minutes=args.max_wait_minutes,
                                         subdir=SUBDIR, script=THIS_SCRIPT, label=JOB_NAME)
        if outcome == "still running":
            return EXIT_STILL_RUNNING
        if outcome == "failed":
            return 1
        report(args.work_dir)
        return 0

    check_prerequisites(args.work_dir)
    done = output_dir(args.work_dir) / ".done"
    if done.exists() and not args.force:
        print(f"{done} is there -- nothing to do (pass --force to recompute)")
        return 0

    threads = thread_count(args.threads)
    set_thread_env(threads)
    print(f"threads={threads}")
    build_nearest_texts(args.work_dir, top=args.top, tolerance=args.tolerance,
                        block_rows=args.block_rows, threads=threads, cache_dir=args.cache_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Tune BM25's parameters toward the Qwen3-Embedding-4B's result.

Issue #267's review fix. The BM25 data set of the Atlas was built with bm25s'
defaults (`lucene`, `k1=1.5`, `b=0.75`, binary queries), chosen once and never
measured against anything. This searches BM25's configuration for the one whose
per-row winners agree most with the 4B's, over the same 112,077 searched rows,
and records the result:

    python tune_bm25.py --dry-run                    # the grid, nothing computed
    python tune_bm25.py --submit                     # the sample grid, 2 Slurm jobs
    python tune_bm25.py --wait --max-wait-minutes 9  # repeat until it does not exit 75
    python tune_bm25.py --stage confirm --submit     # the top 3 + defaults, full sweeps
    python tune_bm25.py --stage confirm --wait --max-wait-minutes 9
    python tune_bm25.py --stage confirm              # compute the full-sweep metrics
    python tune_bm25.py --report                     # the sorted table, offline

Every command takes `--work-dir emb-run-atlas-bm25` and `--target emb-run-atlas-4b`
(the defaults). Neither is written to except under `<work-dir>/tune/`.

**Objective (decided).** Row-level winner agreement with the 4B. For each searched
row, a *hit* is when the set of instruments owning BM25's tied winners intersects
the set the 4B's `nearest.parquet` records for that row (its tied winners'
owners). `hit_rate` is the share of hits over the rows the 4B counted (a row BM25
finds no foreign match for is a miss); `jaccard` is the mean Jaccard of the two
owner sets over the same rows. Only `hit_rate` is optimised. The rest is
reported: `closest_agree` (instruments, of 1,303, whose closest instrument is the
4B's -- the 933 of the defaults), `top5_overlap` (mean size of the intersection of
the two top-five lists), `tie_rows`, `identical_shared_dropped`,
`no_match_rows`, and `mean_m`/`mean_winners` (a configuration that ties half the
corpus would hit by accident, and these two say so). The last two need a whole
matrix, so they exist for the full sweeps only.

**Search space (decided).** `k1 in {0.5, 0.9, 1.2, 1.5, 2.0, 3.0}` x `b in
{0, 0.25, 0.5, 0.75, 1.0}` x bm25s' `method in {lucene, robertson, atire, bm25l,
bm25+}` (`delta` stays bm25s' 0.5) x the query weighting `binary` (today's) or
`tf-saturated` (`scoring.Bm25Config`: a term occurring `c` times in the query
weighs `c (k1 + 1) / (c + k1)`, 1 for `c == 1`, so it equals `binary` when every
query term occurs once). 300 configurations. Not searched: the tokeniser, the
tie tolerance, the identity rule, the mask, the `1/m` weighting.

**Cost (decided): sample, then confirm.** The whole grid runs on a stratified
(by collection) random ~10 % of the searched unit rows (`--sample`, `--seed`;
`tune/sample.parquet` lists them), through `instrument_matrix.sweep_answers` --
the very code a full run uses -- on weights recomputed from `tokens.parquet`
(`scoring.Bm25Counts`: bm25s' own formulas, no re-tokenising, no `bm25-index/`
per configuration). The top 3 by sample `hit_rate`, plus the defaults as the
control, then get a full sweep each: a work directory of their own under
`tune/full/<slug>/` prepared by `prepare_bm25_input.py` and swept by its own
`instrument_matrix.py` Slurm job, so a full result is a normal `matrix.npy` /
`nearest.parquet` and is measured by the same `agreement` as a sample.

Outputs, under `<work-dir>/tune/` (gitignored with the rest of `emb-run-*`):
`sample.parquet`, `grid/<slug>.json` (one per configuration, which is what makes
the grid resumable), `full/<slug>/`, `jobs.json`, `results.parquet` and
`results.json`. Nothing here uploads anything.
"""

from __future__ import annotations

import argparse
import itertools
import json
import multiprocessing
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import instrument_matrix  # noqa: E402
import scoring  # noqa: E402
from project_umap import peak_rss_gb, set_thread_env  # noqa: E402
from submit_umap import EXIT_STILL_RUNNING, queued_jobs  # noqa: E402

SUBDIR = "tune"
SCRIPT = Path(__file__)

K1S = (0.5, 0.9, 1.2, 1.5, 2.0, 3.0)
BS = (0.0, 0.25, 0.5, 0.75, 1.0)

DEFAULT_WORK_DIR = Path("emb-run-atlas-bm25")
DEFAULT_TARGET = Path("emb-run-atlas-4b")
DEFAULT_SOURCE = Path("emb-run-atlas")

#: How many of the sample's configurations one worker process takes, and how
#: many processes one grid job runs. A job owns a whole `geoint` node (~60 cores,
#: ~245 GB); the sparse product is single-threaded, so the parallelism is across
#: configurations.
DEFAULT_PROCESSES = 28
DEFAULT_SHARDS = 2
TIME_LIMIT = "2:00:00"
TOP = 3


def tune_dir(work_dir: Path) -> Path:
    return Path(work_dir) / SUBDIR


def grid() -> list[scoring.Bm25Config]:
    """Every configuration of the search space, `k1` x `b` first, then the
    method, then the query weighting."""
    return [scoring.Bm25Config(method=method, k1=k1, b=b, weighting=weighting)
            for weighting, method, k1, b in itertools.product(
                scoring.QUERY_WEIGHTINGS, scoring.BM25_METHODS, K1S, BS)]


def default_config() -> scoring.Bm25Config:
    """The configuration the data set was built with before the tuning."""
    return scoring.Bm25Config()


# -- the metrics ------------------------------------------------------------ #

def agreement(ref_targets, cand_targets, ref_counted) -> dict:
    """How well candidate winners resemble the reference's, row by row.

    All three arguments are aligned sequences (one entry per searched unit row):
    each target entry is the collection of instruments owning that row's tied
    winners. Only rows the reference counted are measured. A *hit* is a
    non-empty intersection; an empty candidate set (no foreign match) is a miss.
    Returns `rows`, `hits`, `hit_rate` and `jaccard` (the mean Jaccard of the
    two sets).
    """
    rows = hits = 0
    jaccard = 0.0
    for ref, cand, counted in zip(ref_targets, cand_targets, ref_counted):
        if not counted:
            continue
        ref, cand = set(int(j) for j in ref), set(int(j) for j in cand)
        rows += 1
        shared = len(ref & cand)
        hits += shared > 0
        union = len(ref | cand)
        jaccard += shared / union if union else 0.0
    return {"rows": rows, "hits": int(hits),
            "hit_rate": hits / rows if rows else 0.0,
            "jaccard": jaccard / rows if rows else 0.0}


def instrument_agreement(ref_matrix, cand_matrix, top: int = 5) -> dict:
    """`closest_agree` (instruments whose closest instrument is the same in both
    matrices, among those with one in each) and `top5_overlap` (the mean number
    of instruments two top-`top` lists share), from the very ranking the Atlas
    uses (`export_atlas_data.weighted_targets`)."""
    import numpy as np

    from export_atlas_data import weighted_targets

    closest = overlap = measured = 0
    for i in range(ref_matrix.shape[0]):
        ref = [j for j, _ in weighted_targets(ref_matrix, i, top)]
        cand = [j for j, _ in weighted_targets(cand_matrix, i, top)]
        if not ref or not cand:
            continue
        measured += 1
        closest += ref[0] == cand[0]
        overlap += len(set(ref) & set(cand))
    return {"instruments": measured, "closest_agree": int(closest),
            "top5_overlap": overlap / measured if measured else 0.0,
            "matrix_sum": float(np.asarray(cand_matrix).sum())}


def answer_counts(m, n_winners, identical) -> dict:
    """The per-row counts reported beside the hit rate, from arrays aligned to
    the evaluated rows."""
    import numpy as np

    m, n_winners, identical = np.asarray(m), np.asarray(n_winners), np.asarray(identical)
    found = m > 0
    return {"tie_rows": int((n_winners > 1).sum()),
            "identical_shared_dropped": int((identical & (m > 1)).sum()),
            "no_match_rows": int((m == 0).sum()),
            "mean_m": float(m[found].mean()) if found.any() else 0.0,
            "mean_winners": float(n_winners[found].mean()) if found.any() else 0.0}


# -- the context every evaluation shares ------------------------------------- #

class Context:
    """What the grid reads once: the searched unit rows, who owns each vector
    row, the 4B's answers for those rows and BM25's term counts."""

    def __init__(self, work_dir: Path, target: Path, *, cache_dir=None, collections=None,
                 log=print):
        import numpy as np
        import pyarrow.parquet as pq

        work_dir, target = Path(work_dir), Path(target)
        self.cache_dir = cache_dir
        all_units = instrument_matrix.unit_rows(
            work_dir, collections=collections, cache_dir=cache_dir, log=log)
        self.units = all_units[instrument_matrix.searched_mask(all_units)].reset_index(drop=True)
        self.counts = scoring.Bm25Counts.load(work_dir)
        self.owners = instrument_matrix.owners_of_rows(self.units, self.counts.n_rows)
        self.own_rows = instrument_matrix.own_rows_of(self.units)
        self.unowned = np.array([c for c, group in enumerate(self.owners) if not group],
                                dtype=np.int64)
        self.ref = pq.read_table(instrument_matrix.output_dir(target) / "nearest.parquet") \
            .to_pandas()
        self.ref_matrix = np.load(instrument_matrix.output_dir(target) / "matrix.npy")
        self.check_aligned(self.ref, target)
        self.ref_targets = list(self.ref["targets"])
        self.ref_counted = self.ref["counted"].to_numpy()

    def check_aligned(self, nearest, where) -> None:
        """`nearest.parquet` row `n` must be searched unit row `n` of this join,
        as `export_atlas_pairs.load` asserts for its own use."""
        if len(nearest) != len(self.units):
            raise SystemExit(f"{where}: nearest.parquet has {len(nearest)} rows, the join "
                             f"has {len(self.units)} searched unit rows")
        for column in ("i", "eId", "row", "unit_type"):
            if not (self.units[column].to_numpy() == nearest[column].to_numpy()).all():
                raise SystemExit(f"{where}: nearest.parquet's `{column}` does not line up "
                                 "with the searched unit rows of the BM25 work directory")


def stratified_sample(units, fraction: float, seed: int):
    """Positions (sorted) of a random `fraction` of the searched unit rows, drawn
    separately inside each collection so the small ones are represented."""
    import numpy as np

    rng = np.random.default_rng(seed)
    chosen = []
    for _, group in units.groupby("coleccion", sort=True):
        positions = group.index.to_numpy()
        take = max(1, int(round(len(positions) * fraction)))
        chosen.append(rng.choice(positions, size=min(take, len(positions)), replace=False))
    return np.sort(np.concatenate(chosen))


def evaluate_sample(ctx: Context, config: scoring.Bm25Config, positions) -> dict:
    """One configuration, swept over the sampled rows only."""
    started = time.time()
    scorer = scoring.Bm25Scorer.for_config(ctx.counts, config)
    sample = ctx.units.loc[positions, ["i", "row"]]
    answers = instrument_matrix.sweep_answers(scorer, ctx.owners, ctx.unowned, sample,
                                              own_rows=ctx.own_rows, log=lambda *a: None)
    merged = sample.reset_index().merge(answers, on=["i", "row"], how="left") \
        .sort_values("index")
    cand = list(merged["targets"])
    result = agreement([ctx.ref_targets[p] for p in positions], cand,
                       [ctx.ref_counted[p] for p in positions])
    result.update(answer_counts(merged["m"], merged["n_winners"], merged["identical"]))
    result.update(config.record())
    result.update({"slug": config.slug, "stage": "sample",
                   "seconds": round(time.time() - started, 1)})
    return result


def evaluate_full(ctx: Context, config: scoring.Bm25Config, full_dir: Path) -> dict:
    """One configuration's finished full sweep (`instrument_matrix.py` over
    `full_dir`), measured by the same `agreement` and the instrument-level ranking."""
    import numpy as np
    import pyarrow.parquet as pq

    out = instrument_matrix.output_dir(full_dir)
    if not (out / ".done").exists():
        raise SystemExit(f"{out / '.done'} is missing: that sweep has not finished")
    nearest = pq.read_table(out / "nearest.parquet").to_pandas()
    ctx.check_aligned(nearest, full_dir)
    summary = json.loads((out / "matrix.json").read_text(encoding="utf-8"))
    result = agreement(ctx.ref_targets, list(nearest["targets"]), ctx.ref_counted)
    result.update(answer_counts(nearest["m"], nearest["n_winners"],
                                nearest["drop_reason"].eq("identical_shared")))
    result.update(instrument_agreement(ctx.ref_matrix, np.load(out / "matrix.npy")))
    result.update(config.record())
    result.update({
        "slug": config.slug, "stage": "full", "counted_rows": int(summary["counted_rows"]),
        "nonzero_cells": int(summary["nonzero_cells"]),
        "seconds": float(summary["seconds_total"]),
        "seconds_load": float(summary["seconds"].get("load", 0.0)),
        "seconds_sweep": float(summary["seconds"]["sweep"]),
        "peak_rss_gb": float(summary["peak_rss_gb"]), "node": summary.get("node"),
        "slurm_job_id": summary.get("slurm_job_id")})
    return result


# -- the grid ---------------------------------------------------------------- #

_CONTEXT: Context | None = None
_POSITIONS = None


def _work(config: scoring.Bm25Config) -> dict:
    """A pool worker: evaluate and persist one configuration (the persisted file
    is what a rerun skips)."""
    from _atomic import atomic_write_text

    path = grid_path(_CONTEXT_DIR, config)
    result = evaluate_sample(_CONTEXT, config, _POSITIONS)
    atomic_write_text(path, json.dumps(result, indent=2) + "\n")
    return result


_CONTEXT_DIR: Path | None = None


def grid_path(work_dir: Path, config: scoring.Bm25Config) -> Path:
    return tune_dir(work_dir) / "grid" / f"{config.slug}.json"


def write_sample(ctx: Context, work_dir: Path, fraction: float, seed: int, log=print):
    """Draw the sample (or read the one already drawn: it must not change
    between shards) and record it."""
    import numpy as np
    import pyarrow as pa
    import pyarrow.parquet as pq

    from _atomic import atomic_write_table

    path = tune_dir(work_dir) / "sample.parquet"
    if path.exists():
        positions = pq.read_table(path).column("position").to_numpy()
        log(f"{path}: {len(positions)} sampled unit rows, kept")
        return positions
    positions = stratified_sample(ctx.units, fraction, seed)
    sampled = ctx.units.loc[positions]
    atomic_write_table(path, pa.table({
        "position": pa.array(positions, type=pa.int64()),
        "i": pa.array(sampled["i"].to_numpy(), type=pa.int32()),
        "row": pa.array(sampled["row"].to_numpy(), type=pa.int32()),
        "coleccion": pa.array(sampled["coleccion"].astype(str)),
        "eId": pa.array(sampled["eId"].astype(str)),
        "seed": pa.array(np.full(len(positions), seed, dtype=np.int32)),
    }))
    log(f"{path}: {len(positions)} of {len(ctx.units)} unit rows sampled (seed {seed})")
    return positions


def run_grid(work_dir: Path, target: Path, *, fraction: float, seed: int, shard: tuple[int, int],
             processes: int, cache_dir=None, log=print) -> int:
    """Evaluate this shard's configurations that have no result yet; returns how
    many it computed."""
    global _CONTEXT, _POSITIONS, _CONTEXT_DIR

    work_dir = Path(work_dir)
    configs = [c for n, c in enumerate(grid()) if n % shard[1] == shard[0]]
    todo = [c for c in configs if not grid_path(work_dir, c).exists()]
    log(f"shard {shard[0]}/{shard[1]}: {len(configs)} configurations, {len(todo)} to compute")
    if not todo:
        return 0
    started = time.time()
    _CONTEXT = Context(work_dir, target, cache_dir=cache_dir, log=log)
    _POSITIONS = write_sample(_CONTEXT, work_dir, fraction, seed, log=log)
    _CONTEXT_DIR = work_dir
    (tune_dir(work_dir) / "grid").mkdir(parents=True, exist_ok=True)
    set_thread_env(1)
    log(f"context ready in {time.time() - started:.0f}s")

    done = 0
    # `fork` on purpose: workers inherit the context copy-on-write (Python 3.14
    # defaults to `forkserver`, which would reload it in every process).
    pool_context = multiprocessing.get_context("fork")
    with pool_context.Pool(min(processes, len(todo))) as pool:
        for result in pool.imap_unordered(_work, todo):
            done += 1
            log(f"{done}/{len(todo)} {result['slug']}: hit_rate {result['hit_rate']:.4f}, "
                f"{result['seconds']}s, {time.time() - started:.0f}s elapsed")
    return done


def collect_grid(work_dir: Path) -> list[dict]:
    """Every configuration's persisted sample result, in grid order; a
    `SystemExit` naming how many are missing otherwise."""
    results, missing = [], 0
    for config in grid():
        path = grid_path(work_dir, config)
        if path.exists():
            results.append(json.loads(path.read_text(encoding="utf-8")))
        else:
            missing += 1
    if missing:
        raise SystemExit(f"{missing} of {len(grid())} configurations have no result under "
                         f"{tune_dir(work_dir) / 'grid'}: the grid jobs have not all finished")
    return results


# -- results ------------------------------------------------------------------ #

def rank(sample_results: list[dict]) -> list[dict]:
    """Sample results, best `hit_rate` first (ties: higher `jaccard`, then grid
    order, which is stable)."""
    return sorted(sample_results, key=lambda r: (-r["hit_rate"], -r["jaccard"]))


def confirm_configs(sample_results: list[dict], top: int = TOP) -> list[scoring.Bm25Config]:
    """The configurations that get a full sweep: the `top` best by sample
    `hit_rate` other than the defaults, then the defaults as the control."""
    control = default_config()
    best = [r for r in rank(sample_results) if r["slug"] != control.slug][:top]
    return [scoring.bm25_config_of(r) for r in best] + [control]


def results_json_path(work_dir: Path) -> Path:
    return tune_dir(work_dir) / "results.json"


def write_results(work_dir: Path, sample_results: list[dict], full_results: list[dict],
                  meta: dict) -> None:
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq

    from _atomic import atomic_write_table, atomic_write_text

    atomic_write_text(results_json_path(work_dir), json.dumps(
        {"meta": meta, "sample": rank(sample_results), "full": full_results},
        indent=2, ensure_ascii=False) + "\n")
    frame = pd.DataFrame(rank(sample_results) + full_results)
    atomic_write_table(tune_dir(work_dir) / "results.parquet",
                       pa.Table.from_pandas(frame, preserve_index=False))


def format_table(rows: list[dict], columns: list[tuple[str, str, str]], limit=None) -> str:
    """A fixed-width table of `rows`; `columns` are `(key, heading, format)`."""
    lines = ["  ".join(f"{head:>{max(len(head), 8)}}" for _, head, _ in columns)]
    for row in rows[:limit]:
        cells = []
        for key, head, fmt in columns:
            value = row.get(key)
            text = "-" if value is None else format(value, fmt)
            cells.append(f"{text:>{max(len(head), 8)}}")
        lines.append("  ".join(cells))
    return "\n".join(lines)


SAMPLE_COLUMNS = [("method", "method", ""), ("k1", "k1", "g"), ("b", "b", "g"),
                  ("weighting", "query", ""), ("hit_rate", "hit_rate", ".4f"),
                  ("jaccard", "jaccard", ".4f"), ("tie_rows", "tie_rows", "d"),
                  ("no_match_rows", "no_match", "d"), ("mean_m", "mean_m", ".2f")]
FULL_COLUMNS = SAMPLE_COLUMNS + [("closest_agree", "closest", "d"),
                                 ("top5_overlap", "top5", ".3f"),
                                 ("identical_shared_dropped", "id_shared", "d"),
                                 ("counted_rows", "counted", "d"),
                                 ("seconds", "seconds", ".1f"), ("peak_rss_gb", "rss_gb", ".2f")]


def report(work_dir: Path, limit: int = 15, log=print) -> dict:
    """Print the sorted tables from `results.json`, offline."""
    path = results_json_path(work_dir)
    if not path.exists():
        raise SystemExit(f"{path} is missing: run the grid first")
    data = json.loads(path.read_text(encoding="utf-8"))
    log(f"sample grid: {len(data['sample'])} configurations, best first "
        f"(top {limit} of the {data['meta'].get('sample_rows', '?')}-row sample)")
    log(format_table(data["sample"], SAMPLE_COLUMNS, limit))
    if data["full"]:
        log("\nfull sweeps (the control is the defaults):")
        log(format_table(data["full"], FULL_COLUMNS))
    return data


# -- Slurm --------------------------------------------------------------------- #

def jobs_path(work_dir: Path) -> Path:
    return tune_dir(work_dir) / "jobs.json"


def sbatch_command(work_dir: Path, argv: list[str], name: str, *,
                   exclude: str = instrument_matrix.DEFAULT_EXCLUDE) -> list[str]:
    """One `sbatch` line running this script with `argv`; absolute paths, as in
    `instrument_matrix.sbatch_command`."""
    work_dir = Path(work_dir).resolve()
    cmd = ["sbatch", "--parsable", f"--job-name={name}", f"--time={TIME_LIMIT}",
           f"--output={tune_dir(work_dir) / 'slurm-%j.out'}"]
    if exclude:
        cmd.append(f"--exclude={exclude}")
    return cmd + [str(instrument_matrix.SUBMIT_SH), str(SCRIPT)] + argv


def submit_commands(work_dir: Path, target: Path, *, fraction: float, seed: int,
                    shards: int, processes: int, cache_dir=None) -> list[list[str]]:
    work_dir, target = Path(work_dir).resolve(), Path(target).resolve()
    commands = []
    for shard in range(shards):
        argv = ["--work-dir", str(work_dir), "--target", str(target), "--run-grid",
                "--shard", f"{shard}/{shards}", "--processes", str(processes),
                "--sample", repr(fraction), "--seed", str(seed)]
        if cache_dir:
            argv += ["--cache-dir", str(cache_dir)]
        commands.append(sbatch_command(work_dir, argv, f"tune-bm25-{shard}"))
    return commands


def record_jobs(work_dir: Path, stage: str, jobs: list[dict]) -> None:
    from _atomic import atomic_write_text

    path = jobs_path(work_dir)
    state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    state[stage] = {"jobs": jobs, "submitted_at": time.time()}
    atomic_write_text(path, json.dumps(state, indent=2) + "\n")


def submit_grid(work_dir: Path, commands: list[list[str]], log=print) -> list[dict]:
    tune_dir(work_dir).mkdir(parents=True, exist_ok=True)
    jobs = []
    for cmd in commands:
        log(" ".join(cmd))
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        jobs.append({"job_id": result.stdout.strip(), "argv": " ".join(cmd)})
        log(f"tune-bm25: job {jobs[-1]['job_id']}")
    record_jobs(work_dir, "grid", jobs)
    return jobs


def wait_for(work_dir: Path, stage: str, finished, *, poll: int = 60,
             max_wait_minutes: float = 0.0, sleep=time.sleep, log=print) -> str:
    """Poll `squeue` for `stage`'s jobs. `finished()` says whether their outputs
    are all there. Returns `"finished"`, `"still running"` (the chunk's
    `--max-wait-minutes` elapsed; call again) or `"failed"`."""
    state = json.loads(jobs_path(work_dir).read_text(encoding="utf-8")).get(stage)
    if not state:
        raise SystemExit(f"{jobs_path(work_dir)} has no {stage!r} jobs: submit them first")
    ids = [str(job["job_id"]) for job in state["jobs"]]
    deadline = time.time() + max_wait_minutes * 60 if max_wait_minutes else None
    while True:
        if finished():
            log(f"tune-bm25: {stage} outputs are all there")
            return "finished"
        remaining = queued_jobs(ids)
        elapsed = (time.time() - state["submitted_at"]) / 60
        if not remaining:
            if finished():
                return "finished"
            log(f"jobs {','.join(ids)} left the queue after {elapsed:.1f} min without "
                "finishing: see tune/slurm-*.out")
            for out in sorted(tune_dir(work_dir).glob("slurm-*.out")):
                log(f"--- tail of {out} ---")
                for line in out.read_text(encoding="utf-8", errors="replace").splitlines()[-20:]:
                    log(line)
            return "failed"
        log(f"jobs {','.join(sorted(remaining))} still queued or running after {elapsed:.1f} min")
        if deadline is not None and time.time() > deadline:
            return "still running"
        sleep(poll)


def grid_finished(work_dir: Path) -> bool:
    return all(grid_path(work_dir, config).exists() for config in grid())


def full_dir(work_dir: Path, config: scoring.Bm25Config) -> Path:
    return tune_dir(work_dir) / "full" / config.slug


def full_finished(work_dir: Path, configs) -> bool:
    return all((instrument_matrix.output_dir(full_dir(work_dir, c)) / ".done").exists()
               for c in configs)


def prepare_and_submit_full(work_dir: Path, source: Path, configs, *, cache_dir=None,
                            log=print) -> list[dict]:
    """A work directory per configuration (`prepare_bm25_input.py`, with the
    configuration) and one `instrument_matrix.py` Slurm job each."""
    import prepare_bm25_input

    jobs = []
    for config in configs:
        directory = full_dir(work_dir, config)
        log(f"{config.slug}: preparing {directory}")
        prepare_bm25_input.prepare(directory, source, cache_dir=cache_dir, config=config,
                                   log=lambda *a: None)
        state = instrument_matrix.submit(directory, log=log)
        jobs.append({"job_id": state["job_id"], "slug": config.slug,
                     "work_dir": str(directory)})
    record_jobs(work_dir, "confirm", jobs)
    return jobs


# -- the stages ------------------------------------------------------------------ #

def finish_grid(work_dir: Path, target: Path, *, fraction: float, seed: int,
                cache_dir=None, log=print) -> list[dict]:
    """Aggregate the persisted grid results into `results.json`/`.parquet`."""
    import pyarrow.parquet as pq

    sample_results = collect_grid(work_dir)
    sample = pq.read_table(tune_dir(work_dir) / "sample.parquet")
    existing = json.loads(results_json_path(work_dir).read_text(encoding="utf-8")) \
        if results_json_path(work_dir).exists() else {"full": []}
    meta = {"sample_fraction": fraction, "sample_rows": sample.num_rows, "seed": seed,
            "grid": len(grid()), "target": str(target), "objective": "hit_rate",
            "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    write_results(work_dir, sample_results, existing.get("full", []), meta)
    return sample_results


def finish_confirm(work_dir: Path, target: Path, *, cache_dir=None, log=print) -> list[dict]:
    """Measure the finished full sweeps and add them to the results."""
    data = json.loads(results_json_path(work_dir).read_text(encoding="utf-8"))
    configs = confirm_configs(data["sample"])
    ctx = Context(work_dir, target, cache_dir=cache_dir, log=log)
    full_results = []
    for config in configs:
        result = evaluate_full(ctx, config, full_dir(work_dir, config))
        result["control"] = config.slug == default_config().slug
        result["sample_hit_rate"] = next(r["hit_rate"] for r in data["sample"]
                                         if r["slug"] == config.slug)
        full_results.append(result)
    full_results.sort(key=lambda r: (-r["closest_agree"], -r["hit_rate"]))
    write_results(work_dir, data["sample"], full_results, data["meta"])
    return full_results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR,
                        help="the BM25 work directory (default: emb-run-atlas-bm25)")
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET,
                        help="the work directory to resemble (default: emb-run-atlas-4b)")
    parser.add_argument("--from", dest="source", type=Path, default=DEFAULT_SOURCE,
                        help="the dense work directory the full-sweep BM25 directories are "
                             "prepared from (default: emb-run-atlas)")
    parser.add_argument("--cache-dir", type=Path, default=None)
    parser.add_argument("--stage", choices=("grid", "confirm"), default="grid")
    parser.add_argument("--sample", type=float, default=0.1,
                        help="share of the searched unit rows the grid runs on")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--processes", type=int, default=DEFAULT_PROCESSES)
    parser.add_argument("--shards", type=int, default=DEFAULT_SHARDS,
                        help="grid jobs --submit starts, each with its --processes")
    parser.add_argument("--shard", default="0/1", help=argparse.SUPPRESS)
    parser.add_argument("--run-grid", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--poll", type=int, default=60)
    parser.add_argument("--max-wait-minutes", type=float, default=0.0,
                        help="bound one wait chunk: exit status 75 if the jobs are still there")
    parser.add_argument("--submit", action="store_true")
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--report", action="store_true",
                        help="print the sorted tables from results.json and exit")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the grid (and the sbatch lines) without computing")
    args = parser.parse_args(argv)

    if args.report:
        report(args.work_dir)
        return 0

    shard = tuple(int(part) for part in args.shard.split("/"))
    if args.dry_run:
        configs = grid()
        print(f"{len(configs)} configurations: k1 {K1S} x b {BS} x methods "
              f"{scoring.BM25_METHODS} x query {scoring.QUERY_WEIGHTINGS}")
        print(f"sample: {args.sample:.0%} of the searched unit rows, stratified by "
              f"collection, seed {args.seed}; target {args.target}")
        print(format_table([c.record() | {"slug": c.slug} for c in configs],
                           [("method", "method", ""), ("k1", "k1", "g"), ("b", "b", "g"),
                            ("weighting", "query", "")], limit=len(configs)))
        if args.stage == "grid":
            for cmd in submit_commands(args.work_dir, args.target, fraction=args.sample,
                                       seed=args.seed, shards=args.shards,
                                       processes=args.processes, cache_dir=args.cache_dir):
                print(" ".join(cmd))
        return 0

    if args.run_grid:
        run_grid(args.work_dir, args.target, fraction=args.sample, seed=args.seed,
                 shard=shard, processes=args.processes, cache_dir=args.cache_dir)
        return 0

    work_dir = args.work_dir
    if args.stage == "grid":
        if args.submit:
            submit_grid(work_dir, submit_commands(
                work_dir, args.target, fraction=args.sample, seed=args.seed,
                shards=args.shards, processes=args.processes, cache_dir=args.cache_dir))
            if not args.wait:
                return 0
        if args.wait:
            outcome = wait_for(work_dir, "grid", lambda: grid_finished(work_dir),
                               poll=args.poll, max_wait_minutes=args.max_wait_minutes)
            if outcome == "still running":
                return EXIT_STILL_RUNNING
            if outcome == "failed":
                return 1
            finish_grid(work_dir, args.target, fraction=args.sample, seed=args.seed)
            report(work_dir)
            return 0
        # Neither --submit nor --wait: compute here, every shard in turn.
        for k in range(shard[1]):
            run_grid(work_dir, args.target, fraction=args.sample, seed=args.seed,
                     shard=(k, shard[1]), processes=args.processes, cache_dir=args.cache_dir)
        finish_grid(work_dir, args.target, fraction=args.sample, seed=args.seed)
        report(work_dir)
        return 0

    # confirm
    data = json.loads(results_json_path(work_dir).read_text(encoding="utf-8")) \
        if results_json_path(work_dir).exists() else None
    if data is None:
        raise SystemExit(f"{results_json_path(work_dir)} is missing: finish the grid first")
    configs = confirm_configs(data["sample"])
    if args.submit:
        prepare_and_submit_full(work_dir, args.source, configs, cache_dir=args.cache_dir)
        if not args.wait:
            return 0
    if args.wait:
        outcome = wait_for(work_dir, "confirm", lambda: full_finished(work_dir, configs),
                           poll=args.poll, max_wait_minutes=args.max_wait_minutes)
        if outcome == "still running":
            return EXIT_STILL_RUNNING
        if outcome == "failed":
            return 1
    finish_confirm(work_dir, args.target, cache_dir=args.cache_dir)
    report(work_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())

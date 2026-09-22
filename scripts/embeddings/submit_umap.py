"""Launch one Slurm job per UMAP configuration, and wait for them in chunks.

Issue #241. Four configurations (`n_neighbors` 4 / 8 / 16 / 32, the shared
`project_umap.DEFAULT_N_NEIGHBORS`) over the same `vectors.npy`, one exclusive
node each, an 8-hour limit and no retry: a fit that dies is a finding, not
something to hide behind a relaunch.

    python submit_umap.py                       # submit and return
    python submit_umap.py --wait                # submit (if needed) and block
    python submit_umap.py --wait --max-wait-minutes 9   # block for one chunk
    python submit_umap.py --report              # the table, offline
    python submit_umap.py --dry-run             # print the sbatch commands

**Why the wait is chunked.** The launcher's natural shape is "block until
`squeue` is empty", and `--wait` alone still does exactly that (up to
`--max-wait-hours`). But an automated session driving this has a per-command
timeout, and ending a turn to wait for a background process kills the jobs'
own supervisor along with the session. So `--max-wait-minutes N` returns exit
status 75 while jobs remain, naming them and their elapsed time, and the
caller simply calls it again: the state lives in `jobs.json`, not in a
process that has to stay alive.

Exit status: 0 when every configuration finished, 75 when a chunk elapsed
with jobs still running, 1 when a configuration failed or the 8-hour ceiling
was reached (the launcher `scancel`s what is left in that case). A
configuration that finished stays valid regardless — `build_umap_html.py`
builds from whatever is `.done`.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from project_umap import DEFAULT_N_NEIGHBORS  # noqa: E402

SUBMIT_SH = Path(__file__).with_name("submit_umap.sh")
PROJECT_UMAP = Path(__file__).with_name("project_umap.py")

#: The configurations issue #241 sweeps, defined once in `project_umap.py`,
#: and the cheapest one — which is also the one asked for the shared
#: k-nearest-neighbour table, on the days that table has to be built at all
#: (`project_umap.py` keeps an existing one).
DEFAULT_CONFIGS = DEFAULT_N_NEIGHBORS
DEFAULT_KNN_CONFIG = min(DEFAULT_N_NEIGHBORS)
DEFAULT_KNN = 15

#: The login node of this cluster is saturated by another user's work outside
#: Slurm, so every job excludes it by default and the four configurations
#: share `geoint1`/`geoint2` (two run, the rest queue).
DEFAULT_EXCLUDE = "geoint0"

#: Exit status meaning "the chunk elapsed, jobs are still running" — 75 is
#: `EX_TEMPFAIL`, i.e. "try again", which is exactly what the caller does.
EXIT_STILL_RUNNING = 75

JOBS_JSON = "jobs.json"


def config_name(n_neighbors: int) -> str:
    return f"nn{n_neighbors:03d}"


def submission_order(configs) -> list[int]:
    """Heaviest configuration first, i.e. descending `n_neighbors`.

    Two nodes run at a time, so submission order decides what waits, and the
    first sweep measured the fit cost growing with `n_neighbors`
    (`n_neighbors=200` cost ~2.3x `n_neighbors=15`). **The second sweep
    measured the opposite below 32**: `n_neighbors=4` cost 4.7x
    `n_neighbors=32`, a sparser graph costing UMAP many more epochs. So this
    is a heuristic about a cost that is not monotone — see the README's two
    measured tables before trusting it with a sweep whose jobs are hours
    rather than minutes.

    The first pass also pulled the kNN configuration forward, because every
    projection's neighbour highlight waited on it; that tiebreak is gone with
    the reason for it — `neighbors.parquet` is computed once and kept, so no
    job waits on another.
    """
    return sorted(set(configs), reverse=True)


def pending_configs(work_dir: Path, configs) -> list[int]:
    """The configurations with no `.done` yet — re-running the launcher after
    one job failed resubmits only that one."""
    return [n for n in configs if not (work_dir / config_name(n) / ".done").exists()]


def sbatch_command(work_dir: Path, n_neighbors: int, min_dist: float, knn: int,
                   exclude: str, model: str) -> list[str]:
    name = config_name(n_neighbors)
    out_dir = work_dir / name
    cmd = ["sbatch", "--parsable", f"--job-name=umap-{name}",
           f"--output={out_dir / 'slurm-%j.out'}"]
    if exclude:
        cmd.append(f"--exclude={exclude}")
    cmd += [str(SUBMIT_SH), str(PROJECT_UMAP),
            "--work-dir", str(work_dir),
            "--n-neighbors", str(n_neighbors),
            "--min-dist", str(min_dist),
            "--model", model]
    if knn:
        cmd += ["--knn", str(knn)]
    return cmd


def submit(work_dir: Path, *, configs=DEFAULT_CONFIGS, min_dist: float = 0.1,
           knn_config: int | None = DEFAULT_KNN_CONFIG, knn: int = DEFAULT_KNN,
           exclude: str = DEFAULT_EXCLUDE, model: str = "Qwen/Qwen3-Embedding-0.6B",
           max_wait_hours: float = 8.0, dry_run: bool = False, log=print) -> dict:
    """Submit every pending configuration and write `jobs.json`."""
    from _atomic import atomic_write_text

    work_dir = Path(work_dir)
    if not (work_dir / "prepare.done").exists():
        raise SystemExit(
            f"{work_dir / 'prepare.done'} is missing -- run "
            "prepare_umap_input.py first"
        )

    pending = pending_configs(work_dir, submission_order(configs))
    for n in configs:
        if n not in pending:
            log(f"{config_name(n)}: .done exists, skipping")

    jobs = []
    for n_neighbors in pending:
        (work_dir / config_name(n_neighbors)).mkdir(parents=True, exist_ok=True)
        cmd = sbatch_command(work_dir, n_neighbors, min_dist,
                             knn if n_neighbors == knn_config else 0, exclude, model)
        log(" ".join(cmd))
        if dry_run:
            continue
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        job_id = result.stdout.strip()
        log(f"{config_name(n_neighbors)}: job {job_id}")
        jobs.append({
            "name": config_name(n_neighbors),
            "n_neighbors": n_neighbors,
            "knn": knn if n_neighbors == knn_config else 0,
            "job_id": job_id,
        })

    state = {
        "submitted_at": time.time(),
        "max_wait_hours": max_wait_hours,
        "exclude": exclude,
        "configs": list(configs),
        "jobs": jobs,
    }
    if not dry_run:
        atomic_write_text(work_dir / JOBS_JSON,
                          json.dumps(state, indent=2, ensure_ascii=False) + "\n")
    return state


def queued_jobs(job_ids) -> set[str]:
    """Which of `job_ids` `squeue` still knows about.

    `squeue --jobs` fails outright once one of the ids has left the
    accounting window, so a non-zero status falls back to asking about each
    id on its own: a launcher must never read "squeue errored" as "every job
    finished".
    """
    job_ids = [str(j) for j in job_ids]
    if not job_ids:
        return set()
    result = subprocess.run(
        ["squeue", "--noheader", "--jobs", ",".join(job_ids), "--format=%i"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        return {line.strip() for line in result.stdout.split() if line.strip()}
    still: set[str] = set()
    for job_id in job_ids:
        one = subprocess.run(
            ["squeue", "--noheader", "--jobs", job_id, "--format=%i"],
            capture_output=True, text=True,
        )
        if one.returncode == 0 and one.stdout.strip():
            still.add(job_id)
    return still


def load_jobs(work_dir: Path) -> dict:
    path = Path(work_dir) / JOBS_JSON
    if not path.exists():
        raise SystemExit(f"{path} is missing -- run submit_umap.py first")
    return json.loads(path.read_text(encoding="utf-8"))


def wait(work_dir: Path, *, poll: int = 60, max_wait_minutes: float = 0.0,
         sleep=time.sleep, log=print) -> tuple[str, list[str]]:
    """Poll `squeue` until nothing remains.

    Returns the outcome — `"finished"` (the queue is empty), `"still
    running"` (this chunk's `--max-wait-minutes` elapsed) or `"timed out"`
    (the run's own `--max-wait-hours` ceiling was reached, and what remained
    was `scancel`led) — together with the configurations that were still in
    the queue when it returned.
    """
    state = load_jobs(work_dir)
    job_ids = [job["job_id"] for job in state["jobs"]]
    by_id = {job["job_id"]: job for job in state["jobs"]}
    deadline = state["submitted_at"] + state["max_wait_hours"] * 3600
    chunk_deadline = time.time() + max_wait_minutes * 60 if max_wait_minutes else None

    while True:
        remaining = queued_jobs(job_ids)
        if not remaining:
            log("every job has left the queue")
            return "finished", []
        elapsed = (time.time() - state["submitted_at"]) / 60
        names = ", ".join(f"{by_id[j]['name']} ({j})" for j in sorted(remaining))
        pending_names = sorted(by_id[j]["name"] for j in remaining)
        log(f"{len(remaining)} job(s) still queued or running after "
            f"{elapsed:.1f} min: {names}")
        if time.time() > deadline:
            log(f"the {state['max_wait_hours']} h ceiling elapsed -- cancelling {names}")
            subprocess.run(["scancel", *sorted(remaining)], check=False)
            return "timed out", pending_names
        if chunk_deadline is not None and time.time() > chunk_deadline:
            return "still running", pending_names
        sleep(poll)


def report(work_dir: Path, *, configs=DEFAULT_CONFIGS, timed_out=(), log=print) -> list[dict]:
    """The per-configuration table: state, seconds, peak RSS, node."""
    work_dir = Path(work_dir)
    rows = []
    for n_neighbors in sorted(configs):
        name = config_name(n_neighbors)
        out_dir = work_dir / name
        projection = out_dir / "projection.json"
        if (out_dir / ".done").exists() and projection.exists():
            data = json.loads(projection.read_text(encoding="utf-8"))
            rows.append({
                "name": name, "state": "done",
                "seconds": data.get("seconds_total"),
                "peak_rss_gb": data.get("peak_rss_gb"),
                "node": data.get("node"),
                "job_id": data.get("slurm_job_id"),
            })
        else:
            rows.append({
                "name": name,
                "state": "timed out" if name in timed_out else "failed",
                "seconds": None, "peak_rss_gb": None, "node": None, "job_id": None,
            })

    log(f"{'config':<8} {'state':<10} {'seconds':>9} {'peak RSS':>9} {'node':<10} job")
    for row in rows:
        seconds = "-" if row["seconds"] is None else f"{row['seconds']:.0f}"
        rss = "-" if row["peak_rss_gb"] is None else f"{row['peak_rss_gb']:.1f} GB"
        log(f"{row['name']:<8} {row['state']:<10} {seconds:>9} {rss:>9} "
            f"{row['node'] or '-':<10} {row['job_id'] or '-'}")

    neighbors = work_dir / "neighbors.parquet"
    log(f"neighbors.parquet: {'present' if neighbors.exists() else 'MISSING'}")
    for row in rows:
        if row["state"] == "done":
            continue
        for out in sorted((work_dir / row["name"]).glob("slurm-*.out")):
            tail = out.read_text(encoding="utf-8", errors="replace").splitlines()[-20:]
            log(f"--- tail of {out} ---")
            for line in tail:
                log(line)
    return rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--work-dir", type=Path, default=Path("emb-run-umap"))
    parser.add_argument("--configs", default=",".join(str(n) for n in DEFAULT_CONFIGS),
                        help="comma-separated n_neighbors values, one job each")
    parser.add_argument("--min-dist", type=float, default=0.1)
    parser.add_argument("--knn-config", type=int, default=DEFAULT_KNN_CONFIG,
                        help="which configuration also computes the shared kNN table")
    parser.add_argument("--knn", type=int, default=DEFAULT_KNN)
    parser.add_argument("--model", default="Qwen/Qwen3-Embedding-0.6B")
    parser.add_argument("--exclude", default=DEFAULT_EXCLUDE,
                        help="nodes sbatch must not use (default: the saturated login node)")
    parser.add_argument("--poll", type=int, default=60)
    parser.add_argument("--max-wait-hours", type=float, default=8.0)
    parser.add_argument("--max-wait-minutes", type=float, default=0.0,
                        help="bound one wait chunk: return exit status 75 after this "
                             "many minutes if jobs remain (0 = no chunk, wait up to "
                             "--max-wait-hours)")
    parser.add_argument("--wait", action="store_true",
                        help="block polling squeue instead of returning after submitting")
    parser.add_argument("--report", action="store_true",
                        help="print the table for what is on disk and exit (no Slurm)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    configs = tuple(int(n) for n in args.configs.split(",") if n)

    if args.report:
        rows = report(args.work_dir, configs=configs)
        return 0 if all(row["state"] == "done" for row in rows) else 1

    jobs_json = Path(args.work_dir) / JOBS_JSON
    if not (args.wait and jobs_json.exists()):
        submit(args.work_dir, configs=configs, min_dist=args.min_dist,
               knn_config=args.knn_config, knn=args.knn, exclude=args.exclude,
               model=args.model, max_wait_hours=args.max_wait_hours,
               dry_run=args.dry_run)
        if args.dry_run or not args.wait:
            return 0

    outcome, pending = wait(args.work_dir, poll=args.poll,
                            max_wait_minutes=args.max_wait_minutes)
    if outcome == "still running":
        return EXIT_STILL_RUNNING

    rows = report(args.work_dir, configs=configs,
                  timed_out=pending if outcome == "timed out" else ())
    return 0 if outcome == "finished" and all(r["state"] == "done" for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())

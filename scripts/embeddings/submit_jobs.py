"""Run one model over every pending shard of `shards.json` (issue #218 Fase
2): download it once, submit every shard that has no `.done` yet, wait,
retry what is still missing, then delete the weights before returning.

Disk is rationed one model at a time on this cluster (`/home` is 795 GB,
123 GB free) — the same reason `../Chimalli-overleaf/submit_jobs.py`
downloads and deletes per model rather than keeping every model's weights
around. `--keep-weights` is the one exception, and it exists because issue
#227 made this a three-collection run: the same model then goes over
`emb-run-leyes`, `emb-run-reglamentos` and `emb-run-lineamientos` back to
back, and deleting the weights between them would re-download them twice
(~8 GB each for the 4B). Pass it for every collection but the last. `cemieredes` has three A100s and each `sbatch` job asks for one GPU,
so Slurm runs up to three shards in parallel by itself; nothing here manages
that beyond submitting every pending shard at once.

**The wait is chunked, the same way `submit_umap.py` chunks its own**
(issue #256): a `claude -p` session driving this has a per-command timeout,
and ending a turn to wait for a background process kills the supervisor
along with the session. `--max-wait-minutes N` (0 = block until every
pending shard finishes, the pre-#256 behaviour) returns exit status 75 while
shard jobs remain queued or running, and the caller simply calls this again
-- the state lives in `runs/<model-slug>/jobs.json`, not in a process that
has to stay alive. `--report` prints the pending-shard count offline, no
Slurm at all.

    python scripts/embeddings/submit_jobs.py --work-dir ~/emb-run \\
        --model Qwen/Qwen3-Embedding-0.6B
    python scripts/embeddings/submit_jobs.py --work-dir ~/emb-run \\
        --model Qwen/Qwen3-Embedding-0.6B --max-wait-minutes 9   # returns 75, call again
    python scripts/embeddings/submit_jobs.py --work-dir ~/emb-run \\
        --model Qwen/Qwen3-Embedding-0.6B --report
    python scripts/embeddings/submit_jobs.py --work-dir ~/emb-run \\
        --model Qwen/Qwen3-Embedding-0.6B --dry-run
    python scripts/embeddings/submit_jobs.py --work-dir emb-run-leyes \\
        --model Qwen/Qwen3-Embedding-4B --keep-weights   # more collections follow
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
from pathlib import Path

from _atomic import atomic_write_text
from encode_shard import model_slug

SUBMIT_SH = Path(__file__).with_name("submit.sh")
ENCODE_SHARD = Path(__file__).with_name("encode_shard.py")

#: Exit status meaning "this chunk elapsed, shard jobs are still running" --
#: 75 is `EX_TEMPFAIL`, i.e. "try again", exactly what the caller does
#: (mirrors `submit_umap.EXIT_STILL_RUNNING`).
EXIT_STILL_RUNNING = 75


def run(dry_run: bool, cmd: list[str], **kwargs):
    print(" ".join(str(c) for c in cmd))
    if dry_run:
        return None
    return subprocess.run(cmd, check=True, **kwargs)


def pending_shards(work_dir: Path, model: str) -> list[int]:
    plan = json.loads((work_dir / "shards.json").read_text(encoding="utf-8"))
    run_dir = work_dir / "runs" / model_slug(model)
    pending = []
    for shard in plan["shards"]:
        if not (run_dir / f"shard-{shard['index']:04d}.done").exists():
            pending.append(shard["index"])
    return pending


def submit_shard(
    work_dir: Path, model: str, shard: int, batch_size: int, max_batch_tokens: int,
    attn_implementation: str | None, dry_run: bool,
) -> str | None:
    cmd = [
        "sbatch", "--parsable", str(SUBMIT_SH), str(ENCODE_SHARD),
        "--work-dir", str(work_dir), "--model", model, "--shard", str(shard),
        "--batch-size", str(batch_size), "--max-batch-tokens", str(max_batch_tokens),
    ]
    if attn_implementation:
        cmd += ["--attn-implementation", attn_implementation]
    result = run(dry_run, cmd, capture_output=True, text=True)
    if dry_run or result is None:
        return None
    return result.stdout.strip()


def jobs_json_path(work_dir: Path, model: str) -> Path:
    return work_dir / "runs" / model_slug(model) / "jobs.json"


def submit(
    work_dir: Path, model: str, pending: list[int], *, batch_size: int, max_batch_tokens: int,
    attn_implementation: str | None = None, attempt: int, dry_run: bool = False, log=print,
) -> dict:
    """Submit every shard index in `pending` and record the attempt's job ids
    in `runs/<model-slug>/jobs.json` -- the state `wait()` resumes from
    across separate process invocations."""
    job_ids = [
        job_id
        for shard in pending
        if (job_id := submit_shard(
            work_dir, model, shard, batch_size, max_batch_tokens, attn_implementation, dry_run,
        ))
    ]
    state = {
        "model": model, "attempt": attempt, "submitted_at": time.time(), "job_ids": job_ids,
    }
    if not dry_run:
        path = jobs_json_path(work_dir, model)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(state, indent=2, ensure_ascii=False) + "\n")
    log(f"attempt {attempt}: submitted {len(job_ids)} job(s) for {len(pending)} pending shard(s)")
    return state


def queued_jobs(job_ids: list[str]) -> set[str]:
    """Which of `job_ids` `squeue` still knows about (mirrors
    `submit_umap.queued_jobs`): a non-zero status from the batched query
    falls back to asking about each id on its own, so a `squeue` hiccup is
    never read as "every job finished"."""
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


def wait(
    work_dir: Path, model: str, *, poll: int = 60, max_wait_minutes: float = 0.0,
    sleep=time.sleep, log=print,
) -> tuple[str, list[str]]:
    """Poll `squeue` for the attempt recorded in `jobs.json` until nothing
    remains, or until `max_wait_minutes` elapses (0 = no ceiling, blocks
    until every job leaves the queue -- the pre-#256 behaviour).

    Returns `("finished", [])` or `("still running", <job ids still
    queued>)` -- there is no `"timed out"`/`scancel` outcome here, unlike
    `submit_umap.wait`: a shard job that overruns is handled by
    `submit_jobs.py`'s own retry loop, not by a hard ceiling.
    """
    state = json.loads(jobs_json_path(work_dir, model).read_text(encoding="utf-8"))
    job_ids = state["job_ids"]
    chunk_deadline = time.time() + max_wait_minutes * 60 if max_wait_minutes else None
    while True:
        remaining = queued_jobs(job_ids)
        if not remaining:
            log("every shard job has left the queue")
            return "finished", []
        elapsed = (time.time() - state["submitted_at"]) / 60
        log(f"{len(remaining)} shard job(s) still queued or running after {elapsed:.1f} min")
        if chunk_deadline is not None and time.time() > chunk_deadline:
            return "still running", sorted(remaining)
        sleep(poll)


def hf_download(model: str, dry_run: bool) -> None:
    run(dry_run, ["hf", "download", model])


def hf_cache_dir(model: str) -> Path:
    return Path.home() / ".cache" / "huggingface" / "hub" / ("models--" + model.replace("/", "--"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--batch-size", type=int, default=32,
                         help="Upper bound on a batch's row count; --max-batch-tokens is "
                              "what actually governs memory (issue #256).")
    parser.add_argument("--max-batch-tokens", type=int, default=20000,
                         help="Forwarded to encode_shard.py -- the padded-token budget per "
                              "batch (issue #256). The smoke test (step 7) measures a real "
                              "value per model.")
    parser.add_argument("--attn-implementation", default=None,
                         help="Forwarded to encode_shard.py, e.g. 'sdpa' -- only set when the "
                              "smoke test found the default attention backend OOMs.")
    parser.add_argument("--poll-interval", type=int, default=60)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--max-wait-minutes", type=float, default=0.0,
                         help="Bound one wait chunk: return exit status 75 after this many "
                              "minutes if shard jobs remain (0 = no chunk, wait until every "
                              "pending shard finishes -- the pre-#256 behaviour).")
    parser.add_argument("--report", action="store_true",
                         help="Print the pending-shard count for this model and exit -- no "
                              "Slurm, no download.")
    parser.add_argument("--keep-weights", action="store_true",
                         help="Do not delete the model's weights when this run finishes -- "
                              "the same model still has another collection's work directory "
                              "to go over (issue #227). Pass it for every collection but "
                              "the last.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if args.report:
        pending = pending_shards(args.work_dir, args.model)
        print(json.dumps({"model": args.model, "pending": pending}, indent=2))
        return 0 if not pending else 1

    hf_download(args.model, args.dry_run)

    jobs_json = jobs_json_path(args.work_dir, args.model)
    # Resume a chunked wait left over from an earlier, timed-out invocation
    # before ever looking at pending_shards() again -- the jobs it is
    # waiting on are still the truth about what is pending.
    if not args.dry_run and jobs_json.exists():
        outcome, still_queued = wait(
            args.work_dir, args.model, poll=args.poll_interval,
            max_wait_minutes=args.max_wait_minutes,
        )
        if outcome == "still running":
            print(f"{len(still_queued)} shard job(s) still queued or running -- call again")
            return EXIT_STILL_RUNNING
        jobs_json.unlink(missing_ok=True)

    attempt = 0
    failed: list[int] = []
    while True:
        pending = pending_shards(args.work_dir, args.model)
        if not pending:
            break
        attempt += 1
        if attempt > args.max_attempts:
            failed = pending
            break
        print(f"attempt {attempt}/{args.max_attempts}: {len(pending)} pending shard(s)")
        submit(
            args.work_dir, args.model, pending, batch_size=args.batch_size,
            max_batch_tokens=args.max_batch_tokens, attn_implementation=args.attn_implementation,
            attempt=attempt, dry_run=args.dry_run,
        )
        if args.dry_run:
            break
        outcome, still_queued = wait(
            args.work_dir, args.model, poll=args.poll_interval,
            max_wait_minutes=args.max_wait_minutes,
        )
        if outcome == "still running":
            print(f"{len(still_queued)} shard job(s) still queued or running -- call again")
            return EXIT_STILL_RUNNING
        jobs_json.unlink(missing_ok=True)

    if not args.dry_run and not args.keep_weights:
        cache_dir = hf_cache_dir(args.model)
        if cache_dir.exists():
            print(f"rm -rf {cache_dir}")
            shutil.rmtree(cache_dir)

    if not args.dry_run and not failed:
        failed = pending_shards(args.work_dir, args.model)

    done = [] if args.dry_run else [
        s for s in json.loads((args.work_dir / "shards.json").read_text(encoding="utf-8"))["shards"]
        if s["index"] not in failed
    ]
    print(f"done: {len(done)} shard(s); failed: {failed}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())

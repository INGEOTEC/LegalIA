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

    python scripts/embeddings/submit_jobs.py --work-dir ~/emb-run \\
        --model Qwen/Qwen3-Embedding-0.6B
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

from encode_shard import model_slug

SUBMIT_SH = Path(__file__).with_name("submit.sh")
ENCODE_SHARD = Path(__file__).with_name("encode_shard.py")


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


def submit_shard(work_dir: Path, model: str, shard: int, batch_size: int, dry_run: bool) -> str | None:
    cmd = [
        "sbatch", "--parsable", str(SUBMIT_SH), str(ENCODE_SHARD),
        "--work-dir", str(work_dir), "--model", model, "--shard", str(shard),
        "--batch-size", str(batch_size),
    ]
    result = run(dry_run, cmd, capture_output=True, text=True)
    if dry_run or result is None:
        return None
    return result.stdout.strip()


def wait_for(job_ids: list[str], poll_interval: int, dry_run: bool) -> None:
    if dry_run or not job_ids:
        return
    remaining = set(job_ids)
    while remaining:
        time.sleep(poll_interval)
        result = subprocess.run(
            ["squeue", "--noheader", "--jobs", ",".join(remaining), "--format=%i"],
            capture_output=True, text=True,
        )
        still_running = set(result.stdout.split())
        remaining &= still_running


def hf_download(model: str, dry_run: bool) -> None:
    run(dry_run, ["hf", "download", model])


def hf_cache_dir(model: str) -> Path:
    return Path.home() / ".cache" / "huggingface" / "hub" / ("models--" + model.replace("/", "--"))


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--poll-interval", type=int, default=60)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--keep-weights", action="store_true",
                         help="Do not delete the model's weights when this run finishes -- "
                              "the same model still has another collection's work directory "
                              "to go over (issue #227). Pass it for every collection but "
                              "the last.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    hf_download(args.model, args.dry_run)

    attempt = 1
    failed: list[int] = []
    while True:
        pending = pending_shards(args.work_dir, args.model)
        if not pending:
            break
        print(f"attempt {attempt}/{args.max_attempts}: {len(pending)} pending shard(s)")
        job_ids = [
            job_id
            for shard in pending
            if (job_id := submit_shard(args.work_dir, args.model, shard, args.batch_size, args.dry_run))
        ]
        wait_for(job_ids, args.poll_interval, args.dry_run)
        if args.dry_run:
            break
        if attempt >= args.max_attempts:
            failed = pending_shards(args.work_dir, args.model)
            break
        attempt += 1

    if not args.dry_run and not args.keep_weights:
        cache_dir = hf_cache_dir(args.model)
        if cache_dir.exists():
            print(f"rm -rf {cache_dir}")
            shutil.rmtree(cache_dir)

    done = [] if args.dry_run else [
        s for s in json.loads((args.work_dir / "shards.json").read_text(encoding="utf-8"))["shards"]
        if s["index"] not in failed
    ]
    print(f"done: {len(done)} shard(s); failed: {failed}")


if __name__ == "__main__":
    main()

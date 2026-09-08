"""done / pending / failed, for one model's shards under `--work-dir`
(issue #218 Fase 2) -- and the command that relaunches only what is missing.

    python scripts/embeddings/status.py --work-dir ~/emb-run \\
        --model Qwen/Qwen3-Embedding-0.6B
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from encode_shard import model_slug


def shard_status(work_dir: Path, model: str) -> dict[str, list[int]]:
    plan = json.loads((work_dir / "shards.json").read_text(encoding="utf-8"))
    run_dir = work_dir / "runs" / model_slug(model)
    done, failed, pending = [], [], []
    for shard in plan["shards"]:
        index = shard["index"]
        if (run_dir / f"shard-{index:04d}.done").exists():
            done.append(index)
        elif (run_dir / f"shard-{index:04d}.failed").exists():
            failed.append(index)
        else:
            pending.append(index)
    return {"done": done, "failed": failed, "pending": pending}


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--model", required=True)
    args = parser.parse_args(argv)

    status = shard_status(args.work_dir, args.model)
    print(f"done: {len(status['done'])}  pending: {len(status['pending'])}  "
          f"failed: {len(status['failed'])}")
    missing = status["pending"] + status["failed"]
    if missing:
        print(
            "to relaunch only what is missing:\n"
            f"  python scripts/embeddings/submit_jobs.py --work-dir {args.work_dir} "
            f"--model {args.model}"
        )
        print(f"  (shards: {sorted(missing)})")


if __name__ == "__main__":
    main()

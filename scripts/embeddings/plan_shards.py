"""`units.parquet` -> `shards.json`: dedup by `text_sha1`, sort by estimated
token length, balance into shards with roughly equal *token* counts (issue
#218 Fase 2).

Membership is fixed once here and never mutated: `shards.json` is hashed
into the merge manifest, so a changed corpus means a new plan, never a
patched one. Balancing by tokens rather than by row count is deliberate —
equal row counts would leave one shard holding every 25,000-character
transitorio alone, worth as much GPU time by itself as the rest of a shard
combined.

`--against runs/<model>/` is the incremental path: hashes already embedded
(a `.done`-marked shard under that directory) are dropped before planning, so
after a reform only the new units get shipped to the GPU at all.

    python scripts/embeddings/plan_shards.py --work-dir ~/emb-run
    python scripts/embeddings/plan_shards.py --work-dir ~/emb-run --num-shards 12
    python scripts/embeddings/plan_shards.py --work-dir ~/emb-run --against ~/emb-run/runs/qwen3-0.6b
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pyarrow.parquet as pq

from _atomic import atomic_write_text

#: Measured against the real Qwen3-Embedding tokenizer over a 4,000-unit
#: sample of the cached corpus (issue #218's own body) -- not a
#: multilingual rule of thumb, but still an estimate: it is only ever used to
#: *balance* shards, never to decide whether a text fits the model's own
#: context window.
CHARS_PER_TOKEN = 3.57

DEFAULT_NUM_SHARDS = 12


def distinct_texts(units_parquet: Path) -> list[tuple[str, str]]:
    """`(text_sha1, text)`, one per distinct hash in `units_parquet` -- the
    row count of every vector table this run eventually produces."""
    table = pq.read_table(units_parquet, columns=["text_sha1", "text"])
    seen: dict[str, str] = {}
    for sha1, text in zip(table.column("text_sha1").to_pylist(), table.column("text").to_pylist()):
        seen.setdefault(sha1, text)
    return list(seen.items())


def already_embedded(run_dir: Path) -> set[str]:
    """Every `text_sha1` a previous run of `run_dir` already has a
    `.done`-marked shard for -- what `--against` subtracts before planning."""
    done: set[str] = set()
    if not run_dir.exists():
        return done
    for marker in sorted(run_dir.glob("shard-*.done")):
        shard_parquet = marker.with_suffix(".parquet")
        if not shard_parquet.exists():
            continue
        table = pq.read_table(shard_parquet, columns=["text_sha1"])
        done.update(table.column("text_sha1").to_pylist())
    return done


def balance_shards(texts: list[tuple[str, str]], num_shards: int) -> list[dict]:
    """`texts`, longest first, greedily assigned to whichever shard currently
    holds the fewest estimated tokens -- the longest-processing-time
    heuristic, which is what keeps a dozen shards each a couple of minutes of
    GPU time instead of one holding every outlier."""
    sized = [
        (sha1, text, max(1, round(len(text) / CHARS_PER_TOKEN)))
        for sha1, text in texts
    ]
    sized.sort(key=lambda row: row[2], reverse=True)

    shards: list[dict] = [{"index": i, "text_sha1": [], "tokens_estimate": 0} for i in range(num_shards)]
    for sha1, _text, tokens in sized:
        target = min(shards, key=lambda s: s["tokens_estimate"])
        target["text_sha1"].append(sha1)
        target["tokens_estimate"] += tokens
    return [s for s in shards if s["text_sha1"]]


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--num-shards", type=int, default=DEFAULT_NUM_SHARDS)
    parser.add_argument("--against", type=Path, default=None,
                         help="A runs/<model>/ directory: drop hashes it already has a "
                              ".done shard for, for incremental re-embedding.")
    args = parser.parse_args(argv)

    units_parquet = args.work_dir / "units.parquet"
    texts = distinct_texts(units_parquet)

    excluded: set[str] = set()
    if args.against is not None:
        excluded = already_embedded(args.against)
        texts = [(sha1, text) for sha1, text in texts if sha1 not in excluded]

    shards = balance_shards(texts, args.num_shards)

    units_hash = hashlib.sha256(units_parquet.read_bytes()).hexdigest()
    plan = {
        "chars_per_token": CHARS_PER_TOKEN,
        "num_shards": len(shards),
        "units_parquet_sha256": units_hash,
        "total_texts": len(texts),
        "excluded_already_embedded": len(excluded),
        "shards": shards,
    }
    atomic_write_text(args.work_dir / "shards.json", json.dumps(plan, indent=2) + "\n")
    print(f"{len(shards)} shards, {len(texts)} texts "
          f"({len(excluded)} already embedded excluded)")


if __name__ == "__main__":
    main()

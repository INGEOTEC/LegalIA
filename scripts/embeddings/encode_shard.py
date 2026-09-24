"""One shard of `plan_shards.py`'s plan -> `shard-XXXX.parquet` (+ `.done`)
(issue #218 Fase 2).

The representation is one `transformers.pipeline` call and nothing around it
— no hand-rolled tokenization, no manual dtype juggling, no normalization of
any kind (the pattern `../Chimalli-overleaf/juez.py` already runs on this
same cluster for a judge model, not an embedding one, but the pipeline usage
is the same shape). Two things are set explicitly because they were measured
on `cemieredes`, never assumed:

- **`padding_side = "left"` is not optional.** The pooling is last-token;
  with the tokenizer's default right padding, the "last" hidden state of a
  short text in a batch is a padding token's — cosine against a one-at-a-time
  encode measured **0.39-0.77** with right padding, **0.9998** with left.
- **The cast to `float16` is the only transform applied to the output.**
  Parquet has no bfloat16, and this corpus' vectors (norms ~100-111, values
  under 15) round-trip through fp16 at cosine **1.000000**. No L2
  normalization, no quantization, no Matryoshka truncation — a reader who
  wants any of those applies it at read time, from data that still says what
  the model actually produced.

A shard is keyed by `text_sha1` alone: this script never learns which law a
text came from, which is what lets a text four laws share be embedded once
(`merge_shards.py` is where the law comes back).

Idempotent by construction: a `.done` marker already present and valid short-
circuits the whole run before the model is even loaded, so re-running the
entire shard set costs nothing once it is complete. A shard that raises
writes `shard-XXXX.failed` (the traceback, the row range) instead of taking
the run down — `status.py` reports it, and resubmitting only that shard is
the normal way to recover.

    sbatch scripts/embeddings/submit.sh scripts/embeddings/encode_shard.py \\
        --work-dir ~/emb-run --model Qwen/Qwen3-Embedding-0.6B --shard 0

    # the CPU smoke test (issue #218's own): no cluster, no Slurm
    python scripts/embeddings/encode_shard.py --work-dir ~/emb-run \\
        --model Qwen/Qwen3-Embedding-0.6B --shard 0 --device cpu --limit 512
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import traceback
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from _atomic import atomic_write_bytes, atomic_write_text
from plan_shards import distinct_texts

#: `Qwen/Qwen3-Embedding-0.6B` -> `qwen3-0.6b` -- the slug every published
#: filename keys off (`vectors-<slug>-<model>-<K>.parquet`), derived rather
#: than hand-maintained so a third model needs no edit here.
def model_slug(model: str) -> str:
    tail = model.rsplit("/", 1)[-1]
    tail = re.sub(r"[Ee]mbedding-?", "", tail)
    return re.sub(r"[^A-Za-z0-9.]+", "-", tail).strip("-").lower()


def build_pipeline(model: str, device: str, attn_implementation: str | None = None):
    """The `transformers` pipeline, configured exactly as issue #218 Fase 2
    specifies -- split out from `main` so a test can substitute a fake one
    without importing `torch`/`transformers` at all.

    `attn_implementation` (issue #256) is only ever set when the smoke test
    (step 7) measures that the default attention backend OOMs on the
    longest sequences -- `"sdpa"` is the memory-efficient fallback named in
    the issue. `None` leaves the model's own default untouched.
    """
    from transformers import pipeline

    kwargs = {"dtype": "auto"}
    if device == "auto":
        kwargs["device_map"] = "auto"
    else:
        kwargs["device"] = device
    if attn_implementation:
        kwargs["model_kwargs"] = {"attn_implementation": attn_implementation}
    pipe = pipeline("feature-extraction", model=model, **kwargs)
    pipe.tokenizer.padding_side = "left"
    return pipe


def max_position_embeddings(pipe) -> int:
    """The model's own context window, read off its config rather than
    hard-coded (issue #256) -- 32,768 for the 0.6B, 40,960 for the 4B, but
    this reads whatever a third model's config actually says."""
    value = getattr(pipe.model.config, "max_position_embeddings", None)
    if not value:
        raise SystemExit(
            f"{pipe.model.config.__class__.__name__} has no max_position_embeddings"
        )
    return int(value)


def build_batches(
    token_counts: list[int], *, max_batch_tokens: int, batch_size: int
) -> list[list[int]]:
    """`token_counts`' indices, grouped in ascending token-count order into
    batches whose *padded* token budget (the batch's own longest text times
    its row count) never exceeds `max_batch_tokens`, and whose row count
    never exceeds `batch_size` (issue #256) -- `batch_size` is kept as the
    upper bound on rows, `max_batch_tokens` is the new budget that actually
    governs memory.

    A text whose own token count already exceeds `max_batch_tokens` goes
    alone in its own batch rather than blocking every other text from ever
    joining a batch with it.
    """
    order = sorted(range(len(token_counts)), key=lambda i: token_counts[i])
    batches: list[list[int]] = []
    current: list[int] = []
    current_max = 0
    for i in order:
        count = token_counts[i]
        candidate_max = max(current_max, count)
        candidate_rows = len(current) + 1
        if current and (
            candidate_max * candidate_rows > max_batch_tokens or candidate_rows > batch_size
        ):
            batches.append(current)
            current = []
            current_max = 0
        current.append(i)
        current_max = max(current_max, count)
        if current_max > max_batch_tokens:
            # Alone already (a fresh batch, or the check above would have
            # flushed first) -- over budget on its own, nothing to wait for.
            batches.append(current)
            current = []
            current_max = 0
    if current:
        batches.append(current)
    return batches


def token_counts_and_truncated(tokenizer, texts: list[str], max_length: int) -> tuple[list[int], list[int]]:
    """Each text's real token count from `tokenizer` (no truncation), and
    the indices whose count exceeds `max_length` -- pulled out of
    `encode_texts` as a pure function (issue #256) so a test can exercise
    the truncation accounting with a fake tokenizer, no `torch` import
    required (this file's own design: see its module docstring)."""
    token_counts = [len(tokenizer(t)["input_ids"]) for t in texts]
    truncated = [i for i, n in enumerate(token_counts) if n > max_length]
    return token_counts, truncated


def encode_texts(
    pipe, texts: list[str], *, max_length: int, max_batch_tokens: int, batch_size: int
):
    """`texts`, embedded in document order, as one `(len(texts), K)` fp16
    array -- last-token pooling, no normalization, exactly what the pipeline
    produced besides the dtype cast.

    Returns `(vectors, token_counts, truncated, max_tokens_embedded)`
    (issue #256): `token_counts` is each text's real token count from the
    model's own tokenizer (no truncation), `truncated` the indices whose
    count exceeds `max_length` -- windowed, not split or mean-pooled, per
    the issue's own decision -- and `max_tokens_embedded` the longest
    sequence actually handed to the model, post-truncation.
    """
    import torch

    token_counts, truncated = token_counts_and_truncated(pipe.tokenizer, texts, max_length)

    vectors: list[np.ndarray] = [None] * len(texts)  # type: ignore[list-item]
    max_tokens_embedded = 0
    for batch in build_batches(token_counts, max_batch_tokens=max_batch_tokens, batch_size=batch_size):
        batch_texts = [texts[i] for i in batch]
        outputs = pipe(
            batch_texts, batch_size=len(batch_texts), return_tensors=True,
            truncation=True, max_length=max_length,
        )
        for i, out in zip(batch, outputs):
            vectors[i] = out[0][-1].to(torch.float16).cpu().numpy()
            max_tokens_embedded = max(max_tokens_embedded, min(token_counts[i], max_length))
    return np.stack(vectors), token_counts, truncated, max_tokens_embedded


def _write_shard_parquet(path: Path, text_sha1: list[str], vectors: np.ndarray) -> None:
    k = vectors.shape[1]
    flat = pa.array(vectors.reshape(-1), type=pa.float16())
    vector_col = pa.FixedSizeListArray.from_arrays(flat, k)
    table = pa.table({"text_sha1": pa.array(text_sha1), "vector": vector_col})
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink, compression="zstd")
    atomic_write_bytes(path, sink.getvalue().to_pybytes())


def _done_marker_valid(marker: Path, parquet: Path) -> bool:
    if not marker.exists() or not parquet.exists():
        return False
    try:
        info = json.loads(marker.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    sha256 = hashlib.sha256(parquet.read_bytes()).hexdigest()
    if sha256 != info.get("sha256"):
        return False
    rows = pq.read_metadata(parquet).num_rows
    return rows == info.get("rows")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--shard", type=int, required=True)
    parser.add_argument("--device", default="auto", help='"auto", "cpu", or a CUDA device string')
    parser.add_argument("--batch-size", type=int, default=32,
                         help="Upper bound on a batch's row count -- --max-batch-tokens is "
                              "what actually governs memory (issue #256).")
    parser.add_argument("--max-batch-tokens", type=int, default=20000,
                         help="Padded-token budget per batch: the batch's own longest text's "
                              "token count times its row count (issue #256). The default "
                              "approximates the pre-#256 batch-size=32 behaviour; the smoke "
                              "test (step 7) measures a real one per model.")
    parser.add_argument("--attn-implementation", default=None,
                         help="Override the model's own attention backend, e.g. 'sdpa' -- only "
                              "set when the smoke test found the default OOMs on the longest "
                              "sequences (issue #256). Never used to truncate below the "
                              "model's own window.")
    parser.add_argument("--limit", type=int, default=None,
                         help="Only the shard's first N texts -- the CPU smoke test's own flag")
    args = parser.parse_args(argv)

    slug = model_slug(args.model)
    run_dir = args.work_dir / "runs" / slug
    run_dir.mkdir(parents=True, exist_ok=True)
    stem = run_dir / f"shard-{args.shard:04d}"
    parquet_path = stem.with_suffix(".parquet")
    done_path = stem.with_suffix(".done")
    failed_path = stem.with_suffix(".failed")

    if _done_marker_valid(done_path, parquet_path):
        print(f"shard {args.shard}: already done, nothing to do")
        return 0

    plan = json.loads((args.work_dir / "shards.json").read_text(encoding="utf-8"))
    shard = next((s for s in plan["shards"] if s["index"] == args.shard), None)
    if shard is None:
        raise SystemExit(f"no shard {args.shard} in {args.work_dir / 'shards.json'}")

    text_sha1 = shard["text_sha1"]
    if args.limit is not None:
        text_sha1 = text_sha1[: args.limit]
    by_hash = dict(distinct_texts(args.work_dir / "units.parquet"))
    texts = [by_hash[h] for h in text_sha1]

    started = time.time()
    try:
        pipe = build_pipeline(args.model, args.device, args.attn_implementation)
        max_length = max_position_embeddings(pipe)
        vectors, token_counts, truncated, max_tokens_embedded = encode_texts(
            pipe, texts, max_length=max_length, max_batch_tokens=args.max_batch_tokens,
            batch_size=args.batch_size,
        )
        _write_shard_parquet(parquet_path, text_sha1, vectors)
        marker = {
            "rows": len(text_sha1),
            "sha256": hashlib.sha256(parquet_path.read_bytes()).hexdigest(),
            "k": int(vectors.shape[1]),
            "model": args.model,
            "seconds": round(time.time() - started, 1),
            "max_length": max_length,
            "max_tokens_embedded": max_tokens_embedded,
            "texts_truncated": [
                {"text_sha1": text_sha1[i], "original_tokens": token_counts[i]}
                for i in truncated
            ],
        }
        atomic_write_text(done_path, json.dumps(marker, indent=2) + "\n")
        failed_path.unlink(missing_ok=True)
        print(
            f"shard {args.shard}: {len(text_sha1)} rows, K={marker['k']}, "
            f"{len(truncated)} truncated"
        )
        return 0
    except Exception:  # noqa: BLE001 - a shard failing must not take the run down
        failure = {
            "shard": args.shard,
            "rows_attempted": len(text_sha1),
            "traceback": traceback.format_exc(),
        }
        atomic_write_text(failed_path, json.dumps(failure, indent=2) + "\n")
        print(f"shard {args.shard}: FAILED, see {failed_path}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

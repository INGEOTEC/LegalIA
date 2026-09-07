"""`runs/<model>/shard-*.parquet` + `units.parquet` -> one vector file per
law, plus one for the text they share, plus the manifest (issue #218 Fase
2 -- the shape Fase 3 eventually publishes).

A shard is keyed by `text_sha1` alone and never learns which law a text came
from; this is where the law comes back. `units.parquet`'s own
`text_sha1 -> slug` map says, for every distinct text, which law(s) contain
it: exactly one law sends that text's vector to `vectors-<slug>`, more than
one sends it to `vectors-shared` instead — the identical transitorios #217
predicted are exactly what ends up there. Every law gets a file even when it
turns out to contain no text of its own (its vectors are entirely in the
shared file), so a reader never has to special-case a missing asset.

    python scripts/embeddings/merge_shards.py --work-dir ~/emb-run \\
        --model Qwen/Qwen3-Embedding-0.6B
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from _atomic import atomic_write_bytes, atomic_write_text
from encode_shard import model_slug


def _done_shards(run_dir: Path) -> list[Path]:
    parquets = []
    for marker in sorted(run_dir.glob("shard-*.done")):
        parquet = marker.with_suffix(".parquet")
        if parquet.exists():
            parquets.append(parquet)
    return parquets


def load_vectors(run_dir: Path) -> tuple[dict[str, list], int]:
    """`text_sha1 -> vector` over every shard with a valid `.done` marker,
    and the embedding dimension `K` read off the data itself."""
    vectors: dict[str, list] = {}
    k = 0
    for parquet in _done_shards(run_dir):
        table = pq.read_table(parquet)
        if k == 0 and table.num_rows:
            k = table.schema.field("vector").type.list_size
        for sha1, vector in zip(table.column("text_sha1").to_pylist(), table.column("vector").to_pylist()):
            vectors[sha1] = vector
    return vectors, k


def slugs_by_hash(units_parquet: Path) -> dict[str, set[str]]:
    table = pq.read_table(units_parquet, columns=["slug", "text_sha1"])
    by_hash: dict[str, set[str]] = defaultdict(set)
    for slug, sha1 in zip(table.column("slug").to_pylist(), table.column("text_sha1").to_pylist()):
        by_hash[sha1].add(slug)
    return by_hash


def _write_vector_table(path: Path, rows: list[tuple[str, list]], k: int) -> None:
    if rows:
        flat = pa.array([x for _sha1, vec in rows for x in vec], type=pa.float16())
        vector_col = pa.FixedSizeListArray.from_arrays(flat, k)
        table = pa.table({"text_sha1": pa.array([sha1 for sha1, _vec in rows]), "vector": vector_col})
    else:
        table = pa.table({
            "text_sha1": pa.array([], type=pa.string()),
            "vector": pa.array([], type=pa.list_(pa.float16(), k if k else 0)),
        })
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink, compression="zstd")
    atomic_write_bytes(path, sink.getvalue().to_pybytes())


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--model", required=True)
    args = parser.parse_args(argv)

    slug = model_slug(args.model)
    run_dir = args.work_dir / "runs" / slug
    vectors_dir = run_dir / "vectors"
    vectors_dir.mkdir(parents=True, exist_ok=True)

    vectors, k = load_vectors(run_dir)
    by_hash = slugs_by_hash(args.work_dir / "units.parquet")
    all_slugs = sorted({s for slugs in by_hash.values() for s in slugs})

    per_slug: dict[str, list[tuple[str, list]]] = {law: [] for law in all_slugs}
    shared: list[tuple[str, list]] = []
    missing = 0
    for sha1, slugs in by_hash.items():
        vector = vectors.get(sha1)
        if vector is None:
            missing += 1
            continue
        if len(slugs) > 1:
            shared.append((sha1, vector))
        else:
            per_slug[next(iter(slugs))].append((sha1, vector))

    for law in all_slugs:
        _write_vector_table(vectors_dir / f"vectors-{law}-{slug}-{k}.parquet", per_slug[law], k)
    _write_vector_table(vectors_dir / f"vectors-shared-{slug}-{k}.parquet", shared, k)

    manifest = {
        "model": args.model,
        "k": k,
        "dtype": "float16",
        "pooling": "last-token",
        "padding_side": "left",
        "output_transform": "bfloat16 -> float16, no normalization or quantization",
        "laws": len(all_slugs),
        "distinct_texts": len(by_hash),
        "shared_rows": len(shared),
        "per_law_rows": sum(len(rows) for rows in per_slug.values()),
        "missing_vectors": missing,
    }
    atomic_write_text(run_dir / "manifest.json", json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()

"""`runs/<model>/shard-*.parquet` + `units.parquet` -> one vector file per
instrument, plus one for the text they share, plus the manifest (issue #218
Fase 2, generalized to the three collections by #227 Fase 3 -- the shape
#227's Fase 4 publishes).

A shard is keyed by `text_sha1` alone and never learns which instrument a
text came from; this is where the instrument comes back. `units.parquet`'s
own `text_sha1 -> clave` map says, for every distinct text, which
instrument(s) contain it: exactly one sends that text's vector to
`vectors-<clave>`, more than one sends it to `vectors-shared` instead — the
identical transitorios #217 predicted are exactly what ends up there. Every
instrument gets a file even when it turns out to contain no text of its own
(its vectors are entirely in the shared file), so a reader never has to
special-case a missing asset.

`clave` is a law's slug and the `id_ordenamiento` of a reglamento or a
lineamiento (#227's decision 7), so the file name is the same shape for all
three collections and a reader never branches. Dedup is per collection and
never across them (#227's decision 8), which needs no code here at all: one
work directory holds one collection's `units.parquet`.

    python scripts/embeddings/merge_shards.py --work-dir emb-run-leyes \\
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


def claves_by_hash(units_parquet: Path) -> dict[str, set[str]]:
    """`text_sha1 -> {clave}` over `units_parquet` — which instrument(s) each
    distinct text belongs to."""
    table = pq.read_table(units_parquet, columns=["clave", "text_sha1"])
    by_hash: dict[str, set[str]] = defaultdict(set)
    for clave, sha1 in zip(table.column("clave").to_pylist(), table.column("text_sha1").to_pylist()):
        by_hash[sha1].add(clave)
    return by_hash


def coleccion_de(units_parquet: Path) -> str:
    """The one collection `units_parquet` holds — a work directory is built
    for exactly one (#227's decision 8), so the first row answers it."""
    table = pq.read_table(units_parquet, columns=["coleccion"])
    return table.column("coleccion")[0].as_py() if table.num_rows else "leyes"


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

    units_parquet = args.work_dir / "units.parquet"
    vectors, k = load_vectors(run_dir)
    by_hash = claves_by_hash(units_parquet)
    coleccion = coleccion_de(units_parquet)
    todas = sorted({c for claves in by_hash.values() for c in claves})

    por_clave: dict[str, list[tuple[str, list]]] = {clave: [] for clave in todas}
    shared: list[tuple[str, list]] = []
    missing = 0
    for sha1, claves in by_hash.items():
        vector = vectors.get(sha1)
        if vector is None:
            missing += 1
            continue
        if len(claves) > 1:
            shared.append((sha1, vector))
        else:
            por_clave[next(iter(claves))].append((sha1, vector))

    for clave in todas:
        _write_vector_table(
            vectors_dir / f"vectors-{clave}-{slug}-{k}.parquet", por_clave[clave], k
        )
    _write_vector_table(vectors_dir / f"vectors-shared-{slug}-{k}.parquet", shared, k)

    manifest = {
        "model": args.model,
        "coleccion": coleccion,
        "k": k,
        "dtype": "float16",
        "pooling": "last-token",
        "padding_side": "left",
        "output_transform": "bfloat16 -> float16, no normalization or quantization",
        "instruments": len(todas),
        "distinct_texts": len(by_hash),
        "shared_rows": len(shared),
        "per_instrument_rows": sum(len(rows) for rows in por_clave.values()),
        "missing_vectors": missing,
    }
    atomic_write_text(run_dir / "manifest.json", json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()

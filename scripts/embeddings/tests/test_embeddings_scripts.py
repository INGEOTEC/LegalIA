"""The pure-Python logic of `scripts/embeddings/` (issue #218 Fase 2),
against small synthetic data -- no network, no real model download, no Slurm.

Not part of `pytest packages/md2akn` (this directory is orchestration, not a
package): run directly with

    pytest scripts/embeddings/tests -q

`encode_shard`'s own model loading (`build_pipeline`) is never exercised here
-- it is monkeypatched out, so this file has no dependency on `torch`/
`transformers` actually working, only on `encode_shard.py` importing cleanly.
"""

import json
import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _atomic import atomic_write_bytes, atomic_write_text  # noqa: E402
import encode_shard  # noqa: E402
import merge_shards  # noqa: E402
import plan_shards  # noqa: E402
import status  # noqa: E402


# -- _atomic -------------------------------------------------------------- #

def test_atomic_write_leaves_no_partial_file(tmp_path):
    dest = tmp_path / "sub" / "file.txt"
    atomic_write_text(dest, "hello")
    assert dest.read_text() == "hello"
    assert not dest.with_name("file.txt.parcial").exists()


def test_atomic_write_overwrites(tmp_path):
    dest = tmp_path / "file.bin"
    atomic_write_bytes(dest, b"one")
    atomic_write_bytes(dest, b"two")
    assert dest.read_bytes() == b"two"


# -- encode_shard.model_slug ---------------------------------------------- #

@pytest.mark.parametrize("model, expected", [
    ("Qwen/Qwen3-Embedding-0.6B", "qwen3-0.6b"),
    ("Qwen/Qwen3-Embedding-4B", "qwen3-4b"),
])
def test_model_slug(model, expected):
    assert encode_shard.model_slug(model) == expected


# -- plan_shards ------------------------------------------------------------ #

def _write_units_parquet(path, rows):
    table = pa.Table.from_pylist(rows)
    pq.write_table(table, path)


def test_distinct_texts_dedups_by_hash(tmp_path):
    units = tmp_path / "units.parquet"
    _write_units_parquet(units, [
        {"text_sha1": "a", "text": "Uno"},
        {"text_sha1": "a", "text": "Uno"},
        {"text_sha1": "b", "text": "Dos"},
    ])
    texts = dict(plan_shards.distinct_texts(units))
    assert texts == {"a": "Uno", "b": "Dos"}


def test_balance_shards_spreads_the_longest_first():
    texts = [("a", "x" * 100), ("b", "x" * 10), ("c", "x" * 10), ("d", "x" * 10)]
    shards = plan_shards.balance_shards(texts, num_shards=2)
    assert len(shards) == 2
    # The 100-char text lands alone against the other three combined.
    sizes = sorted(len(s["text_sha1"]) for s in shards)
    assert sizes == [1, 3]


def test_balance_shards_drops_empty_shards():
    shards = plan_shards.balance_shards([("a", "x")], num_shards=5)
    assert len(shards) == 1


def test_already_embedded_reads_done_marked_shards_only(tmp_path):
    run_dir = tmp_path / "runs" / "qwen3-0.6b"
    run_dir.mkdir(parents=True)
    pq.write_table(pa.table({"text_sha1": ["a", "b"]}), run_dir / "shard-0000.parquet")
    (run_dir / "shard-0000.done").write_text("{}")
    # No .done for shard 1: its hashes must not count as already embedded.
    pq.write_table(pa.table({"text_sha1": ["c"]}), run_dir / "shard-0001.parquet")
    assert plan_shards.already_embedded(run_dir) == {"a", "b"}


# -- encode_shard: the shard file + done marker contract -------------------- #

def test_write_shard_parquet_and_done_marker_roundtrip(tmp_path):
    vectors = np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]], dtype=np.float16)
    parquet = tmp_path / "shard-0000.parquet"
    encode_shard._write_shard_parquet(parquet, ["a", "b"], vectors)

    table = pq.read_table(parquet)
    assert table.column("text_sha1").to_pylist() == ["a", "b"]
    assert table.schema.field("vector").type.list_size == 3

    done = tmp_path / "shard-0000.done"
    import hashlib
    marker = {"rows": 2, "sha256": hashlib.sha256(parquet.read_bytes()).hexdigest()}
    atomic_write_text(done, json.dumps(marker))
    assert encode_shard._done_marker_valid(done, parquet)


def test_done_marker_invalid_when_sha256_mismatches(tmp_path):
    vectors = np.zeros((1, 2), dtype=np.float16)
    parquet = tmp_path / "shard-0000.parquet"
    encode_shard._write_shard_parquet(parquet, ["a"], vectors)
    done = tmp_path / "shard-0000.done"
    atomic_write_text(done, json.dumps({"rows": 1, "sha256": "wrong"}))
    assert not encode_shard._done_marker_valid(done, parquet)


def test_main_skips_when_already_done(tmp_path, monkeypatch):
    work_dir = tmp_path
    _write_units_parquet(work_dir / "units.parquet", [{"text_sha1": "a", "text": "Uno"}])
    (work_dir / "shards.json").write_text(json.dumps({"shards": [{"index": 0, "text_sha1": ["a"]}]}))

    run_dir = work_dir / "runs" / "qwen3-0.6b"
    run_dir.mkdir(parents=True)
    vectors = np.zeros((1, 4), dtype=np.float16)
    encode_shard._write_shard_parquet(run_dir / "shard-0000.parquet", ["a"], vectors)
    import hashlib
    marker = {"rows": 1, "sha256": hashlib.sha256((run_dir / "shard-0000.parquet").read_bytes()).hexdigest()}
    (run_dir / "shard-0000.done").write_text(json.dumps(marker))

    def _boom(*args, **kwargs):
        raise AssertionError("build_pipeline must not be called when already done")

    monkeypatch.setattr(encode_shard, "build_pipeline", _boom)
    rc = encode_shard.main([
        "--work-dir", str(work_dir), "--model", "Qwen/Qwen3-Embedding-0.6B", "--shard", "0",
    ])
    assert rc == 0


def test_main_encodes_pending_shard_with_a_fake_pipeline(tmp_path, monkeypatch):
    work_dir = tmp_path
    _write_units_parquet(work_dir / "units.parquet", [
        {"text_sha1": "a", "text": "Uno"},
        {"text_sha1": "b", "text": "Dos, algo mas largo"},
    ])
    (work_dir / "shards.json").write_text(
        json.dumps({"shards": [{"index": 0, "text_sha1": ["a", "b"]}]})
    )

    monkeypatch.setattr(encode_shard, "build_pipeline", lambda model, device: object())

    def _fake_encode(pipe, texts, batch_size):
        return np.stack([np.full(4, float(len(t)), dtype=np.float16) for t in texts])

    monkeypatch.setattr(encode_shard, "encode_texts", _fake_encode)

    rc = encode_shard.main([
        "--work-dir", str(work_dir), "--model", "Qwen/Qwen3-Embedding-0.6B", "--shard", "0",
    ])
    assert rc == 0
    run_dir = work_dir / "runs" / "qwen3-0.6b"
    assert (run_dir / "shard-0000.done").exists()
    table = pq.read_table(run_dir / "shard-0000.parquet")
    assert sorted(table.column("text_sha1").to_pylist()) == ["a", "b"]


def test_main_writes_failed_marker_on_error(tmp_path, monkeypatch):
    work_dir = tmp_path
    _write_units_parquet(work_dir / "units.parquet", [{"text_sha1": "a", "text": "Uno"}])
    (work_dir / "shards.json").write_text(json.dumps({"shards": [{"index": 0, "text_sha1": ["a"]}]}))

    def _boom(model, device):
        raise RuntimeError("no GPU here")

    monkeypatch.setattr(encode_shard, "build_pipeline", _boom)
    rc = encode_shard.main([
        "--work-dir", str(work_dir), "--model", "Qwen/Qwen3-Embedding-0.6B", "--shard", "0",
    ])
    assert rc == 1
    failure = json.loads((work_dir / "runs" / "qwen3-0.6b" / "shard-0000.failed").read_text())
    assert "RuntimeError" in failure["traceback"]


# -- merge_shards ------------------------------------------------------------ #

def test_merge_shards_splits_shared_from_per_law(tmp_path, monkeypatch):
    work_dir = tmp_path
    # "shared" text is in both lft and lfd; "only-lft" is in lft alone.
    _write_units_parquet(work_dir / "units.parquet", [
        {"slug": "lft", "text_sha1": "shared", "text": "x"},
        {"slug": "lfd", "text_sha1": "shared", "text": "x"},
        {"slug": "lft", "text_sha1": "only-lft", "text": "y"},
        {"slug": "lfd", "text_sha1": "only-lfd", "text": "z"},
    ])
    run_dir = work_dir / "runs" / "qwen3-0.6b"
    run_dir.mkdir(parents=True)
    vectors = np.array([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]], dtype=np.float16)
    encode_shard._write_shard_parquet(
        run_dir / "shard-0000.parquet", ["shared", "only-lft", "only-lfd"], vectors,
    )
    import hashlib
    marker = {
        "rows": 3,
        "sha256": hashlib.sha256((run_dir / "shard-0000.parquet").read_bytes()).hexdigest(),
    }
    (run_dir / "shard-0000.done").write_text(json.dumps(marker))

    merge_shards.main(["--work-dir", str(work_dir), "--model", "Qwen/Qwen3-Embedding-0.6B"])

    vectors_dir = run_dir / "vectors"
    lft = pq.read_table(vectors_dir / "vectors-lft-qwen3-0.6b-2.parquet")
    lfd = pq.read_table(vectors_dir / "vectors-lfd-qwen3-0.6b-2.parquet")
    shared = pq.read_table(vectors_dir / "vectors-shared-qwen3-0.6b-2.parquet")

    assert lft.column("text_sha1").to_pylist() == ["only-lft"]
    assert lfd.column("text_sha1").to_pylist() == ["only-lfd"]
    assert shared.column("text_sha1").to_pylist() == ["shared"]

    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["laws"] == 2
    assert manifest["shared_rows"] == 1
    assert manifest["per_law_rows"] == 2


def test_merge_shards_writes_an_empty_file_for_a_law_with_no_own_text(tmp_path):
    work_dir = tmp_path
    _write_units_parquet(work_dir / "units.parquet", [
        {"slug": "lft", "text_sha1": "shared", "text": "x"},
        {"slug": "lfd", "text_sha1": "shared", "text": "x"},
    ])
    run_dir = work_dir / "runs" / "qwen3-0.6b"
    run_dir.mkdir(parents=True)
    vectors = np.array([[1.0, 2.0]], dtype=np.float16)
    encode_shard._write_shard_parquet(run_dir / "shard-0000.parquet", ["shared"], vectors)
    import hashlib
    marker = {
        "rows": 1,
        "sha256": hashlib.sha256((run_dir / "shard-0000.parquet").read_bytes()).hexdigest(),
    }
    (run_dir / "shard-0000.done").write_text(json.dumps(marker))

    merge_shards.main(["--work-dir", str(work_dir), "--model", "Qwen/Qwen3-Embedding-0.6B"])

    lft = pq.read_table(run_dir / "vectors" / "vectors-lft-qwen3-0.6b-2.parquet")
    assert lft.num_rows == 0


# -- status ------------------------------------------------------------------ #

def test_shard_status_classifies_done_pending_failed(tmp_path):
    work_dir = tmp_path
    (work_dir / "shards.json").write_text(json.dumps({
        "shards": [{"index": 0, "text_sha1": []}, {"index": 1, "text_sha1": []},
                   {"index": 2, "text_sha1": []}],
    }))
    run_dir = work_dir / "runs" / "qwen3-0.6b"
    run_dir.mkdir(parents=True)
    (run_dir / "shard-0000.done").write_text("{}")
    (run_dir / "shard-0001.failed").write_text("{}")

    result = status.shard_status(work_dir, "Qwen/Qwen3-Embedding-0.6B")
    assert result == {"done": [0], "failed": [1], "pending": [2]}

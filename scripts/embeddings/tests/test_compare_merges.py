"""`compare_merges.py` (issue #293): did an incremental re-embedding change only
what it should? Toy merges, written the way `merge_shards.py` writes them: one
file per instrument (its own texts), an empty one when it owns none, and a
shared file."""

import json
import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import compare_merges  # noqa: E402

MODEL = "Qwen/Qwen3-Embedding-0.6B"
SLUG = "qwen3-0.6b"
K = 4


def vec(seed):
    return np.full(K, seed, dtype=np.float16)


def write_dir(root: Path, units: dict, vectors: dict, *, bad=None):
    """`units`: `{clave: [text_sha1, ...]}`; `vectors`: `{text_sha1: seed}`. Writes
    `units.parquet` and the merge `merge_shards.py` would write: a text owned by
    one instrument goes to its file, by several to the shared one."""
    rows = [(clave, sha) for clave, shas in units.items() for sha in shas]
    root.mkdir(parents=True)
    pq.write_table(pa.table({"clave": [r[0] for r in rows], "text_sha1": [r[1] for r in rows]}),
                   root / "units.parquet")
    owners = {}
    for clave, sha in rows:
        owners.setdefault(sha, []).append(clave)
    out = root / "runs" / SLUG / "vectors"
    out.mkdir(parents=True)
    files = {clave: [] for clave in units}
    files["shared"] = []
    for sha, claves in owners.items():
        files["shared" if len(set(claves)) > 1 else claves[0]].append(sha)
    for key, shas in files.items():
        matrix = np.array([vec(vectors[s]) for s in shas], dtype=np.float16).reshape(len(shas), K)
        if bad and key in bad and len(shas):
            matrix[0, 0] = np.nan
        table = pa.table({
            "text_sha1": pa.array(shas, pa.string()),
            "vector": pa.FixedSizeListArray.from_arrays(pa.array(matrix.reshape(-1), pa.float16()), K),
        })
        pq.write_table(table, out / f"vectors-{key}-{SLUG}-{K}.parquet")


BASE_UNITS = {"a": ["a1", "a2", "s1"], "b": ["b1", "s1"], "c": ["c1"]}
BASE_VECTORS = {"a1": 1, "a2": 2, "b1": 3, "c1": 4, "s1": 5}


@pytest.fixture
def published(tmp_path):
    write_dir(tmp_path / "pub", BASE_UNITS, BASE_VECTORS)
    return tmp_path / "pub"


def rebuilt(tmp_path, units, vectors, **kw):
    write_dir(tmp_path / "new", units, vectors, **kw)
    return tmp_path / "new"


def test_identical_merges_have_no_difference(tmp_path, published):
    result = compare_merges.compare(published, rebuilt(tmp_path, BASE_UNITS, BASE_VECTORS), MODEL)
    assert result["problems"] == []
    assert result["differing"] == [] and result["equal"] == 4
    assert result["gained"] == result["lost"] == []


def test_a_new_text_of_a_listed_instrument_changes_only_its_file(tmp_path, published):
    units = {**BASE_UNITS, "a": ["a1", "a3", "s1"]}          # a2 dropped, a3 new
    result = compare_merges.compare(
        published, rebuilt(tmp_path, units, {**BASE_VECTORS, "a3": 9}), MODEL, changed={"a"})
    assert result["problems"] == []
    assert result["differing"] == ["a"]
    assert (result["texts_new"], result["texts_dropped"]) == (1, 1)
    assert result["texts_with_a_different_vector"] == 0


def test_a_changed_file_of_an_unlisted_instrument_stops(tmp_path, published):
    units = {**BASE_UNITS, "c": ["c1", "c2"]}
    result = compare_merges.compare(
        published, rebuilt(tmp_path, units, {**BASE_VECTORS, "c2": 9}), MODEL, changed={"a"})
    assert any("'c'" in p and "not in the list" in p for p in result["problems"])


def test_a_listed_instrument_whose_file_did_not_change_stops(tmp_path, published):
    # The units say `a` has a new text, but the merge holds the published file:
    # the new text has no vector in it.
    units = {**BASE_UNITS, "a": ["a1", "a2", "a3", "s1"]}
    new = rebuilt(tmp_path, units, {**BASE_VECTORS, "a3": 9})
    stale = published / "runs" / SLUG / "vectors" / f"vectors-a-{SLUG}-{K}.parquet"
    (new / "runs" / SLUG / "vectors" / stale.name).write_bytes(stale.read_bytes())
    result = compare_merges.compare(published, new, MODEL, changed={"a"})
    assert any("'a'" in p and "its set of texts changed but the file is the published one" in p
               for p in result["problems"])
    assert any("'a'" in p and "misses 1" in p for p in result["problems"])


def test_a_re_embedded_unchanged_text_stops(tmp_path, published):
    result = compare_merges.compare(
        published, rebuilt(tmp_path, BASE_UNITS, {**BASE_VECTORS, "c1": 40}), MODEL, changed={"c"})
    assert any("'c'" in p and "its set of texts did not change" in p for p in result["problems"])
    assert result["texts_with_a_different_vector"] == 1


def test_a_text_that_becomes_shared_moves_between_files(tmp_path, published):
    units = {**BASE_UNITS, "c": ["c1", "a2"]}               # a2 is now owned by a and c
    result = compare_merges.compare(published, rebuilt(tmp_path, units, BASE_VECTORS), MODEL,
                                    changed={"a", "c"})
    assert result["problems"] == []
    assert sorted(result["differing"]) == ["a", "shared"]   # c keeps c1 alone
    assert result["texts_with_a_different_vector"] == 0


def test_a_gained_and_a_lost_file(tmp_path, published):
    units = {"a": BASE_UNITS["a"], "b": BASE_UNITS["b"], "d": ["d1"]}   # c gone, d new
    result = compare_merges.compare(
        published, rebuilt(tmp_path, units, {**BASE_VECTORS, "d1": 7}), MODEL, changed={"c", "d"})
    assert result["problems"] == []
    assert result["gained"] == ["d"] and result["lost"] == ["c"]


def test_a_lost_file_that_is_still_on_disk_is_stale(tmp_path, published):
    units = {"a": BASE_UNITS["a"], "b": BASE_UNITS["b"]}
    new = rebuilt(tmp_path, units, BASE_VECTORS)
    leftover = published / "runs" / SLUG / "vectors" / f"vectors-c-{SLUG}-{K}.parquet"
    (new / "runs" / SLUG / "vectors" / leftover.name).write_bytes(leftover.read_bytes())
    result = compare_merges.compare(published, new, MODEL, changed={"c"})
    assert any("'c'" in p and "stale" in p for p in result["problems"])


def test_a_non_finite_vector_stops(tmp_path, published):
    units = {**BASE_UNITS, "a": ["a1", "a3", "s1"]}
    result = compare_merges.compare(
        published, rebuilt(tmp_path, units, {**BASE_VECTORS, "a3": 9}, bad={"a"}), MODEL,
        changed={"a"})
    assert result["non_finite_files"] == [("rebuilt", "a")]
    assert any("not finite" in p for p in result["problems"])


def test_a_different_order_is_not_a_difference(tmp_path, published):
    units = {**BASE_UNITS, "a": ["a2", "a1", "s1"]}
    result = compare_merges.compare(published, rebuilt(tmp_path, units, BASE_VECTORS), MODEL)
    assert result["problems"] == [] and result["reordered"] == ["a"]


def test_main_is_a_system_exit_naming_the_problem_and_read_only(tmp_path, published, capsys):
    units = {**BASE_UNITS, "c": ["c1", "c2"]}
    new = rebuilt(tmp_path, units, {**BASE_VECTORS, "c2": 9})
    changed = tmp_path / "changed.json"
    changed.write_text(json.dumps({"changed": [{"clave": "a"}]}))
    before = sorted((p.name, p.stat().st_mtime_ns) for p in (published / "runs").rglob("*.parquet"))
    with pytest.raises(SystemExit) as stop:
        compare_merges.main(["--published", str(published), "--rebuilt", str(new),
                             "--model", MODEL, "--changed", str(changed)])
    assert "'c'" in str(stop.value)
    assert before == sorted((p.name, p.stat().st_mtime_ns) for p in (published / "runs").rglob("*.parquet"))


def test_main_returns_zero_and_writes_json(tmp_path, published, capsys):
    new = rebuilt(tmp_path, BASE_UNITS, BASE_VECTORS)
    out = tmp_path / "r.json"
    assert compare_merges.main(["--published", str(published), "--rebuilt", str(new),
                                "--model", MODEL, "--json", str(out)]) == 0
    assert json.loads(out.read_text())["problems"] == []


def test_changed_file_names_are_the_differing_files_that_keep_their_name(tmp_path, published):
    units = {**BASE_UNITS, "a": ["a1", "a3", "s1"], "d": ["d1"]}
    result = compare_merges.compare(
        published, rebuilt(tmp_path, units, {**BASE_VECTORS, "a3": 9, "d1": 7}), MODEL)
    assert result["gained"] == ["d"]
    assert result["changed_file_names"] == [f"vectors-a-{SLUG}-{K}.parquet"]


def test_main_writes_the_changed_file_names(tmp_path, published):
    units = {**BASE_UNITS, "a": ["a1", "a3", "s1"]}
    new = rebuilt(tmp_path, units, {**BASE_VECTORS, "a3": 9})
    out = tmp_path / "names.txt"
    compare_merges.main(["--published", str(published), "--rebuilt", str(new), "--model", MODEL,
                         "--changed-files-out", str(out)])
    assert out.read_text() == f"vectors-a-{SLUG}-{K}.parquet\n"

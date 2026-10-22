"""`stage_vectors_cache.py` (issue #294): a legalvec-shaped cache built from a merged
work directory, verified against the sums `package_vectors.py` wrote."""

import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import stage_vectors_cache as stage  # noqa: E402


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_work_dir(root: Path, *, sums=None, extra=None, skip=None):
    """A merged work directory: metadata, two models' vector files and `publish/SHA256SUMS.txt`."""
    files = {
        "units.parquet": b"units", "leaves.parquet": b"leaves", "corpus-manifest.json": b"{}",
        "vectors-manifest.json": b"{}",
    }
    for model, k in (("qwen3-0.6b", 4), ("qwen3-4b", 8)):
        for clave in ("a", "b", "shared"):
            files[f"vectors-{clave}-{model}-{k}.parquet"] = f"{clave}{model}".encode()
    for name, data in files.items():
        if name == skip:
            continue
        if name == "vectors-manifest.json":
            path = root / "publish" / name
        elif name.startswith("vectors-"):
            path = root / "runs" / ("qwen3-0.6b" if "0.6b" in name else "qwen3-4b") / "vectors" / name
        else:
            path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    if extra:
        path = root / "runs" / "qwen3-4b" / "vectors" / extra
        path.write_bytes(b"x")
    (root / "publish").mkdir(parents=True, exist_ok=True)
    listed = sums if sums is not None else {n: digest(d) for n, d in files.items()}
    (root / "publish" / "SHA256SUMS.txt").write_text(
        "".join(f"{d}  {n}\n" for n, d in listed.items()), encoding="utf-8")
    return files


def test_stages_every_file_under_the_release_tag(tmp_path):
    files = make_work_dir(tmp_path / "w")
    report = stage.stage({"leyes": tmp_path / "w"}, tmp_path / "cache", log=lambda *_: None)
    out = tmp_path / "cache" / "scjn-leyes-vectors"
    assert report["leyes"]["files"] == len(files)
    assert {p.name for p in out.iterdir()} == set(files) | {"SHA256SUMS.txt", "staged.done"}
    assert (out / "units.parquet").read_bytes() == b"units"


def test_a_file_is_hard_linked_not_copied(tmp_path):
    make_work_dir(tmp_path / "w")
    stage.stage({"leyes": tmp_path / "w"}, tmp_path / "cache", log=lambda *_: None)
    source = tmp_path / "w" / "units.parquet"
    assert (tmp_path / "cache" / "scjn-leyes-vectors" / "units.parquet").stat().st_ino == source.stat().st_ino


def test_a_wrong_digest_stops_and_names_the_file(tmp_path):
    files = make_work_dir(tmp_path / "w")
    bad = {n: digest(d) for n, d in files.items()}
    bad["vectors-a-qwen3-4b-8.parquet"] = digest(b"other")
    make_work_dir(tmp_path / "w", sums=bad)
    with pytest.raises(SystemExit, match="vectors-a-qwen3-4b-8.parquet"):
        stage.stage({"leyes": tmp_path / "w"}, tmp_path / "cache", log=lambda *_: None)


def test_a_listed_file_that_is_absent_stops(tmp_path):
    make_work_dir(tmp_path / "w")
    (tmp_path / "w" / "runs" / "qwen3-4b" / "vectors" / "vectors-b-qwen3-4b-8.parquet").unlink()
    with pytest.raises(SystemExit, match="listed but absent"):
        stage.stage({"leyes": tmp_path / "w"}, tmp_path / "cache", log=lambda *_: None)


def test_a_file_the_sums_do_not_list_is_not_staged(tmp_path):
    make_work_dir(tmp_path / "w", extra="vectors-stale-qwen3-4b-8.parquet")
    with pytest.raises(SystemExit, match="present but not listed"):
        stage.stage({"leyes": tmp_path / "w"}, tmp_path / "cache", log=lambda *_: None)


def test_missing_sums_asks_for_package_vectors(tmp_path):
    make_work_dir(tmp_path / "w")
    (tmp_path / "w" / "publish" / "SHA256SUMS.txt").unlink()
    with pytest.raises(SystemExit, match="package_vectors.py"):
        stage.stage({"leyes": tmp_path / "w"}, tmp_path / "cache", log=lambda *_: None)


def test_restaging_replaces_the_directory_as_a_whole(tmp_path):
    make_work_dir(tmp_path / "w")
    out = tmp_path / "cache" / "scjn-leyes-vectors"
    out.mkdir(parents=True)
    (out / "vectors-old-qwen3-4b-8.parquet").write_bytes(b"leftover")
    stage.stage({"leyes": tmp_path / "w"}, tmp_path / "cache", log=lambda *_: None)
    assert not (out / "vectors-old-qwen3-4b-8.parquet").exists()


def test_work_dir_argument_names_a_known_collection():
    assert stage.parse_work_dirs(["leyes=emb-run-leyes-051"]) == {"leyes": Path("emb-run-leyes-051")}
    with pytest.raises(SystemExit):
        stage.parse_work_dirs(["nonsense=x"])
    with pytest.raises(SystemExit):
        stage.parse_work_dirs(["leyes"])


def test_main_stages_and_reports(tmp_path, capsys):
    make_work_dir(tmp_path / "w")
    assert stage.main(["--work-dir", f"tratados={tmp_path / 'w'}", "--out", str(tmp_path / "c")]) == 0
    assert (tmp_path / "c" / "scjn-tratados-vectors" / "staged.done").exists()
    assert '"tratados"' in capsys.readouterr().out

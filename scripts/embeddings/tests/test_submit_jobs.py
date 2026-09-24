"""The resumable-wait logic of `submit_jobs.py` (issue #256), against a fake
`sbatch`/`squeue`/`hf` -- no network, no Slurm, no real model download.

Mirrors `test_umap_scripts.py`'s own tests for `submit_umap.wait`/
`queued_jobs`: this file exists precisely because `submit_jobs.py`'s wait
used to block on `squeue` with no ceiling, which a `claude -p` session
cannot hold (the Bash tool caps a foreground command at 600 s, and ending a
turn to wait for a background process kills it).

    pytest scripts/embeddings/tests -q
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import submit_jobs  # noqa: E402


def _write_shards_json(work_dir: Path, indices: list[int]) -> None:
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "shards.json").write_text(json.dumps({
        "shards": [{"index": i, "text_sha1": []} for i in indices],
    }))


def _mark_done(work_dir: Path, model_slug: str, index: int) -> None:
    run_dir = work_dir / "runs" / model_slug
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / f"shard-{index:04d}.done").write_text("{}")


MODEL = "Qwen/Qwen3-Embedding-0.6B"
SLUG = "qwen3-0.6b"


def test_pending_shards_lists_shards_without_done_marker(tmp_path):
    work_dir = tmp_path / "work"
    _write_shards_json(work_dir, [0, 1, 2])
    _mark_done(work_dir, SLUG, 1)
    assert submit_jobs.pending_shards(work_dir, MODEL) == [0, 2]


def test_submit_writes_job_ids_and_attempt_to_jobs_json(tmp_path, monkeypatch):
    work_dir = tmp_path / "work"
    _write_shards_json(work_dir, [0, 1])
    calls = []

    def fake_run(dry_run, cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(stdout=f"{1000 + len(calls)}\n")

    monkeypatch.setattr(submit_jobs, "run", fake_run)
    state = submit_jobs.submit(
        work_dir, MODEL, [0, 1], batch_size=32, max_batch_tokens=20000, attempt=1,
        log=lambda *a: None,
    )
    assert state["job_ids"] == ["1001", "1002"]
    assert state["attempt"] == 1
    saved = json.loads(submit_jobs.jobs_json_path(work_dir, MODEL).read_text())
    assert saved["job_ids"] == ["1001", "1002"]


def test_submit_shard_forwards_max_batch_tokens_and_attn_implementation(tmp_path, monkeypatch):
    work_dir = tmp_path / "work"
    captured = {}

    def fake_run(dry_run, cmd, **kwargs):
        captured["cmd"] = cmd
        return SimpleNamespace(stdout="1001\n")

    monkeypatch.setattr(submit_jobs, "run", fake_run)
    submit_jobs.submit_shard(work_dir, MODEL, 0, 32, 15000, "sdpa", dry_run=False)
    assert "--max-batch-tokens" in captured["cmd"]
    assert "15000" in captured["cmd"]
    assert "--attn-implementation" in captured["cmd"]
    assert "sdpa" in captured["cmd"]


def test_submit_shard_omits_attn_implementation_when_not_set(tmp_path, monkeypatch):
    captured = {}

    def fake_run(dry_run, cmd, **kwargs):
        captured["cmd"] = cmd
        return SimpleNamespace(stdout="1001\n")

    monkeypatch.setattr(submit_jobs, "run", fake_run)
    submit_jobs.submit_shard(Path("work"), MODEL, 0, 32, 20000, None, dry_run=False)
    assert "--attn-implementation" not in captured["cmd"]


def test_wait_returns_finished_when_squeue_reports_nothing(tmp_path, monkeypatch):
    work_dir = tmp_path / "work"
    run_dir = work_dir / "runs" / SLUG
    run_dir.mkdir(parents=True)
    (run_dir / "jobs.json").write_text(json.dumps({
        "model": MODEL, "attempt": 1, "submitted_at": 0.0, "job_ids": ["1001"],
    }))
    answers = ["1001\n", ""]
    monkeypatch.setattr(
        submit_jobs.subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout=answers.pop(0)),
    )
    monkeypatch.setattr(submit_jobs.time, "time", lambda: 10.0)
    outcome, pending = submit_jobs.wait(
        work_dir, MODEL, poll=0, sleep=lambda s: None, log=lambda *a: None,
    )
    assert (outcome, pending) == ("finished", [])


def test_wait_returns_still_running_when_the_chunk_elapses(tmp_path, monkeypatch):
    work_dir = tmp_path / "work"
    run_dir = work_dir / "runs" / SLUG
    run_dir.mkdir(parents=True)
    (run_dir / "jobs.json").write_text(json.dumps({
        "model": MODEL, "attempt": 1, "submitted_at": 0.0, "job_ids": ["1001"],
    }))
    monkeypatch.setattr(
        submit_jobs.subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="1001\n"),
    )
    clock = iter([0.0, 0.0, 100.0, 100.0, 100.0])
    monkeypatch.setattr(submit_jobs.time, "time", lambda: next(clock))
    outcome, pending = submit_jobs.wait(
        work_dir, MODEL, poll=0, max_wait_minutes=1, sleep=lambda s: None, log=lambda *a: None,
    )
    assert (outcome, pending) == ("still running", ["1001"])


def test_wait_with_no_ceiling_blocks_until_finished(tmp_path, monkeypatch):
    work_dir = tmp_path / "work"
    run_dir = work_dir / "runs" / SLUG
    run_dir.mkdir(parents=True)
    (run_dir / "jobs.json").write_text(json.dumps({
        "model": MODEL, "attempt": 1, "submitted_at": 0.0, "job_ids": ["1001"],
    }))
    answers = ["1001\n", "1001\n", ""]
    monkeypatch.setattr(
        submit_jobs.subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout=answers.pop(0)),
    )
    monkeypatch.setattr(submit_jobs.time, "time", lambda: 10.0)
    outcome, pending = submit_jobs.wait(
        work_dir, MODEL, poll=0, max_wait_minutes=0.0, sleep=lambda s: None, log=lambda *a: None,
    )
    assert (outcome, pending) == ("finished", [])


def test_queued_jobs_falls_back_to_one_query_per_job(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if len(calls) == 1:
            return SimpleNamespace(returncode=1, stdout="", stderr="Invalid job id")
        return SimpleNamespace(
            returncode=0, stdout="1002\n" if "1002" in cmd else "", stderr="",
        )

    monkeypatch.setattr(submit_jobs.subprocess, "run", fake_run)
    assert submit_jobs.queued_jobs(["1001", "1002"]) == {"1002"}
    assert len(calls) == 3


def _no_network_hf(monkeypatch):
    monkeypatch.setattr(submit_jobs, "hf_download", lambda model, dry_run: None)


def test_main_report_is_offline_and_needs_no_squeue_or_download(tmp_path, monkeypatch):
    work_dir = tmp_path / "work"
    _write_shards_json(work_dir, [0, 1])
    _mark_done(work_dir, SLUG, 0)

    def _boom(*a, **k):
        raise AssertionError("report must not touch the network")

    monkeypatch.setattr(submit_jobs, "hf_download", _boom)
    monkeypatch.setattr(submit_jobs.subprocess, "run", _boom)
    rc = submit_jobs.main(["--work-dir", str(work_dir), "--model", MODEL, "--report"])
    assert rc == 1  # shard 1 is still pending


def test_main_resumes_a_still_running_chunk_without_resubmitting(tmp_path, monkeypatch):
    work_dir = tmp_path / "work"
    _write_shards_json(work_dir, [0])
    run_dir = work_dir / "runs" / SLUG
    run_dir.mkdir(parents=True)
    (run_dir / "jobs.json").write_text(json.dumps({
        "model": MODEL, "attempt": 1, "submitted_at": 0.0, "job_ids": ["1001"],
    }))
    _no_network_hf(monkeypatch)

    def _boom_sbatch(dry_run, cmd, **kwargs):
        raise AssertionError("must not resubmit while a chunk is outstanding")

    monkeypatch.setattr(submit_jobs, "run", _boom_sbatch)
    monkeypatch.setattr(
        submit_jobs.subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="1001\n"),
    )
    clock = iter([0.0, 1.0, 1.0])
    monkeypatch.setattr(submit_jobs.time, "time", lambda: next(clock))
    rc = submit_jobs.main([
        "--work-dir", str(work_dir), "--model", MODEL, "--max-wait-minutes", "0.0001",
        "--poll-interval", "0",
    ])
    assert rc == submit_jobs.EXIT_STILL_RUNNING
    # The state is still on disk for the next call to resume from.
    assert (run_dir / "jobs.json").exists()


def test_main_happy_path_submits_waits_merges_and_cleans_up_weights(tmp_path, monkeypatch):
    work_dir = tmp_path / "work"
    _write_shards_json(work_dir, [0, 1])
    _no_network_hf(monkeypatch)

    submitted = []

    def fake_sbatch_run(dry_run, cmd, **kwargs):
        submitted.append(cmd)
        return SimpleNamespace(stdout=f"{1000 + len(submitted)}\n")

    monkeypatch.setattr(submit_jobs, "run", fake_sbatch_run)

    # squeue reports the jobs queued once, then gone -- and marks the shards
    # done in between, the way a real shard job would on exit.
    calls = {"n": 0}

    def fake_squeue(cmd, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return SimpleNamespace(returncode=0, stdout="1001\n1002\n")
        _mark_done(work_dir, SLUG, 0)
        _mark_done(work_dir, SLUG, 1)
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(submit_jobs.subprocess, "run", fake_squeue)
    monkeypatch.setattr(submit_jobs, "hf_cache_dir", lambda model: tmp_path / "no-such-cache")

    rc = submit_jobs.main([
        "--work-dir", str(work_dir), "--model", MODEL, "--poll-interval", "0",
    ])
    assert rc == 0
    assert len(submitted) == 2  # one sbatch per pending shard
    assert not (work_dir / "runs" / SLUG / "jobs.json").exists()


def test_main_dry_run_never_calls_squeue_or_deletes_weights(tmp_path, monkeypatch):
    work_dir = tmp_path / "work"
    _write_shards_json(work_dir, [0])
    _no_network_hf(monkeypatch)

    def _boom(*a, **k):
        raise AssertionError("--dry-run must not touch squeue")

    monkeypatch.setattr(submit_jobs.subprocess, "run", _boom)
    rc = submit_jobs.main([
        "--work-dir", str(work_dir), "--model", MODEL, "--dry-run",
    ])
    assert rc == 0

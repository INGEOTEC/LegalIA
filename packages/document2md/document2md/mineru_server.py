"""Manages a persistent `mineru-api` process for batch conversions.

Left to itself, the `mineru` CLI spins up (and tears down) a fresh temporary
API server — reloading all layout/OCR models — on every single invocation.
That's fine for converting one document, but wasteful when a batch job
converts many documents in the same run. MineruServer starts one `mineru-api`
process, waits for it to report healthy, and points `convert_to_markdown` at
it via the MINERU_API_URL environment variable for the duration of the batch.
"""
import os
import socket
import subprocess
import time

import requests

ENV_VAR = "MINERU_API_URL"


def _find_free_port() -> int:
    """Return a currently-unused TCP port on 127.0.0.1, by binding to port 0
    and reading back the OS-assigned port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_until_healthy(base_url: str, timeout: float = 300.0, interval: float = 1.0) -> None:
    """Poll `base_url`'s `/health` endpoint every `interval` seconds until it
    answers 200, raising RuntimeError if it hasn't within `timeout` seconds —
    mineru-api takes a while to come up because it loads its models on
    startup."""
    deadline = time.monotonic() + timeout
    health_url = f"{base_url}/health"
    while time.monotonic() < deadline:
        try:
            if requests.get(health_url, timeout=2).status_code == 200:
                return
        except requests.exceptions.RequestException:
            pass
        time.sleep(interval)
    raise RuntimeError(f"mineru-api did not become healthy within {timeout}s at {health_url}")


class MineruServer:
    """Context manager: starts a persistent mineru-api server on __enter__,
    exposes it to convert_to_markdown() via MINERU_API_URL, and stops it on
    __exit__ (restoring any previous MINERU_API_URL value)."""

    def __init__(self, host: str = "127.0.0.1", port: int | None = None):
        """Create an unstarted server bound to `host`/`port` (a free port is
        picked when `port` is `None`); call `start()` (or use as a context
        manager) to actually launch it."""
        self.host = host
        self.port = port if port is not None else _find_free_port()
        self.base_url = f"http://{self.host}:{self.port}"
        self._process: subprocess.Popen | None = None
        self._previous_env_value: str | None = None

    def start(self) -> None:
        """Launch `mineru-api` as a subprocess, block until it reports
        healthy (stopping it again on failure), and point MINERU_API_URL at
        it so `converter.py` picks it up."""
        self._process = subprocess.Popen(
            ["mineru-api", "--host", self.host, "--port", str(self.port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            _wait_until_healthy(self.base_url)
        except Exception:
            self.stop()
            raise
        self._previous_env_value = os.environ.get(ENV_VAR)
        os.environ[ENV_VAR] = self.base_url

    def stop(self) -> None:
        """Restore MINERU_API_URL to whatever it was before `start()`, and
        terminate the `mineru-api` subprocess (killing it if it doesn't exit
        within 15s)."""
        if self._previous_env_value is None:
            os.environ.pop(ENV_VAR, None)
        else:
            os.environ[ENV_VAR] = self._previous_env_value

        if self._process is None:
            return
        self._process.terminate()
        try:
            self._process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait()
        self._process = None

    def __enter__(self) -> "MineruServer":
        """Call `start()` and return `self`."""
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        """Call `stop()`."""
        self.stop()

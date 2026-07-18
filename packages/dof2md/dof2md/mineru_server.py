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
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_until_healthy(base_url: str, timeout: float = 300.0, interval: float = 1.0) -> None:
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
        self.host = host
        self.port = port if port is not None else _find_free_port()
        self.base_url = f"http://{self.host}:{self.port}"
        self._process: subprocess.Popen | None = None
        self._previous_env_value: str | None = None

    def start(self) -> None:
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
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()

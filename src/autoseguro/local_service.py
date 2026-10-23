"""Start the real quote-service as a subprocess, for integration tests and the benchmark.

The service's instability is configured by environment variables, so each run can choose
its own failure rate without touching `quote-service/`.
"""

from __future__ import annotations

import contextlib
import os
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import httpx

SERVICE_DIR = Path(__file__).resolve().parents[2] / "quote-service"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class QuoteServiceProcess:
    """A quote-service subprocess that can be restarted (same port) with new settings."""

    def __init__(self, port: int | None = None) -> None:
        self.port = port or _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self._proc: subprocess.Popen[bytes] | None = None

    def start(
        self,
        *,
        failure_rate: float = 0.2,
        slow_rate: float = 0.1,
        slow_seconds: float = 8,
        seed: int | None = None,
    ) -> str:
        env = {
            **os.environ,
            "QUOTE_FAILURE_RATE": str(failure_rate),
            "QUOTE_SLOW_RATE": str(slow_rate),
            "QUOTE_SLOW_SECONDS": str(slow_seconds),
        }
        env.pop("VIRTUAL_ENV", None)  # the service has its own uv environment
        if seed is not None:
            env["QUOTE_SEED"] = str(seed)
        cmd = [
            "uv",
            "run",
            "-q",
            "uvicorn",
            "app.main:app",
            "--port",
            str(self.port),
            "--log-level",
            "warning",
        ]
        self._proc = subprocess.Popen(
            cmd, cwd=SERVICE_DIR, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        deadline = time.monotonic() + 60
        while True:
            if self._proc.poll() is not None:
                raise RuntimeError(f"quote-service exited with code {self._proc.returncode}")
            try:
                if httpx.get(f"{self.url}/health", timeout=0.5).status_code == 200:
                    return self.url
            except httpx.HTTPError:
                pass
            if time.monotonic() > deadline:
                raise TimeoutError("quote-service did not start within 60s")
            time.sleep(0.2)

    def stop(self) -> None:
        if self._proc is None:
            return
        self._proc.terminate()
        try:
            self._proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._proc.kill()
        self._proc = None

    def restart(self, **settings: float | int | None) -> str:
        self.stop()
        return self.start(**settings)


@contextlib.contextmanager
def run_quote_service(
    *,
    failure_rate: float = 0.2,
    slow_rate: float = 0.1,
    slow_seconds: float = 8,
    seed: int | None = None,
    port: int | None = None,
) -> Iterator[str]:
    """Yield the base URL of a fresh quote-service; stop it on exit."""
    service = QuoteServiceProcess(port)
    try:
        yield service.start(
            failure_rate=failure_rate, slow_rate=slow_rate, slow_seconds=slow_seconds, seed=seed
        )
    finally:
        service.stop()

"""Run the backend and mock ERP as two independent local processes."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import TextIO


ROOT = Path(__file__).resolve().parents[1]
BACKEND_HOST = "127.0.0.1"
BACKEND_PORT = 8000
ERP_HOST = "127.0.0.1"
ERP_PORT = 8001
STARTUP_TIMEOUT_S = 30.0
SHUTDOWN_TIMEOUT_S = 8.0


def _stream_logs(label: str, stream: TextIO) -> None:
    try:
        for line in iter(stream.readline, ""):
            if not line:
                break
            print(f"[{label}] {line.rstrip()}", flush=True)
    finally:
        stream.close()


def _command(script: str) -> list[str]:
    return [sys.executable, str(ROOT / "scripts" / script)]


def _start(command: list[str], environment: dict[str, str]) -> subprocess.Popen[str]:
    return subprocess.Popen(
        command,
        cwd=ROOT,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )


def _port_ready(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _wait_for_children(processes: dict[str, subprocess.Popen[str]]) -> None:
    deadline = time.monotonic() + STARTUP_TIMEOUT_S
    expected = (("backend", BACKEND_HOST, BACKEND_PORT), ("erp", ERP_HOST, ERP_PORT))
    while time.monotonic() < deadline:
        for label, process in processes.items():
            if process.poll() is not None:
                raise RuntimeError(f"{label} exited during startup with code {process.returncode}")
        if all(_port_ready(host, port) for _, host, port in expected):
            return
        time.sleep(0.1)
    raise TimeoutError("backend and mock ERP did not both open their loopback ports within 30 seconds")


def _stop_children(processes: dict[str, subprocess.Popen[str]]) -> None:
    active = [process for process in processes.values() if process.poll() is None]
    for process in active:
        process.terminate()
    deadline = time.monotonic() + SHUTDOWN_TIMEOUT_S
    for process in active:
        remaining = max(0.0, deadline - time.monotonic())
        try:
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2.0)


def main() -> int:
    environment = os.environ.copy()
    environment["PYTHONUNBUFFERED"] = "1"
    processes: dict[str, subprocess.Popen[str]] = {}
    log_threads: list[threading.Thread] = []
    try:
        processes["erp"] = _start(_command("run_erp.py"), environment)
        processes["backend"] = _start(
            [sys.executable, "-m", "uvicorn", "api.main:app", "--host", BACKEND_HOST, "--port", str(BACKEND_PORT), "--log-level", "info"],
            environment,
        )
        for label, process in processes.items():
            if process.stdout is None:
                raise RuntimeError(f"{label} did not expose a log stream")
            thread = threading.Thread(target=_stream_logs, args=(label, process.stdout), name=f"{label}-logs", daemon=True)
            thread.start()
            log_threads.append(thread)

        _wait_for_children(processes)
        print("SentinelPdM backend is listening on http://127.0.0.1:8000; mock ERP is listening on http://127.0.0.1:8001.", flush=True)
        while True:
            for label, process in processes.items():
                return_code = process.poll()
                if return_code is not None:
                    raise RuntimeError(f"{label} exited with code {return_code}")
            time.sleep(0.25)
    except KeyboardInterrupt:
        print("Stopping backend and mock ERP...", flush=True)
        return 0
    except (OSError, RuntimeError, TimeoutError) as exc:
        print(f"run_all failed: {exc}", file=sys.stderr, flush=True)
        return 1
    finally:
        _stop_children(processes)
        for thread in log_threads:
            thread.join(timeout=1.0)


if __name__ == "__main__":
    raise SystemExit(main())

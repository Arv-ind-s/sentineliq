"""Dependency-aware health checks used by Docker Compose."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from typing import Any


def _json(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=4) as response:
        if response.status >= 400:
            raise RuntimeError(f"HTTP {response.status}")
        return json.load(response)


def check_erp() -> None:
    payload = _json("http://127.0.0.1:8001/erp/v1/health")
    if not isinstance(payload, dict) or payload.get("status") != "healthy":
        raise RuntimeError("ERP did not report healthy")


def check_api() -> None:
    payload = _json("http://127.0.0.1:8000/health")
    dependencies = payload.get("dependencies", []) if isinstance(payload, dict) else []
    states = {
        str(item.get("name")): str(item.get("status"))
        for item in dependencies
        if isinstance(item, dict)
    }
    required = {"db", "tsdb", "models", "erp", "stream"}
    unavailable = sorted(name for name in required if states.get(name) != "up")
    if unavailable:
        raise RuntimeError(f"core API dependencies are not ready: {', '.join(unavailable)}")


def main(argv: list[str] | None = None) -> int:
    arguments = argv or sys.argv[1:]
    checks = {"api": check_api, "erp": check_erp}
    if len(arguments) != 1 or arguments[0] not in checks:
        print("usage: python -m scripts.container_health {api|erp}", file=sys.stderr)
        return 2
    try:
        checks[arguments[0]]()
    except (OSError, RuntimeError, ValueError, urllib.error.URLError, json.JSONDecodeError) as exc:
        print(f"{arguments[0]} health check failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

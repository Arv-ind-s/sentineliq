"""Start the mock ERP as its own loopback process on port 8001."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> None:
    if os.getenv("ERP_FAULT_MODE", "none").strip().lower() == "down":
        print("Mock ERP is configured down; no listener was started.", file=sys.stderr)
        raise SystemExit(1)
    uvicorn.run("erp.app:app", host="127.0.0.1", port=8001, log_level="info")


if __name__ == "__main__":
    main()

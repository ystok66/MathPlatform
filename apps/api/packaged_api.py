from __future__ import annotations

import os
from pathlib import Path

import uvicorn

from app.main import app
from app.security import is_loopback_host


def default_root() -> str:
    return str(Path(__file__).resolve().parents[2])


if __name__ == "__main__":
    os.environ.setdefault("MATH_PLATFORM_ROOT", default_root())
    host = os.getenv("HOST", "127.0.0.1")
    if not is_loopback_host(host) and os.getenv("ALLOW_NON_LOOPBACK_HOST") != "1":
        print(f"Refusing non-loopback HOST={host!r}; using 127.0.0.1. Set ALLOW_NON_LOOPBACK_HOST=1 to override.")
        host = "127.0.0.1"
    port = int(os.getenv("PORT", "8001"))
    uvicorn.run(app, host=host, port=port)

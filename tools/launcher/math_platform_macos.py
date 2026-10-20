"""Native macOS portable launcher. Freeze on a Mac; never distribute as a Python prerequisite."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from urllib.request import urlopen


def release_root() -> Path:
    if getattr(sys, "frozen", False):
        for parent in Path(sys.executable).resolve().parents:
            if parent.suffix == ".app":
                return parent.parent
        raise RuntimeError("Keep MathPlatform.app inside the complete extracted folder.")
    raise RuntimeError("Use the packaged MathPlatform.app, not this development source.")


def paths(root: Path) -> dict[str, Path]:
    return {"api": root / "api/math-api", "node": root / "web/node",
            "server": root / "web/server.js", "db": root / "data/math_platform_dev.db",
            "state": root / "data/macos-services.json"}


def identity(pid: int) -> str:
    result = subprocess.run(["/bin/ps", "-p", str(pid), "-o", "lstart=", "-o", "comm="],
                            capture_output=True, text=True, timeout=2)
    return result.stdout.strip() if result.returncode == 0 else ""


def owned(record: dict) -> bool:
    pid = record.get("pid")
    return (isinstance(pid, int) and pid > 1 and bool(record.get("identity"))
            and identity(pid) == record["identity"])


def read_state(root: Path) -> dict:
    file = paths(root)["state"]
    if not file.exists():
        return {}
    try:
        state = json.loads(file.read_text(encoding="utf-8"))
        if not isinstance(state, dict) or state.get("root") != str(root):
            raise ValueError("release root does not match")
        return state
    except (ValueError, OSError) as error:
        raise RuntimeError("Service state cannot be read safely; do not delete your database. "
                           "Keep data/macos-services.json and logs for diagnosis.") from error


def save_state(root: Path, state: dict) -> None:
    target = paths(root)["state"]
    temp = target.with_suffix(".tmp")
    temp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    temp.chmod(0o600)
    os.replace(temp, target)


@contextmanager
def lock(root: Path):
    import fcntl
    data = root / "data"
    data.mkdir(exist_ok=True, mode=0o700)
    with (data / "macos-launcher.lock").open("a") as handle:
        deadline = time.monotonic() + 8
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Another launcher operation is active. Try again shortly.")
                time.sleep(0.1)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def available_port(preferred: int, reserved: set[int]) -> int:
    for port in [preferred] + [secrets.randbelow(20000) + 20000 for _ in range(10)]:
        if port in reserved:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("No free local port after ten fallback attempts. Close a conflicting app and retry.")


def health(url: str) -> bool:
    try:
        with urlopen(url, timeout=2) as reply:
            return reply.status == 200
    except Exception:
        return False


def stop_records(root: Path, state: dict) -> list[int]:
    records = state.get("services", {})
    targets = [item for item in records.values() if owned(item)]
    for item in targets:
        try:
            os.kill(item["pid"], signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and any(owned(item) for item in targets):
        time.sleep(0.1)
    for item in targets:
        if owned(item):
            os.kill(item["pid"], signal.SIGKILL)
    remaining = [item["pid"] for item in targets if owned(item)]
    state["services"] = {key: value for key, value in records.items() if owned(value)}
    save_state(root, state)
    return remaining


def start(root: Path) -> dict:
    locations = paths(root)
    missing = [str(locations[key].relative_to(root)) for key in ("api", "node", "server", "db")
               if not locations[key].is_file()]
    if missing:
        raise RuntimeError("Extract the complete folder. Missing: " + ", ".join(missing))
    state = read_state(root)
    services = state.get("services", {})
    if all(owned(services.get(name, {})) for name in ("api", "web")):
        return {"started": False, "already_running": True, "url": state["web_url"]}
    if services:
        remaining = stop_records(root, state)
        if remaining:
            raise RuntimeError("Previous local services are still stopping. Run STOP.command first.")
    api_port = available_port(8001, set())
    web_port = available_port(3000, {api_port})
    api_url = f"http://127.0.0.1:{api_port}"
    web_url = f"http://127.0.0.1:{web_port}"
    state = {"root": str(root), "api_url": api_url, "web_url": web_url,
             "created_at": time.time(), "services": {}}
    logs = root / "data/logs"
    logs.mkdir(parents=True, exist_ok=True, mode=0o700)
    (root / "data").chmod(0o700)
    locations["db"].chmod(0o600)
    if (root / ".env").exists():
        (root / ".env").chmod(0o600)
    env = os.environ.copy()
    # Ignore host developer overrides; the portable instance owns its paths/ports.
    for key in ("PYTHONPATH", "PYTHONHOME", "DATABASE_URL", "MATH_PLATFORM_ROOT"):
        env.pop(key, None)
    env.update(MATH_PLATFORM_ROOT=str(root), HOST="127.0.0.1", HOSTNAME="127.0.0.1",
               NODE_ENV="production", DATABASE_URL="sqlite:///" + str(locations["db"]),
               CORS_ORIGINS=f"{web_url},http://localhost:{web_port}",
               MATH_PLATFORM_API_BASE=api_url, NEXT_PUBLIC_API_BASE="")
    commands = [("api", [str(locations["api"])], root, api_port),
                ("web", [str(locations["node"]), str(locations["server"])], root / "web", web_port)]
    save_state(root, state)
    try:
        for name, command, cwd, port in commands:
            child_env = dict(env, PORT=str(port))
            with (logs / f"{name}.out.log").open("ab") as out, (logs / f"{name}.err.log").open("ab") as err:
                process = subprocess.Popen(command, cwd=cwd, env=child_env, stdin=subprocess.DEVNULL,
                                           stdout=out, stderr=err, start_new_session=True, close_fds=True)
            record = {"pid": process.pid, "identity": identity(process.pid)}
            state["services"][name] = record
            save_state(root, state)
            if not record["identity"]:
                raise RuntimeError(f"{name} exited immediately. See data/logs/{name}.err.log.")
    except Exception:
        stop_records(root, state)
        raise
    return {"started": True, "url": web_url, "note": "Services are starting; readiness is checked separately."}


def status(root: Path, doctor: bool = False) -> dict:
    state = read_state(root)
    services = state.get("services", {})
    result = {"root": str(root), "web_url": state.get("web_url"),
              "api_url": state.get("api_url"),
              "api_owned": owned(services.get("api", {})), "web_owned": owned(services.get("web", {}))}
    result["api_ok"] = bool(result["api_owned"] and health(state["api_url"] + "/health"))
    result["web_ok"] = bool(result["web_owned"] and health(state["web_url"]))
    result["ok"] = result["api_ok"] and result["web_ok"]
    if doctor:
        with sqlite3.connect(paths(root)["db"].as_uri() + "?mode=ro", uri=True, timeout=2) as db:
            messages = [row[0] for row in db.execute("PRAGMA quick_check")]
        result["database_quick_check"] = messages
        result["ok"] = result["ok"] and messages == ["ok"]
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", nargs="?", default="launch", choices=("launch", "start", "open", "status", "doctor", "stop"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("This launcher is for native macOS builds only.")
    os.umask(0o077)
    try:
        root = release_root()
        with lock(root):
            if args.command in ("start", "launch"):
                result = start(root)
            elif args.command in ("status", "doctor"):
                result = status(root, args.command == "doctor")
            elif args.command == "stop":
                state = read_state(root) or {"root": str(root), "services": {}}
                result = {"remaining": stop_records(root, state)}
                result["ok"] = not result["remaining"]
            else:
                state = read_state(root)
                if not state.get("web_url"):
                    raise RuntimeError("Start MathPlatform.app before opening its browser page.")
                result = {"url": state["web_url"]}
        if args.command in ("launch", "open"):
            subprocess.run(["/usr/bin/open", result["url"]], check=True, timeout=5)
        if sys.stdout is not None:
            print(json.dumps(result, indent=2))
        return 0 if result.get("ok", True) else 1
    except Exception as error:
        message = str(error)
        if sys.stderr is not None:
            print(json.dumps({"ok": False, "error": message}), file=sys.stderr)
        if args.command == "launch":
            subprocess.run(["/usr/bin/osascript", "-e", "display alert \"Math Platform\" message " + json.dumps(message)], timeout=20)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

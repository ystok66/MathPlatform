from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import random
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TH32CS_SNAPPROCESS = 0x00000002
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
MAX_PATH = 260
BACKUP_MANIFEST_VERSION = "math_platform_backup_v1"
DIAGNOSTIC_BUNDLE_VERSION = "math_platform_diagnostic_bundle_v1"
DATABASE_RELATIVE_PATH = Path("data") / "math_platform_dev.db"
DEFAULT_API_PORT = 8001
DEFAULT_WEB_PORT = 3000
API_FALLBACK_PORT_RANGE = (18001, 18999)
WEB_FALLBACK_PORT_RANGE = (13000, 13999)
PORT_RANDOM_ATTEMPTS = 10
CRITICAL_TABLES = (
    "source_files",
    "learner_profiles",
    "questions",
    "question_versions",
    "answer_specs",
    "taxonomy_tags",
    "question_tags",
    "practice_sessions",
    "submissions",
    "answer_events",
    "generation_jobs",
    "generation_batches",
    "generation_batch_items",
    "generated_candidates",
    "ai_run_logs",
)
SENSITIVE_KEY_MARKERS = (
    "api_key",
    "apikey",
    "secret",
    "token",
    "password",
    "authorization",
    "cookie",
    "encryption_key",
)


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_ulong),
        ("cntUsage", ctypes.c_ulong),
        ("th32ProcessID", ctypes.c_ulong),
        ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", ctypes.c_ulong),
        ("cntThreads", ctypes.c_ulong),
        ("th32ParentProcessID", ctypes.c_ulong),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", ctypes.c_ulong),
        ("szExeFile", ctypes.c_wchar * MAX_PATH),
    ]


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True) if os.name == "nt" else None


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")


def release_root(explicit_root: str | None = None) -> Path:
    if explicit_root:
        return Path(explicit_root).resolve()
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        if exe_dir.name.lower() == "launcher":
            return exe_dir.parent
        return exe_dir
    return Path(__file__).resolve().parents[2]


def normalize_path_env(env: dict[str, str]) -> dict[str, str]:
    path_value = None
    for key in list(env.keys()):
        if key.lower() == "path":
            path_value = path_value or env.get(key)
            del env[key]
    if path_value:
        env["Path"] = path_value
    return env


def query_process_path(pid: int) -> str | None:
    if os.name != "nt" or kernel32 is None:
        return None
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return None
    try:
        size = ctypes.c_ulong(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        ok = kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size))
        if not ok:
            return None
        return buffer.value
    finally:
        kernel32.CloseHandle(handle)


def iter_processes() -> list[dict[str, Any]]:
    if os.name != "nt" or kernel32 is None:
        return []
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID_HANDLE_VALUE:
        return []
    rows: list[dict[str, Any]] = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            pid = int(entry.th32ProcessID)
            name = entry.szExeFile
            rows.append(
                {
                    "pid": pid,
                    "name": name,
                    "parent_pid": int(entry.th32ParentProcessID),
                    "path": query_process_path(pid),
                }
            )
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return rows


def path_is_under(path: str | None, root: Path) -> bool:
    if not path:
        return False
    try:
        resolved = Path(path).resolve()
        resolved.relative_to(root)
        return True
    except Exception:
        return str(path).lower().startswith(str(root).lower())


def release_processes(root: Path) -> list[dict[str, Any]]:
    names = {"math-api.exe", "node.exe"}
    return [
        row
        for row in iter_processes()
        if row.get("name", "").lower() in names and path_is_under(row.get("path"), root)
    ]


def safe_profile_name(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in value.strip())
    return cleaned.strip("-_") or "smoke"


def runtime_data_dir(root: Path, explicit_data_dir: Path | str | None = None, smoke_profile: str | None = None) -> Path:
    if explicit_data_dir:
        candidate = Path(explicit_data_dir)
        return (candidate if candidate.is_absolute() else root / candidate).resolve()
    if smoke_profile:
        return (root / ".smoke-profiles" / safe_profile_name(smoke_profile)).resolve()
    return (root / "data").resolve()


def sqlite_database_url(path: Path) -> str:
    return f"sqlite:///{path.resolve().as_posix()}"


def pid_file(root: Path, name: str, data_dir: Path | None = None) -> Path:
    return runtime_data_dir(root, data_dir) / f"{name}.pid"


def read_pid(root: Path, name: str, data_dir: Path | None = None) -> int | None:
    path = pid_file(root, name, data_dir)
    try:
        return int(path.read_text(encoding="ascii").strip())
    except Exception:
        return None


def write_pid(root: Path, name: str, pid: int, data_dir: Path | None = None) -> None:
    path = pid_file(root, name, data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(pid), encoding="ascii")


def remove_pid(root: Path, name: str, data_dir: Path | None = None) -> None:
    try:
        pid_file(root, name, data_dir).unlink()
    except FileNotFoundError:
        pass


def ports_file(data_dir: Path) -> Path:
    return data_dir / "ports.json"


def read_ports(data_dir: Path) -> dict[str, int]:
    try:
        raw = json.loads(ports_file(data_dir).read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    ports: dict[str, int] = {}
    for key, default in {"api_port": DEFAULT_API_PORT, "web_port": DEFAULT_WEB_PORT}.items():
        try:
            value = int(raw.get(key, default))
        except (TypeError, ValueError):
            value = default
        ports[key] = value
    return ports


def write_ports(data_dir: Path, api_port: int, web_port: int) -> None:
    path = ports_file(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "api_port": int(api_port),
        "web_port": int(web_port),
        "api_base": f"http://127.0.0.1:{int(api_port)}",
        "web_base": f"http://127.0.0.1:{int(web_port)}",
        "updated_at": int(time.time()),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def local_port_available(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, int(port)))
            return True
        except OSError:
            return False


def choose_available_port(preferred: int, fallback_range: tuple[int, int], avoid: set[int] | None = None) -> int | None:
    avoid = set(avoid or set())
    if preferred not in avoid and local_port_available(preferred):
        return preferred
    low, high = fallback_range
    tried: set[int] = {preferred}
    for _ in range(PORT_RANDOM_ATTEMPTS):
        candidate = random.randint(low, high)
        if candidate in tried or candidate in avoid:
            continue
        tried.add(candidate)
        if local_port_available(candidate):
            return candidate
    return None


def port_reachable(url: str, timeout: float = 0.25) -> bool:
    try:
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def health(url: str, timeout: float = 0.8) -> bool:
    if not port_reachable(url):
        return False
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
            return data.get("status") == "ok"
    except Exception:
        return False


def web_health(url: str, timeout: float = 1.5) -> bool:
    if not port_reachable(url):
        return False
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return 200 <= int(response.status) < 400
    except Exception:
        return False


def fetch_json(url: str, timeout: float = 0.8) -> dict[str, Any] | None:
    if not port_reachable(url):
        return None
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return None


def read_release_key_state(root: Path) -> bool:
    env_file = root / ".env"
    if not env_file.exists():
        return False
    for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith("LIAOBOTS_API_KEY="):
            return bool(line.split("=", 1)[1].strip())
    return False


def database_path(root: Path, data_dir: Path | None = None) -> Path:
    return runtime_data_dir(root, data_dir) / DATABASE_RELATIVE_PATH.name


def timestamp_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def unique_child_dir(parent: Path, name: str) -> Path:
    candidate = parent / name
    if not candidate.exists():
        return candidate
    for index in range(2, 1000):
        candidate = parent / f"{name}-{index}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not allocate unique directory under {parent}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_row(path: Path, *, relative_to: Path | None = None) -> dict[str, Any]:
    rel = path.name
    if relative_to is not None:
        try:
            rel = str(path.relative_to(relative_to)).replace("\\", "/")
        except ValueError:
            rel = path.name
    return {
        "path": rel,
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def is_sensitive_key(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_")
    return any(marker in normalized for marker in SENSITIVE_KEY_MARKERS)


def sanitize_env_text(text: str) -> str:
    sanitized: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            sanitized.append(line)
            continue
        key, _, value = line.partition("=")
        if is_sensitive_key(key):
            sanitized.append(f"{key}=<redacted>" if value.strip() else f"{key}=")
        else:
            sanitized.append(line)
    return "\n".join(sanitized) + ("\n" if text.endswith("\n") else "")


def redact_secrets_in_text(text: str) -> str:
    redacted_lines = sanitize_env_text(text).splitlines()
    result: list[str] = []
    for line in redacted_lines:
        lowered = line.lower()
        if "bearer " in lowered:
            prefix, _, _ = line.partition("Bearer ")
            if prefix != line:
                line = f"{prefix}Bearer <redacted>"
            else:
                prefix, _, _ = line.partition("bearer ")
                line = f"{prefix}bearer <redacted>"
        if any(marker in lowered for marker in SENSITIVE_KEY_MARKERS) and "=" not in line:
            result.append("<redacted sensitive line>")
        else:
            result.append(line)
    return "\n".join(result)


def sqlite_readonly_uri(path: Path) -> str:
    normalized = path.resolve().as_posix()
    return "file:" + urllib.parse.quote(normalized, safe="/:") + "?mode=ro"


def sqlite_backup_connection(source_conn: sqlite3.Connection, dest_conn: sqlite3.Connection) -> None:
    source_conn.backup(dest_conn)


def sqlite_integrity_check_connection(conn: sqlite3.Connection) -> dict[str, Any]:
    try:
        rows = conn.execute("PRAGMA integrity_check").fetchall()
        messages = [str(row[0]) for row in rows]
        return {"ok": messages == ["ok"], "messages": messages}
    except Exception as exc:
        return {"ok": False, "messages": [], "error": str(exc)}


def sqlite_table_counts_connection(conn: sqlite3.Connection) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in CRITICAL_TABLES:
        exists = conn.execute(
            "select name from sqlite_master where type='table' and name=?",
            (table,),
        ).fetchone()
        if exists:
            counts[table] = int(conn.execute(f"select count(*) from {table}").fetchone()[0])
    return counts


def open_readonly_sqlite(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(sqlite_readonly_uri(path), uri=True, timeout=30)
    conn.execute("PRAGMA query_only=ON")
    return conn


def sqlite_integrity_check_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"ok": False, "error_code": "database_missing", "path": str(path)}
    conn: sqlite3.Connection | None = None
    try:
        conn = open_readonly_sqlite(path)
        report = sqlite_integrity_check_connection(conn)
        report["path"] = str(path)
        return report
    except Exception as exc:
        return {"ok": False, "error_code": "database_open_failed", "path": str(path), "error": str(exc)}
    finally:
        if conn is not None:
            conn.close()


def sqlite_table_counts_file(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    conn: sqlite3.Connection | None = None
    try:
        conn = open_readonly_sqlite(path)
        return sqlite_table_counts_connection(conn)
    except Exception:
        return {}
    finally:
        if conn is not None:
            conn.close()


def backup_sqlite_database(source: Path, dest: Path) -> None:
    if not source.exists():
        raise FileNotFoundError(str(source))
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    source_conn: sqlite3.Connection | None = None
    dest_conn: sqlite3.Connection | None = None
    try:
        source_conn = open_readonly_sqlite(source)
        dest_conn = sqlite3.connect(str(dest), timeout=30)
        sqlite_backup_connection(source_conn, dest_conn)
        dest_conn.commit()
    finally:
        if dest_conn is not None:
            dest_conn.close()
        if source_conn is not None:
            source_conn.close()


def sqlite_sidecar_paths(path: Path) -> list[Path]:
    return [Path(str(path) + suffix) for suffix in ("-wal", "-shm")]


def safe_unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def write_sanitized_env(root: Path, backup_dir: Path) -> dict[str, Any] | None:
    env_file = root / ".env"
    if not env_file.exists():
        return None
    target = backup_dir / "env.sanitized"
    target.write_text(sanitize_env_text(env_file.read_text(encoding="utf-8", errors="ignore")), encoding="utf-8")
    return file_row(target, relative_to=backup_dir)


def load_release_manifest_summary(root: Path) -> dict[str, Any]:
    path = root / "release_manifest.json"
    if not path.exists():
        return {"exists": False}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"exists": True, "ok": False, "error": str(exc)}
    return {
        "exists": True,
        "ok": True,
        "manifest_version": data.get("manifest_version"),
        "app_version": data.get("app_version"),
        "created_at": data.get("created_at") or data.get("generated_at"),
        "file_count": len(data.get("files") or []),
    }


def create_backup(root: Path, *, prefix: str = "backup", reason: str = "manual", data_dir: Path | None = None) -> dict[str, Any]:
    root = root.resolve()
    data_root = runtime_data_dir(root, data_dir)
    db_path = database_path(root, data_root)
    if not db_path.exists():
        return {"ok": False, "error_code": "database_missing", "database": str(db_path)}

    backups_dir = data_root / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    backup_id = f"{prefix}-{timestamp_id()}"
    backup_dir = unique_child_dir(backups_dir, backup_id)
    backup_dir.mkdir(parents=True, exist_ok=False)

    backup_db = backup_dir / "math_platform_dev.db"
    try:
        backup_sqlite_database(db_path, backup_db)
    except Exception as exc:
        return {
            "ok": False,
            "error_code": "sqlite_backup_failed",
            "backup_dir": str(backup_dir),
            "database": str(db_path),
            "error": str(exc),
        }

    integrity = sqlite_integrity_check_file(backup_db)
    counts = sqlite_table_counts_file(backup_db)
    files = [file_row(backup_db, relative_to=backup_dir)]
    env_row = write_sanitized_env(root, backup_dir)
    if env_row:
        files.append(env_row)

    manifest = {
        "manifest_version": BACKUP_MANIFEST_VERSION,
        "backup_id": backup_dir.name,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "root": str(root),
        "data_dir": str(data_root),
        "reason": reason,
        "database": {
            "source": str(db_path),
            "backup_file": "math_platform_dev.db",
            "size_bytes": backup_db.stat().st_size,
            "sha256": sha256_file(backup_db),
            "integrity_check": integrity,
            "table_counts": counts,
        },
        "files": files,
        "release_manifest": load_release_manifest_summary(root),
        "notes": [
            "SQLite database was copied with the SQLite online backup API.",
            "env.sanitized excludes API keys, tokens, passwords, and encryption secrets.",
            "Restore confirmation phrase is this backup_id; there is no fixed global restore password.",
        ],
    }
    manifest_path = backup_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "ok": bool(integrity.get("ok")),
        "backup_id": backup_dir.name,
        "backup_dir": str(backup_dir),
        "manifest": str(manifest_path),
        "database_sha256": manifest["database"]["sha256"],
        "table_counts": counts,
        "integrity_check": integrity,
        "restore_confirmation": backup_dir.name,
    }


def backup(root: Path, data_dir: Path | None = None) -> dict[str, Any]:
    return create_backup(root, prefix="backup", reason="manual", data_dir=data_dir)


def resolve_restore_manifest_path(root: Path, file_arg: str, data_dir: Path | None = None) -> Path:
    candidate = Path(file_arg)
    if not candidate.is_absolute():
        data_root = runtime_data_dir(root, data_dir)
        rooted = data_root / candidate
        if not rooted.exists():
            rooted = root / candidate
        candidate = rooted if rooted.exists() else candidate.resolve()
    if candidate.is_dir():
        return candidate / "manifest.json"
    return candidate


def validate_backup_manifest(manifest_path: Path) -> dict[str, Any]:
    if not manifest_path.exists():
        return {"ok": False, "error_code": "manifest_missing", "manifest": str(manifest_path)}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "error_code": "manifest_invalid_json", "manifest": str(manifest_path), "error": str(exc)}
    if manifest.get("manifest_version") != BACKUP_MANIFEST_VERSION:
        return {
            "ok": False,
            "error_code": "unsupported_manifest_version",
            "manifest": str(manifest_path),
            "manifest_version": manifest.get("manifest_version"),
        }

    backup_dir = manifest_path.parent
    db_rel = (manifest.get("database") or {}).get("backup_file") or "math_platform_dev.db"
    db_path = backup_dir / db_rel
    errors: list[dict[str, Any]] = []
    if not db_path.exists():
        errors.append({"error_code": "database_backup_missing", "path": str(db_path)})
    else:
        expected_hash = (manifest.get("database") or {}).get("sha256")
        actual_hash = sha256_file(db_path)
        if expected_hash and actual_hash != expected_hash:
            errors.append(
                {
                    "error_code": "database_hash_mismatch",
                    "path": str(db_path),
                    "expected_sha256": expected_hash,
                    "actual_sha256": actual_hash,
                }
            )
    integrity = sqlite_integrity_check_file(db_path) if db_path.exists() else {"ok": False}
    if db_path.exists() and not integrity.get("ok"):
        errors.append({"error_code": "database_integrity_failed", "integrity_check": integrity})

    return {
        "ok": not errors,
        "manifest": str(manifest_path),
        "backup_dir": str(backup_dir),
        "database": str(db_path),
        "backup_id": manifest.get("backup_id"),
        "integrity_check": integrity,
        "table_counts": sqlite_table_counts_file(db_path) if db_path.exists() else {},
        "errors": errors,
        "manifest_data": manifest,
    }


def restore_confirmation_phrase(manifest: dict[str, Any]) -> str:
    return str(manifest.get("backup_id") or "").strip()


def restore_confirmation_matches(manifest: dict[str, Any], confirm: str | None) -> bool:
    phrase = restore_confirmation_phrase(manifest)
    return bool(phrase) and str(confirm or "").strip() == phrase


def raw_pre_restore_copy(root: Path, db_path: Path, reason: str, data_dir: Path | None = None) -> dict[str, Any]:
    backups_dir = runtime_data_dir(root, data_dir) / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    backup_dir = unique_child_dir(backups_dir, f"pre_restore_raw-{timestamp_id()}")
    backup_dir.mkdir(parents=True, exist_ok=False)
    target = backup_dir / "math_platform_dev.db"
    shutil.copy2(db_path, target)
    for sidecar in sqlite_sidecar_paths(db_path):
        if sidecar.exists():
            shutil.copy2(sidecar, backup_dir / sidecar.name)
    manifest = {
        "manifest_version": "math_platform_raw_pre_restore_snapshot_v1",
        "backup_id": backup_dir.name,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "reason": reason,
        "database": file_row(target, relative_to=backup_dir),
        "warning": "This is a raw emergency copy because SQLite backup failed; use only for forensic recovery.",
    }
    manifest_path = backup_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"backup_dir": str(backup_dir), "manifest": str(manifest_path), "mode": "raw_copy"}


def pre_restore_database_path(pre_restore: dict[str, Any] | None) -> Path | None:
    if not pre_restore:
        return None

    manifest_value = pre_restore.get("manifest")
    if manifest_value:
        manifest_path = Path(str(manifest_value))
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            database = manifest.get("database") or {}
            db_rel = database.get("backup_file") or database.get("path") or "math_platform_dev.db"
            db_path = manifest_path.parent / str(db_rel)
            if db_path.exists():
                return db_path
        except Exception:
            pass

    backup_dir_value = pre_restore.get("backup_dir")
    if backup_dir_value:
        db_path = Path(str(backup_dir_value)) / "math_platform_dev.db"
        if db_path.exists():
            return db_path
    return None


def rollback_restored_database(target_db: Path, pre_restore: dict[str, Any] | None) -> dict[str, Any]:
    source = pre_restore_database_path(pre_restore)
    if source is None:
        return {"ok": False, "error_code": "pre_restore_backup_missing"}

    temp_db = target_db.with_name(f"{target_db.name}.rollback-{timestamp_id()}.tmp")
    shutil.copy2(source, temp_db)
    temp_integrity = sqlite_integrity_check_file(temp_db)
    if not temp_integrity.get("ok"):
        safe_unlink(temp_db)
        return {
            "ok": False,
            "error_code": "rollback_database_integrity_failed",
            "source": str(source),
            "integrity_check": temp_integrity,
        }

    for sidecar in sqlite_sidecar_paths(target_db):
        safe_unlink(sidecar)
    os.replace(temp_db, target_db)
    for sidecar in sqlite_sidecar_paths(target_db):
        safe_unlink(sidecar)

    return {
        "ok": True,
        "source": str(source),
        "restored_database": str(target_db),
        "restored_sha256": sha256_file(target_db),
        "integrity_check": sqlite_integrity_check_file(target_db),
        "table_counts": sqlite_table_counts_file(target_db),
    }


def restore_backup(root: Path, *, file_arg: str, dry_run: bool = False, confirm: str | None = None, data_dir: Path | None = None) -> dict[str, Any]:
    root = root.resolve()
    data_root = runtime_data_dir(root, data_dir)
    manifest_path = resolve_restore_manifest_path(root, file_arg, data_root)
    validation = validate_backup_manifest(manifest_path)
    if not validation["ok"]:
        return {"ok": False, "dry_run": dry_run, **validation}

    manifest = validation["manifest_data"]
    required_confirmation = restore_confirmation_phrase(manifest)
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "action": "validated_only",
            "required_confirmation": required_confirmation,
            "restore_source": validation["database"],
            "table_counts": validation["table_counts"],
            "integrity_check": validation["integrity_check"],
        }

    running = release_processes(root)
    runtime_ports = read_ports(data_root)
    api_reachable = port_reachable(f"http://127.0.0.1:{runtime_ports.get('api_port', DEFAULT_API_PORT)}/api/health")
    web_reachable = port_reachable(f"http://127.0.0.1:{runtime_ports.get('web_port', DEFAULT_WEB_PORT)}/")
    if running or api_reachable or web_reachable:
        return {
            "ok": False,
            "error_code": "services_running",
            "message": "Stop the platform before restore: launcher\\MathPlatform.exe stop",
            "processes": running,
            "api_reachable": api_reachable,
            "web_reachable": web_reachable,
        }

    if not restore_confirmation_matches(manifest, confirm):
        return {
            "ok": False,
            "error_code": "confirmation_required",
            "required_confirmation": required_confirmation,
            "message": "Run restore again with --confirm equal to required_confirmation.",
        }

    target_db = database_path(root, data_root)
    target_db.parent.mkdir(parents=True, exist_ok=True)
    pre_restore: dict[str, Any] | None = None
    if target_db.exists():
        pre_restore = create_backup(root, prefix="pre_restore", reason=f"before_restore_from_{required_confirmation}", data_dir=data_root)
        if not pre_restore.get("ok"):
            pre_restore = raw_pre_restore_copy(root, target_db, str(pre_restore.get("error_code") or "sqlite_backup_failed"), data_dir=data_root)

    temp_db = target_db.with_name(f"{target_db.name}.restore-{timestamp_id()}.tmp")
    shutil.copy2(Path(validation["database"]), temp_db)
    temp_integrity = sqlite_integrity_check_file(temp_db)
    if not temp_integrity.get("ok"):
        safe_unlink(temp_db)
        return {
            "ok": False,
            "error_code": "restored_database_integrity_failed",
            "integrity_check": temp_integrity,
            "pre_restore_backup": pre_restore,
        }

    for sidecar in sqlite_sidecar_paths(target_db):
        safe_unlink(sidecar)
    os.replace(temp_db, target_db)
    for sidecar in sqlite_sidecar_paths(target_db):
        safe_unlink(sidecar)

    restored_integrity = sqlite_integrity_check_file(target_db)
    restored_hash = sha256_file(target_db)
    expected_hash = (manifest.get("database") or {}).get("sha256")
    ok = bool(restored_integrity.get("ok")) and (not expected_hash or restored_hash == expected_hash)
    post_restore_validation = {
        "ok": ok,
        "hash_matches": not expected_hash or restored_hash == expected_hash,
        "expected_sha256": expected_hash,
        "actual_sha256": restored_hash,
        "integrity_check": restored_integrity,
    }
    rollback_result = None if ok else rollback_restored_database(target_db, pre_restore)
    return {
        "ok": ok,
        "dry_run": False,
        "error_code": None if ok else "post_restore_validation_failed",
        "restored_database": str(target_db),
        "restored_sha256": restored_hash,
        "expected_sha256": expected_hash,
        "integrity_check": restored_integrity,
        "table_counts": sqlite_table_counts_file(target_db),
        "pre_restore_backup": pre_restore,
        "post_restore_validation": post_restore_validation,
        "rollback": rollback_result,
    }


def latest_backup_summaries(root: Path, limit: int = 5, data_dir: Path | None = None) -> list[dict[str, Any]]:
    backups_dir = runtime_data_dir(root, data_dir) / "backups"
    if not backups_dir.exists():
        return []
    rows: list[dict[str, Any]] = []
    for manifest_path in sorted(backups_dir.glob("*/manifest.json"), key=lambda path: path.stat().st_mtime, reverse=True):
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        rows.append(
            {
                "backup_id": data.get("backup_id"),
                "created_at": data.get("created_at"),
                "manifest_version": data.get("manifest_version"),
                "path": str(manifest_path.parent),
                "database_sha256": (data.get("database") or {}).get("sha256"),
                "table_counts": (data.get("database") or {}).get("table_counts") or {},
            }
        )
        if len(rows) >= limit:
            break
    return rows


def tail_text(path: Path, max_chars: int = 20000) -> str:
    try:
        data = path.read_bytes()
    except Exception as exc:
        return f"<failed to read log: {exc}>"
    tail = data[-max_chars:]
    return tail.decode("utf-8", errors="replace")


def diagnostic_bundle(root: Path, data_dir: Path | None = None) -> dict[str, Any]:
    root = root.resolve()
    data_root = runtime_data_dir(root, data_dir)
    diagnostics_dir = data_root / "diagnostics"
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    bundle_dir = unique_child_dir(diagnostics_dir, f"diagnostic-{timestamp_id()}")
    bundle_dir.mkdir(parents=True, exist_ok=False)

    logs: dict[str, str] = {}
    logs_dir = data_root / "logs"
    if logs_dir.exists():
        for path in sorted(logs_dir.glob("*.log")):
            logs[path.name] = redact_secrets_in_text(tail_text(path))

    db_path = database_path(root, data_root)
    report = {
        "manifest_version": DIAGNOSTIC_BUNDLE_VERSION,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "root": str(root),
        "data_dir": str(data_root),
        "status": status(root, data_root),
        "doctor": doctor(root, data_root),
        "database": {
            "path": str(db_path),
            "exists": db_path.exists(),
            "integrity_check": sqlite_integrity_check_file(db_path) if db_path.exists() else None,
            "table_counts": sqlite_table_counts_file(db_path) if db_path.exists() else {},
            "size_bytes": db_path.stat().st_size if db_path.exists() else 0,
        },
        "release_manifest": load_release_manifest_summary(root),
        "recent_backups": latest_backup_summaries(root, data_dir=data_root),
        "logs": logs,
        "redaction": "API keys, tokens, passwords, cookies, authorization headers, and encryption keys are redacted.",
    }
    report_path = bundle_dir / "diagnostic_bundle.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"ok": True, "bundle_dir": str(bundle_dir), "report": str(report_path)}


def start_process(
    *,
    root: Path,
    data_dir: Path,
    name: str,
    exe: Path,
    args: list[str],
    cwd: Path,
    env_updates: dict[str, str],
) -> int:
    if not exe.exists():
        raise FileNotFoundError(str(exe))
    logs = data_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    stdout_path = logs / f"{name}.out.log"
    stderr_path = logs / f"{name}.err.log"
    stdout = stdout_path.open("ab")
    stderr = stderr_path.open("ab")
    env = normalize_path_env(dict(os.environ))
    env.update(env_updates)
    flags = CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW | DETACHED_PROCESS
    try:
        process = subprocess.Popen(
            [str(exe), *args],
            cwd=str(cwd),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            creationflags=flags,
            close_fds=True,
        )
    finally:
        stdout.close()
        stderr.close()
    write_pid(root, name, process.pid, data_dir=data_dir)
    return process.pid


def start(root: Path, data_dir: Path | None = None) -> dict[str, Any]:
    root = root.resolve()
    data_root = runtime_data_dir(root, data_dir)
    data_root.mkdir(parents=True, exist_ok=True)
    (data_root / "logs").mkdir(parents=True, exist_ok=True)
    db_path = database_path(root, data_root)
    result: dict[str, Any] = {"root": str(root), "data_dir": str(data_root), "database": str(db_path), "actions": [], "ok": True}
    processes = release_processes(root)
    runtime_ports = read_ports(data_root)

    api_path = root / "api" / "math-api.exe"
    web_path = root / "web" / "node.exe"
    web_server = root / "web" / "server.js"

    api_running = any(Path(row["path"]).resolve() == api_path.resolve() for row in processes if row.get("path"))
    web_running = any(Path(row["path"]).resolve() == web_path.resolve() for row in processes if row.get("path"))
    api_port = runtime_ports.get("api_port", DEFAULT_API_PORT)
    web_port = runtime_ports.get("web_port", DEFAULT_WEB_PORT)

    if api_running:
        result["actions"].append({"service": "api", "status": "already_running"})
    else:
        chosen_api_port = choose_available_port(DEFAULT_API_PORT, API_FALLBACK_PORT_RANGE, avoid={web_port})
        if chosen_api_port is None:
            result["actions"].append({
                "service": "api",
                "status": "port_conflict",
                "message": f"API port unavailable: tried {DEFAULT_API_PORT} and {PORT_RANDOM_ATTEMPTS} random fallback ports.",
            })
            result["ok"] = False
        else:
            api_port = chosen_api_port
            write_ports(data_root, api_port, web_port)
            pid = start_process(
                root=root,
                data_dir=data_root,
                name="api",
                exe=api_path,
                args=[],
                cwd=root,
                env_updates={
                    "MATH_PLATFORM_ROOT": str(root),
                    "MATH_PLATFORM_DATA_DIR": str(data_root),
                    "MATH_PLATFORM_PRESERVE_PROCESS_ENV": "1",
                    "DATABASE_URL": sqlite_database_url(db_path),
                    "API_REQUEST_LOG_PATH": str(data_root / "logs" / "api.jsonl"),
                    "HOST": "127.0.0.1",
                    "PORT": str(api_port),
                },
            )
            result["actions"].append({"service": "api", "status": "launched", "pid": pid, "port": api_port})

    if web_running:
        result["actions"].append({"service": "web", "status": "already_running"})
    elif not result["ok"]:
        result["actions"].append({"service": "web", "status": "skipped", "message": "API launch failed; web was not started."})
    else:
        chosen_web_port = choose_available_port(DEFAULT_WEB_PORT, WEB_FALLBACK_PORT_RANGE, avoid={api_port})
        if chosen_web_port is None:
            result["actions"].append({
                "service": "web",
                "status": "port_conflict",
                "message": f"Web port unavailable: tried {DEFAULT_WEB_PORT} and {PORT_RANDOM_ATTEMPTS} random fallback ports.",
            })
            result["ok"] = False
        else:
            web_port = chosen_web_port
            write_ports(data_root, api_port, web_port)
            pid = start_process(
                root=root,
                data_dir=data_root,
                name="web",
                exe=web_path,
                args=[str(web_server)],
                cwd=root / "web",
                env_updates={
                    "MATH_PLATFORM_ROOT": str(root),
                    "MATH_PLATFORM_DATA_DIR": str(data_root),
                    "MATH_PLATFORM_API_BASE": f"http://127.0.0.1:{api_port}",
                    "NEXT_PUBLIC_API_BASE": "",
                    "NODE_ENV": "production",
                    "HOSTNAME": "127.0.0.1",
                    "PORT": str(web_port),
                },
            )
            result["actions"].append({"service": "web", "status": "launched", "pid": pid, "port": web_port})

    write_ports(data_root, api_port, web_port)
    result["ports"] = {"api_port": api_port, "web_port": web_port}
    result["urls"] = {"api": f"http://127.0.0.1:{api_port}", "web": f"http://127.0.0.1:{web_port}"}
    result["message"] = (
        f"Services launched. Web URL: http://127.0.0.1:{web_port}. Run status to verify readiness."
        if result["ok"]
        else "Port conflict detected after bounded random fallback attempts. Stop the conflicting app or retry."
    )
    return result


def taskkill(pid: int) -> dict[str, Any]:
    if os.name == "nt" and kernel32 is not None:
        handle = kernel32.OpenProcess(PROCESS_TERMINATE | PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if handle:
            try:
                if kernel32.TerminateProcess(handle, 1):
                    return {"pid": pid, "exit_code": 0, "stdout": "terminated by TerminateProcess", "stderr": ""}
                error = ctypes.get_last_error()
            finally:
                kernel32.CloseHandle(handle)
            direct_error = f"TerminateProcess failed with Windows error {error}"
        else:
            direct_error = f"OpenProcess failed with Windows error {ctypes.get_last_error()}"
    else:
        direct_error = "TerminateProcess unavailable"

    command = [str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"), "/PID", str(pid), "/T", "/F"]
    try:
        completed = subprocess.run(command, text=True, capture_output=True, timeout=8)
        stderr = completed.stderr.strip()
        if completed.returncode != 0 and direct_error:
            stderr = f"{direct_error}; {stderr}".strip("; ")
        return {"pid": pid, "exit_code": completed.returncode, "stdout": completed.stdout.strip(), "stderr": stderr}
    except Exception as exc:
        return {"pid": pid, "exit_code": -1, "stderr": f"{direct_error}; {exc}"}


def stop(root: Path, data_dir: Path | None = None) -> dict[str, Any]:
    root = root.resolve()
    data_root = runtime_data_dir(root, data_dir)
    killed: list[dict[str, Any]] = []
    seen: set[int] = set()
    for name in ("api", "web"):
        pid = read_pid(root, name, data_dir=data_root)
        if pid:
            path = query_process_path(pid)
            if path_is_under(path, root):
                killed.append(taskkill(pid))
                seen.add(pid)
            remove_pid(root, name, data_dir=data_root)
    for row in release_processes(root):
        pid = int(row["pid"])
        if pid not in seen:
            killed.append(taskkill(pid))
            seen.add(pid)
    time.sleep(0.5)
    return {"root": str(root), "data_dir": str(data_root), "killed": killed, "remaining": release_processes(root)}


def status(root: Path, data_dir: Path | None = None) -> dict[str, Any]:
    root = root.resolve()
    data_root = runtime_data_dir(root, data_dir)
    runtime_ports = read_ports(data_root)
    api_port = runtime_ports.get("api_port", DEFAULT_API_PORT)
    web_port = runtime_ports.get("web_port", DEFAULT_WEB_PORT)
    api_base = f"http://127.0.0.1:{api_port}"
    web_base = f"http://127.0.0.1:{web_port}"
    processes = release_processes(root)
    api_path = (root / "api" / "math-api.exe").resolve()
    web_path = (root / "web" / "node.exe").resolve()
    api_owned = any(Path(row["path"]).resolve() == api_path for row in processes if row.get("path"))
    web_owned = any(Path(row["path"]).resolve() == web_path for row in processes if row.get("path"))
    api_ok = health(f"{api_base}/api/health")
    web_ok = web_health(f"{web_base}/")
    settings = fetch_json(f"{api_base}/api/settings/models") if api_ok else None
    has_api_key = settings.get("has_api_key") if settings else read_release_key_state(root)
    default_model = settings.get("default_model") if settings else None
    return {
        "root": str(root),
        "data_dir": str(data_root),
        "database": str(database_path(root, data_root)),
        "ports": {"api_port": api_port, "web_port": web_port},
        "urls": {"api": api_base, "web": web_base},
        "api_ok": api_ok,
        "web_ok": web_ok,
        "api_owned_by_this_folder": api_owned,
        "web_owned_by_this_folder": web_owned,
        "api_port_occupied_by_other": api_ok and not api_owned,
        "web_port_occupied_by_other": web_ok and not web_owned,
        "has_api_key": bool(has_api_key),
        "default_model": default_model,
        "api_pid_file": read_pid(root, "api", data_dir=data_root),
        "web_pid_file": read_pid(root, "web", data_dir=data_root),
        "process_count": len(processes),
        "processes": processes,
    }


def doctor(root: Path, data_dir: Path | None = None) -> dict[str, Any]:
    root = root.resolve()
    data_root = runtime_data_dir(root, data_dir)
    db_path = database_path(root, data_root)
    required = [
        root / ".env",
        root / "api" / "math-api.exe",
        root / "web" / "node.exe",
        root / "web" / "server.js",
        db_path,
    ]
    missing = [str(path) for path in required if not path.exists()]
    current_status = status(root, data_root)
    db_integrity = sqlite_integrity_check_file(db_path) if db_path.exists() else {"ok": False, "error_code": "database_missing"}
    port_conflict = bool(current_status.get("api_port_occupied_by_other") or current_status.get("web_port_occupied_by_other"))
    services_started = current_status["process_count"] > 0
    services_ready = (
        bool(current_status.get("api_ok"))
        and bool(current_status.get("web_ok"))
        and bool(current_status.get("api_owned_by_this_folder"))
        and bool(current_status.get("web_owned_by_this_folder"))
    )
    return {
        "root": str(root),
        "data_dir": str(data_root),
        "ok": (
            not missing
            and not port_conflict
            and current_status["process_count"] in (0, 2)
            and (not services_started or services_ready)
            and bool(db_integrity.get("ok"))
        ),
        "missing": missing,
        "port_conflict": port_conflict,
        "services_ready": services_ready,
        "status": current_status,
        "database": {
            "path": str(db_path),
            "integrity_check": db_integrity,
            "table_counts": sqlite_table_counts_file(db_path) if db_path.exists() else {},
            "size_bytes": db_path.stat().st_size if db_path.exists() else 0,
        },
        "release_manifest": load_release_manifest_summary(root),
        "recent_backups": latest_backup_summaries(root, limit=3, data_dir=data_root),
    }


def print_result(data: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        for key, value in data.items():
            print(f"{key}: {value}")


def main() -> int:
    configure_stdio()
    parser = argparse.ArgumentParser(prog="MathPlatform")
    parser.add_argument("command", choices=["start", "stop", "status", "doctor", "open", "backup", "restore", "diagnostic"])
    parser.add_argument("--root", default=None)
    parser.add_argument("--data-dir", default=None, help="Use an isolated runtime data directory for database, pid files, logs, backups, and diagnostics.")
    parser.add_argument("--smoke-profile", default=None, help="Shortcut for --data-dir .smoke-profiles/<name>; useful for release smoke tests.")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--file", default=None, help="Restore source: backup directory or manifest.json.")
    parser.add_argument("--dry-run", action="store_true", help="Validate restore source without changing data.")
    parser.add_argument("--confirm", default=None, help="Restore confirmation phrase. Use the backup_id from dry-run.")
    args = parser.parse_args()
    root = release_root(args.root)
    data_dir = runtime_data_dir(root, args.data_dir, args.smoke_profile)

    try:
        if args.command == "start":
            result = start(root, data_dir)
            print_result(result, args.json)
            return 0 if result.get("ok", True) else 1
        if args.command == "stop":
            print_result(stop(root, data_dir), args.json)
            return 0
        if args.command == "status":
            print_result(status(root, data_dir), True if args.json else False)
            return 0
        if args.command == "doctor":
            result = doctor(root, data_dir)
            print_result(result, True if args.json else False)
            return 0 if result.get("ok") else 1
        if args.command == "backup":
            result = backup(root, data_dir)
            print_result(result, True if args.json else False)
            return 0 if result.get("ok") else 1
        if args.command == "restore":
            if not args.file:
                result = {"ok": False, "error_code": "restore_file_required", "message": "Pass --file with a backup directory or manifest.json."}
            else:
                result = restore_backup(root, file_arg=args.file, dry_run=args.dry_run, confirm=args.confirm, data_dir=data_dir)
            print_result(result, True if args.json else False)
            return 0 if result.get("ok") else 1
        if args.command == "diagnostic":
            result = diagnostic_bundle(root, data_dir)
            print_result(result, True if args.json else False)
            return 0 if result.get("ok") else 1
        if args.command == "open":
            web_port = read_ports(data_dir).get("web_port", DEFAULT_WEB_PORT)
            os.startfile(f"http://127.0.0.1:{web_port}")  # type: ignore[attr-defined]
            return 0
    except Exception as exc:
        print_result({"ok": False, "error": str(exc), "error_type": exc.__class__.__name__}, True if args.json else False)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

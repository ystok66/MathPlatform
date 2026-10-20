"""Build a clean, self-contained macOS release on a native macOS runner."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import sqlite3
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]


def run(command: list[str], cwd: Path, timeout: int, name: str, logs: Path, env: dict | None = None) -> None:
    with (logs / f"{name}.log").open("wb") as log:
        child = subprocess.Popen(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT,
                                 start_new_session=True)
        try:
            code = child.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=5)
            raise RuntimeError(f"{name} exceeded {timeout}s; preserved log: {log.name}")
    if code:
        raise RuntimeError(f"{name} failed with exit {code}; see {log.name}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arch", choices=("arm64", "x86_64"), required=True)
    args = parser.parse_args()
    if sys.platform != "darwin" or platform.machine() != args.arch:
        raise SystemExit("Build on a native Mac runner matching --arch. Cross-compilation is not supported.")
    out = ROOT / "out"
    release = out / f"math-platform-macos-{args.arch}"
    work = out / f"macos-work-{args.arch}"
    if release.exists() or work.exists():
        raise SystemExit("Use a clean checkout/output folder; this builder never deletes an existing release.")
    release.mkdir(parents=True)
    work.mkdir()
    logs = out / f"macos-logs-{args.arch}"
    logs.mkdir(exist_ok=True)
    web = ROOT / "apps/web"
    env = dict(os.environ, NEXT_PUBLIC_API_BASE="", NEXT_IGNORE_INCORRECT_LOCKFILE="1", NEXT_TELEMETRY_DISABLED="1")
    env.pop("NEXT_OUTPUT_MODE", None)
    run(["npm", "ci", "--no-audit", "--no-fund"], web, 180, "npm-ci", logs, env)
    run(["npm", "run", "check", "--", "--incremental", "false"], web, 180, "typescript", logs, env)
    run(["npm", "run", "build"], web, 300, "web-build", logs, env)
    common = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
              "--distpath", str(work / "dist"), "--workpath", str(work / "build"),
              "--specpath", str(work), "--target-arch", args.arch]
    run(common + ["--name", "math-api", "--paths", str(ROOT / "apps/api"),
                  "--hidden-import", "app.main", "--collect-submodules", "app",
                  "--collect-data", "certifi", str(ROOT / "apps/api/packaged_api.py")],
        ROOT, 360, "api-build", logs)
    run(common + ["--name", "MathPlatform", "--windowed", "--osx-bundle-identifier",
                  "org.mathplatform.launcher", str(ROOT / "tools/launcher/math_platform_macos.py")],
        ROOT, 180, "launcher-build", logs)
    shutil.copytree(work / "dist/math-api", release / "api", symlinks=True)
    shutil.copytree(work / "dist/MathPlatform.app", release / "MathPlatform.app", symlinks=True)
    shutil.copytree(web / ".next/standalone", release / "web", symlinks=True)
    shutil.copytree(web / ".next/static", release / "web/.next/static", dirs_exist_ok=True)
    if (web / "public").exists():
        shutil.copytree(web / "public", release / "web/public", dirs_exist_ok=True)
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node is missing on the build runner.")
    shutil.copy2(Path(node).resolve(), release / "web/node")
    (release / "web/node").chmod(0o755)
    for name in ("prompts", "packages/schemas", "question_data"):
        if (ROOT / name).exists():
            shutil.copytree(ROOT / name, release / name)
    (release / "data").mkdir(mode=0o700)
    bank = ROOT / "release-data/question-bank.sqlite.gz"
    expected = (ROOT / "release-data/question-bank.sha256").read_text().split()[0]
    if hashlib.file_digest(bank.open("rb"), "sha256").hexdigest() != expected:
        raise RuntimeError("Sanitized question-bank snapshot hash mismatch.")
    database = release / "data/math_platform_dev.db"
    with gzip.open(bank, "rb") as source, database.open("wb") as target:
        shutil.copyfileobj(source, target)
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Question-bank integrity check failed.")
        for table in ("submissions", "practice_sessions", "generation_batches", "encrypted_configs"):
            # Schema names can evolve; inspect presence without skipping a present table.
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                if db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]:
                    raise RuntimeError(f"Public bank contains private/runtime rows: {table}")
        question_count = db.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
    database.chmod(0o600)
    env_text = "\n".join(["APP_ENV=production", "DATABASE_URL=sqlite:///./data/math_platform_dev.db",
                           "LIAOBOTS_API_BASE=https://api.openai.com/v1", "LIAOBOTS_API_KEY=",
                           "DEFAULT_MODEL=gpt-5.6-sol", "PROJECT_ASSISTANT_MODEL=gpt-5.6-sol",
                           "EMERGENCY_RECOVERY_AGENT_MODEL=gpt-5.6-sol", ""])
    (release / ".env").write_text(env_text, encoding="utf-8")
    (release / ".env").chmod(0o600)
    for filename, command in (("START_HERE.command", "launch"), ("STOP.command", "stop"), ("CHECK.command", "doctor")):
        script = '#!/bin/sh\nset -eu\nROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"\n' \
                 + 'exec "$ROOT/MathPlatform.app/Contents/MacOS/MathPlatform" ' + command + '\n'
        (release / filename).write_text(script, encoding="utf-8", newline="\n")
        (release / filename).chmod(0o755)
    shutil.copy2(ROOT / "README.md", release / "README.md")
    metadata = {"platform": "macos", "architecture": args.arch, "build_host": platform.platform(),
                "question_count": question_count, "build_time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "runtime_smoke": "RUN_SEPARATELY", "signing": "PyInstaller ad-hoc; not Apple notarized",
                "end_user_python_required": False, "end_user_node_required": False}
    (release / "mac-build-info.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    archive = out / f"MathPlatform-macOS-{args.arch}.zip"
    run(["/usr/bin/ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(release), str(archive)],
        ROOT, 180, "archive", logs)
    with archive.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    archive.with_suffix(".zip.sha256").write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()

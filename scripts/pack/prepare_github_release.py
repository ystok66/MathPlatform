"""Prepare a NEW source/public portable bundle, never edit the working release.

Run after building the web standalone output and API executable. This script
requires only Python's standard library and does not start services or call AI.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
import zipfile

ROOT = Path(__file__).resolve().parents[2]
EXCLUDED_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache", "backups", "logs", "diagnostics", ".tmp", "coverage"}
SOURCE_EXTENSIONS = {".py", ".pyi", ".cs", ".tsx", ".ts", ".js", ".jsx", ".cjs", ".mjs", ".css", ".json", ".md", ".txt", ".toml", ".ini", ".mako", ".yml", ".yaml", ".ps1", ".sh", ".cmd", ".svg", ".png", ".jpg", ".jpeg", ".ico", ".woff", ".woff2"}
TEXT_EXTENSIONS = SOURCE_EXTENSIONS - {".png", ".jpg", ".jpeg", ".ico", ".woff", ".woff2"}
BANK_TABLES = ("source_files", "taxonomy_tags", "taxonomy_aliases", "questions", "question_versions", "answer_specs", "question_tags", "question_variants", "question_duplicate_aliases")
PRIVATE_JSON_KEY = re.compile(r"api.?key|authorization|secret|password|(^|_)token($|_)|student_|learner_|submitted_|user_answer|user_reasoning|free_text|conversation|messages|raw_response|provider_response|raw_request|request_payload|response_payload|^prompt$|^cot$|^trace_id$|^session_id$", re.I)

ENV_TEMPLATE = """APP_NAME=Math Platform
DATABASE_URL=sqlite:///./data/math_platform_dev.db
QUESTION_DATA_DIR=question_data
CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
LIAOBOTS_BASE_URL=https://api.openai.com/v1
LIAOBOTS_API_KEY=
MODEL_PROVIDER=openai_compatible
DEFAULT_MODEL=gpt-5.6-terra
GENERATOR_MODEL_LE_3=gpt-5.6-terra
GENERATOR_MODEL_4=gpt-5.6-sol
GENERATOR_MODEL_5=gpt-5.6-sol
EMERGENCY_RECOVERY_AGENT_MODEL=gpt-5.6-sol
PROJECT_ASSISTANT_MODEL=gpt-5.6-sol
AI_STRICT_REVIEWER_MODEL_ALLOWLIST=
AI_MODEL_PRICE_JSON=
AI_MONTHLY_BUDGET_USD=0
AI_DAILY_BUDGET_USD=0
AI_INTERACTION_TIMEOUT_SECONDS=60
AI_GENERATOR_TIMEOUT_SECONDS=180
AI_REVIEWER_TIMEOUT_SECONDS=120
AI_JOB_TIMEOUT_SECONDS=420
AI_GLOBAL_MAX_CONCURRENCY=6
AI_EMERGENCY_AGENT_MAX_CONCURRENCY=1
AI_MAX_GENERATION_CALLS_PER_JOB=5
AI_MAX_REVIEWER_CALLS_PER_JOB=5
AI_MAX_GENERATION_CALLS_PER_BATCH=500
AI_MAX_REVIEWER_CALLS_PER_BATCH=500
API_REQUEST_LOG_PATH=logs/api.jsonl
API_REQUEST_LOG_MAX_BYTES=1048576
API_REQUEST_LOG_BACKUP_COUNT=5
API_REQUEST_LOG_RETENTION_DAYS=14
API_SLOW_REQUEST_MS=2000
"""


def write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def secret_values(release: Path) -> set[str]:
    result = set()
    for path in (ROOT / ".env", release / ".env"):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            name, sep, value = line.partition("=")
            if sep and re.search(r"KEY|TOKEN|SECRET|PASSWORD", name, re.I):
                value = value.strip().strip("\"'")
                if len(value) >= 8:
                    result.add(value)
    return result


def scrub_text(value: str, secrets: set[str]) -> str:
    for secret in sorted(secrets, key=len, reverse=True):
        value = value.replace(secret, "REDACTED_LOCAL_SECRET")
    return value


def scrub_json(value, secrets: set[str]):
    if isinstance(value, dict):
        return {key: scrub_json(item, secrets) for key, item in value.items() if not PRIVATE_JSON_KEY.search(key)}
    if isinstance(value, list):
        return [scrub_json(item, secrets) for item in value]
    if isinstance(value, str):
        return scrub_text(value, secrets)
    return value


def copy_tree(source: Path, target: Path, secrets: set[str], *, source_only: bool) -> None:
    if not source.is_dir():
        return
    excluded = EXCLUDED_DIRS | ({"node_modules", ".next", "out", "dist", "build"} if source_only else {"cache"})
    for parent, directories, files in os.walk(source, followlinks=False):
        directories[:] = [name for name in directories if name not in excluded and not (Path(parent) / name).is_symlink()]
        for name in files:
            item = Path(parent) / name
            # certifi's public trust roots are runtime data, not private keys.
            public_ca = not source_only and name == "cacert.pem" and item.parent.name == "certifi"
            if item.is_symlink() or name.startswith(".env") or (name.endswith((".log", ".db", ".db-wal", ".db-shm", ".sqlite", ".pyc", ".tsbuildinfo", ".dpapi", ".pem", ".key")) and not public_ca):
                continue
            if source_only and item.suffix.lower() not in SOURCE_EXTENSIONS:
                continue
            dest = target / item.relative_to(source)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if item.suffix.lower() in TEXT_EXTENSIONS:
                try:
                    value = item.read_text(encoding="utf-8-sig")
                except UnicodeDecodeError:
                    shutil.copy2(item, dest)
                else:
                    write(dest, scrub_text(value, secrets))
            else:
                shutil.copy2(item, dest)


def export_bank(source: Path, target: Path, secrets: set[str]) -> dict[str, int]:
    """Create a fresh DB; do not leave deleted personal data in SQLite free pages."""
    target.parent.mkdir(parents=True, exist_ok=True)
    counts = {}
    with sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True, timeout=10) as old:
        old.row_factory = sqlite3.Row
        old.execute("BEGIN")
        schema = old.execute("SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
        with sqlite3.connect(target) as new:
            new.execute("PRAGMA foreign_keys=OFF")
            for row in schema:
                if row["type"] == "table":
                    new.execute(row["sql"])
            names = {row["name"] for row in schema if row["type"] == "table"}
            for table in BANK_TABLES:
                if table not in names:
                    continue
                columns = old.execute(f'PRAGMA table_info("{table}")').fetchall()
                column_names = [row["name"] for row in columns]
                json_columns = {row["name"] for row in columns if "JSON" in row["type"].upper()}
                placeholders = ",".join("?" for _ in columns)
                quoted_columns = ",".join('"' + name.replace('"', '""') + '"' for name in column_names)
                insert = f'INSERT INTO "{table}" ({quoted_columns}) VALUES ({placeholders})'
                count = 0
                for original in old.execute(f'SELECT * FROM "{table}"'):
                    row = dict(original)
                    for name in json_columns:
                        if row[name] is not None:
                            value = scrub_json(json.loads(row[name]), secrets)
                            if table == "question_versions" and isinstance(value, dict):
                                provenance = value.get("provenance")
                                if isinstance(provenance, dict):
                                    safe = {key: provenance[key] for key in ("origin_type", "model", "generator_model", "reviewer_model", "prompt_version", "importer_version") if isinstance(provenance.get(key), (str, int, bool))}
                                    value["provenance"] = {**safe, "public_export": True}
                            row[name] = json.dumps(value, ensure_ascii=False, sort_keys=True)
                        
                    if table == "question_versions":
                        row["content_hash"] = hashlib.sha256(row["question_v2"].encode("utf-8")).hexdigest()
                    if "reviewer" in row:
                        row["reviewer"] = "public_export"
                    if table == "source_files":
                        row["filename"] = row["filename"].replace("\\", "/").rsplit("/", 1)[-1]
                    new.execute(insert, [scrub_text(row[name], secrets) if isinstance(row[name], str) else row[name] for name in column_names])
                    count += 1
                counts[table] = count
            for row in schema:
                if row["type"] in {"index", "view", "trigger"}:
                    new.execute(row["sql"])
            new.commit()
        old.rollback()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-release", type=Path, default=ROOT / "dist" / "math-platform")
    parser.add_argument("--api-build", type=Path, default=ROOT / ".tmp" / "pack-api-dist" / "math-api")
    parser.add_argument("--start-exe", type=Path, default=ROOT / ".tmp" / "pack-start-exe" / "StartMathPlatform.exe")
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT) or output == ROOT:
        raise SystemExit("Output must be a NEW child directory of the project.")
    release = args.source_release.resolve()
    web_build = ROOT / "apps" / "web" / ".next" / "standalone"
    required = [release / "launcher" / "MathPlatform.exe", release / "web" / "node.exe", args.api_build / "math-api.exe", args.start_exe, web_build / "server.js", release / "data" / "math_platform_dev.db"]
    for path in required:
        if not path.is_file():
            raise SystemExit(f"Required build input is missing: {path}")
    output.mkdir(exist_ok=False)
    source = output / "repository"
    portable = output / "assets" / "math-platform"
    secrets = secret_values(release)
    print("Preparing clean source tree...", flush=True)
    for relative in ("apps/api", "apps/web/app", "apps/web/public", "packages", "prompts", "scripts", "tests", "tools/launcher"):
        copy_tree(ROOT / relative, source / relative, secrets, source_only=True)
    for relative in ("apps/web/package.json", "apps/web/package-lock.json", "apps/web/tsconfig.json", "apps/web/next.config.js", "apps/web/next-env.d.ts", "pytest.ini", "AGENTS.md", "docker-compose.yml", "tools/run-bounded.ps1", "tools/test-ui-language.cjs", "tools/test-startup-sync.cjs", "docs/api.md", "docs/architecture.md", "docs/CHANGELOG.md", "docs/FIRST_RUN_AND_GITHUB_RELEASE.md"):
        item = ROOT / relative
        if item.is_file():
            write(source / relative, scrub_text(item.read_text(encoding="utf-8-sig"), secrets))
    source_env = ENV_TEMPLATE.replace("sqlite:///./data/math_platform_dev.db", "sqlite:///./math_platform_dev.db")
    write(source / ".env.example", source_env)
    write(source / ".gitignore", """.env
.env.*
!.env.example
*.db
*.db-*
*.sqlite*
*.log
*.pyc
*.tsbuildinfo
*.exe
*.dll
*.zip
*.dpapi
*.pem
*.key
__pycache__/
.venv/
node_modules/
.next/
.tmp/
.pytest_cache/
data/
logs/
backups/
dist/
github-release*/
""")
    write(source / "README.md", """# AI-Assisted Elementary Math Olympiad Practice

An AI-assisted elementary math olympiad practice app with a question bank, reviewed AI generation,
practice and strict tests, revision history, learning reports, and an AI assistant.
The interface defaults to English and can switch to Chinese. Existing questions
keep their original language.

## Use the Windows app

1. Download the Windows ZIP from this project's Releases, not the source ZIP.
2. Extract the whole `math-platform` folder to a writable local directory.
3. Double-click `StartMathPlatform.exe` or `START_HERE.cmd`. Python and Node.js are included.
4. In the first-run dialog, enter your provider's API base URL and API key.
5. Click **Connect & fetch models**, select a model, then **Test an actual model call**.
6. Click **Save & start**. You can skip AI setup to use the existing bank.

The provider must support OpenAI-compatible `/models` and `/chat/completions`.
A model appearing in the list is not a guarantee that it is callable by your key;
the selected model is checked by an actual short request, which may incur a fee.
Reopen setup using **API setup** in the header. Configure individual AI roles in Admin.
Use `stop.cmd` to stop the app. Keep the entire folder when transferring computers.

## Source and development

Backend: Python / FastAPI / SQLAlchemy. Frontend: Next.js / React / TypeScript.
Install backend dependencies from `apps/api/requirements.txt`; run `npm ci` in
`apps/web` for frontend dependencies. Copy `.env.example` to `.env` for local setup.
Use the bounded commands and service-management rules in `AGENTS.md`.
Question data, personal configuration, histories, logs, executables and build output
are deliberately not committed in this source tree.

Build the frontend with `npm run build` in `apps/web`, with `NEXT_PUBLIC_API_BASE`
empty for a same-origin portable build. Build the backend and launcher with the
scripts in `scripts/pack`; `build_start_exe.ps1` builds the double-click Windows entry.
The public preparation script takes an existing portable
runtime plus current frontend/API builds and writes a NEW sanitized folder:

```text
python scripts/pack/prepare_github_release.py --output github-release-new --source-release dist/math-platform
```

Only upload its `repository` contents as source. Attach the Windows ZIP under Releases.
Do not upload your in-use runtime folder after entering a personal API key.

## Distribution status

This snapshot has no newly selected software license. Collected competition
questions retain their respective rights; obtain permission before publicly
redistributing the optional question-bank-containing Windows asset.
See `docs/FIRST_RUN_AND_GITHUB_RELEASE.md` for scope and limitations.
""")
    print("Assembling independent Windows runtime...", flush=True)
    copy_tree(args.api_build, portable / "api", secrets, source_only=False)
    shutil.copy2(args.start_exe, portable / "StartMathPlatform.exe")
    copy_tree(release / "launcher", portable / "launcher", secrets, source_only=False)
    # Retain runtime DLLs supplied by the existing working distribution.
    for old in (release / "api" / "_internal").glob("*.dll"):
        dest = portable / "api" / "_internal" / old.name
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(old, dest)
    copy_tree(web_build, portable / "web", secrets, source_only=False)
    copy_tree(ROOT / "apps" / "web" / ".next" / "static", portable / "web" / ".next" / "static", secrets, source_only=False)
    copy_tree(ROOT / "apps" / "web" / "public", portable / "web" / "public", secrets, source_only=False)
    shutil.copy2(release / "web" / "node.exe", portable / "web" / "node.exe")
    for relative in ("prompts", "packages"):
        copy_tree(ROOT / relative, portable / relative, secrets, source_only=True)
    copy_tree(source / "docs", portable / "docs", secrets, source_only=True)
    for item in (release / "question_data").glob("*.md"):
        write(portable / "question_data" / item.name, scrub_text(item.read_text(encoding="utf-8-sig"), secrets))
    write(portable / ".env", ENV_TEMPLATE)
    write(portable / ".env.example", ENV_TEMPLATE)
    for name in ("START_HERE.cmd", "start.cmd", "stop.cmd", "check.cmd", "backup.cmd", "restore.cmd"):
        shutil.copy2(release / name, portable / name)
    write(portable / "README.md", """# AI-Assisted Elementary Math Olympiad Practice

For elementary (primary-school) math olympiad practice. Star ratings indicate difficulty, not school grade.

## Start here

Keep this entire folder together. Double-click StartMathPlatform.exe or START_HERE.cmd on Windows.
No Python or Node.js installation is required. The local service can take time
to initialize; if the page is not ready, wait and refresh it.

The first-run API setup dialog accepts an OpenAI-compatible API base URL and key,
retrieves a selectable model list, and tests an actual short model call before
saving. The test may be billed by your provider. Choose Not now to use the bank
without AI. Reopen setup with API setup in the page header.

This distribution has a question-bank-only database, no saved API key, no learner
history, no assistant conversations and no generation-job history. Your in-use
copy will contain personal data after you start using it: do not publish that copy.

Use check.cmd for service status and stop.cmd to stop services. Default ports
are 3000 and 8001; the launcher can select alternatives when they are occupied.

## 中文

保留整个文件夹，双击 StartMathPlatform.exe 或 START_HERE.cmd；不需要安装 Python 或 Node.js。
首次打开会弹出 API 配置界面：填写地址和密钥，获取模型列表，下拉选择模型，
测试真实调用，成功后保存。测试可能产生少量费用。可点“暂时跳过”先使用题库。
点击 English / 中文 切换语言；以后可从页面顶部“API 设置”重新打开配置。
此发布副本仅带题库，不带个人密钥、答题历史、报告、对话和出题任务记录。
使用后产生的个人数据不要再作为公共发布包上传。
""")
    print("Exporting bank into a fresh database (no history or secret tables)...", flush=True)
    counts = export_bank(release / "data" / "math_platform_dev.db", portable / "data" / "math_platform_dev.db", secrets)
    print(json.dumps({"bank_rows_copied": counts}), flush=True)
    version = "public-first-run-" + datetime.now(timezone.utc).strftime("%Y%m%d")
    subprocess.run([sys.executable, str(ROOT / "scripts" / "pack" / "release_manifest.py"), str(portable), "--app-version", version], check=True, timeout=300)
    archive = output / "assets" / "MathPlatform-Windows.zip"
    print("Compressing Windows release asset...", flush=True)
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=3) as bundle:
        for parent, _, files in os.walk(portable):
            for name in files:
                item = Path(parent) / name
                bundle.write(item, item.relative_to(portable.parent).as_posix())
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(), "version": version,
        "bank_rows_copied": counts, "zip_bytes": archive.stat().st_size,
        "source": "repository", "portable": "assets/math-platform", "archive": "assets/MathPlatform-Windows.zip",
        "original_release_modified": False, "personal_history_included": False,
        "saved_keys_included": False, "runtime_smoke": "NOT_RUN", "paid_api_test": "NOT_RUN",
    }
    write(output / "PREPARATION_REPORT.json", json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    write(output / "PUBLISH.md", """# GitHub publication folder

- Upload the contents of `repository/` as the GitHub source repository.
- Attach `assets/MathPlatform-Windows.zip` to a GitHub Release for end users.
- `assets/math-platform/` is the uncompressed Windows app; double-click START_HERE.cmd.
- Do not commit `assets/` into Git history.
- No GitHub upload has been performed by this preparation step.
- Runtime/browser smoke tests and paid API calls are NOT_RUN for this snapshot.

源码在 repository；Windows 完整运行包在 assets/MathPlatform-Windows.zip。
新电脑解压后，双击 math-platform/START_HERE.cmd。
此目录是本地发布准备，尚未上传 GitHub；软件许可和收集题目的公开分发权需由维护者确认。
原 dist/math-platform 的个人配置与数据库未修改。
""")
    print(json.dumps(report, ensure_ascii=True, indent=2), flush=True)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    main()

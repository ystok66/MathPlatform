"""Create NEW sanitized candidate archives; never promote an accepted release."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import zipfile

from prepare_github_release import BANK_TABLES, ENV_TEMPLATE, SOURCE_EXTENSIONS, copy_tree, export_bank, scrub_text, secret_values, write

ROOT = Path(__file__).resolve().parents[2]


def archive_tree(folder: Path, archive: Path, prefix: str) -> dict:
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=3) as bundle:
        for item in sorted(folder.rglob("*")):
            if item.is_file() and not item.is_symlink():
                bundle.write(item, (Path(prefix) / item.relative_to(folder)).as_posix())
    with archive.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    write(archive.with_suffix(archive.suffix + ".sha256"), f"{digest}  {archive.name}\n")
    return {"name": archive.name, "bytes": archive.stat().st_size, "sha256": digest}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", default="language-repair-candidate-" + datetime.now(timezone.utc).strftime("%Y%m%d"))
    args = parser.parse_args()
    candidate = args.candidate.resolve()
    output = args.output.resolve()
    previous = ROOT / "github-release-20261016"
    if not candidate.is_relative_to(ROOT / "dist") or candidate == ROOT / "dist" / "math-platform":
        raise SystemExit("Use an isolated candidate, not the production runtime.")
    if not output.is_relative_to(ROOT) or output == ROOT or output.exists():
        raise SystemExit("Output must be a new project child directory.")
    for required in (candidate / "api/math-api.exe", candidate / "launcher/MathPlatform.exe", candidate / "web/server.js", previous / "assets/math-platform/data/math_platform_dev.db", ROOT / "docs/LANGUAGE_ACCEPTANCE_REPORT.md"):
        if not required.is_file():
            raise SystemExit(f"Missing required input: {required}")
    output.mkdir()
    repository = output / "repository"
    runtime = output / "assets/math-platform"
    secrets = secret_values(candidate)
    SOURCE_EXTENSIONS.update({".command", ".spec", ".sha256"})
    print("Copying sanitized source and candidate program resources...", flush=True)
    copy_tree(previous / "repository", repository, secrets, source_only=True)
    for name in (".gitignore", ".gitattributes"):
        item = previous / "repository" / name
        if item.is_file():
            write(repository / name, scrub_text(item.read_text(encoding="utf-8-sig"), secrets))
    for relative in ("apps/api", "apps/web/app", "apps/web/public", "packages", "prompts", "scripts", "tests", "tools/launcher"):
        copy_tree(ROOT / relative, repository / relative, secrets, source_only=True)
    for relative in ("apps/web/package.json", "apps/web/package-lock.json", "apps/web/tsconfig.json", "apps/web/next.config.js", "apps/web/next-env.d.ts", "pytest.ini", "AGENTS.md", "tools/run-bounded.ps1", "tools/build-language-resources.cjs", "tools/check-language-resources.cjs", "docs/PUBLIC_README.md", "docs/LANGUAGE_SETTINGS.md", "docs/LANGUAGE_ACCEPTANCE_REPORT.md"):
        item = ROOT / relative
        if item.is_file():
            write(repository / relative, scrub_text(item.read_text(encoding="utf-8-sig"), secrets))
    for relative in ("api", "web", "launcher", "packages", "prompts", "docs"):
        copy_tree(candidate / relative, runtime / relative, secrets, source_only=False)
    copy_tree(previous / "assets/math-platform/question_data", runtime / "question_data", secrets, source_only=True)
    entrypoints = {item.name: item for item in (previous / "assets/math-platform").iterdir() if item.is_file() and item.suffix in {".exe", ".cmd"}}
    entrypoints.update({item.name: item for item in candidate.iterdir() if item.is_file() and item.suffix in {".exe", ".cmd"}})
    for item in entrypoints.values():
        if item.is_file() and item.suffix in {".exe", ".cmd"}:
            target = runtime / item.name
            if item.suffix == ".cmd":
                write(target, scrub_text(item.read_text(encoding="utf-8-sig"), secrets))
            else:
                shutil.copy2(item, target)
    write(runtime / ".env", ENV_TEMPLATE)
    write(runtime / ".env.example", ENV_TEMPLATE)
    write(repository / ".env.example", ENV_TEMPLATE.replace("sqlite:///./data/math_platform_dev.db", "sqlite:///./math_platform_dev.db"))
    counts = export_bank(previous / "assets/math-platform/data/math_platform_dev.db", runtime / "data/math_platform_dev.db", secrets)
    with sqlite3.connect(runtime / "data/math_platform_dev.db") as db:
        names = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        private_counts = {name: db.execute('SELECT count(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0] for name in names if name not in BANK_TABLES and name != "alembic_version"}
    if any(private_counts.values()):
        raise SystemExit("Public candidate contains non-bank records; do not distribute.")
    print("Creating the sanitized source-bank snapshot...", flush=True)
    seed = repository / "release-data/question-bank.sqlite.gz"
    seed.parent.mkdir(parents=True, exist_ok=True)
    with (runtime / "data/math_platform_dev.db").open("rb") as source, seed.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=6, mtime=0, filename="") as target:
            shutil.copyfileobj(source, target)
    with seed.open("rb") as handle:
        seed_digest = hashlib.file_digest(handle, "sha256").hexdigest()
    write(seed.parent / "question-bank.sha256", f"{seed_digest}  {seed.name}\n")
    write(seed.parent / "README.md", "# Sanitized optional question-bank snapshot\n\n"
          f"Contains {counts.get('questions', 0)} bank questions and schema; all non-bank records are empty.\n"
          "This is the clean public-bank snapshot, not the private acceptance database.\n"
          "Question redistribution rights must be confirmed before public distribution.\n")
    note = "> **LANGUAGE CANDIDATE, NOT FULLY ACCEPTED.** Known failures and NOT_RUN cases remain. This banner supersedes the older validation claims below. Do not replace the accepted release. See [acceptance evidence](docs/LANGUAGE_ACCEPTANCE_REPORT.md) and [language settings](docs/LANGUAGE_SETTINGS.md).\n\n> **语言候选包，尚未全功能验收通过。** 下方旧版本验收说明不代表本候选包已通过。问题、失败与未测项目以上述验收报告为准。\n\n"
    readme = scrub_text((previous / "repository/README.md").read_text(encoding="utf-8-sig"), secrets)
    readme = readme.replace("# Math Practice & Learning Insights\n", "# AI-Assisted Elementary Math Olympiad Practice\n\n**AI 辅助小学奥数练习平台**\n\n" + note, 1)
    readme = readme.replace("**A local-first elementary mathematics workspace for practising, generating reviewed questions, revisiting mistakes, and understanding learning progress.**", "**An AI-assisted elementary math olympiad practice platform with a searchable question bank, reviewed question generation, targeted revision, and learning reports.**", 1)
    readme = readme.replace("## At a glance", "**Who it is for:** elementary (primary-school) learners practising math olympiad and competition-style reasoning, supported by parents or teachers. Star ratings indicate problem difficulty, not school grade; choose questions to match the learner's current foundations. `MathPlatform` remains the internal application and executable name.\n\n**适用对象：**小学阶段进行奥数与数学竞赛思维训练的学生，以及辅助学习的家长和教师。星级表示题目难度，不对应年级，应按学生当前基础选题。`MathPlatform` 保留为内部目录和程序名称。\n\n## At a glance", 1)
    readme = readme.replace("管理小学数学练习：", "管理小学奥数与数学竞赛练习：")
    readme += "\nScreenshots retained from the earlier public demonstration are illustrative; they are not visual acceptance evidence for this repair candidate. Private acceptance screenshots are not included.\n"
    readme += "\n## Independent UI and AI languages | 独立语言设置\n\nThe interface defaults to English. AI output follows the interface unless you choose English or Chinese separately. Language switching never translates old bank questions or automatically regenerates reports. New requests and queued tasks retain their submitted output language. See docs/LANGUAGE_SETTINGS.md for the shared catalog and API contract.\n\n界面默认英文；AI 输出可跟随界面或单独选择英文、中文。切换语言不会翻译旧题或自动重新生成报告。后台任务保留提交时的语言快照。新增语言的接入边界和仍待迁移项见语言说明。\n"
    write(repository / "README.md", readme)
    write(runtime / "README.md", "# AI-Assisted Elementary Math Olympiad Practice\n\n**AI 辅助小学奥数练习平台**\n\n" + note + "For elementary (primary-school) math olympiad practice. Star ratings indicate difficulty, not school grade. Extract and keep this entire folder. StartMathPlatform.exe and START_HERE.cmd are the launch entries. Configure your own provider URL and API key. This copy contains only the pre-acceptance clean bank, not test history or credentials.\n")
    for name in ("PUBLIC_README.md", "LANGUAGE_ACCEPTANCE_REPORT.md", "LANGUAGE_SETTINGS.md"):
        write(runtime / "docs" / name, scrub_text((ROOT / "docs" / name).read_text(encoding="utf-8-sig"), secrets))
    for base in (repository, runtime):
        write(base / "docs/images/overview.svg", scrub_text((ROOT / "docs/images/overview.svg").read_text(encoding="utf-8-sig"), secrets))
    packaging_note = ("\n## Sanitized packaging update / 脱敏打包说明\n\n"
                      f"Package version: `{args.version}`. Program resources come from the repaired R2 candidate; this packaging operation does not rebuild the binaries.\n\n"
                      "A new bank-only database and matching source-bank snapshot are exported from the earlier clean public distribution. API keys are blank; non-bank tables are empty; private test screenshots and runtime logs are not copied.\n\n"
                      "The earlier NOT_RUN entry for ZIP creation is historical. The packaging outcome and archive hashes are recorded separately in CANDIDATE_REPORT.json. No new functional tests, paid API calls, GitHub upload or Mac build are performed by this packaging step. Remaining acceptance failures and NOT_RUN cases above are unchanged.\n")
    acceptance = scrub_text((ROOT / "docs/LANGUAGE_ACCEPTANCE_REPORT.md").read_text(encoding="utf-8-sig"), secrets) + packaging_note
    write(repository / "docs/LANGUAGE_ACCEPTANCE_REPORT.md", acceptance)
    write(runtime / "docs/LANGUAGE_ACCEPTANCE_REPORT.md", acceptance)
    print("Applying distribution privacy guards...", flush=True)
    for base in (repository, runtime):
        for item in base.rglob("*"):
            if item.is_file() and item.suffix.lower() in {".env", ".json", ".md", ".txt", ".py", ".js", ".ts", ".tsx", ".ps1", ".cmd"} or item.is_file() and item.name.startswith(".env"):
                text = item.read_text(encoding="utf-8-sig", errors="replace")
                if any(secret in text for secret in secrets):
                    raise SystemExit(f"Secret scan failed: {item.relative_to(output)}")
    version = args.version
    print("Generating the Windows release manifest...", flush=True)
    subprocess.run([sys.executable, str(ROOT / "scripts/pack/release_manifest.py"), str(runtime), "--app-version", version], check=True, timeout=300)
    print("Compressing the Windows and source ZIPs...", flush=True)
    artifacts = [archive_tree(runtime, output / "assets/MathPlatform-Windows-Language-Candidate.zip", "math-platform"), archive_tree(repository, output / "assets/MathPlatform-Source-Language-Candidate.zip", "math-platform-source")]
    report = {"created_at": datetime.now(timezone.utc).isoformat(), "status": "CANDIDATE_NOT_FULLY_ACCEPTED", "version": version, "program_source": candidate.relative_to(ROOT).as_posix(), "artifacts": artifacts, "bank_counts": counts, "source_bank_sha256": seed_digest, "non_bank_record_counts": private_counts, "known_secret_scan": "PASS", "formal_release_overwritten": False, "functional_retest": "NOT_RUN", "new_paid_api_calls": 0, "github_upload": "NOT_RUN", "mac_build": "NOT_RUN", "acceptance_report": "repository/docs/LANGUAGE_ACCEPTANCE_REPORT.md"}
    report["project_title"] = "AI-Assisted Elementary Math Olympiad Practice"
    write(output / "CANDIDATE_REPORT.json", json.dumps(report, ensure_ascii=False, indent=2))
    write(output / "README.md", "# AI-Assisted Elementary Math Olympiad Practice\n\n**AI 辅助小学奥数练习平台**\n\nLanguage candidate, not an accepted release.\n\nWindows: assets/MathPlatform-Windows-Language-Candidate.zip\n\nSource: assets/MathPlatform-Source-Language-Candidate.zip\n\nKnown failures and coverage: repository/docs/LANGUAGE_ACCEPTANCE_REPORT.md\n\nNo GitHub upload or Mac build. Production and previously accepted ZIPs are unchanged.\n")
    print(json.dumps(report, ensure_ascii=True, indent=2), flush=True)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    main()

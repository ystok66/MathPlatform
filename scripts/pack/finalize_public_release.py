"""Finalize an already sanitized public preparation without touching working dist data."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[2]
BANK_TABLES = {"source_files", "taxonomy_tags", "taxonomy_aliases", "questions",
               "question_versions", "answer_specs", "question_tags", "question_variants",
               "question_duplicate_aliases"}


def copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def digest(file: Path) -> str:
    with file.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def archive_tree(folder: Path, destination: Path, prefix: str) -> dict:
    temporary = destination.with_suffix(".pending.zip")
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=3) as archive:
        for file in sorted(folder.rglob("*")):
            if file.is_file():
                archive.write(file, prefix + "/" + file.relative_to(folder).as_posix())
    temporary.replace(destination)
    sha = digest(destination)
    destination.with_suffix(destination.suffix + ".sha256").write_text(
        f"{sha}  {destination.name}\n", encoding="ascii")
    return {"file": destination.name, "bytes": destination.stat().st_size, "sha256": sha}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--screenshot", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT) or output == ROOT:
        raise SystemExit("Output must be a prepared child directory of the workspace.")
    repository = output / "repository"
    runtime = output / "assets/math-platform"
    database = runtime / "data/math_platform_dev.db"
    if not (output / "PREPARATION_REPORT.json").is_file() or not repository.is_dir():
        raise SystemExit("Run clean public preparation first; refusing an arbitrary directory.")
    print("Checking the clean public bank...", flush=True)
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Public-bank integrity failure.")
        tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            if table not in BANK_TABLES and db.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0]:
                raise RuntimeError(f"Public bank was used or contains private rows: {table}")
        question_count = db.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
    for line in (runtime / ".env").read_text(encoding="utf-8-sig").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        if key.strip() in {"LIAOBOTS_API_KEY", "OPENAI_API_KEY", "CONFIG_ENCRYPTION_KEY"} and value.strip().strip('"\''):
            raise RuntimeError("Public runtime contains a nonempty secret setting; refusing publication.")
    print("Adding public CA and double-click entry...", flush=True)
    copy(ROOT / ".tmp/pack-api-dist/math-api/_internal/certifi/cacert.pem", runtime / "api/_internal/certifi/cacert.pem")
    copy(ROOT / ".tmp/pack-start-exe/StartMathPlatform.exe", runtime / "StartMathPlatform.exe")
    files = ["scripts/pack/prepare_github_release.py", "scripts/pack/finalize_public_release.py",
             "scripts/pack/build_start_exe.ps1", "scripts/pack/build_public_macos.py",
             "scripts/pack/smoke_public_macos.py", "tools/launcher/StartMathPlatform.cs",
             "tools/launcher/math_platform_macos.py", ".github/workflows/build-public-macos.yml",
             "docs/PUBLIC_README.md"]
    for relative in files:
        copy(ROOT / relative, repository / relative)
    for folder in (repository, runtime):
        copy(ROOT / "docs/PUBLIC_README.md", folder / "README.md")
        for name in ("PUBLIC_RELEASE_VALIDATION.md", "FIRST_RUN_AND_GITHUB_RELEASE.md"):
            copy(ROOT / "docs" / name, folder / "docs" / name)
        for illustration in (ROOT / "docs/images").iterdir():
            if illustration.is_file() and illustration.suffix.lower() in {".jpg", ".png", ".webp", ".svg", ".md"}:
                copy(illustration, folder / "docs/images" / illustration.name)
        if args.screenshot:
            copy(args.screenshot.resolve(), folder / "docs/images/api-setup-success.jpg")
    attributes = repository / ".gitattributes"
    if not attributes.exists():
        attributes.write_text("*.sh text eol=lf\n*.command text eol=lf\n*.py text eol=lf\n*.yml text eol=lf\n*.cmd text eol=crlf\n", encoding="ascii")
    print("Creating sanitized bank snapshot for native Mac builds...", flush=True)
    seed = repository / "release-data/question-bank.sqlite.gz"
    seed.parent.mkdir(parents=True, exist_ok=True)
    with database.open("rb") as source, seed.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=6, mtime=0, filename="") as target:
            shutil.copyfileobj(source, target)
    (seed.parent / "question-bank.sha256").write_text(f"{digest(seed)}  {seed.name}\n", encoding="ascii")
    (seed.parent / "README.md").write_text(
        "# Sanitized optional question-bank snapshot\n\n"
        f"Contains {question_count} bank questions and schema, with no learner, answer, task, report, key or AI-log rows.\n"
        "Created from the clean public runtime, not a used installation. Verify SHA-256 before decompression.\n"
        "Question redistribution rights must be confirmed before making this repository public.\n", encoding="utf-8")
    for guide in (runtime / "question_data").glob("*.md"):
        copy(guide, repository / "question_data" / guide.name)
    print("Refreshing Windows release manifest (300-second limit)...", flush=True)
    subprocess.run([sys.executable, str(ROOT / "scripts/pack/release_manifest.py"), str(runtime),
                    "--app-version", "public-first-run-20261017-r2"], cwd=ROOT, check=True, timeout=300)
    print("Writing Windows and source ZIPs...", flush=True)
    artifacts = [archive_tree(runtime, output / "assets/MathPlatform-Windows.zip", "math-platform"),
                 archive_tree(repository, output / "assets/MathPlatform-Source.zip", "math-platform-source")]
    report = {"prepared_date": "2026-10-17", "question_count": question_count,
              "windows": "BUILT_AND_FIRST_RUN_SMOKE_PASSED", "macos_arm64": "NATIVE_BUILD_NOT_RUN",
              "macos_x86_64": "NATIVE_BUILD_NOT_RUN", "github_upload": "NOT_RUN",
              "original_working_dist_modified": False,
              "real_api": {"chat_requests": 1, "output_token_cap": 64, "model": "gpt-5.6-sol",
                           "advertised_model_count": 112, "chat_ok": True, "saved_real_credentials": False},
              "artifacts": artifacts,
              "limitations": "See repository/docs/PUBLIC_RELEASE_VALIDATION.md; not a whole-project regression."}
    (output / "FINALIZATION_REPORT.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (output / "RELEASE_STATUS.md").write_text(
        "# Windows prepared; Mac native build pending\n\n"
        "Windows public CA packaging repaired. First-run mock/UI and one real API chat smoke passed.\n"
        "Use assets/MathPlatform-Windows.zip; EXE and CMD entries share the same full folder.\n"
        "Source: repository/ or assets/MathPlatform-Source.zip. Its README contains the full manual.\n"
        "Mac arm64/x86_64 workflow supplied; native builds and tests NOT_RUN on this Windows host.\n"
        "No GitHub upload. Content rights and licensing require maintainer decisions.\n"
        "See FINALIZATION_REPORT.json and repository/docs/PUBLIC_RELEASE_VALIDATION.md.\n", encoding="utf-8")
    (output / "RELEASE_NOT_READY.md").write_text(
        "# Historical Windows blocker resolved\n\n"
        "The missing certifi CA bundle described by this earlier marker is restored.\n"
        "Failed discovery and startup-harness timeout are retained in the validation record.\n"
        "Current status: RELEASE_STATUS.md. Mac native packages remain unbuilt/unverified locally.\n", encoding="utf-8")
    (output / "PUBLISH.md").write_text(
        "# Publishing\n\n"
        "1. Review software licensing and bank redistribution rights.\n"
        "2. Upload repository/ contents including .github/, not the private original workspace.\n"
        "3. Attach assets/MathPlatform-Windows.zip and its .sha256 to a Release; CMD and EXE entries are included.\n"
        "4. Run Build macOS portable packages for arm64 and x86_64.\n"
        "5. After native smoke and a real Mac UI check, publish the resulting architecture-specific ZIPs.\n"
        "6. Do not upload the used smoke runtime, original .env, private DB, keys or raw provider logs.\n\n"
        "Manual: repository/README.md. Evidence: RELEASE_STATUS.md.\n", encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()

from pathlib import Path
import shutil
import sqlite3
import uuid

import pytest

import tools.launcher.math_platform_launcher as launcher
from tools.launcher.math_platform_launcher import (
    create_backup,
    database_path,
    pid_file,
    pre_restore_database_path,
    read_pid,
    restore_backup,
    restore_confirmation_matches,
    restore_confirmation_phrase,
    runtime_data_dir,
    sanitize_env_text,
    sqlite_backup_connection,
    sqlite_database_url,
    sqlite_integrity_check_connection,
    sqlite_table_counts_connection,
    start,
    write_pid,
)


TEST_WORK_ROOT = Path(__file__).resolve().parents[2] / ".test-work" / "launcher-backup"


def make_release_root() -> Path:
    root = TEST_WORK_ROOT / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=False)
    return root


def write_sample_database(path: Path, stems: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("drop table if exists questions")
        conn.execute("create table questions (id integer primary key, stem text not null)")
        conn.executemany("insert into questions (stem) values (?)", [(stem,) for stem in stems])
        conn.commit()
    finally:
        conn.close()


def read_question_stems(path: Path) -> list[str]:
    conn = sqlite3.connect(path)
    try:
        return [str(row[0]) for row in conn.execute("select stem from questions order by id").fetchall()]
    finally:
        conn.close()


@pytest.fixture()
def restore_safe_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launcher, "release_processes", lambda root: [])
    monkeypatch.setattr(launcher, "port_reachable", lambda *args, **kwargs: False)


def test_sanitize_env_text_redacts_provider_and_encryption_secrets():
    raw = "\n".join(
        [
            "LIAOBOTS_API_KEY=sk-live-secret",
            "CONFIG_ENCRYPTION_KEY=local-encryption-secret",
            "DEFAULT_MODEL=gpt-5.5",
            "EMPTY_API_KEY=",
        ]
    )

    sanitized = sanitize_env_text(raw)

    assert "sk-live-secret" not in sanitized
    assert "local-encryption-secret" not in sanitized
    assert "LIAOBOTS_API_KEY=<redacted>" in sanitized
    assert "CONFIG_ENCRYPTION_KEY=<redacted>" in sanitized
    assert "DEFAULT_MODEL=gpt-5.5" in sanitized
    assert "EMPTY_API_KEY=" in sanitized


def test_sqlite_backup_connection_roundtrip_uses_consistent_snapshot():
    source = sqlite3.connect(":memory:")
    dest = sqlite3.connect(":memory:")
    try:
        source.execute("create table questions (id integer primary key, stem text not null)")
        source.execute("insert into questions (stem) values (?)", ("一共有多少种?",))
        source.commit()

        sqlite_backup_connection(source, dest)

        assert dest.execute("select count(*) from questions").fetchone()[0] == 1
        assert dest.execute("select stem from questions").fetchone()[0] == "一共有多少种?"
        assert sqlite_integrity_check_connection(dest)["ok"] is True
        assert sqlite_table_counts_connection(dest)["questions"] == 1
    finally:
        source.close()
        dest.close()


def test_restore_confirmation_uses_backup_id_not_fixed_phrase():
    manifest = {"backup_id": "backup-20260722-120000"}

    assert restore_confirmation_phrase(manifest) == "backup-20260722-120000"
    assert restore_confirmation_matches(manifest, "backup-20260722-120000") is True
    assert restore_confirmation_matches(manifest, "old-fixed-delete-token") is False
    assert restore_confirmation_matches(manifest, "") is False


def test_runtime_profile_uses_isolated_database_and_pid_files():
    root = make_release_root()
    try:
        data_dir = runtime_data_dir(root, smoke_profile="release smoke")
        db_path = database_path(root, data_dir)

        write_pid(root, "api", 12345, data_dir=data_dir)

        assert data_dir == root / ".smoke-profiles" / "release-smoke"
        assert db_path == data_dir / "math_platform_dev.db"
        assert sqlite_database_url(db_path).startswith("sqlite:///")
        assert read_pid(root, "api", data_dir=data_dir) == 12345
        assert read_pid(root, "api") is None
        assert pid_file(root, "api", data_dir=data_dir).parent == data_dir
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_start_passes_isolated_database_url_to_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_release_root()
    try:
        data_dir = runtime_data_dir(root, smoke_profile="smoke-a")
        (root / "api").mkdir(parents=True)
        (root / "web").mkdir(parents=True)
        (root / "api" / "math-api.exe").write_text("", encoding="utf-8")
        (root / "web" / "node.exe").write_text("", encoding="utf-8")
        (root / "web" / "server.js").write_text("", encoding="utf-8")
        monkeypatch.setattr(launcher, "release_processes", lambda root: [])
        monkeypatch.setattr(launcher, "health", lambda *args, **kwargs: False)
        monkeypatch.setattr(launcher, "web_health", lambda *args, **kwargs: False)
        launched: list[dict] = []

        def fake_start_process(**kwargs):
            launched.append(kwargs)
            return 1000 + len(launched)

        monkeypatch.setattr(launcher, "start_process", fake_start_process)

        result = start(root, data_dir)

        api_launch = next(item for item in launched if item["name"] == "api")
        web_launch = next(item for item in launched if item["name"] == "web")
        assert result["data_dir"] == str(data_dir)
        assert api_launch["data_dir"] == data_dir
        assert web_launch["data_dir"] == data_dir
        assert api_launch["env_updates"]["DATABASE_URL"] == sqlite_database_url(database_path(root, data_dir))
        assert api_launch["env_updates"]["API_REQUEST_LOG_PATH"] == str(data_dir / "logs" / "api.jsonl")
        assert api_launch["env_updates"]["MATH_PLATFORM_PRESERVE_PROCESS_ENV"] == "1"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_restore_backup_roundtrip_keeps_pre_restore_snapshot(
    restore_safe_environment: None,
) -> None:
    root = make_release_root()
    try:
        db_path = database_path(root)
        write_sample_database(db_path, ["before backup"])

        backup = create_backup(root)
        assert backup["ok"] is True

        write_sample_database(db_path, ["after backup"])

        dry_run = restore_backup(root, file_arg=backup["backup_dir"], dry_run=True)
        assert dry_run["ok"] is True
        assert dry_run["dry_run"] is True
        assert dry_run["required_confirmation"] == backup["backup_id"]
        assert read_question_stems(db_path) == ["after backup"]

        rejected = restore_backup(root, file_arg=backup["backup_dir"], confirm="wrong-confirmation")
        assert rejected["ok"] is False
        assert rejected["error_code"] == "confirmation_required"
        assert read_question_stems(db_path) == ["after backup"]

        restored = restore_backup(root, file_arg=backup["backup_dir"], confirm=backup["backup_id"])
        assert restored["ok"] is True
        assert restored["post_restore_validation"]["ok"] is True
        assert read_question_stems(db_path) == ["before backup"]

        pre_restore_db = pre_restore_database_path(restored["pre_restore_backup"])
        assert pre_restore_db is not None
        assert read_question_stems(pre_restore_db) == ["after backup"]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_restore_backup_rolls_back_when_post_restore_validation_fails(
    restore_safe_environment: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_release_root()
    try:
        db_path = database_path(root)
        write_sample_database(db_path, ["restore target"])

        backup = create_backup(root)
        assert backup["ok"] is True

        write_sample_database(db_path, ["current before failed restore"])

        original_sha256_file = launcher.sha256_file

        def fake_sha256_file(path: Path) -> str:
            if Path(path).resolve() == db_path.resolve():
                return "0" * 64
            return original_sha256_file(path)

        monkeypatch.setattr(launcher, "sha256_file", fake_sha256_file)

        restored = restore_backup(root, file_arg=backup["backup_dir"], confirm=backup["backup_id"])

        assert restored["ok"] is False
        assert restored["error_code"] == "post_restore_validation_failed"
        assert restored["post_restore_validation"]["hash_matches"] is False
        assert restored["rollback"]["ok"] is True
        assert read_question_stems(db_path) == ["current before failed restore"]
    finally:
        shutil.rmtree(root, ignore_errors=True)

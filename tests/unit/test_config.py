import shutil
import uuid
from pathlib import Path

from apps.api.app import config


def _make_test_root(name: str) -> Path:
    root = Path(__file__).resolve().parents[2] / "tests" / "runtime_tmp" / f"{name}-{uuid.uuid4().hex}"
    root.mkdir(parents=True, exist_ok=False)
    return root


def test_env_file_overrides_stale_process_environment(monkeypatch):
    env_file = Path(".tmp") / "test-config.env"
    env_file.parent.mkdir(exist_ok=True)
    try:
        env_file.write_text(
            "DATABASE_URL=sqlite:///packaged.db\n"
            "LIAOBOTS_BASE_URL=https://ai.liaobots2.work/v1\n"
            "\ufeffAPP_NAME=数学出题与学情诊断平台\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://stale:stale@localhost:5432/stale")
        monkeypatch.setattr(config, "env_file_path", lambda: env_file)
        config.get_settings.cache_clear()

        settings = config.get_settings()

        assert settings.database_url == "sqlite:///packaged.db"
        assert settings.app_name == "数学出题与学情诊断平台"
        assert "\ufeffAPP_NAME" not in config._read_env_map(env_file)
    finally:
        config.get_settings.cache_clear()
        env_file.unlink(missing_ok=True)


def test_launcher_can_preserve_explicit_process_environment(monkeypatch):
    env_file = Path(".tmp") / "test-config-preserve.env"
    env_file.parent.mkdir(exist_ok=True)
    try:
        env_file.write_text(
            "DATABASE_URL=sqlite:///packaged.db\n"
            "API_REQUEST_LOG_PATH=logs/api.jsonl\n"
            "DEFAULT_MODEL=file-model\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("MATH_PLATFORM_PRESERVE_PROCESS_ENV", "1")
        monkeypatch.setenv("DATABASE_URL", "sqlite:///isolated-profile.db")
        monkeypatch.setenv("API_REQUEST_LOG_PATH", "D:/tmp/profile/logs/api.jsonl")
        monkeypatch.setattr(config, "env_file_path", lambda: env_file)
        config.get_settings.cache_clear()

        settings = config.get_settings()

        assert settings.database_url == "sqlite:///isolated-profile.db"
        assert settings.api_request_log_path == "D:/tmp/profile/logs/api.jsonl"
        assert settings.default_model == "file-model"
    finally:
        config.get_settings.cache_clear()
        env_file.unlink(missing_ok=True)


def test_explicit_platform_root_env_file_does_not_fall_back_to_cwd(monkeypatch):
    platform_root = _make_test_root("platform-root")
    cwd_root = _make_test_root("cwd-root")
    try:
        (cwd_root / ".env").write_text("DATABASE_URL=sqlite:///wrong.db\n", encoding="utf-8")
        monkeypatch.chdir(cwd_root)
        monkeypatch.setenv("MATH_PLATFORM_ROOT", str(platform_root))

        assert config.env_file_path() == platform_root / ".env"
    finally:
        shutil.rmtree(platform_root, ignore_errors=True)
        shutil.rmtree(cwd_root, ignore_errors=True)


def test_runtime_config_snapshot_redacts_secret_values(monkeypatch):
    monkeypatch.setenv("LIAOBOTS_API_KEY", "secret-value")
    settings = config.Settings(
        database_url="sqlite:///local.db",
        liaobots_base_url="https://ai.example/v1",
        liaobots_api_key="secret-value",
    )

    snapshot = config.runtime_config_snapshot(settings)
    by_name = {item["name"]: item for item in snapshot["settings"]}

    assert snapshot["schema_version"] == "runtime_config_v1"
    assert by_name["liaobots_api_key"]["redacted"] is True
    assert by_name["liaobots_api_key"]["value"] is None
    assert by_name["liaobots_api_key"]["configured"] is True
    assert by_name["database_url"]["value"] == "sqlite:///local.db"
    assert "secret-value" not in str(snapshot)
    assert snapshot["feature_flags"]["provider_contract_v1"]["enabled"] is True

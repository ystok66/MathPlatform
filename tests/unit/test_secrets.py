import os
import shutil
import uuid
from pathlib import Path

from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from apps.api.app.db import Base
from apps.api.app.models import EncryptedConfig
from apps.api.app import secrets as secrets_module
from apps.api.app.secrets import get_secret, has_secret, set_secret


def _make_test_root() -> Path:
    root = Path(__file__).resolve().parents[2] / "tests" / "runtime_tmp" / f"secrets-{uuid.uuid4().hex}"
    root.mkdir(parents=True, exist_ok=False)
    return root


def test_secret_is_encrypted_at_rest_and_decrypted_with_key():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    encryption_key = Fernet.generate_key().decode("ascii")

    set_secret(db, "LIAOBOTS_API_KEY", "real-api-key", {"provider": "openai_compatible"}, encryption_key)
    db.commit()
    row = db.query(EncryptedConfig).filter_by(key="LIAOBOTS_API_KEY").one()

    assert row.value_ciphertext != "real-api-key"
    assert "real-api-key" not in row.value_ciphertext
    assert has_secret(db, "LIAOBOTS_API_KEY") is True
    assert get_secret(db, "LIAOBOTS_API_KEY", encryption_key) == "real-api-key"


def test_env_encryption_key_still_wins(monkeypatch):
    key = Fernet.generate_key().decode("ascii")

    monkeypatch.setenv("CONFIG_ENCRYPTION_KEY", key)
    monkeypatch.delenv("CONFIG_ENCRYPTION_KEY_SOURCE", raising=False)

    assert secrets_module.get_or_create_encryption_key() == key
    assert os.getenv("CONFIG_ENCRYPTION_KEY_SOURCE") == "env"


def test_dpapi_key_file_used_without_writing_env(monkeypatch):
    test_root = _make_test_root()
    try:
        monkeypatch.delenv("CONFIG_ENCRYPTION_KEY", raising=False)
        monkeypatch.delenv("CONFIG_ENCRYPTION_KEY_SOURCE", raising=False)
        monkeypatch.setattr(secrets_module, "project_root", lambda: test_root)
        monkeypatch.setattr(secrets_module, "dpapi_available", lambda: True)
        monkeypatch.setattr(secrets_module, "protect_with_dpapi", lambda data: b"wrapped:" + data)
        monkeypatch.setattr(secrets_module, "unprotect_with_dpapi", lambda data: data.removeprefix(b"wrapped:"))

        first = secrets_module.get_or_create_encryption_key()
        key_path = test_root / "data" / "config_encryption_key.dpapi"

        assert key_path.exists()
        assert first not in key_path.read_text(encoding="ascii")
        assert not (test_root / ".env").exists()
        assert os.getenv("CONFIG_ENCRYPTION_KEY") is None
        assert os.getenv("CONFIG_ENCRYPTION_KEY_SOURCE") == "dpapi"

        monkeypatch.delenv("CONFIG_ENCRYPTION_KEY_SOURCE", raising=False)
        assert secrets_module.get_or_create_encryption_key() == first
        assert os.getenv("CONFIG_ENCRYPTION_KEY_SOURCE") == "dpapi"
    finally:
        shutil.rmtree(test_root, ignore_errors=True)


def test_plaintext_fallback_requires_explicit_opt_in(monkeypatch):
    test_root = _make_test_root()
    try:
        (test_root / ".env").write_text("", encoding="utf-8")
        monkeypatch.delenv("CONFIG_ENCRYPTION_KEY", raising=False)
        monkeypatch.delenv("CONFIG_ENCRYPTION_KEY_SOURCE", raising=False)
        monkeypatch.setenv("MATH_PLATFORM_ROOT", str(test_root))
        monkeypatch.setattr(secrets_module, "project_root", lambda: test_root)
        monkeypatch.setattr(secrets_module, "dpapi_available", lambda: False)
        monkeypatch.setenv("ALLOW_PLAINTEXT_CONFIG_ENCRYPTION_KEY", "1")

        key = secrets_module.get_or_create_encryption_key()

        assert Fernet(key.encode("ascii"))
        assert (test_root / ".env").exists()
        assert os.getenv("CONFIG_ENCRYPTION_KEY") == key
        assert os.getenv("CONFIG_ENCRYPTION_KEY_SOURCE") == "env_plaintext_fallback"
    finally:
        shutil.rmtree(test_root, ignore_errors=True)

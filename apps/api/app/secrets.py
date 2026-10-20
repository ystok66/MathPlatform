from __future__ import annotations

import base64
import ctypes
import os
from ctypes import wintypes
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import project_root, write_env_updates
from .models import EncryptedConfig, utc_now


DPAPI_KEY_FILENAME = "config_encryption_key.dpapi"
DPAPI_DESCRIPTION = "MathPlatform local config encryption key"


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def dpapi_available() -> bool:
    return os.name == "nt"


def _blob_from_bytes(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data)
    blob = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    return blob, buffer


def _bytes_from_blob(blob: _DataBlob) -> bytes:
    return ctypes.string_at(blob.pbData, blob.cbData)


def protect_with_dpapi(data: bytes) -> bytes:
    if not dpapi_available():
        raise RuntimeError("DPAPI is only available on Windows")
    in_blob, _buffer = _blob_from_bytes(data)
    out_blob = _DataBlob()
    flags = 0x01  # CRYPTPROTECT_UI_FORBIDDEN
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        DPAPI_DESCRIPTION,
        None,
        None,
        None,
        flags,
        ctypes.byref(out_blob),
    )
    if not ok:
        raise ctypes.WinError()
    try:
        return _bytes_from_blob(out_blob)
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def unprotect_with_dpapi(data: bytes) -> bytes:
    if not dpapi_available():
        raise RuntimeError("DPAPI is only available on Windows")
    in_blob, _buffer = _blob_from_bytes(data)
    out_blob = _DataBlob()
    flags = 0x01  # CRYPTPROTECT_UI_FORBIDDEN
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        flags,
        ctypes.byref(out_blob),
    )
    if not ok:
        raise ctypes.WinError()
    try:
        return _bytes_from_blob(out_blob)
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def local_encryption_key_path(root: Path | None = None) -> Path:
    return (root or project_root()) / "data" / DPAPI_KEY_FILENAME


def load_dpapi_encryption_key(path: Path | None = None) -> str | None:
    key_path = path or local_encryption_key_path()
    if not key_path.exists():
        return None
    protected = base64.b64decode(key_path.read_text(encoding="ascii"))
    return unprotect_with_dpapi(protected).decode("ascii")


def store_dpapi_encryption_key(key: str, path: Path | None = None) -> Path:
    key_path = path or local_encryption_key_path()
    key_path.parent.mkdir(parents=True, exist_ok=True)
    protected = protect_with_dpapi(key.encode("ascii"))
    key_path.write_text(base64.b64encode(protected).decode("ascii"), encoding="ascii")
    return key_path


def allow_plaintext_encryption_key_fallback() -> bool:
    return os.getenv("ALLOW_PLAINTEXT_CONFIG_ENCRYPTION_KEY", "").strip() == "1"


def get_or_create_dpapi_encryption_key() -> str:
    existing = load_dpapi_encryption_key()
    if existing:
        os.environ["CONFIG_ENCRYPTION_KEY_SOURCE"] = "dpapi"
        return existing
    key = Fernet.generate_key().decode("ascii")
    store_dpapi_encryption_key(key)
    os.environ["CONFIG_ENCRYPTION_KEY_SOURCE"] = "dpapi"
    return key


def get_or_create_encryption_key() -> str:
    key = os.getenv("CONFIG_ENCRYPTION_KEY")
    if key:
        os.environ["CONFIG_ENCRYPTION_KEY_SOURCE"] = "env"
        return key
    if dpapi_available():
        try:
            return get_or_create_dpapi_encryption_key()
        except Exception:
            if not allow_plaintext_encryption_key_fallback():
                raise
    key = Fernet.generate_key().decode("ascii")
    write_env_updates({"CONFIG_ENCRYPTION_KEY": key})
    os.environ["CONFIG_ENCRYPTION_KEY"] = key
    os.environ["CONFIG_ENCRYPTION_KEY_SOURCE"] = "env_plaintext_fallback"
    return key


def fernet(encryption_key: str | None = None) -> Fernet:
    return Fernet((encryption_key or get_or_create_encryption_key()).encode("ascii"))


def encrypt_value(value: str, encryption_key: str | None = None) -> str:
    return fernet(encryption_key).encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_value(value_ciphertext: str, encryption_key: str | None = None) -> str:
    return fernet(encryption_key).decrypt(value_ciphertext.encode("ascii")).decode("utf-8")


def set_secret(db: Session, key: str, value: str, metadata: dict[str, Any] | None = None, encryption_key: str | None = None) -> EncryptedConfig:
    row = db.scalar(select(EncryptedConfig).where(EncryptedConfig.key == key))
    ciphertext = encrypt_value(value, encryption_key)
    if row is None:
        row = EncryptedConfig(key=key, value_ciphertext=ciphertext, metadata_json=metadata or {})
        db.add(row)
    else:
        row.value_ciphertext = ciphertext
        row.metadata_json = metadata or row.metadata_json
        row.status = "active"
        row.updated_at = utc_now()
    return row


def get_secret(db: Session, key: str, encryption_key: str | None = None) -> str | None:
    row = db.scalar(select(EncryptedConfig).where(EncryptedConfig.key == key, EncryptedConfig.status == "active"))
    if row is None:
        return None
    return decrypt_value(row.value_ciphertext, encryption_key)


def has_secret(db: Session, key: str) -> bool:
    return db.scalar(select(EncryptedConfig.id).where(EncryptedConfig.key == key, EncryptedConfig.status == "active")) is not None


def delete_secret(db: Session, key: str) -> bool:
    row = db.scalar(select(EncryptedConfig).where(EncryptedConfig.key == key, EncryptedConfig.status == "active"))
    if row is None:
        return False
    row.status = "deleted"
    row.updated_at = utc_now()
    return True

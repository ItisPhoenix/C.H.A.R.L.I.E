"""DPAPI-backed local secret store for secrets previously held in plaintext .env.

Reuses the Windows DPAPI helper in :mod:`charlie.rakazo` so one mechanism owns
secret encryption. The store is a per-user file outside the repository; values
never enter logs, reports, or Git.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# Keys migrated out of .env. Hotkeys and non-secret settings are not secrets.
MANAGED_KEYS = ("LLM_API_KEY", "TELEGRAM_BOT_TOKEN", "VISION_LLM_KEY")


def secret_path() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "Charlie" / "secrets.bin"


def _dpapi(data: bytes, *, protect: bool) -> bytes:
    # Reuse the canonical DPAPI implementation; keep one encryption owner.
    from charlie.rakazo import _crypt

    return _crypt(data, protect=protect)


def load_secrets() -> dict[str, str]:
    path = secret_path()
    if not path.exists():
        return {}
    payload = json.loads(_dpapi(path.read_bytes(), protect=False).decode("utf-8"))
    if not isinstance(payload, dict):
        return {}
    return {key: value for key, value in payload.items() if isinstance(key, str) and isinstance(value, str)}


def save_secrets(values: dict[str, str]) -> None:
    path = secret_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(_dpapi(json.dumps(values).encode("utf-8"), protect=True))
    temporary.replace(path)


def migrate_from_env(keys=MANAGED_KEYS) -> list[str]:
    """Copy configured secret values from the process environment into DPAPI.

    Returns only the names of keys written. Never returns or logs values.
    Existing stored values are preserved; only keys with a non-empty value and
    no stored entry are written, so re-running is safe and non-destructive.
    """
    stored = load_secrets()
    written: list[str] = []
    for key in keys:
        value = os.getenv(key, "").strip()
        if value and not stored.get(key):
            stored[key] = value
            written.append(key)
    if written:
        save_secrets(stored)
    return written

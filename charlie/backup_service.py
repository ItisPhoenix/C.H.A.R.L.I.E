"""Local backup export plumbing with an explicit encryption boundary."""

import io
import json
import os
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

_MAGIC = b"CHARLIE-BACKUP-1\0"
_MIN_PASSPHRASE_LENGTH = 12


def _key(passphrase: str, salt: bytes) -> bytes:
    if len(passphrase) < _MIN_PASSPHRASE_LENGTH:
        raise ValueError("backup passphrase is too short")
    return Scrypt(salt=salt, length=32, n=2**14, r=8, p=1).derive(passphrase.encode("utf-8"))


def export_snapshot(target: Path, sources: Mapping[str, Path], passphrase: str | None = None) -> dict:
    target.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "encryption": "scrypt-aesgcm-passphrase" if passphrase else "deferred-key-mechanism",
        "encrypted": passphrase is not None,
        "files": sorted(name for name, path in sources.items() if path.is_file()),
    }
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, indent=2))
        for name, path in sources.items():
            if path.is_file():
                archive.write(path, arcname=name)
    data = raw.getvalue()
    if passphrase:
        salt = os.urandom(16)
        nonce = os.urandom(12)
        data = _MAGIC + salt + nonce + AESGCM(_key(passphrase, salt)).encrypt(nonce, data, _MAGIC)
    target.write_bytes(data)
    return manifest


def decrypt_snapshot(data: bytes, passphrase: str) -> dict[str, bytes]:
    if not data.startswith(_MAGIC):
        payload = data
    else:
        offset = len(_MAGIC)
        salt, nonce = data[offset:offset + 16], data[offset + 16:offset + 28]
        payload = AESGCM(_key(passphrase, salt)).decrypt(nonce, data[offset + 28:], _MAGIC)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        return {name: archive.read(name) for name in archive.namelist() if name != "manifest.json"}

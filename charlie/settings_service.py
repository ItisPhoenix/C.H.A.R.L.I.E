"""Typed SettingsService for Charlie.

Provides validated configuration inspection, live-vs-restart tier metadata,
safe atomic .env persistence without race conditions, and strict secret masking.
"""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import fields
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from charlie.config import Config

logger = logging.getLogger("charlie.settings_service")


class SettingValidationError(ValueError):
    """Raised when a setting update has an invalid value, type, or constraint."""


class SettingsService:
    """Authoritative service for Charlie settings metadata, validation, and safe persistence."""

    def __init__(self, config_instance: Config, env_path: Optional[Path] = None) -> None:
        self.config = config_instance
        self.env_path = env_path if env_path is not None else Path(".env")

    def get_field_specs(self) -> List[Dict[str, Any]]:
        """Return all declared editable field specifications with secret masking."""
        specs = []
        for spec in self.config.editable_field_specs():
            val = getattr(self.config, spec["field"])
            is_secret = bool(spec.get("secret", False))

            # Mask secret values completely; never expose them to frontend or logs
            spec_out = {
                "key": spec["key"],
                "field": spec["field"],
                "group": spec["group"],
                "label": spec["label"],
                "type": spec["type"],
                "secret": is_secret,
                "restart": spec.get("restart"),
                "value": None if is_secret else val,
                "is_set": bool(val and val not in ("no-key", "no_key", "")) if is_secret else None,
            }
            specs.append(spec_out)
        return specs

    def validate_updates(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Validate input updates against declared config field types.
        
        Ignores unknown keys and raises SettingValidationError on type conversion errors.
        """
        by_env = {f.metadata.get("env"): f for f in fields(self.config) if f.metadata.get("env")}
        validated: Dict[str, Any] = {}

        for env_key, raw_value in updates.items():
            f = by_env.get(env_key)
            if f is None:
                continue

            try:
                ftype = f.type
                if ftype is bool:
                    val = raw_value if isinstance(raw_value, bool) else str(raw_value).strip().lower() == "true"
                elif ftype is int:
                    val = int(raw_value)
                elif ftype is float:
                    val = float(raw_value)
                elif ftype == List[str]:
                    if isinstance(raw_value, list):
                        val = [str(v).strip() for v in raw_value if str(v).strip()]
                    else:
                        val = [s.strip() for s in str(raw_value).split(",") if s.strip()]
                else:
                    val = str(raw_value)
                validated[env_key] = val
            except (ValueError, TypeError) as exc:
                raise SettingValidationError(f"Invalid value for setting '{env_key}': {raw_value}") from exc

        return validated

    def update_settings(self, updates: Dict[str, Any]) -> Set[str]:
        """Validate, atomically persist to .env, and apply to in-memory config.
        
        Returns the set of restart tiers touched.
        """
        validated = self.validate_updates(updates)
        if not validated:
            return set()

        # Apply to in-memory config
        touched = self.config.apply_env_updates(validated)

        # Atomic write to .env
        self._atomic_write_env(validated)

        return touched

    def _atomic_write_env(self, updates: Dict[str, Any]) -> None:
        """Atomically update or append settings in .env without losing existing comments or unrelated keys."""
        target_path = self.env_path.resolve()
        parent_dir = target_path.parent
        parent_dir.mkdir(parents=True, exist_ok=True)

        existing_lines: List[str] = []
        if target_path.exists():
            try:
                content = target_path.read_text(encoding="utf-8")
                existing_lines = content.splitlines()
            except Exception as e:
                logger.warning("Could not read existing .env: %s", e)

        new_lines: List[str] = []
        matched_keys: Set[str] = set()

        for line in existing_lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in line:
                parts = line.split("=", 1)
                key = parts[0].strip()
                if key in updates:
                    val = updates[key]
                    val_str = self._format_env_val(val)
                    new_lines.append(f"{key}={val_str}")
                    matched_keys.add(key)
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)

        # Append any new keys that weren't in the file
        for key, val in updates.items():
            if key not in matched_keys:
                val_str = self._format_env_val(val)
                new_lines.append(f"{key}={val_str}")

        new_content = "\n".join(new_lines) + "\n"

        # Write to temporary file in the same directory, then atomic rename
        tmp_fd, tmp_path_str = tempfile.mkstemp(dir=str(parent_dir), prefix=".env.tmp-")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                f.write(new_content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path_str, str(target_path))
        except Exception:
            if os.path.exists(tmp_path_str):
                try:
                    os.unlink(tmp_path_str)
                except OSError:
                    pass
            raise

    @staticmethod
    def _format_env_val(val: Any) -> str:
        if isinstance(val, list):
            return ",".join(str(v) for v in val)
        if isinstance(val, bool):
            return "true" if val else "false"
        return str(val)


# Global default instance
settings_service = SettingsService(Config())

"""Validated settings persistence and main-runtime settings state."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
from copy import deepcopy
from dataclasses import fields
from inspect import stack
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Set

from charlie.config import Config

logger = logging.getLogger("charlie.settings_service")


class SettingValidationError(ValueError):
    """Raised when a setting update has an invalid value, type, or constraint."""


def canonical_settings_request_fingerprint(
    operation: str,
    updates: Mapping[str, Any] | None = None,
) -> str:
    """Produce one stable identity for main-owned settings operations."""
    secret_keys = {
        spec["key"]
        for spec in Config.editable_field_specs()
        if spec.get("secret") is True
    }
    canonical_updates = {}
    for key, value in sorted((updates or {}).items()):
        if key in secret_keys:
            encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
            canonical_updates[key] = {"sha256": hashlib.sha256(encoded).hexdigest()}
        else:
            canonical_updates[key] = value
    identity = {
        "operation": str(operation),
        "updates": canonical_updates,
    }
    return json.dumps(identity, sort_keys=True, separators=(",", ":"), default=str)


class SettingsService:
    """Main-owned settings metadata, validation, persistence, and state."""

    def __init__(self, config_instance: Config, env_path: Optional[Path] = None) -> None:
        self.config = config_instance
        self._lock = threading.RLock()
        self.env_path = env_path if env_path is not None else Path(".env")
        self._field_specs = {spec["key"]: spec for spec in Config.editable_field_specs()}
        self._saved_values: Dict[str, Any] = {
            key: deepcopy(getattr(self.config, spec["field"]))
            for key, spec in self._field_specs.items()
        }
        self._effective_values: Dict[str, Any] = deepcopy(self._saved_values)
        self._pending: Dict[str, Set[str]] = {}
        self._load_persisted_values()
        self._initialize_pending_state()

    def get_field_specs(self) -> List[Dict[str, Any]]:
        """Return metadata with current Config values for compatibility callers."""
        with self._lock:
            return self._build_field_specs(use_state=False)

    def snapshot(self) -> Dict[str, Any]:
        """Return the secret-safe main-owned saved/effective settings projection."""
        with self._lock:
            pending_reload = {
                tier: sorted(keys)
                for tier, keys in sorted(self._pending.items())
                if tier != "process" and keys
            }
            process_restart_required = sorted(self._pending.get("process", set()))
            return {
                "authority": "main_runtime",
                "status": "available",
                "fields": self._build_field_specs(use_state=True),
                "pending_reload": pending_reload,
                "process_restart_required": process_restart_required,
            }

    def pending_tiers(self) -> Dict[str, List[str]]:
        """Return pending fields grouped by metadata restart tier."""
        with self._lock:
            return {
                tier: sorted(keys)
                for tier, keys in sorted(self._pending.items())
                if keys
            }

    def pending_reload_work(self) -> Dict[str, Dict[str, Any]]:
        """Capture pending values before external reload work runs."""
        with self._lock:
            return {
                tier: {
                    key: deepcopy(self._saved_values[key])
                    for key in sorted(keys)
                }
                for tier, keys in sorted(self._pending.items())
                if keys
            }

    def validate_updates(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Validate recognized updates without mutating Config or disk."""
        with self._lock:
            if not isinstance(updates, dict):
                raise SettingValidationError("Settings updates must be an object.")

            by_env = {f.metadata.get("env"): f for f in fields(self.config) if f.metadata.get("env")}
            validated: Dict[str, Any] = {}
            for env_key, raw_value in updates.items():
                f = by_env.get(env_key)
                if f is None:
                    continue
                try:
                    validated[env_key] = _coerce_setting(raw_value, f.type)
                except (ValueError, TypeError) as exc:
                    raise SettingValidationError(f"Invalid value for setting '{env_key}': {raw_value}") from exc
            return validated

    def apply_updates(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            return self._apply_updates(updates)

    def _apply_updates(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Persist a complete update before applying its applicable runtime state."""
        validated = self.validate_updates(updates)
        if not validated:
            raise SettingValidationError("No recognized settings in request.")

        touched = sorted(
            {
                str(self._field_specs[key].get("restart"))
                for key in validated
                if self._field_specs[key].get("restart")
            }
        )

        # Durable write is transaction boundary. No mutable state below changes
        # until this succeeds.
        self._atomic_write_env(validated, source="settings_service.apply_updates")

        for key, value in validated.items():
            self._saved_values[key] = deepcopy(value)

        runtime_updates = {
            key: value
            for key, value in validated.items()
            if self._field_specs[key].get("restart") != "process"
        }
        if runtime_updates:
            self.config.apply_env_updates(runtime_updates)

        applied: List[str] = []
        for key, value in validated.items():
            tier = self._field_specs[key].get("restart")
            if tier == "process":
                if _same_value(value, self._effective_values[key]):
                    self._pending.setdefault(tier, set()).discard(key)
                else:
                    self._pending.setdefault(tier, set()).add(key)
                continue
            if tier:
                if _same_value(value, self._effective_values[key]):
                    self._pending.setdefault(tier, set()).discard(key)
                else:
                    self._pending.setdefault(tier, set()).add(key)
                continue
            self._effective_values[key] = deepcopy(value)
            self._pending.setdefault(tier or "", set()).discard(key)
            applied.append(key)

        snapshot = self.snapshot()
        return {
            "saved": True,
            "applied": sorted(applied),
            "touched": touched,
            "pending_reload": snapshot["pending_reload"],
            "process_restart_required": snapshot["process_restart_required"],
        }

    def update_settings(self, updates: Dict[str, Any]) -> Set[str]:
        """Compatibility wrapper returning restart tiers touched by an update."""
        result = self.apply_updates(updates)
        return set(result["touched"])

    def mark_reload_result(
        self,
        tier: str,
        success: bool,
        expected_values: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Commit or retain one targeted reload outcome."""
        with self._lock:
            if not success:
                return
            current = self._pending.get(tier, set())
            values = expected_values or {
                key: deepcopy(self._saved_values[key])
                for key in current
            }
            for key, expected in values.items():
                if key not in current or not _same_value(self._saved_values[key], expected):
                    continue
                current.discard(key)
                if tier != "process":
                    self._effective_values[key] = deepcopy(expected)
            if not current:
                self._pending.pop(tier, None)

    def _build_field_specs(self, *, use_state: bool) -> List[Dict[str, Any]]:
        specs: List[Dict[str, Any]] = []
        for key, spec in self._field_specs.items():
            is_secret = bool(spec.get("secret", False))
            effective = self._effective_values[key] if use_state else getattr(self.config, spec["field"])
            saved = self._saved_values[key]
            display_value = saved if use_state else effective
            tier = spec.get("restart")
            pending = key in self._pending.get(str(tier), set()) if tier else False
            field_out: Dict[str, Any] = {
                "key": spec["key"],
                "field": spec["field"],
                "group": spec["group"],
                "label": spec["label"],
                "type": spec["type"],
                "secret": is_secret,
                "restart": tier,
                "value": None if is_secret else deepcopy(display_value),
                "is_set": (
                    bool(saved and saved not in ("no-key", "no_key", ""))
                    if is_secret
                    else None
                ),
            }
            if use_state:
                field_out.update(
                    {
                        "saved_value": None if is_secret else deepcopy(saved),
                        "effective_value": None if is_secret else deepcopy(effective),
                        "pending": pending,
                        "pending_tier": tier if pending else None,
                        "state": (
                            "process_restart_required"
                            if pending and tier == "process"
                            else "pending_reload"
                            if pending
                            else "applied"
                        ),
                    }
                )
            specs.append(field_out)
        return specs

    def _load_persisted_values(self) -> None:
        """Read desired values for projection without mutating runtime Config."""
        if not self.env_path.exists():
            return
        if (
            os.getenv("CHARLIE_TEST_MODE", "").lower() == "true"
            and self.env_path.resolve() == Path(".env").resolve()
        ):
            return
        try:
            lines = self.env_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            logger.warning("Could not read settings file for projection", exc_info=True)
            return
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in line:
                continue
            key, raw_value = line.split("=", 1)
            key = key.strip()
            if key not in self._field_specs:
                continue
            raw_value = raw_value.strip()
            if len(raw_value) >= 2 and raw_value[0] == raw_value[-1] and raw_value[0] in "'\"":
                raw_value = raw_value[1:-1]
            try:
                parsed = self.validate_updates({key: raw_value})
            except SettingValidationError:
                logger.warning("Ignoring invalid persisted setting %s", key)
                continue
            if key in parsed:
                self._saved_values[key] = deepcopy(parsed[key])

    def _initialize_pending_state(self) -> None:
        for key, spec in self._field_specs.items():
            tier = spec.get("restart")
            if tier and not _same_value(self._saved_values[key], self._effective_values[key]):
                self._pending.setdefault(tier, set()).add(key)

    def _atomic_write_env(self, updates: Dict[str, Any], *, source: str = "unknown") -> None:
        """Atomically update or append settings in .env."""
        if (
            os.getenv("CHARLIE_TEST_MODE", "").lower() == "true"
            and self.env_path.resolve() == Path(".env").resolve()
        ):
            raise RuntimeError("Tests must provide an isolated settings env_path")

        target_path = self.env_path.resolve()
        parent_dir = target_path.parent
        parent_dir.mkdir(parents=True, exist_ok=True)

        existing_lines: List[str] = []
        if target_path.exists():
            try:
                existing_lines = target_path.read_text(encoding="utf-8").splitlines()
            except OSError:
                logger.warning("Could not read existing .env", exc_info=True)

        new_lines: List[str] = []
        matched_keys: Set[str] = set()
        for line in existing_lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in line:
                key = line.split("=", 1)[0].strip()
                if key in updates:
                    new_lines.append(f"{key}={self._format_env_val(updates[key])}")
                    matched_keys.add(key)
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)

        for key, value in updates.items():
            if key not in matched_keys:
                new_lines.append(f"{key}={self._format_env_val(value)}")

        new_content = "\n".join(new_lines) + "\n"
        logger.warning(
            "env_settings_write source=%s pid=%s keys=%s caller=%s",
            source,
            os.getpid(),
            ",".join(sorted(updates)),
            stack()[1].function,
        )
        tmp_fd, tmp_path_str = tempfile.mkstemp(dir=str(parent_dir), prefix=".env.tmp-")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as handle:
                handle.write(new_content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path_str, str(target_path))
        except Exception:
            if os.path.exists(tmp_path_str):
                try:
                    os.unlink(tmp_path_str)
                except OSError:
                    pass
            raise

    @staticmethod
    def _format_env_val(value: Any) -> str:
        if isinstance(value, list):
            return ",".join(str(item) for item in value)
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)


def _coerce_setting(raw_value: Any, ftype: Any) -> Any:
    if ftype is bool:
        if isinstance(raw_value, bool):
            return raw_value
        if isinstance(raw_value, int) and raw_value in (0, 1):
            return bool(raw_value)
        normalized = str(raw_value).strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
        raise ValueError("boolean value required")
    if ftype is int:
        if isinstance(raw_value, bool):
            raise ValueError("integer value required")
        return int(raw_value)
    if ftype is float:
        if isinstance(raw_value, bool):
            raise ValueError("float value required")
        return float(raw_value)
    if ftype == List[str]:
        if isinstance(raw_value, list):
            return [str(value).strip() for value in raw_value if str(value).strip()]
        if raw_value is None:
            raise ValueError("list value required")
        return [value.strip() for value in str(raw_value).split(",") if value.strip()]
    if raw_value is None:
        raise ValueError("string value required")
    return str(raw_value)


def _same_value(left: Any, right: Any) -> bool:
    return left == right

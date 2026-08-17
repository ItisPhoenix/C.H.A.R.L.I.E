import logging
from dataclasses import fields
from typing import Any, Dict, List, Optional

from charlie.config import Config
from charlie.settings_service import SettingsService, SettingValidationError

logger = logging.getLogger("charlie.self_extension.config_adapter")


class AdapterResult:
    def __init__(self, success: bool, message: str, restart_tiers: Optional[List[str]] = None):
        self.success = success
        self.message = message
        self.restart_tiers = restart_tiers or []


class ConfigAdapter:
    """Applies validated configuration updates and restores previous settings on rollback."""

    def __init__(
        self,
        settings_service: Optional[SettingsService] = None,
        config: Optional[Config] = None,
    ) -> None:
        cfg = config or Config()
        self._settings_service = settings_service or SettingsService(config_instance=cfg)
        self._config = config or getattr(self._settings_service, "config", cfg)

    def capture_preimage(self, keys: List[str]) -> Dict[str, Any]:
        """Capture existing values for declared keys (secret-masked in logs)."""
        by_env = {f.metadata.get("env"): f.name for f in fields(self._config) if f.metadata.get("env")}
        preimage: Dict[str, Any] = {}
        for key in keys:
            norm_key = key.strip()
            field_name = by_env.get(norm_key, norm_key.lower())
            if hasattr(self._config, field_name):
                preimage[norm_key] = getattr(self._config, field_name)
        return preimage

    def apply_updates(self, updates: Dict[str, Any]) -> AdapterResult:
        """Validate and apply settings updates via SettingsService."""
        try:
            restart_tiers = self._settings_service.update_settings(updates)
            return AdapterResult(
                success=True,
                message="Configuration updated successfully.",
                restart_tiers=list(restart_tiers),
            )
        except SettingValidationError as e:
            return AdapterResult(success=False, message=f"Config validation error: {e}")
        except Exception as e:
            return AdapterResult(success=False, message=f"Failed updating configuration: {e}")

    def rollback(self, preimage: Dict[str, Any]) -> AdapterResult:
        """Restore previous configuration values."""
        if not preimage:
            return AdapterResult(success=True, message="No config preimage to restore.")
        return self.apply_updates(preimage)

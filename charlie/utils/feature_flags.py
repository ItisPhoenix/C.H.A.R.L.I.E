"""
Feature flag system for gating risky new code.

Add flags to charlie_config.json:
    "feature_flags": {
        "new_agent_type": false,
        "experimental_llm": false
    }

Then in code:
    from charlie.utils.feature_flags import is_enabled
    if is_enabled("new_agent_type"):
        # risky new code
        pass
"""

from __future__ import annotations

from charlie.utils.logger import get_logger

logger = get_logger(__name__)


def _get_flag(name: str, default: bool = False) -> bool:
    """Read a feature flag from settings.

    Falls back to the default if the flag is not defined or settings is
    unavailable. Does not raise.
    """
    try:
        from charlie.config import settings
        flags = getattr(settings, "feature_flags", {}) or {}
        value = flags.get(name, default)
        if not isinstance(value, bool):
            logger.warning(
                "feature_flag_wrong_type | name=%s | value_type=%s",
                name,
                type(value).__name__,
            )
            return default
        return value
    except Exception as e:
        logger.debug("feature_flag_read_error | name=%s | %s", name, e)
        return default


def is_enabled(name: str) -> bool:
    """True if the feature flag is on."""
    enabled = _get_flag(name, default=False)
    logger.debug("feature_flag_check | name=%s | enabled=%s", name, enabled)
    return enabled


def all_flags() -> dict[str, bool]:
    """Return a snapshot of all known flags."""
    try:
        from charlie.config import settings
        flags = getattr(settings, "feature_flags", {}) or {}
        return {k: bool(v) for k, v in flags.items()}
    except Exception:
        return {}


def set_flag(name: str, value: bool) -> None:
    """Update a flag at runtime. Does NOT persist to disk."""
    try:
        from charlie.config import settings
        if not hasattr(settings, "feature_flags") or settings.feature_flags is None:
            settings.feature_flags = {}
        settings.feature_flags[name] = bool(value)
        logger.info("feature_flag_runtime_set | name=%s | value=%s", name, value)
    except Exception as e:
        logger.warning("feature_flag_set_error | name=%s | %s", name, e)

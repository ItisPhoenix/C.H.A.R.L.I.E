import hashlib
import json
import logging
import os
from typing import Optional

logger = logging.getLogger("charlie.recovery_cache")

CACHE_FILE = ".charlie_recovery_cache.json"

def _get_cache_key(command: str, failure_class: str, error_message: str) -> str:
    """Generates a stable unique hash key for a failure pattern."""
    raw = f"{command.strip()}:{failure_class}:{error_message.strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def get_cached_resolution(command: str, failure_class: str, error_message: str) -> Optional[str]:
    """Retrieves a previously successful recovery command from cache if it exists."""
    if not os.path.exists(CACHE_FILE):
        return None
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)
        key = _get_cache_key(command, failure_class, error_message)
        res = cache.get(key)
        if res:
            logger.info("Recovery cache hit: mapping '%s' to '%s'", command, res)
        return res
    except Exception as e:
        logger.warning("Failed to read recovery cache: %s", e)
        return None

def set_cached_resolution(command: str, failure_class: str, error_message: str, resolved_command: str) -> None:
    """Saves a successfully recovered command mapping to the local cache."""
    cache = {}
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                cache = json.load(f)
        except Exception as e:
            logger.warning("Failed to load recovery cache for writing: %s", e)

    try:
        key = _get_cache_key(command, failure_class, error_message)
        cache[key] = resolved_command
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
        logger.debug("Saved resolved command to recovery cache.")
    except Exception as e:
        logger.warning("Failed to save to recovery cache: %s", e)

"""Process-owned Media executor and request identity helpers."""

from __future__ import annotations

import json
import logging
import math
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

logger = logging.getLogger("charlie.media_runtime")

_executor_lock = threading.Lock()
_media_executor: ThreadPoolExecutor | None = None
_media_thread_id: int | None = None


def _init_media_thread() -> None:
    """Initialize COM on the one thread that may touch Media state."""
    global _media_thread_id
    _media_thread_id = threading.get_ident()
    try:
        import comtypes

        comtypes.CoInitialize()
    except Exception:
        # Non-Windows/unit environments can still exercise truthful unavailable paths.
        logger.debug("Media COM initialization unavailable", exc_info=True)


def get_media_executor() -> ThreadPoolExecutor:
    """Return the process-owned single-worker Media executor."""
    global _media_executor
    with _executor_lock:
        if _media_executor is None:
            _media_executor = ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="charlie-media",
                initializer=_init_media_thread,
            )
        return _media_executor


def media_executor_thread_id() -> int | None:
    return _media_thread_id


def shutdown_media_executor() -> None:
    """Drain and release the Media executor; a later runtime may recreate it."""
    global _media_executor, _media_thread_id
    with _executor_lock:
        executor = _media_executor
        _media_executor = None
        _media_thread_id = None
    if executor is not None:
        try:
            def _uninitialize_media_thread() -> None:
                try:
                    import comtypes

                    comtypes.CoUninitialize()
                except Exception:
                    logger.debug("Media COM uninitialization unavailable", exc_info=True)

            executor.submit(_uninitialize_media_thread).result()
        except Exception:
            logger.debug("Media COM worker finalization failed", exc_info=True)
        executor.shutdown(wait=True, cancel_futures=True)


def _canonical_percent(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    if not math.isfinite(numeric):
        return None
    return int(numeric) if numeric.is_integer() else numeric


def canonical_media_request_fingerprint(
    operation: str,
    *,
    action: Any = None,
    percent: Any = None,
) -> str:
    """Canonical identity shared by web correlation and main idempotency."""
    body: dict[str, Any] = {"operation": operation}
    if operation == "control":
        body["action"] = action if isinstance(action, str) else None
        if action == "set_volume":
            body["percent"] = _canonical_percent(percent)
    return json.dumps(body, sort_keys=True, separators=(",", ":"))

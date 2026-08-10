"""In-memory one-off reminders: schedule, fire, cancel. No persistence, no recurrence."""

import asyncio
import logging
import time
from typing import Callable, Dict, Optional

from charlie.utils import make_id

logger = logging.getLogger("charlie.reminders")

MAX_REMINDER_SECONDS = 24 * 3600

_reminders: Dict[str, Dict[str, object]] = {}  # id -> {text, fire_at}
_futures: Dict[str, "asyncio.Future"] = {}
_loop: Optional[asyncio.AbstractEventLoop] = None
_fire_callback: Optional[Callable[[str, str], None]] = None


def set_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Wire the asyncio loop reminders schedule onto. Called once from main.py."""
    global _loop
    _loop = loop


def set_fire_callback(fn: Callable[[str, str], None]) -> None:
    """fn(reminder_id, text) is invoked on the event loop thread when a reminder fires."""
    global _fire_callback
    _fire_callback = fn


async def _fire(reminder_id: str) -> None:
    entry = _reminders.pop(reminder_id, None)
    _futures.pop(reminder_id, None)
    if entry is None:
        return
    if _fire_callback is not None:
        try:
            _fire_callback(reminder_id, str(entry["text"]))
        except Exception:
            logger.error("Reminder fire callback failed for %s", reminder_id, exc_info=True)


async def _schedule(reminder_id: str, seconds: float) -> None:
    await asyncio.sleep(seconds)
    await _fire(reminder_id)


def set_reminder(text: str, seconds: float) -> str:
    """Schedule a one-off reminder `seconds` from now. Returns the reminder id.
    Raises ValueError if seconds is out of [0, MAX_REMINDER_SECONDS]. Must be
    called after set_loop() -- runs on a worker thread, schedules cross-thread."""
    if _loop is None:
        raise RuntimeError("reminders.set_loop() was never called")
    if seconds < 0 or seconds > MAX_REMINDER_SECONDS:
        raise ValueError(f"seconds must be between 0 and {MAX_REMINDER_SECONDS}")
    reminder_id = make_id(8)
    _reminders[reminder_id] = {"text": text, "fire_at": time.time() + seconds}
    future = asyncio.run_coroutine_threadsafe(_schedule(reminder_id, seconds), _loop)
    _futures[reminder_id] = future
    return reminder_id


def list_reminders() -> Dict[str, Dict[str, object]]:
    return dict(_reminders)


def cancel_reminder(reminder_id: str) -> bool:
    future = _futures.pop(reminder_id, None)
    existed = _reminders.pop(reminder_id, None) is not None
    if future is not None:
        future.cancel()
    return existed

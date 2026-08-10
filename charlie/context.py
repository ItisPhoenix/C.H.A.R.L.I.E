"""UserContext snapshot for the attention/autonomy engines (Phase 3, plan section "Phase 3").

Pure sampling, cached, never on the hot path. Consumers (Phase 4's
charlie/autonomy.py and charlie/attention.py) read a UserContext instead of
querying idle time, foreground app, or task state independently.
"""

import time
from dataclasses import dataclass
from typing import Any, Optional

from charlie.background_task import ACTIVE_STATUSES, get_current_task
from charlie.desktop.session import user_idle_seconds
from charlie.desktop.windows import get_foreground_window
from charlie.known_apps import APP_REGISTRY

_CACHE_TTL_SECONDS = 2.0
# Approximates "sustained typing" as recent input on a productive app -- session.py can't distinguish input types.
_TYPING_ACTIVITY_THRESHOLD_S = 30.0


@dataclass(frozen=True)
class UserContext:
    idle_seconds: float
    foreground_app: Optional[str]  # process name, e.g. "code.exe"
    foreground_title: Optional[str]
    focus_mode: bool
    running_task_count: int
    conversation_age_seconds: Optional[float]
    context_summary: str


_cache: Optional[tuple] = None  # (sampled_at, UserContext)


def _is_productive(process_name: Optional[str]) -> bool:
    if not process_name:
        return False
    target = process_name.lower()
    return any(
        entry.close_process and entry.close_process.lower() == target and entry.is_productive
        for entry in APP_REGISTRY.values()
    )


def build_context(
    *,
    last_turn_ended_at: Optional[float] = None,
    voice_focus_override: Optional[bool] = None,
    world_model: Optional[Any] = None,
    now: Optional[float] = None,
) -> UserContext:
    """Sample idle time, foreground app, focus mode, task count, conversation age.

    last_turn_ended_at, if given, must be a time.monotonic() value from when
    the last conversation turn ended -- matched against `now` (also
    monotonic) to compute conversation_age_seconds.
    """
    global _cache
    now = now if now is not None else time.monotonic()
    if _cache is not None and now - _cache[0] < _CACHE_TTL_SECONDS:
        return _cache[1]

    idle_seconds = user_idle_seconds()
    fg = get_foreground_window()
    foreground_app = fg["process_name"] if fg else None
    foreground_title = fg["title"] if fg else None

    if voice_focus_override is not None:
        focus_mode = voice_focus_override
    else:
        focus_mode = _is_productive(foreground_app) and idle_seconds < _TYPING_ACTIVITY_THRESHOLD_S

    task = get_current_task()
    running_task_count = 1 if task is not None and task.status in ACTIVE_STATUSES else 0

    conversation_age_seconds = (
        max(0.0, now - last_turn_ended_at) if last_turn_ended_at is not None else None
    )

    context_summary = world_model.context_slice() if world_model is not None else ""

    ctx = UserContext(
        idle_seconds=idle_seconds,
        foreground_app=foreground_app,
        foreground_title=foreground_title,
        focus_mode=focus_mode,
        running_task_count=running_task_count,
        conversation_age_seconds=conversation_age_seconds,
        context_summary=context_summary,
    )
    _cache = (now, ctx)
    return ctx

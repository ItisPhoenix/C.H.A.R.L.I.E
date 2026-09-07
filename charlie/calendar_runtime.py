"""Main-owned serialized CalendarStore runtime and request identity helpers."""

from __future__ import annotations

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

from charlie.calendar_store import CalendarStore, normalize_calendar_timestamp


def canonical_calendar_request_fingerprint(operation: str, payload: dict[str, Any]) -> str:
    """Produce one stable identity for web/main Calendar idempotency."""
    def timestamp(value: Any, field: str) -> Any:
        if value is None:
            return None
        try:
            return normalize_calendar_timestamp(value, field)
        except ValueError:
            return value

    if operation == "create":
        identity = {
            "operation": operation,
            "title": str(payload.get("title", "")).strip(),
            "start_at": timestamp(payload.get("start_at"), "start_at"),
            "end_at": timestamp(payload.get("end_at"), "end_at"),
            "reminder_at": timestamp(payload.get("reminder_at"), "reminder_at"),
        }
    elif operation == "update":
        patch = {}
        for key in ("title", "start_at", "end_at", "reminder_at", "completed"):
            if key not in payload:
                continue
            patch[key] = (
                timestamp(payload.get(key), key)
                if key in {"start_at", "end_at", "reminder_at"}
                else payload.get(key)
            )
        identity = {"operation": operation, "event_id": payload.get("event_id"), "patch": patch}
    elif operation == "delete":
        identity = {"operation": operation, "event_id": payload.get("event_id")}
    elif operation == "get":
        identity = {"operation": operation, "event_id": payload.get("event_id")}
    else:
        identity = {"operation": operation, "day": payload.get("day")}
    return json.dumps(identity, sort_keys=True, separators=(",", ":"))


class CalendarRuntime:
    """Single-worker owner of all main CalendarStore access."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._executor: ThreadPoolExecutor | None = None
        self._store: CalendarStore | None = None
        self._lock = threading.Lock()

    def _ensure_worker(self) -> ThreadPoolExecutor:
        with self._lock:
            if self._executor is None:
                self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="charlie-calendar")
            return self._executor

    def _ensure_store(self) -> CalendarStore:
        if self._store is None:
            self._store = CalendarStore(self.db_path)
            self._store.recover_stale_claims()
        return self._store

    def execute_sync(self, method: str, *args: Any, **kwargs: Any) -> Any:
        """Execute one store method on the Calendar worker for sync tool callers."""
        executor = self._ensure_worker()

        def call() -> Any:
            return getattr(self._ensure_store(), method)(*args, **kwargs)

        return executor.submit(call).result()

    async def execute(self, method: str, *args: Any, **kwargs: Any) -> Any:
        """Execute one store method without blocking the main event loop."""
        loop = asyncio.get_running_loop()
        executor = self._ensure_worker()

        def call() -> Any:
            return getattr(self._ensure_store(), method)(*args, **kwargs)

        return await loop.run_in_executor(executor, call)

    def close(self) -> None:
        with self._lock:
            executor = self._executor
            self._executor = None
            store = self._store
            self._store = None
        if executor is None:
            return

        def close_store() -> None:
            if store is not None:
                store.close()

        try:
            executor.submit(close_store).result()
        finally:
            executor.shutdown(wait=True, cancel_futures=True)


_calendar_runtime: Optional[CalendarRuntime] = None


def configure_calendar_runtime(runtime: Optional[CalendarRuntime]) -> None:
    global _calendar_runtime
    _calendar_runtime = runtime


def get_calendar_runtime() -> Optional[CalendarRuntime]:
    return _calendar_runtime


def calendar_runtime_required() -> CalendarRuntime:
    if _calendar_runtime is None:
        raise RuntimeError("Calendar runtime is unavailable")
    return _calendar_runtime

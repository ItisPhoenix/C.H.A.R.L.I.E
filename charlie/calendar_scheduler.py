"""Deterministic delivery of local reminder records."""

import inspect
from typing import Awaitable, Callable

from charlie.calendar_store import CalendarStore

ReminderCallback = Callable[[dict], None | Awaitable[None]]


async def deliver_due_reminders(store: CalendarStore, now_iso: str, callback: ReminderCallback) -> int:
    """Deliver each due reminder once, marking it complete only after delivery."""
    delivered = 0
    for event in store.due_reminders(now_iso):
        result = callback(event)
        if inspect.isawaitable(result):
            await result
        store.update_event(event["id"], {"completed": 1})
        delivered += 1
    return delivered

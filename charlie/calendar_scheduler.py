"""Deterministic at-least-once delivery of local reminder records."""

from __future__ import annotations

import asyncio
import inspect
import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from charlie.calendar_runtime import CalendarRuntime
from charlie.calendar_store import CalendarStore

logger = logging.getLogger("charlie.calendar_scheduler")
ReminderCallback = Callable[[dict], None | Awaitable[None]]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


async def _invoke(callback: Optional[ReminderCallback], event: dict) -> None:
    if callback is None:
        return
    result = callback(event)
    if inspect.isawaitable(result):
        await result


async def deliver_due_reminders(
    runtime_or_store: CalendarRuntime | CalendarStore,
    now_iso: str,
    callback: Optional[ReminderCallback] = None,
    *,
    alert_callback: Optional[ReminderCallback] = None,
    voice_callback: Optional[ReminderCallback] = None,
) -> int:
    """Claim and deliver reminders with durable at-least-once state.

    The legacy ``callback`` form remains for focused store tests; the main
    runtime supplies independent alert/voice channels for partial truth.
    """
    delivered = 0
    while True:
        try:
            if isinstance(runtime_or_store, CalendarRuntime):
                claim = await runtime_or_store.execute("claim_due_reminder", now_iso)
            else:
                claim = runtime_or_store.claim_due_reminder(now_iso)
            if claim is None:
                return delivered

            event_id = claim["id"]
            token = claim["reminder_claim_token"]
            revision = int(claim["reminder_revision"])
            try:
                if callback is not None:
                    if isinstance(runtime_or_store, CalendarRuntime):
                        await _invoke(callback, claim)
                        finalized = await runtime_or_store.execute(
                            "finalize_reminder_claim", event_id, token, revision, delivered=True
                        )
                    else:
                        await _invoke(callback, claim)
                        finalized = runtime_or_store.finalize_reminder_claim(
                            event_id, token, revision, delivered=True
                        )
                    if finalized:
                        delivered += 1
                    continue

                channels = (("alert", alert_callback), ("voice", voice_callback))
                failed: Optional[Exception] = None
                for channel, channel_callback in channels:
                    if channel_callback is None:
                        continue
                    if claim.get(f"reminder_{channel}_accepted_at"):
                        continue
                    current = (
                        await runtime_or_store.execute("claim_is_current", event_id, token, revision)
                        if isinstance(runtime_or_store, CalendarRuntime)
                        else runtime_or_store.claim_is_current(event_id, token, revision)
                    )
                    if not current:
                        break
                    try:
                        await _invoke(channel_callback, claim)
                    except Exception as exc:
                        failed = exc
                        break
                    accepted_at = _now_iso()
                    recorded = (
                        await runtime_or_store.execute(
                            "record_reminder_channel", event_id, token, revision, channel, accepted_at
                        )
                        if isinstance(runtime_or_store, CalendarRuntime)
                        else runtime_or_store.record_reminder_channel(
                            event_id, token, revision, channel, accepted_at
                        )
                    )
                    if not recorded:
                        break

                if failed is not None:
                    message = f"{type(failed).__name__}: {failed}"
                    if isinstance(runtime_or_store, CalendarRuntime):
                        await runtime_or_store.execute(
                            "finalize_reminder_claim", event_id, token, revision, delivered=False, error=message
                        )
                    else:
                        runtime_or_store.finalize_reminder_claim(
                            event_id, token, revision, delivered=False, error=message
                        )
                    logger.warning("Calendar reminder delivery failed for %s: %s", event_id, message)
                    return delivered

                finalized = (
                    await runtime_or_store.execute(
                        "finalize_reminder_claim", event_id, token, revision, delivered=True
                    )
                    if isinstance(runtime_or_store, CalendarRuntime)
                    else runtime_or_store.finalize_reminder_claim(event_id, token, revision, delivered=True)
                )
                if finalized:
                    delivered += 1
            except asyncio.CancelledError:
                if isinstance(runtime_or_store, CalendarRuntime):
                    await runtime_or_store.execute(
                        "finalize_reminder_claim",
                        event_id,
                        token,
                        revision,
                        delivered=False,
                        error="scheduler cancelled",
                    )
                else:
                    runtime_or_store.finalize_reminder_claim(
                        event_id, token, revision, delivered=False, error="scheduler cancelled"
                    )
                raise
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                try:
                    if isinstance(runtime_or_store, CalendarRuntime):
                        await runtime_or_store.execute(
                            "finalize_reminder_claim", event_id, token, revision, delivered=False, error=message
                        )
                    else:
                        runtime_or_store.finalize_reminder_claim(
                            event_id, token, revision, delivered=False, error=message
                        )
                except Exception:
                    logger.warning("Could not requeue failed reminder %s", event_id, exc_info=True)
                logger.warning("Calendar reminder processing failed for %s: %s", event_id, message)
                return delivered
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Calendar reminder scan failed; scheduler will continue", exc_info=True)
            return delivered

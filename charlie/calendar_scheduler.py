"""Deterministic at-least-once delivery of local reminder records."""

from __future__ import annotations

import asyncio
import inspect
import logging
import uuid
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from charlie.calendar_runtime import CalendarRuntime
from charlie.calendar_store import CalendarStore, normalize_calendar_timestamp

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


async def deliver_due_automation_reminders(
    runtime: CalendarRuntime,
    now_iso: str,
    *,
    alert_callback: ReminderCallback,
    voice_callback: Optional[ReminderCallback] = None,
    telegram_callback: Optional[ReminderCallback] = None,
) -> int:
    """Process due automation reminders through the durable run claim."""
    processed = 0
    while True:
        schedule = await runtime.execute("claim_due_automation", now_iso, kind="reminder")
        if schedule is None:
            return processed
        schedule_id = schedule["id"]
        token = schedule["claim_token"]
        revision = int(schedule["revision"])
        started = await runtime.execute("start_automation_run", schedule_id, token, revision)
        if not started:
            return processed

        outcomes = []
        try:
            await _invoke(alert_callback, schedule)
            outcomes.append("alert accepted")
            if voice_callback is None:
                outcomes.append("speech not queued")
            else:
                await _invoke(voice_callback, schedule)
                outcomes.append("speech queued")
            if telegram_callback is None:
                outcomes.append("Telegram message not configured")
            else:
                await _invoke(telegram_callback, schedule)
                outcomes.append("Telegram message accepted")
        except asyncio.CancelledError:
            # Leave the started claim for TaskJournal reconciliation after restart.
            raise
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            finished_at = max(normalize_calendar_timestamp(now_iso, "now"), _now_iso())
            await runtime.execute(
                "finalize_automation_claim", schedule_id, token, revision,
                succeeded=False, result="; ".join(outcomes), error=error,
                completed_at=finished_at,
            )
            processed += 1
            continue

        finished_at = max(normalize_calendar_timestamp(now_iso, "now"), _now_iso())
        finalized = await runtime.execute(
            "finalize_automation_claim", schedule_id, token, revision,
            succeeded=True, result="; ".join(outcomes), completed_at=finished_at,
        )
        processed += int(bool(finalized))


async def deliver_due_automation_tasks(
    runtime: CalendarRuntime,
    now_iso: str,
    *,
    dispatch_callback: ReminderCallback,
    task_journal,
) -> int:
    """Dispatch scheduled tasks and settle claims only from canonical TaskJournal state."""
    from charlie.background_task import RESTART_ERROR

    processed = 0
    while True:
        schedules = await runtime.execute("list_automations")
        for schedule in schedules:
            if schedule["kind"] != "task" or schedule["active_run_status"] not in {"running", "interrupted"}:
                continue
            task_id = schedule.get("active_task_id")
            if not task_id:
                continue
            try:
                record = task_journal.get(task_id)
            except KeyError:
                record = None

            if record is None:
                continue
            status = getattr(record.status, "value", record.status)
            if status not in {"completed", "failed", "cancelled"}:
                continue
            if schedule["active_run_status"] == "interrupted":
                if status == "failed" and record.error_summary == RESTART_ERROR:
                    continue
                token = uuid.uuid4().hex
                resumed = await runtime.execute(
                    "resume_interrupted_automation_run",
                    schedule["id"], task_id, int(schedule["active_run_revision"]), token,
                )
                if not resumed:
                    continue
            else:
                token = schedule.get("claim_token")
                if not token:
                    continue

            finished_at = _now_iso()
            await runtime.execute(
                "finalize_automation_claim",
                schedule["id"], token, int(schedule["active_run_revision"]),
                succeeded=status == "completed",
                result=record.result_reference or f"Task {task_id} {status}",
                error=record.error_summary if status != "completed" else None,
                completed_at=finished_at,
            )
            processed += 1

        schedules = await runtime.execute("list_automations")
        interrupted_without_record = None
        for schedule in schedules:
            if (
                schedule["kind"] != "task"
                or schedule["active_run_status"] != "interrupted"
                or schedule["next_run_at"] > normalize_calendar_timestamp(now_iso, "now")
                or not schedule.get("active_task_id")
            ):
                continue
            try:
                task_journal.get(schedule["active_task_id"])
            except KeyError:
                interrupted_without_record = schedule
                break

        claim = None
        if interrupted_without_record is not None:
            claim = await runtime.execute(
                "claim_due_automation", now_iso, kind="task", retry_interrupted=True,
                schedule_id=interrupted_without_record["id"],
            )
        if claim is None:
            claim = await runtime.execute("claim_due_automation", now_iso, kind="task")
        if claim is None:
            return processed

        schedule_id = claim["id"]
        token = claim["claim_token"]
        revision = int(claim["revision"])
        started = await runtime.execute("start_automation_run", schedule_id, token, revision)
        if not started:
            continue
        try:
            await _invoke(dispatch_callback, claim)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            try:
                task_journal.get(claim["active_task_id"])
            except KeyError:
                error = f"{type(exc).__name__}: {exc}"
                await runtime.execute(
                    "finalize_automation_claim", schedule_id, token, revision,
                    succeeded=False, error=error, completed_at=_now_iso(),
                )
            logger.warning("Scheduled task dispatch failed for %s: %s", schedule_id, exc)
        processed += 1

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from charlie import reminders


@pytest.fixture(autouse=True)
def _reset_reminders():
    reminders._reminders.clear()
    reminders._futures.clear()
    reminders._loop = None
    reminders._fire_callback = None
    yield
    reminders._reminders.clear()
    reminders._futures.clear()
    reminders._loop = None
    reminders._fire_callback = None


@pytest.mark.asyncio
async def test_zero_second_reminder_fires_once():
    reminders.set_loop(asyncio.get_running_loop())
    fired = []
    reminders.set_fire_callback(lambda rid, text: fired.append((rid, text)))

    reminder_id = reminders.set_reminder("check the oven", 0)
    await asyncio.sleep(0.1)

    assert fired == [(reminder_id, "check the oven")]
    assert reminders.list_reminders() == {}


@pytest.mark.asyncio
async def test_cancel_reminder_prevents_fire():
    reminders.set_loop(asyncio.get_running_loop())
    fired = []
    reminders.set_fire_callback(lambda rid, text: fired.append((rid, text)))

    reminder_id = reminders.set_reminder("never fires", 10)
    assert reminders.cancel_reminder(reminder_id) is True
    await asyncio.sleep(0.1)

    assert fired == []
    assert reminders.list_reminders() == {}
    assert reminders.cancel_reminder(reminder_id) is False


def test_over_cap_delay_rejected():
    reminders.set_loop(asyncio.new_event_loop())
    with pytest.raises(ValueError):
        reminders.set_reminder("too far out", reminders.MAX_REMINDER_SECONDS + 1)


def test_negative_delay_rejected():
    reminders.set_loop(asyncio.new_event_loop())
    with pytest.raises(ValueError):
        reminders.set_reminder("negative", -1)

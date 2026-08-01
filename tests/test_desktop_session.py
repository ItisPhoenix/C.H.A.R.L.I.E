import ctypes
import sys

import pytest

from charlie.desktop import session


@pytest.fixture(autouse=True)
def _reset_owner():
    session._owner = None
    yield
    session._owner = None


def test_mutex_exclusive():
    assert session.acquire_desktop("task-1")
    assert not session.acquire_desktop("task-2")
    session.release_desktop("task-1")
    assert session.acquire_desktop("task-2")
    session.release_desktop("task-2")


def test_release_wrong_owner_is_noop():
    session.acquire_desktop("task-1")
    session.release_desktop("task-2")
    assert session.current_owner() == "task-1"
    session.release_desktop("task-1")


def test_acquire_same_owner_twice_is_idempotent():
    assert session.acquire_desktop("task-1")
    assert session.acquire_desktop("task-1")
    session.release_desktop("task-1")


def test_current_owner_reflects_state():
    assert session.current_owner() is None
    session.acquire_desktop("task-1")
    assert session.current_owner() == "task-1"
    session.release_desktop("task-1")


def test_user_idle_seconds(monkeypatch):
    monkeypatch.setattr(session, "_last_input_tick_ms", lambda: 5_000)
    monkeypatch.setattr(session, "_now_tick_ms", lambda: 65_000)
    assert session.user_idle_seconds() == 60.0


@pytest.mark.skipif(sys.platform != "win32", reason="real GetTickCount/GetLastInputInfo calls, Windows only")
def test_tick_functions_return_int():
    assert isinstance(session._now_tick_ms(), int)
    assert isinstance(session._last_input_tick_ms(), int)


@pytest.mark.skipif(sys.platform != "win32", reason="real GetTickCount call, Windows only")
def test_gettickcount_restype_is_unsigned():
    # ctypes defaults an unset restype to signed c_int/c_long. Without this,
    # GetTickCount's raw DWORD gets read as signed, going negative after
    # 2**31 ms (~24.85 days) instead of the true 2**32 ms (~49.7 day) wraparound.
    assert session._kernel32.GetTickCount.restype is ctypes.c_uint


def test_large_dword_value_is_positive_unsigned_but_negative_signed():
    # This is the exact mechanism behind the bug: the same raw 32-bit tick
    # count is a large positive DWORD as unsigned, but negative as signed.
    raw_value = 3_000_000_000  # > 2**31, still within uint32 range
    assert ctypes.c_uint(raw_value).value == raw_value
    assert ctypes.c_int(raw_value).value < 0


def test_user_idle_seconds_past_signed_wraparound_point(monkeypatch):
    # Simulate tick counts past the ~24.85 day signed-overflow point that
    # would have gone negative under the old (implicitly signed) restype.
    monkeypatch.setattr(session, "_last_input_tick_ms", lambda: 3_000_000_000)
    monkeypatch.setattr(session, "_now_tick_ms", lambda: 3_000_005_000)
    assert session.user_idle_seconds() == pytest.approx(5.0)


def test_external_input_since_false_for_automations_own_action(monkeypatch):
    # The task's own click bumped GetLastInputInfo at tick 1000 -- must not read as external.
    monkeypatch.setattr(session, "_last_input_tick_ms", lambda: 1_000)
    assert session.external_input_since(1_000) is False


def test_external_input_since_true_for_real_input_after_action(monkeypatch):
    # Real input landed at tick 2000, after the automation's last action at 1000.
    monkeypatch.setattr(session, "_last_input_tick_ms", lambda: 2_000)
    assert session.external_input_since(1_000) is True

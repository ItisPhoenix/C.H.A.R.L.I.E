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


def test_tick_functions_return_int():
    assert isinstance(session._now_tick_ms(), int)
    assert isinstance(session._last_input_tick_ms(), int)

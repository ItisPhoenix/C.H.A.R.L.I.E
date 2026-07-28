"""Tests for the H.E.L.M. autonomous desktop operator agent (Task C3)."""

import json

import pytest

from charlie.agents import AGENT_REGISTRY
from charlie.agents.helm import HELM
from charlie.blackboard import Blackboard
from charlie.desktop import session


class _FakeResponse:
    def __init__(self, content: str):
        self._content = content

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


class _FakeLLMClient:
    """Returns each queued response in order, one per _complete() call."""

    def __init__(self, contents):
        self.model = "test-model"
        self._contents = list(contents)

    async def post(self, path, *, json=None, **kwargs):
        content = self._contents.pop(0) if self._contents else '{"done": true, "result": "done"}'
        return _FakeResponse(content)


@pytest.fixture(autouse=True)
def _reset_desktop_session():
    session.release_desktop(session.current_owner() or "")
    yield
    session.release_desktop(session.current_owner() or "")


def test_helm_registered_in_agent_registry():
    assert AGENT_REGISTRY.get("H.E.L.M.") is HELM


def test_helm_allowed_tools_covers_desktop_and_shell():
    blackboard = Blackboard()
    agent = HELM(blackboard, None)
    assert "shell_execute" in agent.allowed_tools
    assert "desktop_observe" in agent.allowed_tools
    assert "desktop_click_at" in agent.allowed_tools
    assert all(t.startswith("desktop_") or t == "shell_execute" for t in agent.allowed_tools)


@pytest.mark.asyncio
async def test_helm_fails_fast_when_desktop_busy(monkeypatch):
    monkeypatch.setattr(session, "acquire_desktop", lambda owner: False)

    blackboard = Blackboard()
    task = blackboard.add_task("do something", assigned_to="H.E.L.M.")
    agent = HELM(blackboard, _FakeLLMClient([]))
    agent._task_id = task.id

    result = await agent._do_action(task.name, task)

    assert "Desktop is in use" in result


@pytest.mark.asyncio
async def test_helm_fails_fast_when_user_not_idle(monkeypatch):
    monkeypatch.setattr(session, "acquire_desktop", lambda owner: True)
    monkeypatch.setattr(session, "release_desktop", lambda owner: None)
    monkeypatch.setattr(session, "user_idle_seconds", lambda: 1.0)

    blackboard = Blackboard()
    task = blackboard.add_task("do something", assigned_to="H.E.L.M.")
    agent = HELM(blackboard, _FakeLLMClient([]))
    agent._task_id = task.id

    result = await agent._do_action(task.name, task)

    assert "idle" in result.lower()


@pytest.mark.asyncio
async def test_helm_releases_mutex_on_completion(monkeypatch):
    released = []
    monkeypatch.setattr(session, "acquire_desktop", lambda owner: True)
    monkeypatch.setattr(session, "release_desktop", lambda owner: released.append(owner))
    monkeypatch.setattr(session, "user_idle_seconds", lambda: 999.0)
    monkeypatch.setattr(session, "_last_input_tick_ms", lambda: 1000)
    monkeypatch.setattr(
        "charlie.tools.registry.execute_tool", lambda name, arguments: "ok"
    )

    blackboard = Blackboard()
    task = blackboard.add_task("do something", assigned_to="H.E.L.M.")
    agent = HELM(blackboard, _FakeLLMClient([json.dumps({"done": True, "result": "All done"})]))
    agent._task_id = task.id

    result = await agent._do_action(task.name, task)

    assert result == "All done"
    assert released == [task.id]


@pytest.mark.asyncio
async def test_helm_releases_mutex_on_exception(monkeypatch):
    released = []
    monkeypatch.setattr(session, "acquire_desktop", lambda owner: True)
    monkeypatch.setattr(session, "release_desktop", lambda owner: released.append(owner))
    monkeypatch.setattr(session, "user_idle_seconds", lambda: 999.0)
    monkeypatch.setattr(session, "_last_input_tick_ms", lambda: 1000)

    def _boom(name, arguments):
        raise RuntimeError("kaboom")

    monkeypatch.setattr("charlie.tools.registry.execute_tool", _boom)

    blackboard = Blackboard()
    task = blackboard.add_task("do something", assigned_to="H.E.L.M.")
    agent = HELM(blackboard, _FakeLLMClient([json.dumps({"tool": "desktop_click_at", "arguments": {"x": 1, "y": 1}})]))
    agent._task_id = task.id

    with pytest.raises(RuntimeError):
        await agent._do_action(task.name, task)

    assert released == [task.id]


@pytest.mark.asyncio
async def test_helm_pauses_on_external_input(monkeypatch):
    """Between our own actions, a fresh tick newer than the one we recorded
    right after our last action means the *user* touched the machine (not
    our own pyautogui synthetic input) -- must pause immediately, per the
    comparison charlie/desktop/session.py's docstring anticipates."""
    monkeypatch.setattr(session, "acquire_desktop", lambda owner: True)
    monkeypatch.setattr(session, "release_desktop", lambda owner: None)
    monkeypatch.setattr(session, "user_idle_seconds", lambda: 999.0)

    ticks = iter([1000, 5000])  # second read is newer -> external input
    monkeypatch.setattr(session, "_last_input_tick_ms", lambda: next(ticks))
    monkeypatch.setattr(
        "charlie.tools.registry.execute_tool", lambda name, arguments: "should not be reached"
    )

    blackboard = Blackboard()
    task = blackboard.add_task("do something", assigned_to="H.E.L.M.")
    agent = HELM(blackboard, _FakeLLMClient([]))
    agent._task_id = task.id

    result = await agent._do_action(task.name, task)

    assert "paused" in result.lower()

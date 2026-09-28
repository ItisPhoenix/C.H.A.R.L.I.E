"""Tests for the background-task chat/voice trigger: Brain._handle_start_background_task
and its _exec_one interception (charlie/core.py) -- mirrors test_propose_new_tool.py.
"""

import asyncio
import json

import pytest

from charlie import background_task
from charlie.config import Config
from charlie.core import Brain
from charlie.task_journal import TaskJournal, TaskStatus
from charlie.tasks import TaskManager


@pytest.fixture
def brain_config():
    return Config(
        llm_url="https://example.com/v1", llm_key="test-key", llm_model="dummy",
        iteration_budget_max=5, world_model_db_path=":memory:",
    )


class _FakeTask:
    id = "abc123"
    status = "running"


class TestHandleStartBackgroundTask:
    @pytest.mark.asyncio
    async def test_valid_text_starts_task_and_confirms(self, monkeypatch, brain_config):
        sentinel_store = object()
        sentinel_graph = object()
        sentinel_service = object()
        brain = Brain(
            brain_config,
            memory_store=sentinel_store,
            memory_graph=sentinel_graph,
            memory_service=sentinel_service,
        )

        class _FakeBus:
            async def emit(self, *a, **kw):
                pass

        import charlie.recovery as recovery
        monkeypatch.setattr(recovery, "_event_bus", _FakeBus())

        captured = {}
        callbacks = [lambda *_args, **_kwargs: None for _ in range(4)]
        brain.on_tool_call, brain.on_tool_result = callbacks[:2]
        brain.on_operation_result, brain.on_thinking_update = callbacks[2:]
        def approval_callback(*_args, **_kwargs):
            return True

        brain.on_tool_approval_request = approval_callback

        async def fake_start(config, event_bus, text, session_store=None, memory_store=None,
                              voice=None, priority=0, depends_on=None, on_result_stored=None,
                              memory_graph=None, memory_service=None, **kwargs):
            captured["text"] = text
            captured["priority"] = priority
            captured["depends_on"] = depends_on
            captured["memory_store"] = memory_store
            captured["memory_graph"] = memory_graph
            captured["memory_service"] = memory_service
            captured.update(kwargs)
            return _FakeTask()

        monkeypatch.setattr(background_task, "start", fake_start)

        result = await brain._handle_start_background_task(
            {"text": "organize downloads folder"}, platform="telegram"
        )
        assert "abc123" in result
        assert captured["text"] == "organize downloads folder"
        assert captured["memory_store"] is sentinel_store
        assert captured["memory_graph"] is sentinel_graph
        assert captured["memory_service"] is sentinel_service
        assert captured["on_tool_call"] is callbacks[0]
        assert captured["on_tool_result"] is callbacks[1]
        assert captured["on_operation_result"] is callbacks[2]
        assert captured["on_thinking_update"] is callbacks[3]
        assert captured["on_tool_approval_request"] is approval_callback
        assert captured["approval_platform"] == "telegram"
        assert captured["require_successful_operation"] is False

    @pytest.mark.asyncio
    async def test_missing_text_returns_error_without_starting(self, monkeypatch, brain_config):
        brain = Brain(brain_config)
        called = {"n": 0}

        async def fake_start(*a, **kw):
            called["n"] += 1
            return _FakeTask()

        monkeypatch.setattr(background_task, "start", fake_start)
        result = await brain._handle_start_background_task({"text": "  "})
        assert result.startswith("Error")
        assert called["n"] == 0

    @pytest.mark.asyncio
    async def test_no_event_bus_returns_error_without_starting(self, monkeypatch, brain_config):
        brain = Brain(brain_config)
        import charlie.recovery as recovery
        monkeypatch.setattr(recovery, "_event_bus", None)
        called = {"n": 0}

        async def fake_start(*a, **kw):
            called["n"] += 1
            return _FakeTask()

        monkeypatch.setattr(background_task, "start", fake_start)
        result = await brain._handle_start_background_task({"text": "do something"})
        assert result.startswith("Error")
        assert called["n"] == 0

    @pytest.mark.asyncio
    async def test_priority_and_depends_on_forwarded(self, monkeypatch, brain_config):
        brain = Brain(brain_config)

        class _FakeBus:
            async def emit(self, *a, **kw):
                pass

        import charlie.recovery as recovery
        monkeypatch.setattr(recovery, "_event_bus", _FakeBus())

        captured = {}

        async def fake_start(config, event_bus, text, session_store=None, memory_store=None,
                              voice=None, priority=0, depends_on=None, on_result_stored=None,
                              memory_graph=None, memory_service=None, **kwargs):
            captured["priority"] = priority
            captured["depends_on"] = depends_on
            captured.update(kwargs)
            return _FakeTask()

        monkeypatch.setattr(background_task, "start", fake_start)
        await brain._handle_start_background_task(
            {"text": "step two", "priority": 5, "depends_on": ["earlier-id"]},
            require_successful_operation=True,
        )
        assert captured["priority"] == 5
        assert captured["depends_on"] == ["earlier-id"]
        assert captured["require_successful_operation"] is True


def _sse_tool_call_response(tool_name, arguments, call_id="1"):
    fn = {"name": tool_name, "arguments": json.dumps(arguments)}
    delta = {"tool_calls": [{"index": 0, "id": call_id, "function": fn}]}
    line = "data: " + json.dumps({"choices": [{"delta": delta}]})

    class MockResponse:
        def raise_for_status(self):
            pass

        async def aiter_lines(self):
            yield line
            yield "data: [DONE]"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

    return MockResponse()


@pytest.mark.asyncio
async def test_foreground_start_queues_task_before_delayed_planner(monkeypatch, brain_config, tmp_path):
    import charlie.recovery as recovery
    from charlie import core as core_module

    task_id = "bg-plan-before-return"
    planning_started = asyncio.Event()
    release_planner = asyncio.Event()
    observations = {}
    journal = TaskJournal(state_path=tmp_path / "task-journal.json")
    manager = TaskManager(max_parallel=1, on_status_change=background_task._on_manager_status_change)

    class EventBus:
        async def emit(self, *_args, **_kwargs):
            return True

    class DelayedPlannerBrain:
        instances = []

        def __init__(self, config, **_kwargs):
            self.config = config
            self.closed = False
            self.on_result_stored = _kwargs.get("on_result_stored")
            type(self).instances.append(self)

        async def chat_stream(self, user_input, **kwargs):
            if "Break the following task" in user_input:
                try:
                    observations["journal_before_plan"] = journal.get(kwargs["task_id"])
                except KeyError:
                    observations["journal_before_plan"] = None
                planning_started.set()
                try:
                    await release_planner.wait()
                finally:
                    observations["planner_unblocked"] = True
                yield "1. Step one\n2. Step two\n"
            else:
                observations.setdefault("executed_steps", []).append(user_input)
                yield "Step finished."

        async def close(self):
            self.closed = True

        def cancel_chat(self):
            return None

    monkeypatch.setattr(background_task, "_journal", journal)
    monkeypatch.setattr(background_task, "_manager", manager)
    monkeypatch.setattr(background_task, "_active_tasks", {})
    monkeypatch.setattr(background_task, "_current_task", None)
    monkeypatch.setattr(background_task, "_active_event_bus", None)
    monkeypatch.setattr(background_task, "_DESKTOP_AVAILABLE", False)
    monkeypatch.setattr(background_task, "_register_takeover_listener", lambda: None)
    monkeypatch.setattr(background_task, "make_id", lambda length=8: task_id if length == 8 else "session")
    monkeypatch.setattr(background_task, "Brain", DelayedPlannerBrain)
    monkeypatch.setattr(recovery, "_event_bus", EventBus())
    original_timeout = core_module._tool_timeout
    monkeypatch.setattr(
        core_module,
        "_tool_timeout",
        lambda name, operation=None: 0.2 if name == "start_background_task" else original_timeout(name, operation),
    )

    brain = Brain(brain_config)
    manager_task = None
    try:
        async def start_from_foreground_capability():
            return await brain._handle_start_background_task(
                {"text": "do the thing"},
                platform="telegram",
                session_id="foreground-session",
                turn_id="foreground-turn",
            )

        outcome = await brain.execute_tool_operation(
            "start_background_task",
            {"text": "do the thing"},
            request="Start the task",
            task_id="foreground-task",
            session_id="foreground-session",
            turn_id="foreground-turn",
            platform="telegram",
            execute_override=start_from_foreground_capability,
        )
        await asyncio.wait_for(planning_started.wait(), timeout=1)

        row = observations["journal_before_plan"]
        assert row is not None, (
            "foreground task.background.start timed out before queue submission: "
            f"journal row missing, manager row={manager.get(task_id)!r}, "
            f"result={outcome.result!r}, failure_kind={(outcome.data or {}).get('failure_kind')!r}"
        )
        assert row.status is TaskStatus.PLANNING
        assert manager.get(task_id) is not None
        assert outcome.status == "completed"
        assert not (outcome.data or {}).get("failure_kind")
    finally:
        release_planner.set()
        await brain.close()
        if task_id in manager._task_handles:
            manager_task = manager._task_handles[task_id]
            await asyncio.wait_for(asyncio.gather(manager_task, return_exceptions=True), timeout=2)
        for planner in DelayedPlannerBrain.instances:
            if not planner.closed:
                await planner.close()


@pytest.mark.asyncio
async def test_unusable_plan_fails_without_executing_request_text(monkeypatch, brain_config, tmp_path):
    task_id = "bg-invalid-plan"
    executed_steps = []
    journal = TaskJournal(state_path=tmp_path / "task-journal.json")
    manager = TaskManager(max_parallel=1, on_status_change=background_task._on_manager_status_change)

    class EventBus:
        async def emit(self, *_args, **_kwargs):
            return True

    class InvalidPlannerBrain:
        def __init__(self, config, **_kwargs):
            self.config = config
            self.closed = False
            self.on_result_stored = _kwargs.get("on_result_stored")

        async def chat_stream(self, user_input, **_kwargs):
            if "Break the following task" in user_input:
                yield "I cannot create a numbered plan."
            else:
                executed_steps.append(user_input)
                yield "Unexpected execution."

        async def close(self):
            self.closed = True

    monkeypatch.setattr(background_task, "_journal", journal)
    monkeypatch.setattr(background_task, "_manager", manager)
    monkeypatch.setattr(background_task, "_active_tasks", {})
    monkeypatch.setattr(background_task, "_current_task", None)
    monkeypatch.setattr(background_task, "_active_event_bus", None)
    monkeypatch.setattr(background_task, "_DESKTOP_AVAILABLE", False)
    monkeypatch.setattr(background_task, "_register_takeover_listener", lambda: None)
    monkeypatch.setattr(background_task, "make_id", lambda length=8: task_id if length == 8 else "session")
    monkeypatch.setattr(background_task, "Brain", InvalidPlannerBrain)

    task = await background_task.start(brain_config, EventBus(), "perform the requested action", task_id=task_id)
    handle = manager._task_handles[task.id]
    await asyncio.wait_for(handle, timeout=1)

    assert journal.get(task.id).status is TaskStatus.FAILED
    assert executed_steps == []


class TestExecOneInterception:
    @pytest.mark.asyncio
    async def test_start_background_task_bypasses_registry_stub(self, monkeypatch, brain_config):
        """The registered start_background_task func is a stub that always errors --
        _exec_one must intercept before reaching it, proving the real handler ran."""
        brain = Brain(brain_config)
        args = {"text": "organize downloads folder"}

        calls = {"n": 0}

        def mock_stream(*a, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                return _sse_tool_call_response("start_background_task", args)
            delta = {"content": "Done."}
            line = "data: " + json.dumps({"choices": [{"delta": delta}]})

            class MockResponse:
                def raise_for_status(self):
                    pass

                async def aiter_lines(self):
                    yield line
                    yield "data: [DONE]"

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *a):
                    pass

            return MockResponse()

        monkeypatch.setattr(brain.client, "stream", mock_stream)

        import charlie.recovery as recovery
        monkeypatch.setattr(recovery, "_event_bus", None)

        chunks = []
        async for chunk in brain.chat_stream("organize my downloads folder in the background", platform="web"):
            chunks.append(chunk)
        result = "".join(chunks)
        assert "must be intercepted" not in result

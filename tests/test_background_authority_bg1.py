import asyncio
import threading

import pytest

import charlie.core as core
from charlie import background_task, resource_locks
from charlie.browser import task as browser_task_module
from charlie.browser.recipes import BrowserResult
from charlie.config import Config
from charlie.core import Brain
from charlie.fastpaths import FastPathMatch, FastPathResult
from charlie.task_journal import TaskJournal, TaskStatus
from charlie.tasks import TaskManager
from charlie.turn_contracts import ResultStatus


class _EventBus:
    async def emit(self, *_args, **_kwargs):
        return None


def _config(tmp_path) -> Config:
    return Config(
        llm_url="http://localhost:11434",
        llm_key="no-key",
        llm_model="dummy",
        desktop_control_enabled=True,
        background_max_parallel_tasks=1,
        session_db_path=str(tmp_path / "bg1-sessions.db"),
    )


@pytest.fixture(autouse=True)
def _isolate_background_state(monkeypatch, tmp_path):
    background_task._unregister_takeover_listener()
    with background_task._active_tasks_lock:
        background_task._active_tasks.clear()
    owner = resource_locks.current_owner("desktop")
    if owner is not None:
        resource_locks.release("desktop", owner)
    monkeypatch.setattr(background_task, "_DESKTOP_AVAILABLE", False)
    monkeypatch.setattr(background_task, "_journal", TaskJournal(state_path=tmp_path / "bg1-journal.json"))
    background_task._manager = TaskManager(
        max_parallel=1,
        on_status_change=background_task._on_manager_status_change,
    )
    background_task._active_event_bus = None
    yield
    background_task._unregister_takeover_listener()
    with background_task._active_tasks_lock:
        background_task._active_tasks.clear()
    owner = resource_locks.current_owner("desktop")
    if owner is not None:
        resource_locks.release("desktop", owner)


@pytest.mark.asyncio
async def test_background_start_propagates_identity_callbacks_without_outer_desktop_lease(
    monkeypatch, tmp_path
):
    calls = []
    callbacks = [lambda *_args, **_kwargs: None for _ in range(4)]

    async def fake_chat_stream(self, user_input, **kwargs):
        calls.append((user_input, kwargs, resource_locks.current_owner("desktop")))
        if kwargs.get("skip_tools"):
            yield "1. Run one step\n"
        else:
            yield "step complete"

    monkeypatch.setattr(Brain, "chat_stream", fake_chat_stream)
    task = await background_task.start(
        _config(tmp_path),
        _EventBus(),
        "run one step",
        task_id="bg-identity",
        session_id="session-bg1",
        turn_id="turn-bg1",
        on_tool_call=callbacks[0],
        on_tool_result=callbacks[1],
        on_operation_result=callbacks[2],
        on_thinking_update=callbacks[3],
        announce=False,
    )
    await asyncio.sleep(0.05)

    assert task.status == "done"
    assert len(calls) == 2
    planning, execution = calls
    assert planning[1]["task_id"] == task.id
    assert planning[1]["turn_id"] == task.turn_id
    assert planning[1]["session_id"] == task.session_id
    assert planning[1]["execution_owner_id"] == task.id
    assert planning[1]["skip_tools"] is True
    assert execution[1]["task_id"] == task.id
    assert execution[1]["turn_id"] == task.turn_id
    assert execution[1]["session_id"] == task.session_id
    assert execution[1]["execution_owner_id"] == task.id
    assert execution[1].get("skip_tools", False) is False
    assert planning[2] is None
    assert execution[2] is None
    assert task.brain.on_tool_call is callbacks[0]
    assert task.brain.on_tool_result is callbacks[1]
    assert task.brain.on_operation_result is callbacks[2]
    assert task.brain.on_thinking_update is callbacks[3]
    assert resource_locks.current_owner("desktop") is None


@pytest.mark.asyncio
async def test_canonical_desktop_operation_uses_background_task_as_lease_owner(tmp_path):
    brain = Brain(_config(tmp_path), register_panic_hotkey=False)
    seen_owners = []

    async def execute_override():
        seen_owners.append(resource_locks.current_owner("desktop"))
        return "opened"

    try:
        outcome = await asyncio.wait_for(
            brain._execute_operation_primitive(
                "desktop_open_app",
                {"apps": ["notepad"]},
                request="open notepad",
                task_id="bg-operation",
                session_id="session-bg1",
                turn_id="turn-bg1",
                execution_owner_id="bg-operation",
                execute_override=execute_override,
            ),
            timeout=1.0,
        )
    finally:
        await brain.close()

    assert outcome.status == ResultStatus.COMPLETED.value
    assert outcome.task_id == "bg-operation"
    assert outcome.session_id == "session-bg1"
    assert outcome.turn_id == "turn-bg1"
    assert seen_owners == ["bg-operation"]
    assert resource_locks.current_owner("desktop") is None


@pytest.mark.asyncio
async def test_background_and_foreground_desktop_operations_serialize_without_self_deadlock(tmp_path):
    background_brain = Brain(_config(tmp_path), register_panic_hotkey=False)
    foreground_brain = Brain(_config(tmp_path), register_panic_hotkey=False)
    started = asyncio.Event()
    release = asyncio.Event()
    seen = []

    async def background_operation():
        seen.append(("background", resource_locks.current_owner("desktop")))
        started.set()
        await release.wait()
        return "background complete"

    async def foreground_operation():
        seen.append(("foreground", resource_locks.current_owner("desktop")))
        return "foreground complete"

    background_call = asyncio.create_task(
        background_brain._execute_operation_primitive(
            "desktop_open_app",
            {"apps": ["notepad"]},
            request="open notepad",
            task_id="bg-owner",
            session_id="session-bg1",
            turn_id="turn-bg1",
            execution_owner_id="bg-owner",
            execute_override=background_operation,
        )
    )
    foreground_call = None
    try:
        await asyncio.wait_for(started.wait(), timeout=1.0)
        foreground_call = asyncio.create_task(
            foreground_brain._execute_operation_primitive(
                "desktop_open_app",
                {"apps": ["calculator"]},
                request="open calculator",
                task_id="foreground-owner",
                session_id="session-foreground",
                turn_id="turn-foreground",
                execution_owner_id="foreground-owner",
                execute_override=foreground_operation,
            )
        )
        await asyncio.sleep(0.03)
        assert not foreground_call.done()
        release.set()
        await asyncio.wait_for(asyncio.gather(background_call, foreground_call), timeout=1.0)
    finally:
        release.set()
        if not background_call.done():
            background_call.cancel()
        if foreground_call is not None and not foreground_call.done():
            foreground_call.cancel()
        await background_brain.close()
        await foreground_brain.close()

    assert seen == [("background", "bg-owner"), ("foreground", "foreground-owner")]
    assert resource_locks.current_owner("desktop") is None


class _BlockingBrain:
    def __init__(self, config):
        self.config = config
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.cancel_calls = 0
        self.calls = []

    def cancel_chat(self):
        self.cancel_calls += 1
        self.cancelled.set()

    async def chat_stream(self, _text, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            self.started.set()
            await self.cancelled.wait()
        yield "step"

    async def close(self):
        return None


async def _admit_blocking_task(tmp_path, task_id="bg-takeover"):
    task = background_task.BackgroundTask(
        id=task_id,
        text="run desktop task",
        steps=["first", "second"],
        capability_requirements=("desktop",),
        cancel_event=asyncio.Event(),
        owner_loop=asyncio.get_running_loop(),
    )
    task.brain = _BlockingBrain(_config(tmp_path))
    background_task._record_task_lifecycle(task, status=TaskStatus.PLANNING)
    with background_task._active_tasks_lock:
        background_task._active_tasks[task.id] = task
    bus = _EventBus()
    background_task._active_event_bus = bus
    background_task._register_takeover_listener()
    background_task._manager.submit(task, lambda: background_task._run_loop(task, bus))
    await asyncio.wait_for(task.brain.started.wait(), timeout=1.0)
    return task


@pytest.mark.asyncio
async def test_default_manager_takeover_from_worker_thread_cancels_background_and_skips_later_steps(tmp_path):
    task = await _admit_blocking_task(tmp_path)
    lease = await resource_locks.default_lease_manager.acquire("desktop", task.id)
    try:
        revoked = await asyncio.to_thread(
            resource_locks.default_lease_manager.manual_takeover,
            ("desktop",),
        )
        await asyncio.sleep(0)
        for _ in range(50):
            if task.status == "cancelled":
                break
            await asyncio.sleep(0.01)
    finally:
        await lease.release()

    assert revoked == {task.id}
    assert task.cancel_requested is True
    assert task.brain.cancel_calls == 1
    assert len(task.brain.calls) == 1
    assert task.status == "cancelled"
    assert resource_locks.current_owner("desktop") is None


@pytest.mark.asyncio
async def test_unrelated_foreground_takeover_owner_does_not_cancel_background_task(tmp_path):
    task = await _admit_blocking_task(tmp_path, task_id="bg-unrelated")
    assert resource_locks.acquire("desktop", "foreground-owner") is True
    try:
        revoked = resource_locks.default_lease_manager.manual_takeover(("desktop",))
        await asyncio.sleep(0)
        assert revoked == {"foreground-owner"}
        assert task.cancel_requested is False
        assert task.brain.cancel_calls == 0
    finally:
        background_task.cancel(task.id)
        await asyncio.sleep(0.05)


def test_global_takeover_listener_does_not_duplicate_instance_callback():
    calls = []

    def listener(owner_id, resources):
        calls.append((owner_id, resources))

    manager = resource_locks.CapabilityLeaseManager(on_takeover=listener)
    resource_locks.register_takeover_listener(listener)
    try:
        assert asyncio.run(manager.acquire("desktop", "listener-owner"))
        assert manager.manual_takeover(("desktop",)) == {"listener-owner"}
    finally:
        resource_locks.unregister_takeover_listener(listener)

    assert calls == [("listener-owner", ("desktop",))]


@pytest.mark.asyncio
async def test_background_shutdown_unregisters_takeover_listener_without_outer_lease(monkeypatch, tmp_path):
    started = asyncio.Event()
    block = asyncio.Event()

    async def fake_chat_stream(self, _text, **kwargs):
        if kwargs.get("skip_tools"):
            yield "1. Wait\n"
            return
        started.set()
        await block.wait()
        yield "done"

    monkeypatch.setattr(Brain, "chat_stream", fake_chat_stream)
    task = await background_task.start(
        _config(tmp_path),
        _EventBus(),
        "wait for shutdown",
        task_id="bg-shutdown",
        announce=False,
    )
    await asyncio.wait_for(started.wait(), timeout=1.0)
    assert background_task._takeover_listener_registered is True
    assert resource_locks.current_owner("desktop") is None

    await background_task.shutdown()

    assert task.status == "cancelled"
    assert background_task._takeover_listener_registered is False
    assert resource_locks.current_owner("desktop") is None


def _focus_fast_path_match() -> FastPathMatch:
    return FastPathMatch(
        intent="focus_app",
        semantic_op_id="desktop.window.focus",
        tool_name="desktop_focus",
        arguments={"title": "notepad"},
        target_domain="desktop",
    )


@pytest.mark.asyncio
async def test_deterministic_fast_path_uses_explicit_execution_owner(monkeypatch, tmp_path):
    owners = []
    monkeypatch.setattr("charlie.fastpaths.match_fast_path", lambda _query: _focus_fast_path_match())
    monkeypatch.setattr(
        "charlie.fastpaths.execute_fast_path",
        lambda _match: owners.append(resource_locks.current_owner("desktop")) or FastPathResult("focused"),
    )
    brain = Brain(_config(tmp_path), register_panic_hotkey=False)
    try:
        [
            chunk
            async for chunk in brain.chat_stream(
                "focus notepad",
                platform="text",
                skip_pre_search=True,
                session_id="session-bg1",
                task_id="bg-test",
                turn_id="turn-bg1",
                execution_owner_id="bg-test",
            )
        ]
    finally:
        await brain.close()

    assert owners == ["bg-test"]
    assert resource_locks.current_owner("desktop") is None


@pytest.mark.asyncio
async def test_deterministic_fast_path_keeps_foreground_owner_fallback(monkeypatch, tmp_path):
    owners = []
    monkeypatch.setattr("charlie.fastpaths.match_fast_path", lambda _query: _focus_fast_path_match())
    monkeypatch.setattr(
        "charlie.fastpaths.execute_fast_path",
        lambda _match: owners.append(resource_locks.current_owner("desktop")) or FastPathResult("focused"),
    )
    brain = Brain(_config(tmp_path), register_panic_hotkey=False)
    try:
        [chunk async for chunk in brain.chat_stream("focus notepad", platform="text", skip_pre_search=True)]
    finally:
        await brain.close()

    assert owners == ["fastpath.focus_app"]
    assert resource_locks.current_owner("desktop") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("execution_owner_id", "expected_owner"),
    [("bg-browser", "bg-browser"), (None, "turn:turn-browser")],
)
async def test_browser_execution_owner_prefers_explicit_owner_and_preserves_fallback(
    monkeypatch, tmp_path, execution_owner_id, expected_owner
):
    owners = []

    async def fake_resolve(*_args, **kwargs):
        owners.append(kwargs["owner_id"])
        return BrowserResult(answer="Verified browser result.", success=True, verification="verified")

    monkeypatch.setattr(core, "_BROWSER_AVAILABLE", True)
    monkeypatch.setattr("charlie.recovery._event_bus", None)
    monkeypatch.setattr(browser_task_module, "resolve", fake_resolve)
    brain = Brain(
        Config(
            llm_url="http://localhost:11434",
            llm_key="no-key",
            llm_model="dummy",
            browser_enabled=True,
        ),
        register_panic_hotkey=False,
    )
    try:
        outcome = await brain.browser_task(
            "Search shop.example for laptops.",
            platform="web",
            task_id="task-browser",
            session_id="session-bg1",
            turn_id="turn-browser",
            execution_owner_id=execution_owner_id,
            return_envelope=True,
        )
    finally:
        await brain.close()

    assert owners == [expected_owner]
    assert outcome.task_id == "task-browser"
    assert outcome.session_id == "session-bg1"
    assert outcome.turn_id == "turn-browser"


@pytest.mark.asyncio
async def test_background_fast_path_takeover_maps_to_task_cancellation(monkeypatch, tmp_path):
    started = threading.Event()
    release = threading.Event()
    observed_owners = []
    execution_inputs = []
    original_chat_stream = Brain.chat_stream

    monkeypatch.setattr("charlie.fastpaths.match_fast_path", lambda _query: _focus_fast_path_match())

    def execute_fast_path(_match):
        observed_owners.append(resource_locks.current_owner("desktop"))
        started.set()
        release.wait(2.0)
        return FastPathResult("focused")

    monkeypatch.setattr("charlie.fastpaths.execute_fast_path", execute_fast_path)

    async def wrapped_chat_stream(self, user_input, **kwargs):
        if kwargs.get("skip_tools"):
            yield "1. Focus notepad\n2. Must not run\n"
            return
        execution_inputs.append(user_input)
        async for chunk in original_chat_stream(self, user_input, **kwargs):
            yield chunk

    monkeypatch.setattr(Brain, "chat_stream", wrapped_chat_stream)
    task = await background_task.start(
        _config(tmp_path),
        _EventBus(),
        "focus notepad",
        task_id="bg-fastpath",
        session_id="session-bg1",
        turn_id="turn-bg1",
        announce=False,
    )
    try:
        assert await asyncio.to_thread(started.wait, 1.0)
        assert resource_locks.current_owner("desktop") == task.id
        revoked = await asyncio.to_thread(
            resource_locks.default_lease_manager.manual_takeover,
            ("desktop",),
        )
        await asyncio.sleep(0)
        release.set()
        for _ in range(100):
            if task.status == "cancelled":
                break
            await asyncio.sleep(0.01)
    finally:
        release.set()
        if task.status not in {"done", "failed", "cancelled"}:
            background_task.cancel(task.id)
            await asyncio.sleep(0.05)

    assert revoked == {task.id}
    assert observed_owners == [task.id]
    assert task.cancel_requested is True
    assert execution_inputs == ["Focus notepad"]
    assert task.status == "cancelled"
    assert resource_locks.current_owner("desktop") is None

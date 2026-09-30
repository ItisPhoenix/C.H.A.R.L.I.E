from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

import charlie.core as core
import charlie.desktop as desktop_module
from charlie import resource_locks, router
from charlie.autonomy import Requirement, RiskClass, evaluate
from charlie.browser import task as browser_task_module
from charlie.browser.recipes import BrowserResult
from charlie.capabilities import capability_index
from charlie.config import Config
from charlie.desktop import actions
from charlie.desktop import apps as desktop_apps
from charlie.desktop import takeover as takeover_module
from charlie.desktop.takeover import UserTakeoverDetector
from charlie.tools import registry
from charlie.turn_contracts import ResultEnvelope, ResultStatus


def _brain(*, desktop_control_enabled: bool = True, browser_enabled: bool = True) -> core.Brain:
    return core.Brain(
        Config(
            llm_url="http://localhost:11434",
            llm_key="no-key",
            llm_model="dummy",
            desktop_control_enabled=desktop_control_enabled,
            browser_enabled=browser_enabled,
        ),
        register_panic_hotkey=False,
    )


def _envelope(
    *,
    request: str,
    tool_name: str,
    operation: str,
    result: str,
    status: str = ResultStatus.COMPLETED.value,
    task_id: str = "task-desk-1",
    session_id: str = "session-desk-1",
    turn_id: str = "turn-desk-1",
) -> ResultEnvelope:
    return ResultEnvelope(
        request=request,
        task_id=task_id,
        session_id=session_id,
        turn_id=turn_id,
        capability=capability_index.get_operation_domain(tool_name),
        operation=operation,
        status=status,
        result=result,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("utterance", "tool_name", "operation", "expected_key"),
    [
        ("open notepad", "desktop_open_app", "desktop.app.open", "apps"),
        ("close notepad", "desktop_close_app", "desktop.app.close", "apps"),
    ],
)
async def test_deterministic_app_paths_use_canonical_operation(
    monkeypatch, utterance, tool_name, operation, expected_key
):
    brain = _brain()
    calls = []
    events = []

    async def execute(name, arguments, **kwargs):
        calls.append((name, arguments, kwargs))
        return _envelope(
            request=utterance,
            tool_name=name,
            operation=operation,
            result=f"{tool_name} completed",
        )

    monkeypatch.setattr(brain, "execute_tool_operation", execute)
    monkeypatch.setattr(brain.world_model, "record_event", lambda *args: events.append(args))
    try:
        chunks = [
            chunk
            async for chunk in brain.chat_stream(
                utterance,
                platform="text",
                session_id="session-desk-1",
                task_id="task-desk-1",
                turn_id="turn-desk-1",
            )
        ]
    finally:
        await brain.close()

    assert calls and calls[0][0] == tool_name
    assert calls[0][1][expected_key] == ["notepad"]
    assert calls[0][2]["execution_owner_id"] == "turn:turn-desk-1"
    assert calls[0][2]["turn_id"] == "turn-desk-1"
    assert calls[0][2]["task_id"] == "task-desk-1"
    assert chunks == [f"{tool_name} completed"]
    assert events == [
        ("app_open" if tool_name == "desktop_open_app" else "app_close", f"{tool_name} completed")
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("utterance", "tool_name", "operation", "event_type", "status"),
    [
        ("open notepad", "desktop_open_app", "desktop.app.open", "app_open", ResultStatus.COMPLETED.value),
        ("open notepad", "desktop_open_app", "desktop.app.open", "app_open", ResultStatus.CANCELLED.value),
        ("open notepad", "desktop_open_app", "desktop.app.open", "app_open", ResultStatus.FAILED.value),
        ("close notepad", "desktop_close_app", "desktop.app.close", "app_close", ResultStatus.COMPLETED.value),
        ("close notepad", "desktop_close_app", "desktop.app.close", "app_close", ResultStatus.CANCELLED.value),
        ("close notepad", "desktop_close_app", "desktop.app.close", "app_close", ResultStatus.FAILED.value),
    ],
)
async def test_deterministic_app_event_requires_completed_outcome(
    monkeypatch, utterance, tool_name, operation, event_type, status
):
    brain = _brain()
    events = []

    async def execute(name, _arguments, **_kwargs):
        return _envelope(
            request=utterance,
            tool_name=name,
            operation=operation,
            result=f"{status} result",
            status=status,
        )

    monkeypatch.setattr(brain, "execute_tool_operation", execute)
    monkeypatch.setattr(brain.world_model, "record_event", lambda *args: events.append(args))
    try:
        chunks = [chunk async for chunk in brain.chat_stream(utterance, platform="text")]
    finally:
        await brain.close()

    assert chunks == [f"{status} result"]
    expected_events = [(event_type, f"{status} result")] if status == ResultStatus.COMPLETED.value else []
    assert events == expected_events


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("utterance", "tool_name", "event_type"),
    [
        ("open notepad", "desktop_open_app", "app_open"),
        ("close notepad", "desktop_close_app", "app_close"),
    ],
)
async def test_canonical_success_and_deterministic_caller_emit_one_lifecycle_event(
    monkeypatch, utterance, tool_name, event_type
):
    brain = _brain()
    events = []
    monkeypatch.setattr("charlie.tools._desktop_ready", lambda: True)
    if tool_name == "desktop_open_app":
        monkeypatch.setattr(desktop_apps, "launch_apps", lambda *_args: "I've opened Notepad for you.")
    else:
        monkeypatch.setattr(desktop_apps, "close_apps", lambda *_args: "Notepad has been closed for you.")

        async def approve(*_args, **_kwargs):
            return True

        monkeypatch.setattr(brain, "request_tool_approval", approve)
    monkeypatch.setattr(brain.world_model, "record_event", lambda *args: events.append(args))
    try:
        chunks = [chunk async for chunk in brain.chat_stream(utterance, platform="text")]
    finally:
        await brain.close()

    expected_chunks = (
        ["I've opened Notepad for you."]
        if tool_name == "desktop_open_app"
        else ["Notepad has been closed for you."]
    )
    assert chunks == expected_chunks
    assert [event[0] for event in events] == [event_type]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("utterance", "classifier_name", "tool_name", "operation", "event_type"),
    [
        ("fire up spotify", "open_app", "desktop_open_app", "desktop.app.open", "app_open"),
        ("shut spotify", "close_app", "desktop_close_app", "desktop.app.close", "app_close"),
    ],
)
async def test_router_classifier_app_event_is_emitted_once(
    monkeypatch, utterance, classifier_name, tool_name, operation, event_type
):
    brain = _brain()
    brain.config.router_classifier_enabled = True
    events = []

    async def classify(_query):
        return router.RouteMatch(classifier_name, {"app": "spotify"})

    async def execute(name, _arguments, **_kwargs):
        return _envelope(
            request=utterance,
            tool_name=name,
            operation=operation,
            result=f"{name} completed",
        )

    monkeypatch.setattr(brain, "_classify_router_intent", classify)
    monkeypatch.setattr(brain, "execute_tool_operation", execute)
    monkeypatch.setattr(brain.world_model, "record_event", lambda *args: events.append(args))
    try:
        chunks = [chunk async for chunk in brain.chat_stream(utterance, platform="text")]
    finally:
        await brain.close()

    assert chunks == [f"{tool_name} completed"]
    assert events == [(event_type, f"{tool_name} completed")]


@pytest.mark.asyncio
async def test_denied_close_never_reaches_physical_app_action(monkeypatch):
    brain = _brain()
    physical_calls = []

    async def reject(*_args, **_kwargs):
        return False

    monkeypatch.setattr(brain, "request_tool_approval", reject)
    monkeypatch.setattr(
        desktop_apps,
        "close_apps",
        lambda *args, **kwargs: physical_calls.append((args, kwargs)) or "wrong",
    )
    try:
        result = await brain.execute_tool_operation(
            "desktop_close_app",
            {"apps": ["notepad"], "processes": ["notepad.exe"]},
            request="close notepad",
            task_id="task-desk-1",
            session_id="session-desk-1",
            turn_id="turn-desk-1",
            platform="text",
            execution_owner_id="turn:turn-desk-1",
        )
    finally:
        await brain.close()

    assert result.status == ResultStatus.CANCELLED.value
    assert result.risk_class == RiskClass.DESTRUCTIVE.value
    assert result.data["approval_status"] == "rejected"
    assert physical_calls == []


def test_desktop_host_effect_metadata_is_canonical():
    expected = {
        "desktop_open_app": ("desktop", "desktop.app.open", "reversible"),
        "desktop_close_app": ("desktop", "desktop.app.close", "destructive"),
        "desktop_open_url": ("desktop", "desktop.browser.open_url", "reversible"),
    }
    for name, (domain, operation_id, risk) in expected.items():
        operation = capability_index.get_operation(name)
        assert operation is not None
        assert capability_index.get_operation_domain(name) == domain
        assert operation.id == operation_id
        assert operation.risk_class == risk
        assert operation.required_leases == ("desktop",)
        assert operation.name in registry.get_tool_names()


def test_router_matches_apps_but_does_not_own_physical_execution(monkeypatch):
    """The router may interpret intent, never spawn or kill anything.

    Previously asserted that four strings were absent from router.py's source,
    which keeps passing if the router starts a process through a differently
    spelled API. Arm every process entry point to raise, then match: a router
    that stays pure returns its matches; one that reaches for execution fails.
    """
    import os
    import subprocess

    import charlie.router as router

    def forbidden(*_args, **_kwargs):
        raise AssertionError("router must not own physical execution")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "call", forbidden)
    monkeypatch.setattr(subprocess, "check_output", forbidden)
    monkeypatch.setattr(os, "startfile", forbidden, raising=False)
    monkeypatch.setattr(os, "system", forbidden)

    assert router.match_open_app("open notepad") == (["notepad"], ["notepad"], None)
    assert router.match_close_app("close notepad") == (["notepad"], ["notepad.exe"])


@pytest.mark.asyncio
async def test_forced_screen_observation_uses_canonical_operation_and_fresh_context(monkeypatch):
    brain = _brain()
    operation_calls = []
    payloads = []

    async def execute(name, arguments, **kwargs):
        operation_calls.append((name, arguments, kwargs))
        return _envelope(
            request="what's on my screen",
            tool_name=name,
            operation="desktop.screen.observe",
            result="[1] Button \"Save\"",
        )

    async def stream(payload, _generation):
        payloads.append(payload)
        return "The screen has a Save button.", []

    monkeypatch.setattr(brain, "execute_tool_operation", execute)
    monkeypatch.setattr(brain, "_stream_completion", stream)
    try:
        chunks = [
            chunk
            async for chunk in brain.chat_stream(
                "what's on my screen",
                platform="text",
                skip_pre_search=True,
                session_id="session-desk-1",
                task_id="task-desk-1",
                turn_id="turn-desk-1",
            )
        ]
    finally:
        await brain.close()

    assert operation_calls[0][0] == "desktop_observe"
    assert operation_calls[0][2]["execution_owner_id"] == "turn:turn-desk-1"
    assert "Save" in str(payloads[0]["messages"][-1]["content"])
    assert chunks == ["The screen has a Save button."]


@pytest.mark.asyncio
async def test_canonical_screen_observation_holds_desktop_lease(monkeypatch):
    brain = _brain()
    seen_owners = []

    def observe(_name, _arguments):
        seen_owners.append(resource_locks.default_lease_manager.current_owner("desktop"))
        return "fresh screen text"

    monkeypatch.setattr(core, "_get_uia_executor", lambda: None)
    monkeypatch.setattr(core.tool_registry, "execute_tool", observe)
    resource_locks.default_lease_manager.manual_takeover(("desktop",))
    try:
        result = await brain.execute_tool_operation(
            "desktop_observe",
            {},
            request="screen",
            task_id="task-desk-1",
            session_id="session-desk-1",
            turn_id="turn-desk-1",
            execution_owner_id="turn:turn-desk-1",
        )
    finally:
        await brain.close()

    assert result.status == ResultStatus.COMPLETED.value
    assert seen_owners == ["turn:turn-desk-1"]
    assert resource_locks.default_lease_manager.current_owner("desktop") is None


@pytest.mark.asyncio
async def test_physical_input_session_starts_after_lease_and_ends_on_failure(monkeypatch):
    brain = _brain()
    detector = takeover_module.user_takeover_detector
    lifecycle = []

    monkeypatch.setattr(detector, "start_session", lambda owner: lifecycle.append(("start", owner)))
    monkeypatch.setattr(detector, "end_session", lambda: lifecycle.append(("end",)))
    monkeypatch.setattr(core, "_get_uia_executor", lambda: None)
    monkeypatch.setattr(
        core.tool_registry,
        "execute_tool",
        lambda _name, _arguments: (_ for _ in ()).throw(RuntimeError("physical input failed")),
    )
    try:
        result = await brain.execute_tool_operation(
            "desktop_click",
            {"mark_id": 1},
            request="click",
            task_id="task-desk-1",
            session_id="session-desk-1",
            turn_id="turn-desk-1",
            execution_owner_id="turn:turn-desk-1",
        )
    finally:
        await brain.close()

    assert result.status == ResultStatus.FAILED.value
    assert lifecycle == [("start", "turn:turn-desk-1"), ("end",)]


@pytest.mark.asyncio
async def test_physical_input_session_ends_after_cancellation(monkeypatch):
    brain = _brain()
    detector = takeover_module.user_takeover_detector
    lifecycle = []
    started = threading.Event()
    release = threading.Event()

    def block(_name, _arguments):
        started.set()
        release.wait(2.0)
        return "clicked"

    monkeypatch.setattr(detector, "start_session", lambda owner: lifecycle.append(("start", owner)))
    monkeypatch.setattr(detector, "end_session", lambda: lifecycle.append(("end",)))
    monkeypatch.setattr(core, "_get_uia_executor", lambda: None)
    monkeypatch.setattr(core.tool_registry, "execute_tool", block)
    operation = asyncio.create_task(
        brain.execute_tool_operation(
            "desktop_click",
            {"mark_id": 1},
            request="click",
            task_id="task-desk-1",
            session_id="session-desk-1",
            turn_id="turn-desk-1",
            execution_owner_id="turn:turn-desk-1",
        )
    )
    try:
        assert await asyncio.to_thread(started.wait, 1.0)
        operation.cancel()
        release.set()
        with pytest.raises(core.OperationCancelled):
            await operation
    finally:
        release.set()
        await brain.close()

    assert lifecycle == [("start", "turn:turn-desk-1"), ("end",)]


@pytest.mark.asyncio
async def test_browser_interactive_flow_stays_on_charlie_browser(monkeypatch):
    brain = _brain()
    browser_calls = []
    host_calls = []

    async def resolve(*_args, **kwargs):
        browser_calls.append(kwargs)
        return BrowserResult(
            url="https://example.test/result",
            answer="Verified browser result.",
            success=True,
            verification="result-opened",
            evidence={"browser_surface": "playwright", "target_id": "target-desk-1"},
        )

    async def execute(name, arguments, **kwargs):
        host_calls.append((name, arguments, kwargs))
        raise AssertionError("interactive browser tasks must not hand off to desktop_open_url")

    monkeypatch.setattr(browser_task_module, "resolve", resolve)
    monkeypatch.setattr(brain, "execute_tool_operation", execute)
    monkeypatch.setattr(core, "_BROWSER_AVAILABLE", True)
    try:
        outcome = await brain.browser_task(
            "Show it to me.",
            platform="web",
            task_id="task-desk-1",
            session_id="session-desk-1",
            turn_id="turn-desk-1",
            execution_owner_id="turn:turn-desk-1",
            return_envelope=True,
        )
    finally:
        await brain.close()

    assert browser_calls and browser_calls[0]["user_visible"] is True
    assert host_calls == []
    assert outcome.capability == "browser"
    assert outcome.data["browser_evidence"]["browser_surface"] == "playwright"
    assert outcome.data["browser_evidence"]["target_id"] == "target-desk-1"
    assert outcome.result == "Verified browser result."


def test_takeover_revokes_only_canonical_desktop_ownership(monkeypatch):
    detector = UserTakeoverDetector()
    tick = [1000]
    takeover_calls = []
    monkeypatch.setattr(detector, "get_last_input_tick", lambda: tick[0])
    monkeypatch.setattr(
        resource_locks.default_lease_manager,
        "manual_takeover",
        lambda capabilities: takeover_calls.append(tuple(capabilities)) or {"turn:desk"},
    )
    actions.clear_halt()
    try:
        detector.start_session("turn:desk")
        tick[0] = 1350
        assert detector.check_takeover() is True
    finally:
        detector.end_session()
        actions.clear_halt()

    assert takeover_calls == [("desktop",)]
    # Physical input ownership is not a Charlie capability: revoking it must
    # not release a lease the user holds. Assert on the live lease map instead
    # of grepping the module for release("physical_mouse").
    assert "physical_mouse" not in resource_locks.default_lease_manager.snapshot()
    assert "keyboard" not in resource_locks.default_lease_manager.snapshot()
    assert set(resource_locks.default_lease_manager.snapshot()) <= {
        "desktop",
        "browser",
        "telegram",
        "voice",
    }


def test_desktop_close_and_window_close_require_destructive_approval():
    requirement, risk, _ = evaluate("desktop_close_app", {"apps": ["notepad"]})
    assert (requirement, risk) == (Requirement.APPROVE, RiskClass.DESTRUCTIVE)

    requirement, risk, reason = evaluate("desktop_window", {"window": "Notepad", "action": "close"})
    assert (requirement, risk) == (Requirement.APPROVE, RiskClass.DESTRUCTIVE)
    assert "unsaved" in reason

    requirement, risk, _ = evaluate("desktop_observe", {})
    assert (requirement, risk) == (Requirement.ALLOW, RiskClass.SAFE)
    assert capability_index.get_operation_domain("system_control") == "media"


def test_uia_shutdown_is_idempotent_and_rejects_new_work(monkeypatch):
    calls = []

    class Future:
        def result(self):
            return None

    class Executor:
        def submit(self, function):
            calls.append("submit")
            function()
            return Future()

        def shutdown(self, **kwargs):
            calls.append(("shutdown", kwargs))

    executor = Executor()
    monkeypatch.setattr(desktop_module, "DESKTOP_AVAILABLE", True)
    monkeypatch.setattr(desktop_module, "UIA_EXECUTOR", executor)
    monkeypatch.setattr(desktop_module, "_UIA_EXECUTOR_SHUTDOWN", False)
    monkeypatch.setattr(desktop_module, "_uninitialize_com_thread", lambda: calls.append("uninitialize"))

    desktop_module.shutdown_uia_executor()
    desktop_module.shutdown_uia_executor()

    assert calls == ["submit", "uninitialize", ("shutdown", {"wait": True, "cancel_futures": True})]
    with pytest.raises(RuntimeError, match="UIA executor is shut down"):
        desktop_module.get_uia_executor()


def test_main_shutdown_closes_uia_only_after_the_submission_drain():
    """Ordering contract, asserted on the shutdown block itself.

    The old check compared two raw-string offsets across the whole file, so it
    passed on any occurrence of the text anywhere -- including inside the
    startup path -- and inverted silently if the drain moved into a later
    `finally`. This resolves both calls to their real statements inside main's
    teardown block and requires the drain to come first.
    """
    import ast

    source = Path(__file__).resolve().parents[1].joinpath("main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    main_fn = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "main"
    )
    teardown = next(
        node
        for node in ast.walk(main_fn)
        if isinstance(node, ast.Try) and node.finalbody and node.finalbody[0].lineno > 5000
    )

    def first_line(predicate) -> int:
        return min(
            node.lineno
            for node in ast.walk(teardown)
            if isinstance(node, ast.Call) and predicate(node)
        )

    drain_line = first_line(
        lambda call: isinstance(call.func, ast.Name)
        and call.func.id == "_drain_runtime_submissions"
    )
    uia_line = first_line(
        lambda call: isinstance(call.func, ast.Name)
        and call.func.id == "shutdown_uia_executor"
    )

    assert drain_line < uia_line, (
        f"UIA executor is closed at line {uia_line} before the submission drain "
        f"at line {drain_line}: in-flight EventBus work would be abandoned"
    )


@pytest.mark.asyncio
async def test_a_drain_that_never_quiesces_blocks_dependent_store_close(monkeypatch):
    """The ordering above exists because a failed drain must not be silent.

    If drain reported success while work was still running, the UIA executor
    and the SQLite stores would be torn down under live publishers. Verify the
    failure path really is observable: the drain raises, and an unverified
    quiescence refuses to close dependent stores.
    """
    import asyncio

    import main

    registry = main._EventBusSubmissionRegistry()
    loop = asyncio.get_running_loop()
    stubborn_started = asyncio.Event()
    stubborn_release = asyncio.Event()

    async def stubborn():
        # Cancellation is deliberately ineffective here: a publisher that
        # swallows cancel() and keeps publishing is exactly the failure this
        # test exists to catch, so the drain must not be able to stop it.
        # `stubborn_release` is therefore the *only* exit, and the polling
        # sleep keeps that exit reachable within milliseconds.
        stubborn_started.set()
        while not stubborn_release.is_set():
            try:
                await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                continue

    registry.submit_task(stubborn(), loop)
    await asyncio.wait_for(stubborn_started.wait(), 1.0)

    with pytest.raises(RuntimeError, match="did not reach quiescence"):
        await main._drain_event_bus_submissions(registry, loop=loop, timeout=0.05)

    closed = []

    class Store:
        def close(self):
            closed.append("closed")

    main._close_runtime_stores(None, Store(), quiescent=False)
    assert closed == [], "stores were closed despite unverified quiescence"

    # The task must be gone before the test returns. Leaving it pending hangs
    # the whole suite: pytest-asyncio's loop teardown cancels all pending tasks
    # and then waits on them, and a task that swallows cancellation never
    # settles, so the loop parks in select() forever.
    stubborn_release.set()
    pending = [task for task in registry.snapshot()[0] if not task.done()]
    if pending:
        _, still_pending = await asyncio.wait(pending, timeout=5.0)
        assert not still_pending, (
            f"stubborn publisher did not terminate: {still_pending}. It would "
            "block event-loop teardown and hang the entire pytest run."
        )


def test_web_process_does_not_create_uia_executor_at_import(tmp_path):
    """Importing the web-process modules must not build the UIA executor.

    A fresh interpreter is the only honest probe: an in-process import would
    merely re-read the module-level global this suite already populated. The
    child is bounded and always killed, so a regression here can never wedge
    the suite the way an unbounded ``run`` would.
    """
    script = (
        "import charlie.tools; import charlie.capabilities; "
        "import charlie.desktop as d; assert d.UIA_EXECUTOR is None"
    )
    child_env = dict(os.environ)
    # Keep the probe off production state: no .env overlay, and any log the
    # child emits lands in the test's temp dir rather than the repo. The script
    # imports modules only and never calls main(), so the single-instance lock
    # is never acquired; the assertion below proves the child never reached it.
    child_env["CHARLIE_TEST_MODE"] = "true"
    child_env["CHARLIE_LOG_FILE"] = str(tmp_path / "child.log")

    child = subprocess.Popen(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env=child_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        stdout, stderr = child.communicate(timeout=60)
    except subprocess.TimeoutExpired:
        child.kill()
        child.communicate()
        pytest.fail(f"import probe hung and was killed (pid {child.pid})")
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate()

    assert child.returncode == 0, f"stdout={stdout!r}\nstderr={stderr!r}"
    assert "charlie.lock" not in stderr, (
        f"child engaged the single-instance lock, which is outside this test's "
        f"intent: {stderr}"
    )

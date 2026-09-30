"""Focused approval-send and background TaskJournal regression coverage."""

import ast
import asyncio
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

import main
from charlie.autonomy import Requirement, RiskClass
from charlie.config import Config
from charlie.core import (
    ApprovalDecision,
    Brain,
    _approval_operation_preview,
    get_active_tool_approval,
    pending_tool_approvals,
    resolve_tool_approval,
)
from charlie.task_journal import TaskJournal, TaskOrigin, TaskStatus


def test_approval_preview_names_file_target_without_exposing_contents():
    preview = _approval_operation_preview(
        "file_write",
        {"path": r"C:\Users\Charlie\Downloads\summary.md", "content": "private draft"},
    )

    assert "summary.md" in preview
    assert "Write to" in preview
    assert "private draft" not in preview


def test_telegram_approval_reason_uses_the_policy_reason():
    assert main._telegram_approval_reason(
        "file_write", RiskClass.SECURITY_SENSITIVE,
        "overwrite of existing file 'summary.md' requires approval",
    ) == "Overwrite of existing file 'summary.md' requires approval."


def _telegram_startup_block():
    """main()'s real `if config.telegram_enabled:` startup block, as source.

    The block lives inline inside main(), so there is no importable symbol to
    call. Extracting and executing it (the same harness this module already uses
    for the approval callbacks) still runs main.py's own code: a stubbed
    TelegramBot whose start() raises must leave the runtime with no bot
    reference, no live poller, and telegram projected DEGRADED.
    """
    source = Path(main.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    main_node = next(
        node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "main"
    )
    block = next(
        node
        for node in ast.walk(main_node)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Attribute)
        and node.test.attr == "telegram_enabled"
    )
    return textwrap.dedent(ast.get_source_segment(source, block))


class _FailingStartBot:
    """TelegramBot stand-in whose startup fails, as it does when polling dies."""

    def __init__(self, *_args, **_kwargs):
        self.started = False
        self.stop_calls = 0

    async def start(self):
        self.started = True
        raise RuntimeError("telegram poller failed to start")

    async def stop(self):
        self.stop_calls += 1


@pytest.mark.asyncio
async def test_telegram_startup_failure_clears_dead_bot_and_degrades(monkeypatch):
    from charlie import telegram_bot as telegram_bot_module

    bot = _FailingStartBot()
    health = []
    monkeypatch.setattr(telegram_bot_module, "TelegramBot", lambda *_a, **_k: bot)
    monkeypatch.setattr(
        main,
        "_set_subsystem_health",
        lambda name, status, detail=None: health.append((name, status, detail)),
    )

    namespace = vars(main).copy()
    namespace.update(
        {
            "config": SimpleNamespace(
                telegram_enabled=True,
                telegram_bot_token="token",
                telegram_user_id=123,
            ),
            "brain": None,
            "self_extension_orchestrator": None,
            "logger": _NullLogger(),
        }
    )
    wrapper = (
        "async def _run():\n" + textwrap.indent(_telegram_startup_block(), "    ") + "\n    return telegram_bot\n"
    )
    exec(compile(wrapper, "<main.telegram_startup>", "exec"), namespace)
    remaining_bot = await namespace["_run"]()

    assert bot.started is True
    # The dead reference must not survive: later code (shutdown, status
    # updates, approval relay) dereferences telegram_bot without a None check.
    assert remaining_bot is None
    assert bot.stop_calls == 1, "failed startup must release the bot's poller"
    assert health == [("telegram", main.HealthStatus.DEGRADED, None)]


@pytest.mark.asyncio
async def test_gated_call_declines_rather_than_hangs_after_telegram_startup_failed(tmp_path):
    """With no live bot, an approval-gated call must settle -- not park forever.

    A dead bot reference left in place makes on_tool_approval_request send
    through a client that no longer receives updates, so the approval future is
    never resolved and a foreground turn waits for its full timeout.
    """
    journal = _background_task_journal(tmp_path)
    approval_callback, _ = _main_approval_callbacks(None, journal)
    brain = _brain(approval_callback)
    try:
        decision = await asyncio.wait_for(
            brain._request_tool_approval_decision(
                "shell_execute",
                {"command": "taskkill /?"},
                "approved shell command",
                platform="telegram",
                task_id="bg-approval-test",
            ),
            timeout=1,
        )
        assert decision is ApprovalDecision.UNAVAILABLE
    finally:
        await brain.close()


class _NullLogger:
    def info(self, *_args, **_kwargs):
        pass

    warning = error = debug = exception = info


class _ApprovalBot:
    def __init__(self, *, error=None):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.error = error
        self.request_id = None
        self.sent_request_ids = []

    async def send_approval_request(
        self, _chat_id, request_id, _tool_name, _reason, operation_preview=None
    ):
        self.request_id = request_id
        self.sent_request_ids.append(request_id)
        self.started.set()
        await self.release.wait()
        if self.error is not None:
            raise self.error


def _main_approval_callbacks(bot, journal):
    source = Path(main.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    main_node = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "main")
    wanted = {
        "_resolve_tool_approval_and_notify",
        "on_tool_approval_request",
        "on_telegram_approval",
    }
    functions = {
        node.name: ast.get_source_segment(source, node)
        for node in ast.walk(main_node)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in wanted
    }
    assert set(functions) == wanted

    async def no_status_update(*_args, **_kwargs):
        return None

    namespace = vars(main).copy()
    namespace.update(
        {
            "telegram_bot": bot,
            "config": SimpleNamespace(telegram_user_id=123),
            "event_bus": None,
            "get_task_journal": lambda: journal,
            "telegram_approval_turn_by_request": {},
            "telegram_approval_expiry_tasks": {},
            "telegram_background_task_ids": set(),
            "_set_telegram_turn_status": no_status_update,
            "_schedule_telegram_status_update": lambda *_args, **_kwargs: None,
            "_telegram_approval_reason": main._telegram_approval_reason,
        }
    )
    for name in (
        "_resolve_tool_approval_and_notify",
        "on_tool_approval_request",
        "on_telegram_approval",
    ):
        snippet = textwrap.dedent(functions[name])
        exec(compile(snippet, f"<main.{name}>", "exec"), namespace)
    return namespace["on_tool_approval_request"], namespace["on_telegram_approval"]


def _background_task_journal(tmp_path):
    journal = TaskJournal(state_path=tmp_path / "tasks.json")
    journal.create_task(
        "run one approved operation",
        task_id="bg-approval-test",
        origin=TaskOrigin.BACKGROUND,
        status=TaskStatus.RUNNING,
    )
    return journal


def _brain(approval_callback):
    return Brain(
        Config(llm_url="http://localhost:11434/v1", llm_key="test-key", llm_model="dummy"),
        on_tool_approval_request=approval_callback,
        approval_timeout=None,
        is_background=True,
    )


@pytest.mark.asyncio
async def test_background_telegram_approval_waits_for_send_and_matches_request(tmp_path):
    journal = _background_task_journal(tmp_path)
    bot = _ApprovalBot()
    approval_callback, telegram_callback = _main_approval_callbacks(bot, journal)
    brain = _brain(approval_callback)
    try:
        decision_task = asyncio.create_task(
            brain._request_tool_approval_decision(
                "shell_execute",
                {"command": "taskkill /?"},
                "approved shell command",
                platform="telegram",
                task_id="bg-approval-test",
            )
        )
        await asyncio.wait_for(bot.started.wait(), timeout=1)
        request_id = bot.request_id
        pending = journal.get("bg-approval-test")
        assert pending.status is TaskStatus.APPROVAL_REQUIRED
        assert pending.approval_reference == request_id
        assert not decision_task.done()

        assert resolve_tool_approval(request_id, True, expected_platform="voice") is False
        assert telegram_callback("stale-request", True) is False
        assert telegram_callback(request_id, True) is True
        resumed = journal.get("bg-approval-test")
        assert resumed.status is TaskStatus.RUNNING
        assert resumed.approval_reference == ""
        assert not decision_task.done()

        bot.release.set()
        assert await asyncio.wait_for(decision_task, timeout=1) is ApprovalDecision.APPROVED
    finally:
        bot.release.set()
        await brain.close()


@pytest.mark.asyncio
async def test_stale_background_callback_fails_journaled_task_closed(tmp_path, monkeypatch):
    from charlie import core as core_module

    journal = _background_task_journal(tmp_path)
    bot = _ApprovalBot()
    approval_callback, telegram_callback = _main_approval_callbacks(bot, journal)
    brain = _brain(approval_callback)
    try:
        decision_task = asyncio.create_task(
            brain._request_tool_approval_decision(
                "shell_execute",
                {"command": "taskkill /?"},
                "approved shell command",
                platform="telegram",
                task_id="bg-approval-test",
            )
        )
        await asyncio.wait_for(bot.started.wait(), timeout=1)
        request_id = bot.request_id
        monkeypatch.setattr(core_module, "resolve_tool_approval", lambda *_args, **_kwargs: False)

        assert telegram_callback(request_id, True) is False
        record = journal.get("bg-approval-test")
        assert record.status is TaskStatus.FAILED
        assert record.approval_reference == ""
        assert "stale" in record.error_summary.casefold()

        monkeypatch.undo()
        assert resolve_tool_approval(request_id, False, expected_platform="telegram") is True
        bot.release.set()
        assert await asyncio.wait_for(decision_task, timeout=1) is ApprovalDecision.REJECTED
    finally:
        bot.release.set()
        await brain.close()


@pytest.mark.asyncio
async def test_background_telegram_send_failure_is_unavailable_and_clears_pending_state(tmp_path):
    journal = _background_task_journal(tmp_path)
    bot = _ApprovalBot(error=RuntimeError("test send failure"))
    bot.release.set()
    approval_callback, _ = _main_approval_callbacks(bot, journal)
    brain = _brain(approval_callback)
    try:
        decision = await asyncio.wait_for(
            brain._request_tool_approval_decision(
                "shell_execute",
                {"command": "taskkill /?"},
                "approved shell command",
                platform="telegram",
                task_id="bg-approval-test",
            ),
            timeout=1,
        )
        assert decision is ApprovalDecision.UNAVAILABLE
        record = journal.get("bg-approval-test")
        assert record.status is TaskStatus.RUNNING
        assert record.approval_reference == ""
        assert bot.request_id not in pending_tool_approvals
    finally:
        await brain.close()


@pytest.mark.asyncio
async def test_second_background_approval_is_unavailable_without_replacing_active_request(tmp_path):
    journal = _background_task_journal(tmp_path)
    bot = _ApprovalBot()
    approval_callback, telegram_callback = _main_approval_callbacks(bot, journal)
    brain = _brain(approval_callback)
    try:
        first = asyncio.create_task(
            brain._request_tool_approval_decision(
                "shell_execute",
                {"command": "taskkill /?"},
                "first operation",
                platform="telegram",
                task_id="bg-approval-test",
            )
        )
        await asyncio.wait_for(bot.started.wait(), timeout=1)
        first_id = bot.request_id

        second = await asyncio.wait_for(
            brain._request_tool_approval_decision(
                "shell_execute",
                {"command": "taskkill /IM example.exe /F"},
                "second operation",
                platform="telegram",
                task_id="bg-approval-test",
            ),
            timeout=1,
        )
        assert second is ApprovalDecision.UNAVAILABLE
        assert get_active_tool_approval() == (first_id, "telegram")
        assert bot.sent_request_ids == [first_id]
        assert telegram_callback(first_id, True) is True
        bot.release.set()
        assert await asyncio.wait_for(first, timeout=1) is ApprovalDecision.APPROVED
    finally:
        bot.release.set()
        await brain.close()


@pytest.mark.asyncio
async def test_telegram_callback_confirms_delivery_when_send_response_times_out(tmp_path):
    journal = _background_task_journal(tmp_path)
    bot = _ApprovalBot(error=asyncio.TimeoutError())
    approval_callback, telegram_callback = _main_approval_callbacks(bot, journal)
    brain = _brain(approval_callback)
    try:
        decision_task = asyncio.create_task(
            brain._request_tool_approval_decision(
                "shell_execute",
                {"command": "taskkill /?"},
                "approved shell command",
                platform="telegram",
                task_id="bg-approval-test",
            )
        )
        await asyncio.wait_for(bot.started.wait(), timeout=1)
        request_id = bot.request_id
        assert telegram_callback(request_id, True) is True
        assert journal.get("bg-approval-test").status is TaskStatus.RUNNING

        bot.release.set()
        assert await asyncio.wait_for(decision_task, timeout=1) is ApprovalDecision.APPROVED
        record = journal.get("bg-approval-test")
        assert record.status is TaskStatus.RUNNING
        assert record.approval_reference == ""
        assert get_active_tool_approval() is None
        assert request_id not in pending_tool_approvals
    finally:
        bot.release.set()
        await brain.close()


@pytest.mark.asyncio
async def test_fast_path_reports_unavailable_instead_of_claiming_decline(monkeypatch):
    brain = _brain(None)
    executed = []

    async def unavailable(*_args, **_kwargs):
        return ApprovalDecision.UNAVAILABLE

    monkeypatch.setattr(brain, "_request_tool_approval_decision", unavailable)
    monkeypatch.setattr(
        "charlie.core.autonomy_evaluate",
        lambda *_args, **_kwargs: (Requirement.APPROVE, RiskClass.SECURITY_SENSITIVE, "owner approval required"),
    )
    monkeypatch.setattr(
        "charlie.fastpaths.execute_fast_path",
        lambda _match: executed.append(True) or "unexpected execution",
    )
    try:
        result = "".join([chunk async for chunk in brain.chat_stream("what is the cpu usage?", platform="telegram")])
    finally:
        await brain.close()

    assert "approval channel unavailable" in result.lower()
    assert "declined" not in result.lower()
    assert executed == []

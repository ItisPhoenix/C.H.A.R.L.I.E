"""The self-extension approval must be raised on the channel that can answer it.

``main()``'s self-extension owner decision hardcoded ``platform="voice"``.  Only
the Telegram branch of ``on_tool_approval_request`` emits an Approve/Decline
keyboard, and the Telegram resolver only accepts ``expected_platform="telegram"``,
so on a Telegram-only or headless deployment the prompt was unanswerable.  It
also parked the single global approval slot (every other gated tool call on that
Brain is declined ``UNAVAILABLE`` while it is held).

The contract asserted here:

* the real turn platform is threaded into the approval request;
* when the platform is not knowable, the approval fails closed *fast* -- it is
  never parked, so the slot is not occupied;
* when the platform is knowable, it is the turn's own channel.

Evidence class: TEST/MOCK.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path
from typing import Optional

import pytest

import main
from charlie.self_extension.approval_bridge import ApprovalChannelRegistry


def _self_extension_owner_decision_source() -> str:
    source = Path(main.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    main_node = next(
        node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "main"
    )
    node = next(
        node
        for node in ast.walk(main_node)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_self_extension_owner_decision"
    )
    return textwrap.dedent(ast.get_source_segment(source, node))


def _call_tree() -> ast.AST:
    return ast.parse(_self_extension_owner_decision_source())


class TestMainDoesNotHardcodeTheChannel:
    def test_the_approval_request_has_no_literal_voice_channel(self):
        for node in ast.walk(_call_tree()):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg == "platform":
                    assert not (
                        isinstance(keyword.value, ast.Constant)
                        and keyword.value.value == "voice"
                    ), (
                        "main() must thread the real turn platform into the "
                        "self-extension approval, not the literal 'voice'"
                    )

    def test_the_source_resolves_a_channel_before_asking(self):
        source = _self_extension_owner_decision_source()

        assert "resolve" in source, (
            "the decision must resolve the answerable channel before prompting"
        )


class TestChannelResolution:
    def test_no_turn_is_unknowable_and_fails_closed(self):
        registry = ApprovalChannelRegistry()

        assert registry.resolve() is None

    def test_a_single_voice_turn_resolves_to_voice(self):
        registry = ApprovalChannelRegistry()
        registry.begin("t1", "voice")

        assert registry.resolve() == "voice"

    def test_a_single_telegram_turn_resolves_to_telegram(self):
        registry = ApprovalChannelRegistry()
        registry.begin("t1", "telegram")

        assert registry.resolve() == "telegram"

    def test_a_finished_turn_releases_its_channel(self):
        registry = ApprovalChannelRegistry()
        registry.begin("t1", "telegram")
        registry.end("t1")

        assert registry.resolve() is None

    def test_two_turns_on_the_same_channel_are_unambiguous(self):
        registry = ApprovalChannelRegistry()
        registry.begin("t1", "web")
        registry.begin("t2", "web")

        assert registry.resolve() == "web"

    def test_two_turns_on_different_channels_are_ambiguous_and_fail_closed(self):
        registry = ApprovalChannelRegistry()
        registry.begin("t1", "voice")
        registry.begin("t2", "telegram")

        assert registry.resolve() is None

    def test_an_unknown_or_blank_channel_never_resolves(self):
        registry = ApprovalChannelRegistry()
        registry.begin("t1", "")
        assert registry.resolve() is None
        registry.end("t1")
        registry.begin("t2", None)
        assert registry.resolve() is None

    def test_ending_an_unknown_turn_is_a_no_op(self):
        registry = ApprovalChannelRegistry()
        registry.end("never-started")

        assert registry.resolve() is None


class _RecordingBrain:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def _request_tool_approval_decision(
        self, tool_name, arguments, reason, platform="voice", risk_class=None, **kwargs
    ):
        self.calls.append(
            {"tool_name": tool_name, "arguments": arguments, "reason": reason, "platform": platform}
        )

        class _Decision:
            value = "approved"

        return _Decision()


def _load_decision(channel_registry, brain):
    import logging

    from charlie.core import ApprovalDecision
    from charlie.self_extension.approval_bridge import sanitize_approval_reason

    namespace = {
        "Any": object,
        "Optional": Optional,
        "logger": logging.getLogger("test.main.self_extension"),
        "_ApprovalDecision": ApprovalDecision,
        "brain": brain,
        "approval_channels": channel_registry,
        "sanitize_approval_reason": sanitize_approval_reason,
    }
    exec(
        compile(_self_extension_owner_decision_source(), "<main._self_extension_owner_decision>", "exec"),
        namespace,
    )
    return namespace["_self_extension_owner_decision"]


PAYLOAD = {
    "approval_binding": "digest-1",
    "argv": ["npx", "-y", "@weather/mcp"],
    "reason": "MCP server integration launches a new external process.",
}


class TestTheOwnerDecisionUsesTheRealTurnChannel:
    @pytest.mark.asyncio
    async def test_a_telegram_turn_is_prompted_on_telegram(self):
        registry = ApprovalChannelRegistry()
        registry.begin("t1", "telegram")
        brain = _RecordingBrain()

        granted = await _load_decision(registry, brain)(dict(PAYLOAD))

        assert granted == "digest-1"
        assert [call["platform"] for call in brain.calls] == ["telegram"]

    @pytest.mark.asyncio
    async def test_a_console_turn_is_prompted_on_console(self):
        registry = ApprovalChannelRegistry()
        registry.begin("t1", "console")
        brain = _RecordingBrain()

        await _load_decision(registry, brain)(dict(PAYLOAD))

        assert [call["platform"] for call in brain.calls] == ["console"]

    @pytest.mark.asyncio
    async def test_an_unknown_channel_never_parks_an_approval(self):
        """Fail closed FAST: refuse without occupying the single approval slot."""
        registry = ApprovalChannelRegistry()
        brain = _RecordingBrain()

        granted = await _load_decision(registry, brain)(dict(PAYLOAD))

        assert granted is None
        assert brain.calls == [], (
            "the owner must not be prompted at all when the channel is unknowable; "
            "parking it would block every other gated tool call"
        )

    @pytest.mark.asyncio
    async def test_an_ambiguous_channel_never_parks_an_approval(self):
        registry = ApprovalChannelRegistry()
        registry.begin("t1", "voice")
        registry.begin("t2", "telegram")
        brain = _RecordingBrain()

        granted = await _load_decision(registry, brain)(dict(PAYLOAD))

        assert granted is None
        assert brain.calls == []

    @pytest.mark.asyncio
    async def test_a_missing_binding_or_argv_is_refused(self):
        registry = ApprovalChannelRegistry()
        registry.begin("t1", "telegram")
        brain = _RecordingBrain()

        assert await _load_decision(registry, brain)({"argv": ["npx"]}) is None
        assert await _load_decision(registry, brain)({"approval_binding": "d"}) is None
        assert brain.calls == []

    @pytest.mark.asyncio
    async def test_an_owner_refusal_returns_none(self):
        from charlie.core import ApprovalDecision

        registry = ApprovalChannelRegistry()
        registry.begin("t1", "telegram")

        class _Declining(_RecordingBrain):
            async def _request_tool_approval_decision(self, *a, **k):
                await super()._request_tool_approval_decision(*a, **k)

                class _D:
                    value = ApprovalDecision.REJECTED.value

                return _D()

        granted = await _load_decision(registry, _Declining())(dict(PAYLOAD))
        assert granted is None

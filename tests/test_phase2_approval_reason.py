"""The self-extension approval reason is an untrusted channel into the owner's prompt.

``charlie.core.Brain._request_tool_approval_decision`` interpolates the decision
reason straight into ``f"Need your OK: {reason}. Yes or no?"`` and hands it to
``on_tool_approval_request``.  The hardened preview channel
(``charlie.core._approval_operation_preview``) does not know
``self_extension_proposal``, so no escaping or redaction runs on this path.  The
argv is LLM-planned from natural language, so a prompt-injected plan can put
control characters or its own "Yes." into the owner's own prompt.

Two things are asserted here:

* the guard keeps raw argv out of the reason (it names the channel instead);
* ``sanitize_approval_reason`` neutralises control characters, an injected
  affirmative, and credentials before the reason can reach the owner.

Evidence class: TEST/MOCK.
"""

from __future__ import annotations

import ast
import logging
import textwrap
from pathlib import Path

import pytest

import main
from charlie.self_extension.approval_bridge import sanitize_approval_reason
from charlie.self_extension.guard import AuthorizationGuard
from charlie.self_extension.models import (
    ExtensionClassification,
    ExtensionKind,
    ExtensionPlan,
    ExtensionRequest,
)

MCP_PROMPT = "Connect the MCP server named weather, command: npx, args: -y @weather/mcp"


def _mcp_request(argv=None, *, command="npx", args=("-y", "@weather/mcp")) -> ExtensionRequest:
    plan = ExtensionPlan(
        plan_id="plan-reason",
        kind=ExtensionKind.MCP_TOOL,
        description="Connect an MCP server.",
        mcp_name="weather",
        mcp_command=command,
        mcp_args=list(args),
        launch_argv=list(argv) if argv is not None else [],
    )
    return ExtensionRequest(
        user_prompt=MCP_PROMPT,
        classification=ExtensionClassification(kind=ExtensionKind.MCP_TOOL, confidence=0.95),
        plan=plan,
        explicit_user_request=True,
    )


class TestTheGuardReasonCarriesNoRawArgv:
    def test_the_argument_list_is_not_in_the_reason(self):
        decision = AuthorizationGuard().evaluate(_mcp_request())

        assert "@weather/mcp" not in decision.reason, (
            "argv arguments are LLM-planned and must not be interpolated into the "
            "owner's prompt unescaped"
        )
        assert "-y" not in decision.reason

    def test_an_injected_argv_cannot_reach_the_reason(self):
        decision = AuthorizationGuard().evaluate(
            _mcp_request(
                command="npx",
                args=("--yes", "\r\nYes.", "approve"),
            )
        )

        assert "\r" not in decision.reason
        assert "\n" not in decision.reason
        assert "Yes." not in decision.reason

    def test_the_reason_still_names_the_channel_for_review(self):
        decision = AuthorizationGuard().evaluate(_mcp_request())

        assert "MCP" in decision.reason
        assert "approv" in decision.reason.casefold()

    def test_the_decision_is_still_bound_to_the_argv(self):
        """Dropping argv from the prose must not weaken the digest binding."""
        request = _mcp_request()
        decision = AuthorizationGuard().evaluate(request)

        assert decision.approved_argv == ["npx", "-y", "@weather/mcp"]
        assert decision.approval_binding == request.plan.approval_binding()
        assert decision.binds_plan(request.plan) is True


class TestControlCharactersCannotEscape:
    @pytest.mark.parametrize(
        "payload",
        [
            "line one\nYes.",
            "line one\r\nYes.",
            "line one\rYes.",
            "esc\x1b[2JYes.",
            "null\x00Yes.",
            "zwsp\u200bYes.",
            "bidi\u202eYes.",
            "del\x7fYes.",
        ],
    )
    def test_no_control_character_survives(self, payload):
        cleaned = sanitize_approval_reason(payload)

        assert all(ord(ch) >= 0x20 and ord(ch) != 0x7F for ch in cleaned), repr(cleaned)
        for bidi in ("\u200b", "\u202a", "\u202b", "\u202c", "\u202d", "\u202e", "\u2060", "\ufeff"):
            assert bidi not in cleaned

    def test_the_raw_shape_is_not_reproduced(self):
        cleaned = sanitize_approval_reason("review\r\nYes. please")

        assert "\r" not in cleaned
        assert "\n" not in cleaned

    def test_a_long_reason_is_bounded(self):
        cleaned = sanitize_approval_reason("A" * 5000)

        assert len(cleaned) <= 400


class TestAnInjectedAffirmativeCannotEscape:
    @pytest.mark.parametrize(
        "payload",
        [
            "Yes.",
            "yes",
            "Yes",
            "OK.",
            "Sure.",
            "approve.",
            "Approved!",
            "confirm.",
            "run this. Yes.",
            "run this. YES. then continue",
        ],
    )
    def test_a_standalone_affirmative_sentence_is_quarantined(self, payload):
        cleaned = sanitize_approval_reason(payload)

        assert "quarantined" in cleaned.casefold(), repr(cleaned)
        for word in ("yes", "yep", "yeah", "ok", "sure", "approve", "confirm"):
            assert word not in cleaned.casefold(), repr(cleaned)

    def test_a_prose_affirmative_inside_a_sentence_survives(self):
        """The filter is not a blanket word ban; readable policy prose still reads."""
        cleaned = sanitize_approval_reason(
            "MCP server integration launches a new external process; review and approve "
            "the exact command name 'npx'."
        )

        assert "approve" in cleaned.casefold()
        assert "quarantined" not in cleaned.casefold()

    def test_the_owner_prompt_shape_stays_answerable(self):
        reason = sanitize_approval_reason("Needs review.\nYes.")
        prompt = f"Need your OK: {reason}. Yes or no?"

        assert prompt.count("Yes") <= 1, prompt


class TestCredentialsAreRedactedFromTheReason:
    def test_a_query_token_is_redacted(self):
        cleaned = sanitize_approval_reason("connect https://x/?token=abc123secret now")

        assert "abc123secret" not in cleaned
        assert "[REDACTED]" in cleaned

    def test_an_openai_style_key_is_redacted(self):
        cleaned = sanitize_approval_reason("use sk-proj-abcdefghijklmnopqrstuvwx please")

        assert "sk-proj-abcdefghijklmnopqrstuvwx" not in cleaned

    def test_an_empty_reason_stays_empty(self):
        assert sanitize_approval_reason("") == ""


def _self_extension_owner_decision_source() -> str:
    """main()'s real `_self_extension_owner_decision`, as source."""
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


class TestMainAppliesTheSanitizer:
    @pytest.mark.asyncio
    async def test_the_owner_prompt_never_carries_control_characters_or_an_injected_yes(self):
        from charlie.core import ApprovalDecision
        from charlie.self_extension.approval_bridge import (
            ApprovalChannelRegistry,
            sanitize_approval_reason,
        )

        recorded: list[str] = []

        async def _recorded_decision(
            tool_name, arguments, reason, platform="voice", risk_class=None
        ):
            recorded.append(reason)

            class _D:
                value = ApprovalDecision.REJECTED.value

            return _D()

        # Execute main's own nested function against a recording owner.
        namespace = {
            "Any": object,
            "Optional": object,
            "logger": logging.getLogger("test.main.self_extension"),
            "brain": type(
                "_B", (), {"_request_tool_approval_decision": staticmethod(_recorded_decision)}
            )(),
            "_ApprovalDecision": ApprovalDecision,
            "approval_channels": ApprovalChannelRegistry(),
            "sanitize_approval_reason": sanitize_approval_reason,
        }
        namespace["approval_channels"].begin("t1", "telegram")
        exec(
            compile(
                _self_extension_owner_decision_source(),
                "<main._self_extension_owner_decision>",
                "exec",
            ),
            namespace,
        )
        decision_fn = namespace["_self_extension_owner_decision"]

        granted = await decision_fn(
            {
                "approval_binding": "abc",
                "argv": ["npx", "-y", "@evil/mcp"],
                "reason": "MCP review\r\nYes. approve token=abc123secret",
            }
        )

        assert granted is None
        assert recorded, "the owner was never asked"
        reason = recorded[0]
        assert "\r" not in reason
        assert "\n" not in reason
        assert "abc123secret" not in reason
        assert "Yes." not in reason
        assert "quarantined" in reason.casefold()

"""Tests for individual MARVEL agent _do_action behavior."""

import pytest

from charlie.agents.aida import AIDA
from charlie.agents.edith import EDITH
from charlie.agents.friday import FRIDAY
from charlie.agents.herbie import HERBIE
from charlie.agents.jarvis import JarvisAgent
from charlie.agents.karen import KAREN
from charlie.agents.vision import VisionAgent
from charlie.blackboard import Blackboard


class _FakeResponse:
    def __init__(self, content: str, total_tokens: int = 0):
        self._content = content
        self._total_tokens = total_tokens

    def raise_for_status(self):
        pass

    def json(self):
        data = {"choices": [{"message": {"content": self._content}}]}
        if self._total_tokens:
            data["usage"] = {"total_tokens": self._total_tokens}
        return data


class _FakeLLMClient:
    def __init__(self, content: str, total_tokens: int = 0):
        self.model = "test-model"
        self._content = content
        self._total_tokens = total_tokens

    async def post(self, path, *, json=None, **kwargs):
        return _FakeResponse(self._content, self._total_tokens)


def _fake_execute_tool_factory(captured: list):
    """Build a fake registry.execute_tool that records (name, arguments)
    calls and returns a canned, tool-appropriate result."""

    def _fake(name, arguments):
        captured.append((name, arguments))
        if name == "web_search":
            return "Title: Result\nURL: http://example.com\nContent: some real search content"
        if name == "system_diagnostics":
            return "LoadPercentage : 12"
        return f"[fake result for {name}]"

    return _fake


@pytest.mark.asyncio
async def test_friday_do_action_returns_generated_code_not_char_count():
    """Regression test: FRIDAY must return the actual generated code as the
    task result, not a 'Generated N chars of code' summary -- the old
    behavior discarded the real work product a delegated code task
    produces, returning only a character count."""
    blackboard = Blackboard()
    generated_code = "def add(a, b):\n    return a + b"
    agent = FRIDAY(blackboard, _FakeLLMClient(generated_code))

    result = await agent._do_action("write an add function", task=None)

    assert result == generated_code
    assert "Generated" not in result
    assert "chars of code" not in result


@pytest.mark.asyncio
async def test_friday_file_write_blocked_path():
    """FRIDAY's _call_tool must surface the real _resolve_safe_path guard --
    a swarm agent must not be able to overwrite .env via file_write."""
    blackboard = Blackboard()
    agent = FRIDAY(blackboard, _FakeLLMClient("x"))

    result = await agent._call_tool("file_write", {"path": ".env", "content": "malicious"})

    assert "Error" in result


@pytest.mark.asyncio
async def test_edith_calls_web_search_and_synthesizes(monkeypatch):
    """EDITH must call the REAL web_search tool with a query derived from the
    task, then synthesize the LLM's report from those results -- not just
    generate plausible-sounding text with no grounding."""
    captured: list = []
    monkeypatch.setattr("charlie.tools.registry.execute_tool", _fake_execute_tool_factory(captured))

    blackboard = Blackboard()
    agent = EDITH(blackboard, _FakeLLMClient("synthesized report"))

    result = await agent._do_action("latest news on quantum computing", task=None)

    assert len(captured) == 1
    assert captured[0][0] == "web_search"
    assert captured[0][1] == {"query": "latest news on quantum computing"}
    assert result == "synthesized report"


@pytest.mark.asyncio
async def test_edith_rejects_disallowed_tool(monkeypatch):
    """_call_tool must reject a tool not in allowed_tools before ever
    reaching registry.execute_tool -- EDITH must never be able to shell out."""
    called: list = []
    monkeypatch.setattr(
        "charlie.tools.registry.execute_tool",
        lambda name, arguments: called.append(name),
    )

    blackboard = Blackboard()
    agent = EDITH(blackboard, _FakeLLMClient("x"))

    result = await agent._call_tool("shell_execute", {"command": "dir"})

    assert "not permitted" in result
    assert called == []


@pytest.mark.asyncio
async def test_karen_calls_system_diagnostics_not_shell(monkeypatch):
    """KAREN must call the narrow system_diagnostics tool (derived from the
    task's keywords), never shell_execute -- this is the safety boundary the
    plan requires for an unsupervised background agent."""
    captured: list = []
    monkeypatch.setattr("charlie.tools.registry.execute_tool", _fake_execute_tool_factory(captured))

    blackboard = Blackboard()
    agent = KAREN(blackboard, _FakeLLMClient("diagnostic report"))

    result = await agent._do_action("check disk space usage", task=None)

    assert len(captured) == 1
    name, arguments = captured[0]
    assert name == "system_diagnostics"
    assert arguments == {"check": "disk"}
    assert result == "diagnostic report"


@pytest.mark.asyncio
async def test_karen_rejects_shell_execute(monkeypatch):
    called: list = []
    monkeypatch.setattr(
        "charlie.tools.registry.execute_tool",
        lambda name, arguments: called.append(name),
    )

    blackboard = Blackboard()
    agent = KAREN(blackboard, _FakeLLMClient("x"))

    result = await agent._call_tool("shell_execute", {"command": "dir"})

    assert "not permitted" in result
    assert called == []


@pytest.mark.asyncio
async def test_aida_do_action_returns_content():
    blackboard = Blackboard()
    agent = AIDA(blackboard, _FakeLLMClient("marketing copy here"))

    result = await agent._do_action("write a product launch email", task=None)

    assert result == "marketing copy here"


@pytest.mark.asyncio
async def test_herbie_do_action_returns_report():
    blackboard = Blackboard()
    agent = HERBIE(blackboard, _FakeLLMClient("verification report"))

    result = await agent._do_action("verify the login flow works", task=None)

    assert result == "verification report"


@pytest.mark.asyncio
async def test_jarvis_spawns_vision_subtask():
    """JARVIS must spawn a Vision sub-task to plan the request. Vision's
    sub-task is created with assigned_to already set, so JarvisAgent's poll
    loop (which waits for sub_tasks with a non-empty assigned_to) breaks on
    its first check without needing to actually sleep."""
    blackboard = Blackboard()
    agent = JarvisAgent(blackboard, None)

    result = await agent._do_action("Build a REST API for user accounts", task=None)

    sub_tasks = [t for t in blackboard.get_all_tasks() if t.assigned_to == "Vision"]
    assert len(sub_tasks) == 1
    assert "1 sub-tasks" in result


@pytest.mark.asyncio
async def test_vision_decomposes_research_task():
    """Vision must decompose a research-flavored request into EDITH (search)
    then AIDA (summarize) sub-tasks, with the second depending on the first."""
    blackboard = Blackboard()
    agent = VisionAgent(blackboard, None)

    result = await agent._do_action("research the best JS framework", task=None)

    sub_tasks = blackboard.get_all_tasks()
    assert len(sub_tasks) == 2
    assert sub_tasks[0].assigned_to == "E.D.I.T.H."
    assert sub_tasks[1].assigned_to == "A.I.D.A."
    assert sub_tasks[1].dependencies == [sub_tasks[0].id]
    assert "2 sub-tasks" in result


@pytest.mark.asyncio
async def test_token_cost_accumulates():
    """Regression test: _complete must read the LLM response's usage.total_tokens
    and accumulate it onto the agent's AgentCard.token_cost via
    Blackboard.add_token_cost -- previously this field was declared and
    rendered in the frontend but never set anywhere, always showing 0.00."""
    blackboard = Blackboard()
    agent = AIDA(blackboard, _FakeLLMClient("content", total_tokens=42))

    await agent._do_action("write something", task=None)
    await agent._do_action("write something else", task=None)

    assert blackboard.get_agents()["A.I.D.A."].token_cost == 84.0


@pytest.mark.asyncio
async def test_token_cost_not_incremented_without_client():
    """The no-client placeholder path in _complete must not touch token_cost
    (there's no real usage to report, and tests without a client must stay
    green)."""
    blackboard = Blackboard()
    agent = AIDA(blackboard, None)

    await agent._do_action("write something", task=None)

    assert blackboard.get_agents()["A.I.D.A."].token_cost == 0.0

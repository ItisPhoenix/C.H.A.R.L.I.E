import asyncio

import pytest

from charlie.budget import IterationBudget
from charlie.config import Config
from charlie.core import Brain


@pytest.fixture
def brain():
    return Brain(
        Config(llm_url="http://localhost:11434", llm_key="no-key", llm_model="dummy")
    )


@pytest.mark.asyncio
async def test_concurrency_cap(brain, monkeypatch):
    running = 0
    peak = 0

    async def fake_run_subagent(agent_id, task):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.05)
        running -= 1
        return f"done: {task}"

    monkeypatch.setattr(brain, "_run_subagent", fake_run_subagent)

    results = await asyncio.gather(*(brain.spawn_agent(f"task-{i}") for i in range(6)))

    assert peak <= 3
    assert all(r.startswith("done: task-") for r in results)


@pytest.mark.asyncio
async def test_timeout_is_graceful(brain, monkeypatch):
    monkeypatch.setattr("charlie.core._AGENT_TIMEOUT_SEC", 0.05)

    async def slow_run_subagent(agent_id, task):
        await asyncio.sleep(10)
        return "should never get here"

    monkeypatch.setattr(brain, "_run_subagent", slow_run_subagent)

    result = await brain.spawn_agent("slow task")

    assert "timed out" in result
    assert brain._active_agents == {}


@pytest.mark.asyncio
async def test_cancel_agent(brain, monkeypatch):
    spawned_id = {}

    def capture_spawn(agent_id, task):
        spawned_id["id"] = agent_id

    brain.on_agent_spawned = capture_spawn

    async def slow_run_subagent(agent_id, task):
        await asyncio.sleep(10)
        return "should never get here"

    monkeypatch.setattr(brain, "_run_subagent", slow_run_subagent)

    task = asyncio.ensure_future(brain.spawn_agent("cancel me"))
    await asyncio.sleep(0.01)
    assert brain.cancel_agent(spawned_id["id"]) is True

    result = await task
    assert "cancelled" in result
    assert brain._active_agents == {}


@pytest.mark.asyncio
async def test_cancel_agent_unknown_id_returns_false(brain):
    assert brain.cancel_agent("does-not-exist") is False


@pytest.mark.asyncio
async def test_nested_spawn_excluded_from_subagent_tools(brain, monkeypatch):
    """Sub-agents must not see spawn_agent in their own tool schema."""
    seen_payloads = []

    async def fake_stream_completion(payload, generation):
        seen_payloads.append(payload)
        return ("final answer", [])

    monkeypatch.setattr(brain, "_stream_completion", fake_stream_completion)
    brain._use_native_tools = True

    result = await brain._run_subagent("agent-1", "do something")

    assert result == "final answer"
    tool_names = {t["function"]["name"] for t in seen_payloads[0].get("tools", [])}
    assert "spawn_agent" not in tool_names


def test_spawn_agent_costs_more_than_a_single_tool_call():
    budget = IterationBudget(max_turns=5)
    assert budget.try_spend("spawn_agent") is True
    assert budget.turns_used == 3


@pytest.mark.asyncio
async def test_exhausted_budget_synthesizes_best_effort_instead_of_discarding(brain, monkeypatch):
    """When a sub-agent runs out of tool turns, it should report whatever it
    already found instead of returning the bare "ran out of budget" string."""
    monkeypatch.setattr("charlie.core._AGENT_MAX_TOOL_TURNS", 2)
    monkeypatch.setattr("charlie.tools.registry.execute_tool", lambda name, args: "mock search result")
    brain._use_native_tools = True

    async def fake_stream_completion(payload, generation):
        if payload.get("tools"):
            return ("thinking", [{"id": "c1", "name": "web_search", "arguments": {"query": "x"}}])
        return ("Here's what I found so far: mock search result", [])

    monkeypatch.setattr(brain, "_stream_completion", fake_stream_completion)

    result = await brain._run_subagent("agent-1", "research something")

    assert "mock search result" in result
    assert "reached its" not in result

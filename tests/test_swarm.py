"""Tests for swarm reentrancy.

Concurrent tasks for the same agent name must each get a DISTINCT agent
instance. Agents are not reentrant, so the orchestrator must build a fresh
instance per dispatch rather than caching one.
"""

from unittest.mock import MagicMock

from charlie.agents.base import BaseAgent
from charlie.swarm import AGENT_REGISTRY, SwarmOrchestrator


class _FakeAgent(BaseAgent):
    """Minimal agent used only to observe instance identity."""

    name = "_test_fake_agent"

    async def _do_action(self, task_name: str, task) -> str:  # pragma: no cover
        return "ok"


def _make_orchestrator() -> SwarmOrchestrator:
    blackboard = MagicMock()
    blackboard.register_agent = MagicMock()
    llm_client = MagicMock()
    return SwarmOrchestrator(blackboard, llm_client)


def _register_fake() -> str:
    name = "_test_fake_agent"
    AGENT_REGISTRY[name] = _FakeAgent
    return name


def _unregister_fake(name: str) -> None:
    AGENT_REGISTRY.pop(name, None)


class TestSwarmReentrancy:
    """_get_agent must return a fresh instance on every call."""

    def setup_method(self):
        self.name = _register_fake()

    def teardown_method(self):
        _unregister_fake(self.name)

    def test_concurrent_same_name_yields_distinct_instances(self):
        orch = _make_orchestrator()
        a1 = orch._get_agent(self.name)
        a2 = orch._get_agent(self.name)
        assert a1 is not None
        assert a2 is not None
        assert type(a1) is _FakeAgent
        # Distinct instances: concurrent same-name tasks never share state.
        assert a1 is not a2

    def test_distinct_names_build_independently(self):
        orch = _make_orchestrator()
        a1 = orch._get_agent(self.name)
        a2 = orch._get_agent(self.name)
        a3 = orch._get_agent(self.name)
        assert len({id(a1), id(a2), id(a3)}) == 3

    def test_unknown_agent_returns_none(self):
        orch = _make_orchestrator()
        assert orch._get_agent("_does_not_exist_xyz") is None

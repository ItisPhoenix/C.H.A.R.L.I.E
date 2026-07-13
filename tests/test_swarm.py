"""Tests for swarm reentrancy.

Concurrent tasks for the same agent name must each get a DISTINCT agent
instance. Agents are not reentrant, so the orchestrator must build a fresh
instance per dispatch rather than caching one.
"""

import asyncio
from unittest.mock import MagicMock

import pytest

from charlie.agents.base import BaseAgent
from charlie.blackboard import Blackboard
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


class TestTerminateAgent:
    """terminate_agent must look up assigned_to via the Blackboard Task,
    not the asyncio.Task stored in _active_tasks (which has no such attr)."""

    @pytest.mark.asyncio
    async def test_terminate_cancels_matching_task(self):
        blackboard = Blackboard()
        orch = SwarmOrchestrator(blackboard)
        task = blackboard.add_task(name="do work", assigned_to="F.R.I.D.A.Y.")

        async def _never_ends():
            await asyncio.sleep(100)

        atask = asyncio.ensure_future(_never_ends())
        orch._active_tasks[task.id] = atask

        result = orch.terminate_agent("F.R.I.D.A.Y.")

        assert result is True
        bb_task = blackboard.get_task(task.id)
        assert bb_task.status == "failed"
        assert bb_task.result == "Terminated by user"

        # Let the cancellation propagate so the task doesn't leak a warning.
        with pytest.raises(asyncio.CancelledError):
            await atask

    @pytest.mark.asyncio
    async def test_terminate_no_active_task_returns_false(self):
        blackboard = Blackboard()
        orch = SwarmOrchestrator(blackboard)
        assert orch.terminate_agent("H.E.R.B.I.E.") is False

    @pytest.mark.asyncio
    async def test_terminate_ignores_done_tasks(self):
        blackboard = Blackboard()
        orch = SwarmOrchestrator(blackboard)
        task = blackboard.add_task(name="finished work", assigned_to="A.I.D.A.")

        async def _immediate():
            return "done"

        atask = asyncio.ensure_future(_immediate())
        await atask  # let it complete
        orch._active_tasks[task.id] = atask

        assert orch.terminate_agent("A.I.D.A.") is False


class TestHandleEscalation:
    """Regression tests: a task that exhausts MAX_RETRIES must actually get
    marked permanently failed (the "exceeded max retries" branch was
    unreachable dead code before this fix, because check_escalation's own
    filter excluded such tasks before _handle_escalation ever saw them)."""

    def test_exhausted_retries_marked_permanently_failed(self):
        from charlie.blackboard import MAX_RETRIES

        blackboard = Blackboard()
        orch = SwarmOrchestrator(blackboard)
        task = blackboard.add_task(name="flaky", assigned_to="F.R.I.D.A.Y.")
        blackboard.update_task(task.id, status="failed", retry_count=MAX_RETRIES)

        orch._handle_escalation()

        updated = blackboard.get_task(task.id)
        assert updated.status == "failed"
        assert updated.result == "Exceeded max retries"

    def test_exhausted_retries_escalation_is_idempotent(self):
        """A second sweep must not re-process an already-escalated task
        (otherwise it would re-log a warning and rewrite the same result
        forever, once per second, for the life of the process)."""
        from charlie.blackboard import MAX_RETRIES

        blackboard = Blackboard()
        orch = SwarmOrchestrator(blackboard)
        task = blackboard.add_task(name="flaky", assigned_to="F.R.I.D.A.Y.")
        blackboard.update_task(task.id, status="failed", retry_count=MAX_RETRIES)

        orch._handle_escalation()

        call_count = 0
        original_update_task = blackboard.update_task

        def _counting_update(task_id, **kwargs):
            nonlocal call_count
            if task_id == task.id:
                call_count += 1
            return original_update_task(task_id, **kwargs)

        blackboard.update_task = _counting_update
        orch._handle_escalation()
        assert call_count == 0

    def test_below_max_retries_gets_reset_for_retry(self):
        blackboard = Blackboard()
        orch = SwarmOrchestrator(blackboard)
        task = blackboard.add_task(name="flaky", assigned_to="F.R.I.D.A.Y.")
        blackboard.update_task(task.id, status="failed")

        orch._handle_escalation()

        updated = blackboard.get_task(task.id)
        assert updated.status == "pending"
        assert updated.retry_count == 1

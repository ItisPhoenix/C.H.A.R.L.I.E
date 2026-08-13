import asyncio

import pytest

from charlie.tasks import ManagedTask, TaskManager


def _entry(id_, priority=0, depends_on=None):
    return ManagedTask(id=id_, priority=priority, depends_on=depends_on or [])


@pytest.mark.asyncio
async def test_single_task_runs_immediately():
    mgr = TaskManager(max_parallel=1)
    ran = []

    async def run():
        ran.append(mgr.get("a").status)

    mgr.submit(_entry("a"), run)
    await asyncio.sleep(0.01)
    assert ran == ["running"]


@pytest.mark.asyncio
async def test_second_task_queues_behind_first_at_max_parallel_one():
    mgr = TaskManager(max_parallel=1)
    release_a = asyncio.Event()
    release_b = asyncio.Event()

    async def run_a():
        await release_a.wait()

    async def run_b():
        await release_b.wait()

    mgr.submit(_entry("a"), run_a)
    mgr.submit(_entry("b"), run_b)
    await asyncio.sleep(0.01)
    assert mgr.get("a").status == "running"
    assert mgr.get("b").status == "queued"

    release_a.set()
    await asyncio.sleep(0.01)
    assert mgr.get("b").status == "running"
    release_b.set()
    await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_run_fn_sets_terminal_status_on_completion():
    mgr = TaskManager(max_parallel=1)

    async def run():
        mgr.get("a").status = "done"

    mgr.submit(_entry("a"), run)
    await asyncio.sleep(0.01)
    assert mgr.get("a").status == "done"


@pytest.mark.asyncio
async def test_run_fn_exception_marks_task_failed():
    mgr = TaskManager(max_parallel=1)

    async def run():
        raise RuntimeError("planned failure")

    mgr.submit(_entry("a"), run)
    await asyncio.sleep(0.01)

    assert mgr.get("a").status == "failed"


@pytest.mark.asyncio
async def test_higher_priority_runs_before_lower_when_both_queued():
    mgr = TaskManager(max_parallel=1)
    release = asyncio.Event()
    order = []

    async def blocker():
        await release.wait()

    async def make_run(task_id):
        async def run():
            order.append(task_id)
        return run

    mgr.submit(_entry("blocker"), blocker)
    mgr.submit(_entry("low", priority=0), await make_run("low"))
    mgr.submit(_entry("high", priority=10), await make_run("high"))
    await asyncio.sleep(0.01)

    release.set()
    await asyncio.sleep(0.01)
    assert order == ["high", "low"]


@pytest.mark.asyncio
async def test_task_waits_for_dependency_before_running():
    mgr = TaskManager(max_parallel=2)
    dep_done = asyncio.Event()
    ran = []

    async def dep_run():
        await dep_done.wait()
        mgr.get("dep").status = "done"

    async def dependent_run():
        ran.append("dependent")

    mgr.submit(_entry("dep"), dep_run)
    mgr.submit(_entry("dependent", depends_on=["dep"]), dependent_run)
    await asyncio.sleep(0.01)
    assert mgr.get("dependent").status == "queued"
    assert ran == []

    dep_done.set()
    await asyncio.sleep(0.01)
    assert ran == ["dependent"]


@pytest.mark.asyncio
async def test_dependency_failure_cancels_dependent_without_running_it():
    mgr = TaskManager(max_parallel=2)
    ran = []

    async def dep_run():
        mgr.get("dep").status = "failed"

    async def dependent_run():
        ran.append("dependent")

    mgr.submit(_entry("dep"), dep_run)
    mgr.submit(_entry("dependent", depends_on=["dep"]), dependent_run)
    await asyncio.sleep(0.01)
    assert mgr.get("dependent").status == "cancelled"
    assert ran == []


def test_cancel_queued_task_marks_cancelled_and_returns_true():
    mgr = TaskManager(max_parallel=0)
    mgr.submit(_entry("a"), lambda: None)
    assert mgr.cancel("a") is True
    assert mgr.get("a").status == "cancelled"


@pytest.mark.asyncio
async def test_cancel_running_task_sets_flag_not_status():
    mgr = TaskManager(max_parallel=1)
    release = asyncio.Event()

    async def run():
        await release.wait()

    mgr.submit(_entry("a"), run)
    await asyncio.sleep(0.01)
    assert mgr.cancel("a") is True
    assert mgr.get("a").status == "running"
    assert mgr.get("a").cancel_requested is True
    release.set()
    await asyncio.sleep(0.01)


def test_cancel_unknown_task_returns_false():
    mgr = TaskManager(max_parallel=1)
    assert mgr.cancel("nope") is False


@pytest.mark.asyncio
async def test_max_parallel_two_runs_both_concurrently():
    mgr = TaskManager(max_parallel=2)
    release = asyncio.Event()

    async def run():
        await release.wait()

    mgr.submit(_entry("a"), run)
    mgr.submit(_entry("b"), run)
    await asyncio.sleep(0.01)
    assert mgr.get("a").status == "running"
    assert mgr.get("b").status == "running"
    release.set()
    await asyncio.sleep(0.01)


def test_active_count_counts_queued_and_running():
    mgr = TaskManager(max_parallel=0)
    mgr.submit(_entry("a"), lambda: None)
    mgr.submit(_entry("b"), lambda: None)
    assert mgr.active_count() == 2


@pytest.mark.asyncio
async def test_on_status_change_fires_for_manager_driven_transitions():
    seen = []
    mgr = TaskManager(max_parallel=1, on_status_change=lambda t: seen.append((t.id, t.status)))
    release = asyncio.Event()

    async def run_a():
        await release.wait()

    async def run_b():
        pass

    mgr.submit(_entry("a"), run_a)
    mgr.submit(_entry("b"), run_b)
    assert ("a", "running") in seen
    assert ("b", "queued") in seen

    release.set()
    await asyncio.sleep(0.01)
    assert ("b", "running") in seen


def test_list_returns_all_submitted_tasks():
    mgr = TaskManager(max_parallel=0)
    mgr.submit(_entry("a"), lambda: None)
    mgr.submit(_entry("b"), lambda: None)
    ids = {t.id for t in mgr.list()}
    assert ids == {"a", "b"}

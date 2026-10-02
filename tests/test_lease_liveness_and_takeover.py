"""Lease liveness, refcounted release, fenced takeover, and capture staleness.

These are the ownership contracts that the doctor, the capability lease
authority, and the desktop perception layer all share.  The evidence class is
TEST/MOCK: leases and the monotonic capture clock are driven directly, with no
real host input, screen grab, or runtime journal required.
"""

from __future__ import annotations

import asyncio
import time
import types
from uuid import uuid4

import pytest

from charlie import resource_locks
from charlie.desktop import uia
from charlie.doctor import CharlieDoctor, CheckStatus
from charlie.resource_locks import CapabilityLeaseManager
from charlie.task_journal import TaskJournal

# ---------------------------------------------------------------------------
# Isolated lease-state fixtures.  _owners / _active_leases are process-global,
# so every test snapshots and restores them rather than relying on ordering.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_lease_state():
    with resource_locks._lock:
        owners = dict(resource_locks._owners)
        active = {k: set(v) for k, v in resource_locks._active_leases.items()}
        objects = {k: dict(v) for k, v in resource_locks._lease_objects.items()}
        revocations = dict(resource_locks._revocations)
        listeners = set(resource_locks._takeover_listeners)
    yield
    with resource_locks._lock:
        resource_locks._owners.clear()
        resource_locks._owners.update(owners)
        resource_locks._active_leases.clear()
        for key, leases in active.items():
            resource_locks._active_leases[key] = leases
        resource_locks._lease_objects.clear()
        for key, leases in objects.items():
            resource_locks._lease_objects[key] = leases
        resource_locks._revocations.clear()
        resource_locks._revocations.update(revocations)
        resource_locks._takeover_listeners.clear()
        resource_locks._takeover_listeners.update(listeners)


@pytest.fixture(autouse=True)
def _isolated_capture_state():
    previous = (uia._LAST_CAPTURE_BOUNDS, getattr(uia, "_LAST_CAPTURE_AT", None))
    uia.set_last_capture_bounds(None)
    yield
    uia.set_last_capture_bounds(previous[0])
    if previous[0] is not None:
        # Preserve the recorded age too, so a test cannot leak a fake clock.
        uia._LAST_CAPTURE_AT = previous[1]


def _introspector(active_leases: dict[str, str], active_tasks: list[dict] | None = None) -> types.SimpleNamespace:
    """A minimal introspector exposing exactly the two reads the lease check makes."""
    return types.SimpleNamespace(
        get_leases_info=lambda: {
            "active_leases": dict(active_leases),
            "leased_resources_count": len(active_leases),
        },
        get_tasks_info=lambda: {
            "counts": {"running": len(active_tasks or [])},
            "active_tasks": list(active_tasks or []),
            "total_tasks": len(active_tasks or []),
        },
    )


# ---------------------------------------------------------------------------
# 1. Lease liveness is derived from actual holders, not journal membership.
# ---------------------------------------------------------------------------


def test_operation_owner_with_active_lease_is_not_reported_as_orphan():
    """charlie/core.py mints `operation:<uuid4>` whenever task_id is None, which
    is the ordinary chat/tool loop. Journal membership therefore cannot be the
    liveness signal, or every in-flight lease reads as an orphan."""
    owner = f"operation:{uuid4().hex}"
    doctor = CharlieDoctor(introspector=_introspector({"desktop": owner}))

    check = doctor._check_capability_leases()

    assert check.status == CheckStatus.OK, check.evidence
    assert "terminated/unknown owners" not in check.evidence
    assert owner in check.evidence


def test_fastpath_owner_with_active_lease_is_not_reported_as_orphan():
    """Fast-path owners are `fastpath.<intent>` and never appear in the journal."""
    doctor = CharlieDoctor(introspector=_introspector({"browser": "fastpath.open_url"}))

    check = doctor._check_capability_leases()

    assert check.status == CheckStatus.OK, check.evidence


def test_genuine_orphan_owner_is_still_reported():
    """An owner that matches no live shape and is absent from the journal is a
    real orphan and must still surface a repairable warning."""
    doctor = CharlieDoctor(introspector=_introspector({"desktop": "orphan-task-999"}))

    check = doctor._check_capability_leases()

    assert check.status == CheckStatus.WARNING
    assert "orphan" in check.evidence.lower()
    assert check.repair_id == "repair_stale_leases"


def test_journal_task_owner_is_not_reported_as_orphan():
    journal = TaskJournal()
    task = journal.create_task("Active work")
    journal.transition(task.id, "running")
    doctor = CharlieDoctor(introspector=_introspector(
        {"desktop": task.id},
        [{"task_id": task.id}],
    ))

    check = doctor._check_capability_leases()

    assert check.status == CheckStatus.OK, check.evidence


def test_repair_stale_leases_does_not_release_a_live_operation_lease():
    """The check and the repair must agree on liveness, otherwise the offered
    repair steals control from the very holder the check just cleared."""
    journal = TaskJournal()
    manager = CapabilityLeaseManager()
    live = f"operation:{uuid4().hex}"
    dead = "orphan-task-999"
    introspector = types.SimpleNamespace(
        get_leases_info=lambda: {
            "active_leases": manager.snapshot(),
            "leased_resources_count": 0,
        },
        get_tasks_info=lambda: {"counts": {"running": 0}, "active_tasks": [], "total_tasks": 0},
        _get_task_journal=lambda: journal,
        _get_lease_manager=lambda: manager,
    )
    doctor = CharlieDoctor(introspector=introspector)

    async def _hold() -> tuple:
        held = await manager.acquire("desktop", live)
        stale = await manager.acquire("browser", dead)
        assert held is not None and stale is not None
        result = doctor.execute_repair("repair_stale_leases")
        return result, held, stale

    result, held, stale = asyncio.run(_hold())
    try:
        assert result["success"] is True
        assert manager.current_owner("desktop") == live
        assert manager.current_owner("browser") is None
    finally:
        for lease in (held, stale):
            lease.manager._release_lease(lease)


# ---------------------------------------------------------------------------
# 2. release() is refcount-aware.
# ---------------------------------------------------------------------------


def test_release_retains_ownership_while_a_sibling_lease_is_active():
    """Two leases on one (capability, owner); the legacy module-level release()
    drops one of them and must not hand the capability away from work that is
    still running under the same owner."""
    manager = CapabilityLeaseManager()

    async def _scenario() -> tuple:
        first = await manager.acquire("desktop", "task-a")
        second = await manager.acquire("desktop", "task-a")
        resource_locks.release("desktop", "task-a")
        owner_after_legacy_release = manager.current_owner("desktop")
        await first.release()
        owner_after_first = manager.current_owner("desktop")
        await second.release()
        return owner_after_legacy_release, owner_after_first, manager.current_owner("desktop")

    after_legacy, after_first, after_second = asyncio.run(_scenario())

    assert after_legacy == "task-a"
    assert after_first == "task-a"
    assert after_second is None


def test_release_still_frees_the_capability_when_no_sibling_lease_remains():
    """A legacy sync hold (no lease object) is freed immediately, as before."""
    assert resource_locks.acquire("desktop", "task-a") is True
    resource_locks.release("desktop", "task-a")
    assert resource_locks.current_owner("desktop") is None


def test_force_release_breaks_a_lease_record_for_a_dead_holder():
    """The repair path must be able to break a lease whose holder terminated
    without releasing -- the one case where ignoring the refcount is correct."""
    manager = CapabilityLeaseManager()

    async def _scenario() -> tuple:
        held = await manager.acquire("browser", "orphan-task-999")
        forced = resource_locks.force_release("browser", "orphan-task-999")
        owner = manager.current_owner("browser")
        held.manager._release_lease(held)
        return forced, owner, manager.current_owner("browser")

    forced, owner_after, owner_after_lease = asyncio.run(_scenario())

    assert forced is True
    assert owner_after is None
    assert owner_after_lease is None


def test_force_release_declines_a_different_owner():
    resource_locks.acquire("desktop", "task-a")
    try:
        assert resource_locks.force_release("desktop", "task-b") is False
        assert resource_locks.current_owner("desktop") == "task-a"
    finally:
        resource_locks.release("desktop", "task-a")


def test_release_ignores_a_non_owner_while_a_sibling_lease_is_active():
    manager = CapabilityLeaseManager()

    async def _scenario() -> object:
        held = await manager.acquire("desktop", "task-a")
        resource_locks.release("desktop", "task-b")
        owner = manager.current_owner("desktop")
        await held.release()
        return owner

    assert asyncio.run(_scenario()) == "task-a"


# ---------------------------------------------------------------------------
# 3. manual_takeover fences the capability instead of transferring it.
# ---------------------------------------------------------------------------


def test_manual_takeover_does_not_admit_a_second_holder_while_prior_work_runs():
    """A takeover must cancel physical control, not silently hand a still-busy
    capability to a second holder."""
    manager = CapabilityLeaseManager()

    async def _scenario() -> tuple:
        held = await manager.acquire("desktop", "operation:aaa")
        revoked = manager.manual_takeover(("desktop",))
        # A different owner must not be admitted.
        second = manager._try_acquire("desktop", "operation:bbb")
        # Nor may the fenced owner resurrect control from a cancelled task.
        same = manager._try_acquire("desktop", "operation:aaa")
        # A blocked waiter is the realistic shape of the second holder.
        with pytest.raises(asyncio.TimeoutError):
            await manager.acquire("desktop", "operation:bbb", timeout=0.01)
        return revoked, held, second, same, manager.current_owner("desktop")

    revoked, held, second, same, owner = asyncio.run(_scenario())
    try:
        assert revoked == {"operation:aaa"}
        assert second is None
        assert same is None
        assert owner == "operation:aaa"
    finally:
        manager._release_lease(held)


def test_manual_takeover_notifies_the_prior_owner():
    """Revocation must reach the existing listener mechanism so the prior owner
    can cancel its own work."""
    notifications: list[tuple[str, tuple[str, ...]]] = []
    manager = CapabilityLeaseManager(on_takeover=lambda owner, resources: notifications.append((owner, resources)))
    resource_locks.register_takeover_listener(
        lambda owner, resources: notifications.append((owner, resources))
    )
    lease = asyncio.run(manager.acquire_many(("desktop", "physical_mouse"), "operation:ccc"))

    revoked = manager.manual_takeover(("physical_mouse",))

    assert revoked == {"operation:ccc"}
    assert notifications == [
        ("operation:ccc", ("physical_mouse",)),
        ("operation:ccc", ("physical_mouse",)),
    ]
    assert manager.current_owner("desktop") == "operation:ccc"
    asyncio.run(lease.release())


def test_takeover_fence_clears_once_the_prior_holder_releases():
    """Otherwise a fenced capability would be wedged forever."""
    manager = CapabilityLeaseManager()

    async def _scenario() -> tuple:
        held = await manager.acquire("desktop", "operation:ddd")
        manager.manual_takeover(("desktop",))
        blocked = manager._try_acquire("desktop", "operation:eee")
        await held.release()
        admitted = manager._try_acquire("desktop", "operation:eee")
        return blocked, admitted

    blocked, admitted = asyncio.run(_scenario())
    assert blocked is None
    assert admitted is not None
    assert admitted.owner_id == "operation:eee"
    asyncio.run(admitted.release())


def test_takeover_of_a_free_capability_creates_no_fence():
    manager = CapabilityLeaseManager()
    assert manager.manual_takeover(("desktop",)) == set()
    lease = asyncio.run(manager.acquire("desktop", "operation:fff"))
    assert manager.current_owner("desktop") == "operation:fff"
    asyncio.run(lease.release())


def test_takeover_halts_desktop_dispatch_for_the_physical_control_session():
    """charlie.desktop.takeover._trigger_halt must both halt dispatch and fence
    the capability while a holder is still able to run."""
    from charlie.desktop import actions
    from charlie.desktop import takeover as takeover_module

    actions.clear_halt()

    async def _scenario() -> None:
        holder = await resource_locks.default_lease_manager.acquire("desktop", "operation:ggg")
        try:
            outcome = takeover_module.user_takeover_detector._trigger_halt()
            assert outcome.dispatch_halted is True
            assert outcome.error is None
            assert outcome.fenced_capabilities == ("desktop",)
            assert outcome.fenced_owners == ("operation:ggg",)
            assert actions.is_halted() is True
            # Fenced, not transferred: the revoked holder keeps the record and
            # no second holder is admitted.
            assert resource_locks.current_owner("desktop") == "operation:ggg"
            assert resource_locks.default_lease_manager._try_acquire("desktop", "operation:hhh") is None
        finally:
            resource_locks.default_lease_manager._release_lease(holder)

    try:
        asyncio.run(_scenario())
    finally:
        actions.clear_halt()


def test_takeover_does_not_fence_a_lease_whose_loop_is_already_closed():
    """A lease on a closed event loop cannot still be running, so fencing it
    would only wedge the capability."""
    manager = CapabilityLeaseManager()

    def _hold_then_close() -> None:
        asyncio.run(manager.acquire("desktop", "operation:iii"))
        assert manager.current_owner("desktop") == "operation:iii"

    _hold_then_close()

    assert manager.manual_takeover(("desktop",)) == {"operation:iii"}
    assert manager.is_revoked("desktop") is False
    assert manager.current_owner("desktop") is None
    lease = asyncio.run(manager.acquire("desktop", "operation:jjj"))
    assert manager.current_owner("desktop") == "operation:jjj"
    asyncio.run(lease.release())


# ---------------------------------------------------------------------------
# 4. Capture bounds carry a timestamp and stale ones are refused.
# ---------------------------------------------------------------------------


def test_capture_staleness_default_is_conservative():
    assert isinstance(uia.CAPTURE_STALENESS_SECONDS, float)
    assert uia.CAPTURE_STALENESS_SECONDS > 0.0
    # Conservative means "long enough for a normal tool round-trip, short enough
    # that a moved window is caught", not "never refuse".
    assert 5.0 <= uia.CAPTURE_STALENESS_SECONDS <= 60.0


def test_fresh_capture_bounds_are_accepted(monkeypatch):
    base = 1000.0
    monkeypatch.setattr(uia.time, "monotonic", lambda: base)
    uia.set_last_capture_bounds((100, 200, 900, 800))

    monkeypatch.setattr(uia.time, "monotonic", lambda: base + (uia.CAPTURE_STALENESS_SECONDS - 0.5))
    assert uia.last_capture_staleness() is None
    assert uia.image_to_screen(50, 60) == (150, 260)


def test_stale_capture_bounds_are_rejected_with_a_reason(monkeypatch):
    base = 1000.0
    monkeypatch.setattr(uia.time, "monotonic", lambda: base)
    uia.set_last_capture_bounds((100, 200, 900, 800))

    monkeypatch.setattr(uia.time, "monotonic", lambda: base + uia.CAPTURE_STALENESS_SECONDS + 0.5)
    reason = uia.last_capture_staleness()
    assert reason is not None
    assert "stale" in reason.lower()
    assert uia.image_to_screen(50, 60) is None


def test_absent_capture_bounds_report_no_capture(monkeypatch):
    monkeypatch.setattr(uia.time, "monotonic", lambda: 1000.0)
    uia.set_last_capture_bounds(None)
    assert uia.last_capture_staleness() == "no capture recorded"
    assert uia.image_to_screen(1, 1) is None


def test_clear_capture_bounds_drops_the_timestamp(monkeypatch):
    """A cleared capture must not leave a timestamp that later reads as fresh."""
    base = 1000.0
    monkeypatch.setattr(uia.time, "monotonic", lambda: base)
    uia.set_last_capture_bounds((0, 0, 800, 600))
    uia.set_last_capture_bounds(None)

    monkeypatch.setattr(uia.time, "monotonic", lambda: base + 1.0)
    assert uia.last_capture_staleness() == "no capture recorded"
    assert uia.get_last_capture_bounds() is None


@pytest.mark.parametrize("effector", ["click_at", "move_to"])
def test_stale_capture_is_refused_by_image_space_effectors(monkeypatch, effector):
    """click_at/move_to map image coords through the last capture, so a stale
    capture must not reach pyautogui at all."""
    from charlie.desktop import actions

    dispatched: list[tuple] = []
    monkeypatch.setattr(actions, "_HAS_PYAUTOGUI", True, raising=False)
    monkeypatch.setattr(actions, "_to_screen", lambda x, y: uia.image_to_screen(x, y), raising=False)
    import pyautogui

    monkeypatch.setattr(pyautogui, "click", lambda *a, **k: dispatched.append(a), raising=False)
    monkeypatch.setattr(pyautogui, "moveTo", lambda *a, **k: dispatched.append(a), raising=False)
    actions.clear_halt()

    base = 1000.0
    monkeypatch.setattr(uia.time, "monotonic", lambda: base)
    uia.set_last_capture_bounds((100, 200, 900, 800))
    monkeypatch.setattr(uia.time, "monotonic", lambda: base + uia.CAPTURE_STALENESS_SECONDS + 1.0)

    result = getattr(actions, effector)(50, 60)

    assert dispatched == []
    assert "capture" in result.lower()

    monkeypatch.setattr(uia.time, "monotonic", lambda: base + 0.5)
    ok = getattr(actions, effector)(50, 60)
    assert len(dispatched) == 1
    assert "error" not in ok.lower()
    actions.clear_halt()


def test_drag_is_refused_for_a_stale_capture(monkeypatch):
    import pyautogui

    from charlie.desktop import actions

    dispatched: list[tuple] = []
    monkeypatch.setattr(actions, "_HAS_PYAUTOGUI", True, raising=False)
    monkeypatch.setattr(actions, "_to_screen", lambda x, y: uia.image_to_screen(x, y), raising=False)
    monkeypatch.setattr(pyautogui, "moveTo", lambda *a, **k: dispatched.append(a), raising=False)
    monkeypatch.setattr(pyautogui, "dragTo", lambda *a, **k: dispatched.append(a), raising=False)
    actions.clear_halt()

    base = 1000.0
    monkeypatch.setattr(uia.time, "monotonic", lambda: base)
    uia.set_last_capture_bounds((100, 200, 900, 800))
    monkeypatch.setattr(uia.time, "monotonic", lambda: base + uia.CAPTURE_STALENESS_SECONDS + 1.0)

    result = actions.drag(10, 10, 200, 200)

    assert dispatched == []
    assert "capture" in result.lower()
    actions.clear_halt()


def test_fresh_capture_keeps_effectors_working(monkeypatch):
    """Guard against the staleness gate breaking the normal observe -> act loop."""
    import pyautogui

    from charlie.desktop import actions

    dispatched: list[tuple] = []
    monkeypatch.setattr(actions, "_HAS_PYAUTOGUI", True, raising=False)
    monkeypatch.setattr(actions, "_to_screen", lambda x, y: uia.image_to_screen(x, y), raising=False)
    monkeypatch.setattr(pyautogui, "moveTo", lambda *a, **k: dispatched.append(a), raising=False)
    monkeypatch.setattr(pyautogui, "dragTo", lambda *a, **k: dispatched.append(a), raising=False)
    actions.clear_halt()

    base = time.monotonic()
    monkeypatch.setattr(uia.time, "monotonic", lambda: base)
    uia.set_last_capture_bounds((100, 200, 900, 800))

    result = actions.drag(10, 10, 200, 200)

    assert len(dispatched) == 2
    assert dispatched[0] == (110, 210)
    assert dispatched[1] == (300, 400)
    assert "error" not in result.lower()
    actions.clear_halt()

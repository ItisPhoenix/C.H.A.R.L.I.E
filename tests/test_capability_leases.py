import asyncio

import pytest

from charlie.resource_locks import CapabilityLeaseManager


@pytest.mark.asyncio
async def test_lease_manager_waits_wakes_and_releases() -> None:
    manager = CapabilityLeaseManager()
    first = await manager.acquire("desktop", "task-1")
    acquired = asyncio.Event()

    async def wait_for_desktop() -> None:
        lease = await manager.acquire("desktop", "task-2")
        acquired.set()
        await lease.release()

    waiter = asyncio.create_task(wait_for_desktop())
    await asyncio.sleep(0)
    assert not acquired.is_set()
    await first.release()
    await asyncio.wait_for(acquired.wait(), timeout=1)
    await waiter
    assert manager.current_owner("desktop") is None


@pytest.mark.asyncio
async def test_lease_manager_supports_timeout_cancel_and_idempotent_release() -> None:
    manager = CapabilityLeaseManager()
    held = await manager.acquire("keyboard", "task-1")

    with pytest.raises(asyncio.TimeoutError):
        await manager.acquire("keyboard", "task-2", timeout=0.01)

    cancelled = asyncio.Event()
    waiter = asyncio.create_task(manager.acquire("keyboard", "task-2", cancel_event=cancelled))
    await asyncio.sleep(0)
    cancelled.set()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    await held.release()
    await held.release()
    assert manager.current_owner("keyboard") is None


@pytest.mark.asyncio
async def test_acquire_many_is_deterministic_and_rolls_back_on_cancellation() -> None:
    manager = CapabilityLeaseManager()
    cancellation = asyncio.Event()
    held = await manager.acquire("keyboard", "other")
    waiter = asyncio.create_task(
        manager.acquire_many(("desktop", "keyboard"), "task-1", cancel_event=cancellation)
    )
    await asyncio.sleep(0)
    cancellation.set()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert manager.current_owner("desktop") is None
    await held.release()


@pytest.mark.asyncio
async def test_manual_takeover_releases_matching_owner_and_notifies() -> None:
    notifications: list[tuple[str, tuple[str, ...]]] = []
    manager = CapabilityLeaseManager(on_takeover=lambda owner, resources: notifications.append((owner, resources)))
    lease = await manager.acquire_many(("desktop", "physical_mouse"), "task-1")

    released = manager.manual_takeover(("physical_mouse",))

    assert released == {"task-1"}
    assert notifications == [("task-1", ("physical_mouse",))]
    assert manager.current_owner("desktop") == "task-1"
    await lease.release()

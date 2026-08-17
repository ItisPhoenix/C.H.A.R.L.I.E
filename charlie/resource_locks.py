"""Per-capability resource lock: two callers touching the same capability (desktop, browser,
...) must serialize, not race. Non-blocking test-and-set with an owner id (callers poll on
failure) rather than a real blocking lock -- the same primitive has to work from both plain
async code and desktop control's own worker-thread callers without risking a cross-thread
deadlock, and generalizes charlie/desktop/session.py's original desktop-only mutex exactly.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, Optional

from charlie.utils import make_id

_lock = threading.Lock()
_owners: Dict[str, str] = {}
_active_leases: Dict[tuple[str, str], set[str]] = {}
_waiters: Dict[str, set[asyncio.Event]] = {}


def _validate_capability(capability: str) -> str:
    value = str(capability).strip()
    if not value:
        raise ValueError("Capability lease key cannot be empty")
    return value


def _wake(capabilities: Iterable[str]) -> None:
    events: set[asyncio.Event] = set()
    with _lock:
        for capability in capabilities:
            events.update(_waiters.get(capability, set()))
    for event in events:
        try:
            event_loop = event._loop
            event_loop.call_soon_threadsafe(event.set)
        except RuntimeError:
            pass


def acquire(capability: str, owner_id: str) -> bool:
    capability = _validate_capability(capability)
    with _lock:
        current = _owners.get(capability)
        if current is None or current == owner_id:
            _owners[capability] = owner_id
            return True
        return False


def release(capability: str, owner_id: str) -> None:
    capability = _validate_capability(capability)
    with _lock:
        if _owners.get(capability) == owner_id:
            _owners.pop(capability, None)
            _active_leases.pop((capability, owner_id), None)
    _wake((capability,))


def current_owner(capability: str) -> Optional[str]:
    capability = _validate_capability(capability)
    with _lock:
        return _owners.get(capability)


@dataclass
class CapabilityLease:
    manager: "CapabilityLeaseManager"
    capability: str
    owner_id: str
    lease_id: str
    _released: bool = False

    async def release(self) -> None:
        if self._released:
            return
        self._released = True
        self.manager._release_lease(self)

    async def __aenter__(self) -> "CapabilityLease":
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        await self.release()


@dataclass
class CapabilityLeaseBundle:
    leases: tuple[CapabilityLease, ...]

    async def release(self) -> None:
        for lease in reversed(self.leases):
            await lease.release()

    async def __aenter__(self) -> "CapabilityLeaseBundle":
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        await self.release()


class CapabilityLeaseManager:
    """Async waitable lease authority sharing ownership with legacy sync callers.

    Multi-capability acquisition always sorts keys first, so two tasks cannot
    deadlock by requesting the same set in opposite order.  A lease is released
    on explicit release or async context-manager exit and is idempotent.
    """

    def __init__(
        self,
        on_takeover: Optional[Callable[[str, tuple[str, ...]], None]] = None,
    ) -> None:
        self._on_takeover = on_takeover

    def current_owner(self, capability: str) -> Optional[str]:
        return current_owner(capability)

    def snapshot(self) -> dict[str, str]:
        with _lock:
            return dict(_owners)

    async def acquire(
        self,
        capability: str,
        owner_id: str,
        *,
        timeout: Optional[float] = None,
        cancel_event: Optional[asyncio.Event] = None,
    ) -> CapabilityLease:
        capability = _validate_capability(capability)
        if not owner_id:
            raise ValueError("Capability lease owner cannot be empty")
        deadline = None if timeout is None else asyncio.get_running_loop().time() + timeout
        while True:
            lease = self._try_acquire(capability, owner_id)
            if lease is not None:
                return lease
            if cancel_event is not None and cancel_event.is_set():
                raise asyncio.CancelledError
            if deadline is not None and deadline <= asyncio.get_running_loop().time():
                raise asyncio.TimeoutError
            remaining = None if deadline is None else max(0.0, deadline - asyncio.get_running_loop().time())
            try:
                await asyncio.wait_for(self._wait_for_change(capability, cancel_event), timeout=remaining)
            except asyncio.TimeoutError:
                raise

    async def acquire_many(
        self,
        capabilities: Iterable[str],
        owner_id: str,
        *,
        timeout: Optional[float] = None,
        cancel_event: Optional[asyncio.Event] = None,
    ) -> CapabilityLeaseBundle:
        ordered = tuple(sorted({_validate_capability(value) for value in capabilities}))
        if not ordered:
            raise ValueError("At least one capability lease key is required")
        deadline = None if timeout is None else asyncio.get_running_loop().time() + timeout
        leases: list[CapabilityLease] = []
        try:
            for capability in ordered:
                remaining = None if deadline is None else max(0.0, deadline - asyncio.get_running_loop().time())
                leases.append(await self.acquire(capability, owner_id, timeout=remaining, cancel_event=cancel_event))
            return CapabilityLeaseBundle(tuple(leases))
        except BaseException:
            for lease in reversed(leases):
                await lease.release()
            raise

    def manual_takeover(self, capabilities: Iterable[str]) -> set[str]:
        requested = tuple(sorted({_validate_capability(value) for value in capabilities}))
        owners: dict[str, list[str]] = {}
        with _lock:
            for capability in requested:
                owner = _owners.pop(capability, None)
                if owner is not None:
                    owners.setdefault(owner, []).append(capability)
                    _active_leases.pop((capability, owner), None)
        _wake(requested)
        for owner, resources in owners.items():
            if self._on_takeover is not None:
                self._on_takeover(owner, tuple(resources))
        return set(owners)

    def _try_acquire(self, capability: str, owner_id: str) -> Optional[CapabilityLease]:
        lease_id = f"{owner_id}:{make_id(8)}"
        with _lock:
            current = _owners.get(capability)
            if current is not None and current != owner_id:
                return None
            _owners[capability] = owner_id
            _active_leases.setdefault((capability, owner_id), set()).add(lease_id)
            return CapabilityLease(self, capability, owner_id, lease_id)

    def _release_lease(self, lease: CapabilityLease) -> None:
        released = False
        with _lock:
            active = _active_leases.get((lease.capability, lease.owner_id))
            if active is not None:
                active.discard(lease.lease_id)
                if not active:
                    _active_leases.pop((lease.capability, lease.owner_id), None)
                    if _owners.get(lease.capability) == lease.owner_id:
                        _owners.pop(lease.capability, None)
                        released = True
            elif _owners.get(lease.capability) == lease.owner_id:
                _owners.pop(lease.capability, None)
                released = True
        if released:
            _wake((lease.capability,))

    async def _wait_for_change(self, capability: str, cancel_event: Optional[asyncio.Event]) -> None:
        wake_event = asyncio.Event()
        with _lock:
            _waiters.setdefault(capability, set()).add(wake_event)
        cancel_task: Optional[asyncio.Task] = None
        try:
            if cancel_event is None:
                await wake_event.wait()
                return
            cancel_task = asyncio.create_task(cancel_event.wait())
            wake_task = asyncio.create_task(wake_event.wait())
            done, pending = await asyncio.wait(
                (wake_task, cancel_task), return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            if cancel_task in done and cancel_task.result():
                raise asyncio.CancelledError
        finally:
            if cancel_task is not None and not cancel_task.done():
                cancel_task.cancel()
            with _lock:
                waiters = _waiters.get(capability)
                if waiters is not None:
                    waiters.discard(wake_event)
                    if not waiters:
                        _waiters.pop(capability, None)


default_lease_manager = CapabilityLeaseManager()


def get_all_leases() -> Dict[str, str]:
    """Return a snapshot of all active capability owners."""
    with _lock:
        return dict(_owners)


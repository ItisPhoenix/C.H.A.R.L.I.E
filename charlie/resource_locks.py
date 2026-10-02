"""Per-capability resource lock: two callers touching the same capability (desktop, browser,
...) must serialize, not race. Non-blocking test-and-set with an owner id (callers poll on
failure) rather than a real blocking lock -- the same primitive has to work from both plain
async code and desktop control's own worker-thread callers without risking a cross-thread
deadlock, and generalizes charlie/desktop/session.py's original desktop-only mutex exactly.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, Optional

from charlie.utils import make_id

logger = logging.getLogger("charlie.capability_leases")

_lock = threading.Lock()
_owners: Dict[str, str] = {}
_active_leases: Dict[tuple[str, str], set[str]] = {}
_waiters: Dict[str, set[asyncio.Event]] = {}
_takeover_listeners: set[Callable[[str, tuple[str, ...]], None]] = set()
_revocations: Dict[str, "CapabilityRevocation"] = {}
_lease_objects: Dict[tuple[str, str], Dict[str, "CapabilityLease"]] = {}


@dataclass(frozen=True)
class CapabilityRevocation:
    """A manual-takeover fence on one capability.

    A revocation is not a transfer.  The revoked owner keeps the recorded
    ownership so that no second holder can be admitted while its work is still
    unwinding, and the capability stays fenced until that owner actually
    releases.  ``token`` is fresh per revocation so a revoked owner (and any
    listener) can tell this takeover apart from a later one.
    """

    capability: str
    owner_id: str
    token: str
    revoked_at: float


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
    """Drop one reference to ``owner_id``'s hold on ``capability``.

    Refcount-aware: while sibling leases for the same (capability, owner) are
    still running, ownership is retained, because the owner is still doing the
    work the capability was leased for.  Releasing the last reference drops both
    the ownership record and any takeover fence.
    """
    capability = _validate_capability(capability)
    released = False
    with _lock:
        revocation = _revocations.get(capability)
        if revocation is not None and revocation.owner_id == owner_id:
            # The fenced owner reported that it stopped. Unfence even though
            # ownership was already dropped, so a takeover cannot wedge a
            # capability whose holder does come back to release.
            _revocations.pop(capability, None)
            released = True
        elif _owners.get(capability) == owner_id:
            if _active_leases.get((capability, owner_id)):
                logger.debug(
                    "Capability release deferred: capability=%s owner=%s still has active leases",
                    capability,
                    owner_id,
                )
                return
            _owners.pop(capability, None)
            _active_leases.pop((capability, owner_id), None)
            _lease_objects.pop((capability, owner_id), None)
            _revocations.pop(capability, None)
            released = True
    if released:
        _wake((capability,))


def force_release(capability: str, owner_id: str) -> bool:
    """Break a lease record outright, ignoring any live lease objects.

    This is the repair path for a *positively proven dead* holder -- a task
    that terminated without releasing. It is deliberately separate from
    :func:`release`, which is cooperative and refcount-aware: cooperative
    callers (desktop/session, grounding) must never be able to strip a
    capability from work that is still running. Returns True only if this call
    is what dropped the ownership.
    """
    capability = _validate_capability(capability)
    with _lock:
        if _owners.get(capability) != owner_id:
            return False
        _owners.pop(capability, None)
        _active_leases.pop((capability, owner_id), None)
        _lease_objects.pop((capability, owner_id), None)
        _revocations.pop(capability, None)
    logger.warning(
        "Capability lease force-released: capability=%s owner=%s (refcount ignored)",
        capability,
        owner_id,
    )
    _wake((capability,))
    return True


def current_owner(capability: str) -> Optional[str]:
    """Return the current holder of ``capability``, or None when it is free."""
    capability = _validate_capability(capability)

    with _lock:
        return _owners.get(capability)


def register_takeover_listener(listener: Callable[[str, tuple[str, ...]], None]) -> None:
    """Register one process-wide callback for revoked capability ownership."""
    if not callable(listener):
        raise TypeError("Takeover listener must be callable")
    with _lock:
        _takeover_listeners.add(listener)


def unregister_takeover_listener(listener: Callable[[str, tuple[str, ...]], None]) -> None:
    """Remove one process-wide takeover callback, if registered."""
    with _lock:
        _takeover_listeners.discard(listener)


@dataclass
class CapabilityLease:
    manager: "CapabilityLeaseManager"
    capability: str
    owner_id: str
    lease_id: str
    # The loop this lease was acquired on. A lease whose loop has been closed
    # cannot still be running: the coroutine holding it died with the loop. That
    # is the one provable liveness signal the runtime has, so it is what decides
    # whether a takeover must fence or may release outright.
    loop: Optional[asyncio.AbstractEventLoop] = None
    _released: bool = False

    @property
    def can_still_run(self) -> bool:
        return self.loop is not None and not self.loop.is_closed()

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

    def get_all_leases(self) -> dict[str, str]:
        return self.snapshot()

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
                logger.info(
                    "Capability lease acquired: capability=%s owner=%s lease=%s",
                    capability,
                    owner_id,
                    lease.lease_id,
                )
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

    def _owner_work_can_still_run(self, capability: str, owner_id: str) -> bool:
        """True only when a lease held by ``owner_id`` is provably still able to run.

        A live event loop means the acquiring coroutine may still be mid-flight,
        so a takeover must fence instead of transferring. A closed loop, or no
        lease object at all (a legacy sync ``acquire()``), means there is no
        in-flight work to protect -- fencing there would only wedge a capability
        whose holder is already gone.
        """
        for lease in tuple(_lease_objects.get((capability, owner_id), {}).values()):
            if lease.can_still_run:
                return True
        return False

    def manual_takeover(self, capabilities: Iterable[str]) -> set[str]:
        """Revoke the named capabilities from their current holders.

        A takeover is a cancellation, not a transfer. When the revoked owner's
        work is provably still able to run, the capability is *fenced*: ownership
        stays recorded so no second holder is admitted and the revoked owner
        cannot resurrect control from a task that is still unwinding, and the
        capability is unfenced when that owner releases. Otherwise ownership is
        dropped outright, exactly as before -- there is no in-flight work to
        protect, and a permanent fence would be a wedge.

        Takeover listeners (and the physical-control halt the desktop detector
        performs first) are what actually cancel the work; the return value and
        the listener calls are unchanged.
        """
        requested = tuple(sorted({_validate_capability(value) for value in capabilities}))
        owners: dict[str, list[str]] = {}
        fenced: list[str] = []
        with _lock:
            for capability in requested:
                owner = _owners.get(capability)
                if owner is None:
                    continue
                owners.setdefault(owner, []).append(capability)
                if self._owner_work_can_still_run(capability, owner):
                    fenced.append(capability)
                    _revocations[capability] = CapabilityRevocation(
                        capability=capability,
                        owner_id=owner,
                        token=make_id(12),
                        revoked_at=time.monotonic(),
                    )
                else:
                    _owners.pop(capability, None)
                    _active_leases.pop((capability, owner), None)
                    _lease_objects.pop((capability, owner), None)
                    _revocations.pop(capability, None)
            listeners = tuple(_takeover_listeners)
        _wake(requested)
        for owner, resources in owners.items():
            callbacks = list(listeners)
            if self._on_takeover is not None and self._on_takeover not in callbacks:
                callbacks.append(self._on_takeover)
            for callback in callbacks:
                try:
                    callback(owner, tuple(resources))
                except Exception:
                    logger.warning("Capability takeover listener failed", exc_info=True)
        for owner, resources in sorted(owners.items()):
            held = [cap for cap in resources if cap in fenced]
            if held:
                logger.warning(
                    "Capability takeover fenced: owner=%s capabilities=%s (no new holder admitted "
                    "until the revoked owner releases)",
                    owner,
                    ",".join(held),
                )
            dropped = [cap for cap in resources if cap not in fenced]
            if dropped:
                logger.warning(
                    "Capability takeover released: owner=%s capabilities=%s (no in-flight work to protect)",
                    owner,
                    ",".join(dropped),
                )
        return set(owners)

    def revocation(self, capability: str) -> Optional[CapabilityRevocation]:
        """Return the active takeover fence for ``capability``, if any."""
        capability = _validate_capability(capability)
        with _lock:
            return _revocations.get(capability)

    def is_revoked(self, capability: str) -> bool:
        return self.revocation(capability) is not None

    def _try_acquire(self, capability: str, owner_id: str) -> Optional[CapabilityLease]:
        lease_id = f"{owner_id}:{make_id(8)}"
        try:
            loop: Optional[asyncio.AbstractEventLoop] = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        with _lock:
            revocation = _revocations.get(capability)
            if revocation is not None:
                logger.info(
                    "Capability lease refused: capability=%s owner=%s fenced by takeover token=%s",
                    capability,
                    owner_id,
                    revocation.token,
                )
                return None
            current = _owners.get(capability)
            if current is not None and current != owner_id:
                return None
            lease = CapabilityLease(self, capability, owner_id, lease_id, loop)
            _owners[capability] = owner_id
            _active_leases.setdefault((capability, owner_id), set()).add(lease_id)
            _lease_objects.setdefault((capability, owner_id), {})[lease_id] = lease
            return lease

    def _release_lease(self, lease: CapabilityLease) -> None:
        released = False
        with _lock:
            active = _active_leases.get((lease.capability, lease.owner_id))
            objects = _lease_objects.get((lease.capability, lease.owner_id))
            if active is not None:
                active.discard(lease.lease_id)
                if objects is not None:
                    objects.pop(lease.lease_id, None)
                    if not objects:
                        _lease_objects.pop((lease.capability, lease.owner_id), None)
                if not active:
                    _active_leases.pop((lease.capability, lease.owner_id), None)
                    _lease_objects.pop((lease.capability, lease.owner_id), None)
                    if _owners.get(lease.capability) == lease.owner_id:
                        _owners.pop(lease.capability, None)
                        _revocations.pop(lease.capability, None)
                        released = True
            elif _owners.get(lease.capability) == lease.owner_id:
                _owners.pop(lease.capability, None)
                _lease_objects.pop((lease.capability, lease.owner_id), None)
                _revocations.pop(lease.capability, None)
                released = True
        if released:
            logger.info(
                "Capability lease released: capability=%s owner=%s lease=%s",
                lease.capability,
                lease.owner_id,
                lease.lease_id,
            )
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


def get_capability_lease_manager() -> CapabilityLeaseManager:
    """Return process-wide canonical capability lease authority."""
    return default_lease_manager


def get_all_leases() -> Dict[str, str]:
    """Return a snapshot of all active capability owners."""
    with _lock:
        return dict(_owners)


def get_revocations() -> Dict[str, CapabilityRevocation]:
    """Return a snapshot of capabilities currently fenced by a manual takeover."""
    with _lock:
        return dict(_revocations)


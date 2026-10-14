"""One warm Cua runtime per Charlie process, reached from synchronous tools.

Cua's Python SDK is async; Charlie's desktop and browser tools are sync. This module
owns one worker thread running one event loop for the life of the process, holds one
warm :class:`CuaDriver` inside it, and lets sync callers submit coroutines with
:func:`asyncio.run_coroutine_threadsafe`.

Rules this satisfies: one warm driver, one loop, a bounded timeout on every call, and
a refusal when the call comes from the worker thread itself (blocking it would
deadlock the loop that must run the call). Teardown closes the driver, stops the loop
and joins the thread.

Charlie policy, approval and lease checks all run before a coroutine is submitted,
and the driver is bounded at construction. Cua authorization is never routed back
into Charlie's loop, which is what keeps a blocked loop from becoming a deadlock.
"""

from __future__ import annotations

import asyncio
import inspect
import threading
import time
from concurrent.futures import Future
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Iterator, Optional, TypeVar

__all__ = [
    "CuaBridgeError",
    "CuaBridgeTakeover",
    "CuaBridgeTimeout",
    "CuaBridgeUnavailable",
    "CuaBridgeLoopThreadRefused",
    "CuaRuntimeBridge",
]

T = TypeVar("T")


@dataclass(eq=False)
class _PendingCall:
    exited: threading.Event = field(default_factory=threading.Event)
    cancel_requested: bool = False
    task: Optional[asyncio.Task[Any]] = None

# Bounded by default: a UI action that has not answered within this many seconds is
# treated as failed rather than allowed to block a Charlie tool indefinitely.
DEFAULT_TIMEOUT_S = 15.0

# Startup budget for the driver process itself. Kept separate from the per-call
# timeout because this one is a cold-start cost, not an action latency.
DEFAULT_STARTUP_TIMEOUT_S = 30.0

# Takeover is sampled while an action is in flight. The bounded wait is sliced rather
# than blocked, so detection cancels the action instead of being noticed only when the
# call times out. One GetLastInputInfo per slice is cheap; a slice never elapses
# without the wait itself blocking, so this is not a spin.
TAKEOVER_POLL_INTERVAL_S = 0.05

# Cua tool names that dispatch physical input or mutate the target window. Only these
# arm takeover watching and are refused outright once takeover is latched.
_NATIVE_MUTATION_TOOLS = frozenset(
    {
        "bring_to_front", "invoke_menu", "double_click", "move_cursor", "click",
        "drag", "scroll", "set_window_frame", "set_value", "type_text",
        "press_key", "hotkey",
    }
)


@contextmanager
def _charlie_input() -> Iterator[None]:
    """Attribute every host input tick produced inside this block to Charlie.

    Windows exposes one shared input tick for injected and physical input alike, so a
    long type_text or drag would otherwise read back as the user seizing the mouse and
    latch takeover for the life of the process.

    This brackets the dispatch itself and must stay on the worker thread: the caller's
    blocking wait is not dispatch, and closing the bracket only when the whole submit
    returns would leave the post-dispatch takeover sample running while Charlie is
    still marked as dispatching, which mutes the sample that exists to catch exactly
    that.
    """
    from charlie.desktop.takeover import user_takeover_detector  # noqa: PLC0415

    user_takeover_detector.begin_self_input()
    try:
        yield
    finally:
        user_takeover_detector.note_self_input()


class CuaBridgeError(Exception):
    """Base class for every bridge failure surfaced to Charlie."""


class CuaBridgeUnavailable(CuaBridgeError):
    """The runtime is absent, failed to start, or has been shut down."""


class CuaBridgeTimeout(CuaBridgeError):
    """A submitted action did not complete inside its bounded timeout."""


class CuaBridgeTakeover(CuaBridgeTimeout):
    """The user took physical input while the action was in flight.

    A ``CuaBridgeTimeout`` because that is the truthful envelope for an action that
    stopped without a confirmed end state: it may have partly applied, and callers
    already report that as executed-but-not-verified rather than success.
    """


class CuaBridgeLoopThreadRefused(CuaBridgeError):
    """A call was submitted from the Cua worker thread and would deadlock."""


async def _ImmediateDriver(driver: Any) -> Any:
    """Adapt a synchronously constructed driver to the awaited construction path."""
    return driver


class CuaRuntimeBridge:
    """Owns one warm Cua runtime on a dedicated thread for the process lifetime."""

    def __init__(
        self,
        *,
        driver_factory: Optional[Callable[[], Any]] = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        startup_timeout_s: float = DEFAULT_STARTUP_TIMEOUT_S,
        scope: str = "native",
        origins: Optional[Any] = None,
        window_target: Optional[dict[str, int]] = None,
    ) -> None:
        # Injected so tests can supply a fake Cua protocol. Production passes None and
        # the real CuaDriver.create is imported lazily, so importing Charlie does not
        # require Cua to be installed.
        self._driver_factory = driver_factory
        self._timeout_s = float(timeout_s)
        self._startup_timeout_s = float(startup_timeout_s)
        # Which bounded ceiling this runtime is pinned to. Native application control
        # and origin-scoped browsing need different ceilings, and Cua refuses to
        # combine them, so they cannot share one runtime.
        self._scope = str(scope)
        # Origins are compiled into the manifest, so an origin-scoped runtime can
        # only be built for the origins its task already declares.
        self._origins = tuple(origins) if origins else None
        self._window_target = dict(window_target) if window_target is not None else None

        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._driver: Any = None
        self._startup_error: Optional[BaseException] = None
        self._shutdown_error: Optional[str] = None
        self._loop_ready = threading.Event()
        self._shutdown = False
        self._last_activity_at: Optional[float] = None
        self._pending_calls: set[_PendingCall] = set()
        self._driver_close_future: Optional[Future[Any]] = None
        self._driver_closed = False

    # ------------------------------------------------------------------ lifecycle

    @property
    def is_available(self) -> bool:
        """True only when a live driver is reachable on the worker loop."""
        with self._lock:
            return bool(self._driver is not None and not self._shutdown)

    @property
    def is_idle_expired(self) -> bool:
        from .manifest import IDLE_TIMEOUT_SECONDS

        with self._lock:
            return bool(
                self._driver is not None
                and not self._shutdown
                and self._last_activity_at is not None
                and time.monotonic() - self._last_activity_at >= IDLE_TIMEOUT_SECONDS
            )

    @property
    def driver(self) -> Any:
        """The warm driver. Only valid for reads of identity metadata."""
        with self._lock:
            return self._driver

    def start(self) -> bool:
        """Start the worker thread and the single warm driver.

        Idempotent. Returns True when a driver is available afterwards. A startup
        failure is recorded rather than raised, so Charlie can degrade to its
        deterministic adapters instead of failing to boot.
        """
        with self._lock:
            if self._driver is not None:
                return not self._shutdown
            if self._shutdown:
                raise CuaBridgeUnavailable("bridge has been shut down")
            if self._thread is None:
                self._loop = asyncio.new_event_loop()
                self._thread = threading.Thread(
                    target=self._serve,
                    name="CuaRuntimeBridge",
                    daemon=True,
                )
                self._thread.start()
        started = self._loop_ready.wait(self._startup_timeout_s)
        with self._lock:
            return bool(started and self._driver is not None and not self._shutdown)

    @property
    def startup_error(self) -> Optional[BaseException]:
        """Why the runtime failed to come up, if it did."""
        return self._startup_error

    def _serve(self) -> None:
        """Body of the worker thread: own the loop and the driver."""
        loop = self._loop
        assert loop is not None
        asyncio.set_event_loop(loop)
        # Create the single warm driver *on this loop* before announcing readiness,
        # so the first caller never races a half-built runtime.
        try:
            driver = loop.run_until_complete(self._create_driver())
            with self._lock:
                self._driver = driver
                self._last_activity_at = time.monotonic()
        except BaseException as exc:  # noqa: BLE001 - recorded, not raised
            with self._lock:
                self._startup_error = exc
                if self._shutdown:
                    self._shutdown_error = f"driver startup interrupted during shutdown: {type(exc).__name__}"
        if self._driver is None:
            with self._lock:
                if self._startup_error is not None and self._shutdown:
                    self._shutdown_error = f"driver startup failed during shutdown: {type(self._startup_error).__name__}"
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()
            self._loop_ready.set()
            return
        loop.call_soon(self._loop_ready.set)
        try:
            loop.run_forever()
        finally:
            try:
                # Drain anything already queued so the driver gets a clean stop.
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
            except Exception:
                pass
            try:
                loop.close()
            except Exception:
                pass

    def shutdown(self, *, timeout_s: float = 10.0) -> bool:
        """Shut the driver down, stop the loop, and join the worker.

        Safe to call more than once. Returns false when driver close or worker join
        cannot be confirmed within one shared deadline.
        """
        deadline = time.monotonic() + max(0.0, float(timeout_s))
        current_thread = threading.current_thread()
        with self._lock:
            if self._thread is current_thread:
                self._shutdown = True
                self._shutdown_error = "shutdown requested from the Cua worker thread"
                return False
            if self._shutdown and self._thread is None and self._driver is None:
                return self._shutdown_error is None
            self._shutdown = True
            pending_calls = list(self._pending_calls)
            loop = self._loop
            thread = self._thread

        for pending in pending_calls:
            self._cancel_pending(pending)
        for pending in pending_calls:
            if not pending.exited.wait(timeout=max(0.0, deadline - time.monotonic())):
                with self._lock:
                    self._shutdown_error = "admitted Cua calls did not exit before shutdown deadline"
                return False

        if thread is not None and not self._loop_ready.wait(timeout=max(0.0, deadline - time.monotonic())):
            with self._lock:
                self._shutdown_error = "driver startup did not finish before shutdown deadline"
            return False

        with self._lock:
            loop = self._loop
            thread = self._thread
            driver = self._driver
            if driver is None and self._startup_error is not None:
                self._shutdown_error = f"driver startup failed without a close handle: {type(self._startup_error).__name__}"
                return False
            close_future = self._driver_close_future
            if driver is not None and not self._driver_closed and (
                close_future is None
                or (close_future.done() and (close_future.cancelled() or close_future.exception() is not None))
            ):
                if loop is None or loop.is_closed():
                    self._shutdown_error = "driver close loop is unavailable"
                    return False
                try:
                    close_future = asyncio.run_coroutine_threadsafe(self._close_driver(driver), loop)
                except RuntimeError as exc:
                    self._shutdown_error = f"driver close scheduling failed: {type(exc).__name__}"
                    return False
                self._driver_close_future = close_future

        if close_future is not None and not self._driver_closed:
            try:
                close_future.result(timeout=max(0.0, deadline - time.monotonic()))
            except TimeoutError:
                with self._lock:
                    self._shutdown_error = "driver close did not finish before shutdown deadline"
                return False
            except Exception as exc:
                with self._lock:
                    self._shutdown_error = f"driver close failed: {type(exc).__name__}"
                return False
            with self._lock:
                self._driver_closed = True
                self._shutdown_error = None

        with self._lock:
            loop = self._loop
            thread = self._thread
        if loop is not None:
            try:
                loop.call_soon_threadsafe(loop.stop)
            except Exception:
                pass
        if thread is not None:
            thread.join(timeout=max(0.0, deadline - time.monotonic()))
        if thread is not None and thread.is_alive():
            with self._lock:
                self._shutdown_error = "Cua worker did not stop before shutdown deadline"
            return False
        with self._lock:
            if self._driver is not None and not self._driver_closed:
                self._shutdown_error = "driver close was not confirmed"
                return False
            self._loop = None
            self._thread = None
            self._driver = None
            self._driver_close_future = None
            self._driver_closed = False
            self._shutdown_error = None
            return True

    @staticmethod
    async def _close_driver(driver: Any) -> None:
        closer = getattr(driver, "shutdown", None)
        if not callable(closer):
            raise CuaBridgeUnavailable("Cua driver does not expose shutdown")
        result = closer()
        if inspect.isawaitable(result):
            await result

    # --------------------------------------------------------------------- submit

    def _schedule_locked(self, loop: asyncio.AbstractEventLoop, factory: Callable[[], Any]):
        pending = _PendingCall()

        async def execute() -> Any:
            task = asyncio.current_task()
            with self._lock:
                pending.task = task
                cancel_requested = pending.cancel_requested
            try:
                if cancel_requested:
                    raise asyncio.CancelledError
                with _charlie_input():
                    result = factory()
                    if inspect.isawaitable(result):
                        result = await result
                with self._lock:
                    self._last_activity_at = time.monotonic()
                return result
            finally:
                pending.exited.set()
                with self._lock:
                    self._pending_calls.discard(pending)

        self._pending_calls.add(pending)
        coroutine = execute()
        try:
            future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        except Exception:
            coroutine.close()
            pending.exited.set()
            self._pending_calls.discard(pending)
            raise
        return pending, future

    def _cancel_pending(self, pending: _PendingCall) -> None:
        with self._lock:
            if pending.exited.is_set():
                return
            pending.cancel_requested = True
            task = pending.task
            loop = self._loop
        if task is not None and loop is not None:
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass

    def submit(
        self,
        factory: Callable[[], Coroutine[Any, Any, T]],
        *,
        timeout_s: Optional[float] = None,
        mutation: bool = False,
    ) -> T:
        """Run ``factory()``'s coroutine on the worker loop and await the result.

        ``factory`` rather than a coroutine object on purpose: the coroutine must be
        created *inside* the worker loop, never on the caller's. ``mutation`` asks for
        physical-input takeover to be watched for the duration of the call.
        """
        if threading.current_thread() is self._thread:
            raise CuaBridgeLoopThreadRefused(
                "a Cua call was submitted from the Cua worker thread; blocking it "
                "would deadlock the loop that must run the call"
            )
        bound = self._timeout_s if timeout_s is None else float(timeout_s)
        with self._lock:
            if self._shutdown:
                raise CuaBridgeUnavailable("bridge has been shut down")
            loop = self._loop
            if self._driver is None:
                raise CuaBridgeUnavailable(
                    "Cua runtime is not available; the deterministic adapter remains "
                    "authoritative for this action"
                )
            if loop is None or loop.is_closed() or not loop.is_running():
                raise CuaBridgeUnavailable("Cua worker loop is not running")
            pending, future = self._schedule_locked(loop, factory)
        return self._await_bounded(future, pending, bound, mutation=mutation)

    def _await_bounded(
        self, future: Future[Any], pending: _PendingCall, bound: float, *,
        mutation: bool,
    ) -> Any:
        """Await ``future`` inside ``bound``, sampling takeover while it is in flight.

        The wait is sliced so a takeover interrupts the action instead of surfacing
        only at the bound. Detection cancels exactly like a timeout does, because an
        abandoned coroutine can still be dispatching input after the caller gave up.
        One more sample runs once dispatch has ended, while the session is still armed.
        """
        deadline = time.monotonic() + bound
        detector = self._takeover_detector()
        watching = self._arm_takeover_watch(detector, mutation)
        try:
            while True:
                slice_s = max(
                    0.0, min(deadline - time.monotonic(), TAKEOVER_POLL_INTERVAL_S)
                )
                try:
                    return future.result(timeout=slice_s)
                except TimeoutError as exc:
                    # A future that completed *with* a TimeoutError lands here too.
                    if future.done():
                        return future.result()
                    # Takeover outranks expiry: sample before deciding it is a timeout.
                    if self._takeover_observed(detector):
                        self._cancel_pending(pending)
                        raise CuaBridgeTakeover(
                            "user took physical input while the Cua action was in "
                            "flight; the action was cancelled and its effect is "
                            "unconfirmed"
                        ) from exc
                    if time.monotonic() >= deadline:
                        self._cancel_pending(pending)
                        raise CuaBridgeTimeout(
                            f"Cua action did not complete within {bound:.1f}s"
                        ) from exc
        finally:
            if watching:
                # Post-dispatch sample: the first moment Charlie is not dispatching, so
                # the newest input tick is one a human cannot have overwritten. Recorded,
                # never raised -- the action's own result is the truthful outcome here, and
                # raising from a finally would replace a real timeout with a takeover.
                self._takeover_observed(detector)
                self._release_takeover_watch(detector)

    @staticmethod
    def _takeover_detector() -> Any:
        """The shared detector, or None where this host has no takeover concept."""
        try:
            from charlie.desktop.takeover import user_takeover_detector  # noqa: PLC0415
        except Exception:
            return None
        return user_takeover_detector

    def _arm_takeover_watch(self, detector: Any, mutation: bool) -> bool:
        """Arm the physical-input session when this call dispatches input.

        core.py already brackets desktop tools, and an armed session there is left
        untouched. Arming is skipped once takeover is latched so the pre-dispatch
        refusal stays authoritative instead of being cleared by a new session.
        """
        if not mutation or self._scope != "native" or detector is None:
            return False
        try:
            status = detector.status()
            if status.active or status.takeover_detected:
                return False
            detector.start_session("cua_native_dispatch")
        except Exception:
            return False
        return True

    @staticmethod
    def _release_takeover_watch(detector: Any) -> None:
        if detector is None:
            return
        try:
            detector.end_session()
        except Exception:
            pass

    @staticmethod
    def _takeover_observed(detector: Any) -> bool:
        """Sample the armed session. Real detection latches inside the detector."""
        if detector is None:
            return False
        try:
            return bool(detector.check_takeover())
        except Exception:
            return False

    async def call(self, name: str, arguments_json: str) -> Any:
        """Invoke a Cua tool by name on the warm driver.

        Used only where Cua exposes no typed method. Typed methods are preferred
        because they return typed objects rather than a JSON envelope.
        """
        driver = self.driver
        if driver is None:
            raise CuaBridgeUnavailable("Cua runtime is not available")
        result = driver.call_tool(name, arguments_json)
        if asyncio.iscoroutine(result):
            return await result
        return result

    def call_tool(
        self, name: str, arguments_json: str, *, timeout_s: Optional[float] = None
    ) -> Any:
        """Synchronous entry point for :meth:`call`."""
        mutation = name in _NATIVE_MUTATION_TOOLS
        if mutation:
            self._ensure_native_mutation_admitted()
        return self.submit(
            lambda: self.call(name, arguments_json),
            timeout_s=timeout_s,
            mutation=mutation,
        )

    def _ensure_native_mutation_admitted(self) -> None:
        if self._scope != "native":
            return
        from charlie.desktop.takeover import user_takeover_detector

        # Latched by _await_bounded when a takeover interrupts an in-flight action.
        if user_takeover_detector.status().takeover_detected:
            raise CuaBridgeUnavailable(
                "native Cua dispatch refused after user takeover was detected"
            )

    def call_typed(
        self, factory: Callable[[Any], Coroutine[Any, Any, T]], *,
        timeout_s: float | None = None, mutation: bool = False,
    ) -> T:
        """Run a *typed* Cua SDK method on the worker loop.

        ``factory`` receives the warm driver and must return the coroutine, so the
        typed ``Input`` object is constructed inside the loop.
        """
        if mutation:
            self._ensure_native_mutation_admitted()
        if threading.current_thread() is self._thread:
            raise CuaBridgeLoopThreadRefused(
                "a Cua call was submitted from the Cua worker thread; blocking it "
                "would deadlock the loop that must run the call"
            )
        bound = self._timeout_s if timeout_s is None else float(timeout_s)
        with self._lock:
            if self._shutdown:
                raise CuaBridgeUnavailable("bridge has been shut down")
            driver = self._driver
            loop = self._loop
            if driver is None:
                raise CuaBridgeUnavailable("Cua runtime is not available")
            if loop is None or loop.is_closed() or not loop.is_running():
                raise CuaBridgeUnavailable("Cua worker loop is not running")
            pending, future = self._schedule_locked(loop, lambda: factory(driver))
        return self._await_bounded(future, pending, bound, mutation=mutation)

    async def _create_driver(self) -> Any:
        factory = self._driver_factory
        if factory is not None:
            driver = factory()
            if asyncio.iscoroutine(driver):
                driver = await driver
            return driver
        # Imported lazily so Charlie still imports when Cua is not installed.
        import cua_driver  # noqa: PLC0415

        driver = self._build_bounded_driver(
            cua_driver, self._scope, self._origins, self._window_target
        )
        if asyncio.iscoroutine(driver):
            driver = await driver
        return driver

    @staticmethod
    def _build_bounded_driver(
        cua_driver: Any,
        scope: str = "native",
        origins: Optional[Any] = None,
        window_target: Optional[dict[str, int]] = None,
    ) -> Any:
        """Create the runtime bounded, under an exact capability ceiling.

        There is deliberately no fallback to ``CuaDriver.create()``. A bounded
        configuration that cannot be built is a hard failure: silently starting a
        standard runtime would hand Charlie broader authority than it claims to hold,
        which is worse than reporting the capability unavailable.

        No Python authorization host is attached. Cua only consults one for a residual
        boundary that requires a host grant, and it has no public constructor in the
        Python SDK; every in-manifest bounded operation bypasses it. A residual
        boundary therefore stays refused, which is the behaviour Charlie wants.
        """
        from .manifest import scope_manifest_path  # noqa: PLC0415

        if scope == "native" and window_target is None:
            raise CuaBridgeUnavailable(
                "native bounded Cua requires an exact window target selected before startup"
            )
        manifest = scope_manifest_path(
            scope, origins=origins, window_target=window_target
        )
        if not manifest:
            raise CuaBridgeUnavailable(
                f"{scope} bounded capability manifest could not be written; refusing to "
                "start a runtime without a declared tool/resource ceiling"
            )

        options = cua_driver.ConfiguredDriverOptions(
            claude_code_compatibility=False,
            authorization=cua_driver.RuntimeAuthorizationOptions(
                allowed_modes=[cua_driver.SessionPermissionMode.BOUNDED],
                compatibility_mode=cua_driver.SessionPermissionMode.BOUNDED,
                compatibility_capability_manifest_path=manifest,
                compatibility_bounded_manifest_path=manifest,
                unrestricted_acknowledged=False,
                max_session_ttl_seconds=1800,
                max_idle_ttl_seconds=300,
            ),
        )

        factory = getattr(cua_driver.CuaDriver, "create_configured", None)
        if factory is None:
            raise CuaBridgeUnavailable(
                "cua-driver does not expose create_configured; refusing to start a "
                "runtime that cannot be pinned to a bounded manifest"
            )
        driver = factory(options)
        if asyncio.iscoroutine(driver):
            return driver
        # This Cua build constructs synchronously and hands back the driver.
        # Accept that, but only if it really looks like a driver: the point of this
        # check is to catch a wrong-shaped return, not to re-litigate the mode.
        if driver is None or not hasattr(driver, "call_tool"):
            raise CuaBridgeUnavailable(
                "bounded driver construction returned an unexpected value"
            )
        return _ImmediateDriver(driver)

    def ensure_started(self) -> bool:
        """Start if needed and report availability. Used by the composition root."""
        if self.is_available:
            return True
        return self.start()

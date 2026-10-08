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
import threading
from typing import Any, Callable, Coroutine, Optional, TypeVar

__all__ = [
    "CuaBridgeError",
    "CuaBridgeTimeout",
    "CuaBridgeUnavailable",
    "CuaBridgeLoopThreadRefused",
    "CuaRuntimeBridge",
]

T = TypeVar("T")

# Bounded by default: a UI action that has not answered within this many seconds is
# treated as failed rather than allowed to block a Charlie tool indefinitely.
DEFAULT_TIMEOUT_S = 15.0

# Startup budget for the driver process itself. Kept separate from the per-call
# timeout because this one is a cold-start cost, not an action latency.
DEFAULT_STARTUP_TIMEOUT_S = 30.0


class CuaBridgeError(Exception):
    """Base class for every bridge failure surfaced to Charlie."""


class CuaBridgeUnavailable(CuaBridgeError):
    """The runtime is absent, failed to start, or has been shut down."""


class CuaBridgeTimeout(CuaBridgeError):
    """A submitted action did not complete inside its bounded timeout."""


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
        self._loop_ready = threading.Event()
        self._shutdown = False

    # ------------------------------------------------------------------ lifecycle

    @property
    def is_available(self) -> bool:
        """True only when a live driver is reachable on the worker loop."""
        with self._lock:
            return bool(self._driver is not None and not self._shutdown)

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
                return True
            if self._shutdown:
                raise CuaBridgeUnavailable("bridge has been shut down")
            if self._thread is not None:
                # A previous start attempt is still in flight.
                started = self._loop_ready.wait(self._startup_timeout_s)
                return bool(started and self._driver is not None)

            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(
                target=self._serve,
                name="CuaRuntimeBridge",
                daemon=True,
            )
            self._thread.start()

        self._loop_ready.wait(self._startup_timeout_s)
        with self._lock:
            return self._driver is not None

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
                if self._shutdown:
                    # Shut down while we were starting: close it here and stop.
                    self._startup_error = CuaBridgeUnavailable(
                        "bridge was shut down during startup"
                    )
                else:
                    self._driver = driver
        except BaseException as exc:  # noqa: BLE001 - recorded, not raised
            with self._lock:
                self._startup_error = exc
        self._loop_ready.set()
        if self._driver is None:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()
            return
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

    def shutdown(self, *, timeout_s: float = 10.0) -> None:
        """Shut the driver down, stop the loop, and join the worker.

        Safe to call more than once. Never raises: teardown must not mask the
        shutdown reason.
        """
        with self._lock:
            if self._shutdown:
                return
            self._shutdown = True
            loop = self._loop
            driver = self._driver
            thread = self._thread
            self._driver = None

        if loop is not None and driver is not None:
            try:
                asyncio.run_coroutine_threadsafe(
                    self._close_driver(driver), loop
                ).result(timeout=timeout_s)
            except Exception:
                pass

        if loop is not None:
            try:
                loop.call_soon_threadsafe(loop.stop)
            except Exception:
                pass
        if thread is not None:
            thread.join(timeout=timeout_s)

        with self._lock:
            self._loop = None
            self._thread = None

    @staticmethod
    async def _close_driver(driver: Any) -> None:
        closer = getattr(driver, "shutdown", None)
        if closer is None:
            return
        result = closer()
        if asyncio.iscoroutine(result):
            await result

    # --------------------------------------------------------------------- submit

    def submit(
        self,
        factory: Callable[[], Coroutine[Any, Any, T]],
        *,
        timeout_s: Optional[float] = None,
    ) -> T:
        """Run ``factory()``'s coroutine on the worker loop and await the result.

        ``factory`` rather than a coroutine object on purpose: the coroutine must be
        created *inside* the worker loop, never on the caller's.
        """
        if threading.current_thread() is self._thread:
            raise CuaBridgeLoopThreadRefused(
                "a Cua call was submitted from the Cua worker thread; blocking it "
                "would deadlock the loop that must run the call"
            )
        with self._lock:
            loop = self._loop
            driver = self._driver
            shutdown = self._shutdown
        if shutdown:
            raise CuaBridgeUnavailable("bridge has been shut down")
        if driver is None:
            raise CuaBridgeUnavailable(
                "Cua runtime is not available; the deterministic adapter remains "
                "authoritative for this action"
            )
        if loop is None or loop.is_closed() or not loop.is_running():
            raise CuaBridgeUnavailable("Cua worker loop is not running")

        bound = self._timeout_s if timeout_s is None else float(timeout_s)
        future = asyncio.run_coroutine_threadsafe(factory(), loop)
        try:
            return future.result(timeout=bound)
        except asyncio.TimeoutError as exc:
            # Abandon the coroutine rather than leave it running: an orphaned Cua
            # action can still be dispatching input after the caller gave up.
            future.cancel()
            raise CuaBridgeTimeout(
                f"Cua action did not complete within {bound:.1f}s"
            ) from exc

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
        return self.submit(
            lambda: self.call(name, arguments_json), timeout_s=timeout_s
        )

    def call_typed(
        self, factory: Callable[[Any], Coroutine[Any, Any, T]], *, timeout_s: float | None = None
    ) -> T:
        """Run a *typed* Cua SDK method on the worker loop.

        ``factory`` receives the warm driver and must return the coroutine, so the
        typed ``Input`` object is constructed inside the loop.
        """
        if threading.current_thread() is self._thread:
            raise CuaBridgeLoopThreadRefused(
                "a Cua call was submitted from the Cua worker thread; blocking it "
                "would deadlock the loop that must run the call"
            )
        with self._lock:
            driver = self._driver
            loop = self._loop
            shutdown = self._shutdown
        if shutdown:
            raise CuaBridgeUnavailable("bridge has been shut down")
        if driver is None:
            raise CuaBridgeUnavailable("Cua runtime is not available")
        if loop is None or loop.is_closed() or not loop.is_running():
            raise CuaBridgeUnavailable("Cua worker loop is not running")
        bound = self._timeout_s if timeout_s is None else float(timeout_s)
        future = asyncio.run_coroutine_threadsafe(factory(driver), loop)
        try:
            return future.result(timeout=bound)
        except asyncio.TimeoutError as exc:
            future.cancel()
            raise CuaBridgeTimeout(
                f"Cua action did not complete within {bound:.1f}s"
            ) from exc

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

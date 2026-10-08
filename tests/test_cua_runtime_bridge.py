"""Behavioural tests for the Cua runtime bridge against a fake Cua protocol.

These prove the bridge's own contract: one warm driver, one loop, bounded calls,
loop-thread refusal, and clean teardown. They deliberately do not assert anything
about Cua's behaviour, which is Cua's contract and is covered by real-host evidence.

Evidence class: TEST/MOCK.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from charlie.computer.cua_bridge import (
    CuaBridgeLoopThreadRefused,
    CuaBridgeTimeout,
    CuaBridgeUnavailable,
    CuaRuntimeBridge,
)


class FakeDriver:
    """Minimal stand-in for CuaDriver: async methods, sync close."""

    def __init__(self) -> None:
        self.shutdown_calls = 0
        self.thread_ids: list[int] = []
        self.loop_ids: list[int] = []

    async def call_tool(self, name: str, arguments_json: str):
        self.thread_ids.append(threading.get_ident())
        self.loop_ids.append(id(asyncio.get_running_loop()))
        return {"tool": name, "args": arguments_json}

    async def shutdown(self) -> None:
        self.shutdown_calls += 1


def _bridge(**kwargs) -> CuaRuntimeBridge:
    return CuaRuntimeBridge(driver_factory=FakeDriver, **kwargs)


class TestWarmRuntimeLifecycle:
    def test_start_creates_exactly_one_driver(self):
        bridge = _bridge()
        try:
            assert bridge.start() is True
            driver = bridge.driver
            assert driver is not None
            # Repeated availability checks must not spawn another runtime.
            assert bridge.ensure_started() is True
            assert bridge.ensure_started() is True
            assert bridge.driver is driver, "the runtime must stay warm, not be recreated"
        finally:
            bridge.shutdown()

    def test_one_loop_is_shared_across_calls(self):
        bridge = _bridge()
        try:
            bridge.start()
            for _ in range(4):
                bridge.call_tool("list_windows", "{}")
            driver = bridge.driver
            assert len(set(driver.loop_ids)) == 1, "every action must share one loop"
            assert len(set(driver.thread_ids)) == 1, "every action must run on one thread"
        finally:
            bridge.shutdown()

    def test_shutdown_closes_the_driver_and_is_idempotent(self):
        bridge = _bridge()
        bridge.start()
        driver = bridge.driver
        bridge.shutdown()
        bridge.shutdown()  # must not raise
        assert driver.shutdown_calls == 1
        assert bridge.is_available is False

    def test_submit_after_shutdown_is_refused(self):
        bridge = _bridge()
        bridge.start()
        bridge.shutdown()
        with pytest.raises(CuaBridgeUnavailable):
            bridge.call_tool("list_windows", "{}")


class TestInitializationFailure:
    def test_a_factory_failure_leaves_the_bridge_unavailable_not_crashing(self):
        def _boom():
            raise RuntimeError("cua-driver binary missing")

        bridge = CuaRuntimeBridge(driver_factory=_boom)
        try:
            assert bridge.start() is False
            assert bridge.is_available is False
            with pytest.raises(CuaBridgeUnavailable):
                bridge.call_tool("list_windows", "{}")
        finally:
            bridge.shutdown()

    def test_charlie_degrades_rather_than_raising(self):
        """An absent Cua must not break deterministic adapters."""

        def _boom():
            raise ImportError("No module named 'cua_driver'")

        bridge = CuaRuntimeBridge(driver_factory=_boom)
        try:
            assert bridge.ensure_started() is False
        finally:
            bridge.shutdown()


class TestBoundedTimeout:
    def test_a_hung_action_raises_instead_of_blocking_forever(self):
        started = threading.Event()

        class _Hang(FakeDriver):
            async def call_tool(self, name, arguments_json):
                started.set()
                await asyncio.sleep(30)

        bridge = CuaRuntimeBridge(driver_factory=_Hang, timeout_s=0.25)
        try:
            bridge.start()
            with pytest.raises(CuaBridgeTimeout):
                bridge.call_tool("click", "{}")
            assert started.is_set(), "the action must actually have been dispatched"
        finally:
            bridge.shutdown()

    def test_timeout_message_states_the_bound(self):
        class _Hang(FakeDriver):
            async def call_tool(self, name, arguments_json):
                await asyncio.sleep(30)

        bridge = CuaRuntimeBridge(driver_factory=_Hang, timeout_s=0.2)
        try:
            bridge.start()
            with pytest.raises(CuaBridgeTimeout, match="0.2"):
                bridge.call_tool("click", "{}")
        finally:
            bridge.shutdown()

    def test_a_per_call_timeout_overrides_the_default(self):
        bridge = _bridge(timeout_s=30.0)
        try:
            bridge.start()
            assert bridge.call_tool("list_windows", "{}", timeout_s=2.0) is not None
        finally:
            bridge.shutdown()


class TestLoopThreadRefusal:
    def test_a_call_from_the_cua_worker_thread_is_refused(self):
        """Blocking the worker on its own loop would deadlock it."""
        bridge = _bridge()
        captured: dict = {}
        try:
            bridge.start()

            async def _attempt():
                try:
                    bridge.call_tool("list_windows", "{}")
                except BaseException as exc:  # noqa: BLE001 - recording for assert
                    captured["error"] = exc

            # Run the attempt *on the worker loop* via the driver itself.
            bridge.submit(lambda: _attempt())
            assert isinstance(
                captured.get("error"), CuaBridgeLoopThreadRefused
            ), f"expected refusal, got {captured.get('error')!r}"
        finally:
            bridge.shutdown()

    def test_the_refusal_does_not_break_the_bridge(self):
        bridge = _bridge()
        try:
            bridge.start()
            bridge.call_tool("list_windows", "{}")
            assert bridge.is_available is True
        finally:
            bridge.shutdown()


class TestCallToolPassThrough:
    def test_tool_name_and_arguments_reach_the_driver(self):
        bridge = _bridge()
        try:
            bridge.start()
            out = bridge.call_tool("bring_to_front", '{"pid": 1234}')
            assert out == {"tool": "bring_to_front", "args": '{"pid": 1234}'}
        finally:
            bridge.shutdown()

    def test_no_call_runs_on_the_caller_thread(self):
        bridge = _bridge()
        try:
            bridge.start()
            bridge.call_tool("list_windows", "{}")
            assert bridge.driver.thread_ids[0] != threading.get_ident(), (
                "the coroutine must run on the worker, not the caller"
            )
        finally:
            bridge.shutdown()

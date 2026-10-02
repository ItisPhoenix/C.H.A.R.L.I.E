"""The approval bridge must fail closed, and must never deadlock the loop.

Evidence class: TEST/MOCK.
"""

from __future__ import annotations

import asyncio
import threading
import time

from charlie.self_extension.approval_bridge import (
    build_self_extension_approval_callback,
    is_on_loop_thread,
)


class _LoopThread:
    """Runs an event loop on a background thread so the caller is an executor thread."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        # Wait for the loop to actually be running before anyone submits to it.
        deadline = time.monotonic() + 5.0
        while not self.loop.is_running() and time.monotonic() < deadline:
            time.sleep(0.005)
        assert self.loop.is_running(), "background loop failed to start"

    def _serve(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def close(self):
        if self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=5)
        self.loop.close()


class TestLoopThreadDetection:
    def test_a_plain_thread_is_not_the_loop_thread(self):
        assert is_on_loop_thread() is False

    def test_the_loop_thread_is_detected(self):
        async def _check():
            return is_on_loop_thread()

        assert asyncio.run(_check()) is True

    def test_a_thread_whose_loop_lives_elsewhere_is_not_the_loop_thread(self):
        server = _LoopThread()
        try:
            seen = {}

            def _worker():
                seen["value"] = is_on_loop_thread()

            thread = threading.Thread(target=_worker)
            thread.start()
            thread.join(timeout=5)
            assert seen["value"] is False
        finally:
            server.close()


class TestTheBridgeRefusesOnTheLoopThread:
    def test_calling_from_the_loop_thread_returns_none(self):
        """Blocking the loop thread would deadlock; the bridge must refuse."""

        async def _owner(payload):
            raise AssertionError("the loop thread must never reach the owner")

        callback = build_self_extension_approval_callback(_owner)

        async def _run():
            return callback({"approval_binding": "abc"})

        assert asyncio.run(_run()) is None

    def test_the_owner_is_never_invoked_from_the_loop_thread(self):
        called = threading.Event()

        async def _owner(payload):
            called.set()
            return "abc"

        callback = build_self_extension_approval_callback(_owner)

        async def _run():
            return callback({"approval_binding": "abc"})

        assert asyncio.run(_run()) is None
        assert not called.is_set(), "the owner must not even be called"


class TestTheBridgeServesExecutorThreads:
    def test_an_approved_binding_reaches_an_executor_thread(self):
        async def _owner(payload):
            await asyncio.sleep(0)
            return payload["approval_binding"]

        server = _LoopThread()
        try:
            callback = build_self_extension_approval_callback(_owner, loop=server.loop)
            assert callback({"approval_binding": "digest-123"}) == "digest-123"
        finally:
            server.close()

    def test_a_refusal_returns_none(self):
        async def _owner(payload):
            await asyncio.sleep(0)
            return None

        server = _LoopThread()
        try:
            callback = build_self_extension_approval_callback(_owner, loop=server.loop)
            assert callback({"approval_binding": "digest-123"}) is None
        finally:
            server.close()

    def test_a_raising_owner_fails_closed(self):
        async def _owner(payload):
            await asyncio.sleep(0)
            raise RuntimeError("owner channel is down")

        server = _LoopThread()
        try:
            callback = build_self_extension_approval_callback(_owner, loop=server.loop)
            assert callback({"approval_binding": "d"}) is None, (
                "an owner failure must fail closed, not crash the tool"
            )
        finally:
            server.close()

    def test_a_closed_loop_returns_none(self):
        async def _owner(payload):  # pragma: no cover - never reached
            return "x"

        loop = asyncio.new_event_loop()
        loop.close()
        callback = build_self_extension_approval_callback(_owner, loop=loop)
        assert callback({"approval_binding": "d"}) is None

    def test_a_stopped_loop_returns_none(self):
        async def _owner(payload):  # pragma: no cover - never reached
            return "x"

        loop = asyncio.new_event_loop()
        callback = build_self_extension_approval_callback(_owner, loop=loop)
        try:
            assert callback({"approval_binding": "d"}) is None
        finally:
            loop.close()


class TestTheBridgeCannotWidenTheGrant:
    def test_the_callback_only_echoes_the_binding_it_was_shown(self):
        """The bridge returns whatever the owner resolved to, verbatim.

        Widening is prevented downstream: the orchestrator recomputes the digest
        from the argv it reviews and refuses a mismatch. The bridge's own
        obligation is narrower but real -- it must not substitute a binding of
        its own when the owner returns something else.
        """
        async def _owner(payload):
            await asyncio.sleep(0)
            return "a-different-binding"

        server = _LoopThread()
        try:
            callback = build_self_extension_approval_callback(_owner, loop=server.loop)
            granted = callback({"approval_binding": "shown-binding"})
            assert granted == "a-different-binding"
            assert granted != "shown-binding"
        finally:
            server.close()


if __name__ == "__main__":  # pragma: no cover
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))

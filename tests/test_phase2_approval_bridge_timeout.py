"""The approval bridge must not wedge the single gated-approval slot on timeout.

``concurrent.futures.Future.result(timeout=)`` never cancels the coroutine it was
waiting on.  An abandoned owner coroutine keeps awaiting inside
``charlie.core.Brain._request_tool_approval_decision``, holding
``pending_tool_approvals[request_id]`` and ``_active_tool_approval_id``.  Because
that slot is global and singular, every *later* gated tool call is then declined
``UNAVAILABLE`` -- for as long as the owner keeps waiting, which for a background
Brain (``approval_timeout=None``) is forever.

These tests drive the real ``Brain`` so the slot assertions are about the actual
runtime state, not a stand-in.

Evidence class: TEST/MOCK.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from charlie.core import (
    ApprovalDecision,
    Brain,
    get_active_tool_approval,
    pending_tool_approvals,
)
from charlie.self_extension.approval_bridge import (
    build_self_extension_approval_callback,
    resolve_approval_timeout,
)


class _LoopThread:
    """Runs an event loop on a background thread so the caller is an executor thread."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 5.0
        while not self.loop.is_running() and time.monotonic() < deadline:
            time.sleep(0.005)
        assert self.loop.is_running(), "background loop failed to start"

    def _serve(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def close(self) -> None:
        if self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=5)
        self.loop.close()


def _parking_brain() -> Brain:
    """A background Brain: the owner parks the approval forever by construction."""
    from charlie.config import Config

    return Brain(
        Config(llm_url="http://localhost:11434/v1", llm_key="test-key", llm_model="dummy"),
        on_tool_approval_request=lambda *_a, **_k: True,
        # A non-telegram approval channel is only considered available when the
        # Brain can speak to the owner; without it the slot is taken and released
        # in the same tick, which would mask the wedge this test is about.
        on_thought_callback=lambda *_a, **_k: None,
        approval_timeout=None,
        is_background=True,
    )


def _wait_for(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class TestBridgeTimeoutIsBounded:
    def test_the_bridge_gives_up_and_returns_none(self):
        """A silent owner must not turn into an unbounded block."""

        async def _owner(_payload):
            await asyncio.sleep(3600)

        server = _LoopThread()
        try:
            callback = build_self_extension_approval_callback(
                _owner, loop=server.loop, timeout=0.2
            )
            started = time.monotonic()
            assert callback({"approval_binding": "d"}) is None
            assert time.monotonic() - started < 5.0, "the bridge must return promptly"
        finally:
            server.close()

    def test_the_bridge_cancels_the_abandoned_owner_coroutine(self):
        """Timeout must cancel, not merely abandon: an abandoned coroutine keeps
        holding the owner's approval slot."""
        cancelled = threading.Event()

        async def _owner(_payload):
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        server = _LoopThread()
        try:
            callback = build_self_extension_approval_callback(
                _owner, loop=server.loop, timeout=0.2
            )
            assert callback({"approval_binding": "d"}) is None
            assert cancelled.wait(timeout=3.0), (
                "the owner coroutine was abandoned instead of cancelled"
            )
        finally:
            server.close()


class TestBridgeTimeoutReleasesTheApprovalSlot:
    @pytest.mark.asyncio
    async def test_a_parked_approval_does_not_wedge_the_next_gated_call(self):
        """The regression this guards: slot wedged -> every later gated call UNAVAILABLE.

        The owner coroutine here is the *real* one, so the slot is genuinely taken
        and its release is genuinely observed.
        """
        brain = _parking_brain()
        server = _LoopThread()

        async def _owner(payload):
            # This is the real owner path, so it registers the global approval slot
            # and then parks forever (approval_timeout=None, is_background=True).
            return await brain._request_tool_approval_decision(
                "self_extension_proposal",
                {"command": list(payload.get("argv") or [])},
                "self-extension proposal needs review",
                platform="voice",
            )

        try:
            callback = build_self_extension_approval_callback(
                _owner, loop=server.loop, timeout=0.2
            )
            granted = await asyncio.to_thread(
                callback,
                {"approval_binding": "d", "argv": ["npx", "-y", "@weather/mcp"]},
            )

            assert granted is None
            assert await asyncio.to_thread(
                _wait_for, lambda: get_active_tool_approval() is None
            ), (
                "the approval slot was still held after the bridge timed out; "
                f"active={get_active_tool_approval()!r}"
            )
            assert pending_tool_approvals == {}

            # A later gated call must be able to take the single slot again.
            second = asyncio.create_task(
                brain._request_tool_approval_decision(
                    "shell_execute",
                    {"command": ["taskkill", "/?"]},
                    "a later gated call",
                    platform="voice",
                )
            )
            try:
                await asyncio.sleep(0.2)
                pending = get_active_tool_approval()
                assert pending is not None, (
                    "a later gated call could not acquire the approval slot"
                )
                assert not second.done(), (
                    "a later gated call was declined instead of waiting for the owner"
                )
                from charlie.core import resolve_tool_approval

                assert (
                    resolve_tool_approval(pending[0], False, expected_platform="voice")
                    is True
                )
                assert await asyncio.wait_for(second, timeout=3.0) is ApprovalDecision.REJECTED
            finally:
                second.cancel()
                await asyncio.gather(second, return_exceptions=True)
        finally:
            leaked = get_active_tool_approval()
            if leaked is not None:
                from charlie.core import resolve_tool_approval

                resolve_tool_approval(leaked[0], False, expected_platform="voice")
            await brain.close()
            server.close()

    @pytest.mark.asyncio
    async def test_the_wedge_reproduction_is_real_without_the_cancel(self):
        """Control: an abandoned (uncancelled) coroutine keeps running.

        This is the failure mode the fix removes, so it must be demonstrated
        rather than assumed -- otherwise the regression test above could pass
        vacuously.
        """
        started = asyncio.Event()
        server = _LoopThread()

        async def _owner(_payload):
            started.set()
            await asyncio.sleep(3600)  # never cancelled, never resolved

        try:
            import concurrent.futures

            future = asyncio.run_coroutine_threadsafe(_owner({}), server.loop)
            await asyncio.wait_for(started.wait(), timeout=3.0)
            with pytest.raises(concurrent.futures.TimeoutError):
                future.result(timeout=0.2)

            assert not future.cancelled(), "result(timeout=) never cancels on its own"
            assert not future.done(), (
                "expected: after result(timeout=) the owner coroutine is neither "
                "finished nor cancelled, i.e. still parked on the approval"
            )
        finally:
            future.cancel()
            server.close()


class TestTheBridgeWaitsStrictlyLessThanTheOwner:
    """The owner must always outlive the bridge so its ``finally`` clears the globals."""

    def _foreground_brain(self) -> Brain:
        from charlie.config import Config

        return Brain(
            Config(llm_url="http://localhost:11434/v1", llm_key="test-key", llm_model="dummy"),
            on_tool_approval_request=lambda *_a, **_k: True,
        )

    def test_default_voice_owner_wait(self):
        brain = self._foreground_brain()
        try:
            owner_wait = float(brain._approval_timeout)
            assert resolve_approval_timeout(owner_timeout=owner_wait) < owner_wait
        finally:
            pass

    def test_telegram_owner_wait_is_longer_and_still_outlived(self):
        # charlie.core._TELEGRAM_TOOL_APPROVAL_TIMEOUT_SEC
        assert resolve_approval_timeout(owner_timeout=120.0) < 120.0

    def test_background_owner_that_parks_forever_is_still_bounded(self):
        assert 0 < resolve_approval_timeout(owner_timeout=None) <= 45.0

    def test_an_explicit_caller_timeout_cannot_outlive_the_owner(self):
        assert resolve_approval_timeout(timeout=900.0, owner_timeout=45.0) < 45.0

    def test_an_explicit_shorter_caller_timeout_is_honoured(self):
        assert resolve_approval_timeout(timeout=1.0, owner_timeout=45.0) == 1.0

    @pytest.mark.parametrize("bad", [0.0, -1.0, float("inf"), float("nan")])
    def test_a_nonsense_owner_wait_still_yields_a_bounded_bridge_wait(self, bad):
        assert 0 < resolve_approval_timeout(owner_timeout=bad) <= 45.0

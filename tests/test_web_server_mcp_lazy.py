"""Regression tests for web_server's lazy MCP client startup.

Before this fix, web_server.py's lifespan() unconditionally called
start_mcp() at every launch -- a second, redundant windows-mcp subprocess
alongside the one main.py's voice process already starts for the real chat
tool-calling loop, spawned even when the dashboard's Extensions tab is
never opened. Now it starts only on first actual need.
"""
import asyncio
import time
from unittest.mock import MagicMock

import pytest

import charlie.web_server as web_server


@pytest.fixture(autouse=True)
def _reset_mcp_client(monkeypatch):
    monkeypatch.setattr(web_server, "mcp_client", None)
    yield
    monkeypatch.setattr(web_server, "mcp_client", None)


def test_ensure_mcp_client_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(web_server.config, "mcp_enabled", False)
    called = MagicMock()
    monkeypatch.setattr("charlie.mcp_client.start_mcp", called)

    result = web_server._ensure_mcp_client()

    assert result is None
    called.assert_not_called()


def test_ensure_mcp_client_starts_once_when_enabled(monkeypatch):
    monkeypatch.setattr(web_server.config, "mcp_enabled", True)
    fake_client = MagicMock(name="mcp_client")
    called = MagicMock(return_value=fake_client)
    monkeypatch.setattr("charlie.mcp_client.start_mcp", called)

    first = web_server._ensure_mcp_client()
    second = web_server._ensure_mcp_client()

    assert first is fake_client
    assert second is fake_client
    called.assert_called_once()


@pytest.mark.asyncio
async def test_ensure_mcp_client_async_does_not_block_event_loop(monkeypatch):
    """Regression: the lazy start must run on a thread, not synchronously in
    the request handler -- otherwise the first Extensions-tab open (or any
    MCP-touching endpoint) freezes every other request/WS message on this
    process for up to ~30s (mcp_client.py:477's handshake timeout)."""
    monkeypatch.setattr(web_server.config, "mcp_enabled", True)

    def slow_start_mcp(config):
        time.sleep(0.3)
        return MagicMock(name="mcp_client")

    monkeypatch.setattr("charlie.mcp_client.start_mcp", slow_start_mcp)

    ticks = []

    async def ticker():
        for _ in range(6):
            await asyncio.sleep(0.05)
            ticks.append(time.monotonic())

    ticker_task = asyncio.create_task(ticker())
    result = await web_server._ensure_mcp_client_async()
    await ticker_task

    assert result is not None
    gaps = [b - a for a, b in zip(ticks, ticks[1:])]
    assert max(gaps) < 0.25, f"event loop stalled: gaps={gaps}"

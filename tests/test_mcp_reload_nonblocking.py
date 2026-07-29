import asyncio
import time
from unittest.mock import MagicMock

import pytest

import main as main_module


@pytest.mark.asyncio
async def test_restart_mcp_client_does_not_block_event_loop(monkeypatch):
    """A slow start_mcp() must not stall other coroutines on the same loop.

    Regression test for main.py:770 _reload_mcp_client: it used to call the
    synchronous, handshake-blocking start_mcp()/stop() directly inside an
    async def, freezing consume_web_commands (and the whole dashboard) for
    up to 30s per server on every system_restart.
    """

    def slow_start_mcp(config):
        time.sleep(0.3)
        return MagicMock(name="new_client")

    monkeypatch.setattr("charlie.mcp_client.start_mcp", slow_start_mcp)

    config = MagicMock()
    config.mcp_enabled = True

    ticks = []

    async def ticker():
        for _ in range(6):
            await asyncio.sleep(0.05)
            ticks.append(time.monotonic())

    ticker_task = asyncio.create_task(ticker())
    new_client = await main_module._restart_mcp_client(None, config)
    await ticker_task

    assert new_client is not None
    gaps = [b - a for a, b in zip(ticks, ticks[1:])]
    # If the loop were blocked by the 0.3s sleep, one gap would absorb it
    # instead of six even ~0.05s ticks landing throughout.
    assert max(gaps) < 0.25, f"event loop stalled: gaps={gaps}"

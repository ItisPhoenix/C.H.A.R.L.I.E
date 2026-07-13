from unittest.mock import MagicMock

import pytest

from charlie.config import Config
from charlie.core import _compress_messages, _halve_history, _token_count


def _stub_config(context_window: int = 1000, keep_recent: int = 4) -> Config:
    """Config with no llm_url -> summary LLM call skipped, stub used."""
    return Config(
        context_window=context_window,
        compression_threshold=0.8,
        history_keep_recent=keep_recent,
        history_summary_max_chars=400,
        small_llm_url="",
    )


@pytest.mark.asyncio
async def test_compression_trigger():
    config = _stub_config()

    # Large middle section so summary savings clearly exceed overhead
    large_text = "word " * 400  # ~400 tokens
    messages = [
        {"role": "system", "content": "You are Charlie."},
        {"role": "user", "content": large_text},
        {"role": "assistant", "content": large_text},
        {"role": "user", "content": large_text},
        {"role": "assistant", "content": large_text},
        {"role": "user", "content": "recent question"},
        {"role": "assistant", "content": "recent answer"},
        {"role": "user", "content": "latest question"},
        {"role": "assistant", "content": "latest answer"},
    ]

    initial_count = _token_count(messages)
    assert initial_count > 800

    compressed = await _compress_messages(messages, config)
    compressed_count = _token_count(compressed)

    assert compressed_count < initial_count
    assert compressed[0]["role"] == "system"
    # Summary marker present (stub or real)
    assert any(
        "summary" in str(m.get("content", "")).lower()
        or "omitted" in str(m.get("content", "")).lower()
        for m in compressed
    )


@pytest.mark.asyncio
async def test_compression_threshold_from_config_is_honored():
    """Regression test: config.compression_threshold must actually control
    when compression kicks in. Before this fix, core.py used a hardcoded
    module constant (_COMPRESSION_THRESHOLD = 0.8) and silently ignored the
    COMPRESSION_THRESHOLD env / config value entirely."""
    messages = [
        {"role": "system", "content": "You are Charlie."},
        {"role": "user", "content": "word " * 150},
        {"role": "assistant", "content": "word " * 150},
        {"role": "user", "content": "recent question"},
        {"role": "assistant", "content": "recent answer"},
    ]
    total = _token_count(messages)
    assert 100 < total < 800  # falls between the two thresholds below

    # history_keep_recent=2 so the two large messages fall in the
    # summarizable "middle" once compression triggers (5 msgs > 2 + 1 system).

    # High threshold (0.8 of a 1000-token window = 800): must NOT compress.
    high_config = Config(
        context_window=1000, compression_threshold=0.8,
        history_keep_recent=2, small_llm_url="",
    )
    result_high = await _compress_messages(messages, high_config)
    assert result_high == messages

    # Low threshold (0.1 of a 1000-token window = 100): must compress.
    low_config = Config(
        context_window=1000, compression_threshold=0.1,
        history_keep_recent=2, small_llm_url="",
    )
    result_low = await _compress_messages(messages, low_config)
    assert _token_count(result_low) < total


@pytest.mark.asyncio
async def test_halve_keeps_system_and_recent():
    """_halve_history must keep system msg + last N messages verbatim."""
    config = MagicMock()
    config.llm_url = ""  # -> stub summary, no network
    config.history_keep_recent = 4
    config.history_summary_max_chars = 400

    messages = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "u3"},
        {"role": "assistant", "content": "a3"},
        {"role": "user", "content": "u4"},
        {"role": "assistant", "content": "a4"},
        {"role": "user", "content": "u5"},
    ]

    result = await _halve_history(messages, config)

    # System preserved as first
    assert result[0]["role"] == "system"
    assert result[0]["content"] == "SYS"
    # Summary injected as second
    assert result[1]["role"] == "system"
    assert "summary" in result[1]["content"].lower()
    # Last 4 messages kept verbatim
    tail = result[2:]
    assert len(tail) == 4
    assert tail[-1]["content"] == "u5"
    assert tail[0]["content"] == "a3"


@pytest.mark.asyncio
async def test_halve_no_drop_when_short():
    """If message count <= keep_recent + system, no halving needed."""
    config = MagicMock()
    config.llm_url = ""
    config.history_keep_recent = 4
    config.history_summary_max_chars = 400

    messages = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
    ]

    result = await _halve_history(messages, config)
    # Too short -> unchanged
    assert result == messages


@pytest.mark.asyncio
async def test_halve_no_system_msg():
    """Works when there's no leading system message."""
    config = MagicMock()
    config.llm_url = ""
    config.history_keep_recent = 3
    config.history_summary_max_chars = 400

    messages = [
        {"role": "user", "content": f"u{i}"} for i in range(10)
    ]

    result = await _halve_history(messages, config)
    # Summary present somewhere
    assert any(
        "summary" in str(m.get("content", "")).lower()
        for m in result
    )
    # Last 3 kept
    tail = result[-3:]
    assert [m["content"] for m in tail] == ["u7", "u8", "u9"]


@pytest.mark.asyncio
async def test_halve_stub_fallback_no_url():
    """When llm_url empty, summary is a stub (no network call)."""
    config = MagicMock()
    config.llm_url = ""
    config.history_keep_recent = 2
    config.history_summary_max_chars = 400

    messages = [
        {"role": "system", "content": "SYS"},
    ] + [
        {"role": "user", "content": f"msg {i} " * 10}
        for i in range(10)
    ]

    result = await _halve_history(messages, config)
    summary_msg = result[1]
    # Stub contains "omitted"
    assert "omitted" in summary_msg["content"].lower()

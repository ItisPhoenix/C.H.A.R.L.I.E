from charlie.config import Config
from charlie.core import _compress_messages, _token_count


def test_compression_trigger():
    config = Config(
        context_window=1000,
        compression_threshold=0.8,
    )

    # Initial history containing lots of tokens to trigger compression
    # The threshold is 800 tokens. Let's create a message array of >800 tokens.
    large_text = "word " * 400  # ~400 tokens
    messages = [
        {"role": "system", "content": "You are Charlie."},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "Let me search."},
        {"role": "tool", "name": "web_search", "content": large_text},
        {"role": "assistant", "content": "Let me search again."},
        {"role": "tool", "name": "web_search", "content": large_text},
        {"role": "user", "content": "tell me more"},
    ]

    initial_count = _token_count(messages)
    assert initial_count > 800

    compressed = _compress_messages(messages, config)
    compressed_count = _token_count(compressed)

    # Assert compression was triggered and history size was reduced
    assert compressed_count < initial_count
    # Verify the system message is preserved
    assert compressed[0]["role"] == "system"
    # Verify we omitted content
    assert any("omitted" in str(m.get("content", "")) for m in compressed)

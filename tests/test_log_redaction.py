import logging

from charlie.log_redaction import SensitiveDataFilter


def test_sensitive_data_filter_redacts_bearer_tokens_and_bot_urls() -> None:
    record = logging.LogRecord(
        "httpx",
        logging.INFO,
        __file__,
        1,
        "Request to https://api.example.test/bot123456:secret-token/sendMessage Authorization: Bearer llm-secret",
        (),
        None,
    )

    assert SensitiveDataFilter().filter(record) is True
    assert "secret-token" not in record.getMessage()
    assert "llm-secret" not in record.getMessage()
    assert "[REDACTED]" in record.getMessage()

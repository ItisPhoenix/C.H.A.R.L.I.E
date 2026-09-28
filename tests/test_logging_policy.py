"""Console/file logging policy contracts."""

import logging
import sys

from charlie.logging_policy import (
    ConciseConsoleFormatter,
    ConsolePolicyFilter,
    RedactingFormatter,
    parse_log_level,
)


def _record(name: str, level: int, message: str, *, exc_info=None) -> logging.LogRecord:
    return logging.LogRecord(name, level, __file__, 1, message, (), exc_info)


def test_internal_info_is_file_only_by_default():
    policy = ConsolePolicyFilter(logging.INFO, diagnostics=False)

    assert not policy.filter(_record("charlie.voice", logging.INFO, "pipeline_stage | stage=asr"))
    assert not policy.filter(_record("faster_whisper", logging.INFO, "Processing audio"))


def test_operational_info_remains_visible():
    policy = ConsolePolicyFilter(logging.INFO, diagnostics=False)

    assert policy.filter(_record("charlie.core", logging.INFO, "Primary LLM probe succeeded (model=test)"))
    assert policy.filter(_record("charlie.tools", logging.INFO, "Executing tool 'browser_task'"))


def test_warnings_and_errors_remain_visible_from_noisy_loggers():
    policy = ConsolePolicyFilter(logging.INFO, diagnostics=False)

    assert policy.filter(_record("telegram.ext", logging.WARNING, "polling failed"))
    assert policy.filter(_record("httpx", logging.ERROR, "request failed"))


def test_diagnostic_console_allows_internal_debug_records():
    policy = ConsolePolicyFilter(logging.DEBUG, diagnostics=True)

    assert policy.filter(_record("charlie.voice", logging.DEBUG, "vad_rms=0.0"))
    assert policy.filter(_record("faster_whisper", logging.INFO, "Processing audio"))


def test_concise_console_formatter_omits_traceback_but_file_keeps_it():
    try:
        raise RuntimeError("bad provider response")
    except RuntimeError:
        exc_info = sys.exc_info()

    record = _record("charlie.core", logging.ERROR, "Request failed", exc_info=exc_info)
    console_text = ConciseConsoleFormatter("%(levelname)s %(message)s").format(record)
    file_text = RedactingFormatter("%(levelname)s %(message)s").format(record)

    assert "RuntimeError: bad provider response" in console_text
    assert "Traceback (most recent call last)" not in console_text
    assert "Traceback (most recent call last)" in file_text


def test_console_and_file_formatters_redact_credentials():
    record = _record("charlie.http", logging.ERROR, "Authorization: Bearer top-secret-token")

    console_text = ConciseConsoleFormatter("%(message)s").format(record)
    file_text = RedactingFormatter("%(message)s").format(record)

    assert "top-secret-token" not in console_text
    assert "top-secret-token" not in file_text
    assert "[REDACTED]" in console_text
    assert "[REDACTED]" in file_text


def test_log_level_parser_falls_back_safely():
    assert parse_log_level("WARNING", logging.DEBUG) == logging.WARNING
    assert parse_log_level("not-a-level", logging.DEBUG) == logging.DEBUG

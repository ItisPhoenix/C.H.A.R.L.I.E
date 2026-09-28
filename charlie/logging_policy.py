"""Split logging policy for Charlie's diagnostic file and normal console."""

from __future__ import annotations

import logging
import os
from typing import Final

from charlie.log_redaction import redact_sensitive_text

NOISY_LOGGER_PREFIXES: Final[tuple[str, ...]] = (
    "asyncio",
    "chromadb",
    "comtypes",
    "faster_whisper",
    "filelock",
    "httpcore",
    "httpx",
    "huggingface_hub",
    "onnxruntime",
    "playwright",
    "sentence_transformers",
    "telegram",
    "trafilatura",
)

_OPERATIONAL_INFO_MARKERS: Final[dict[str, tuple[str, ...]]] = {
    "charlie.main": (
        "charlie is waking up",
        "loading ai models",
        "plugin system active",
        "telegram bot started",
        "main_shutdown_begin",
        "auditstore closed",
        "sessionstore closed",
        "brain closed",
        "memory graph closed",
        "eventbus/zmq closed",
        "port_release",
    ),
    "charlie.core": (
        "primary llm probe succeeded",
        "canonical browser url action",
        "fast-path browser task",
        "fast-path browser continuation",
        "fast-path browser media continuation",
        "desktop control resumed",
        "sustained_research_started",
    ),
    "charlie.memory_store": ("memorystore initialized",),
    "charlie.voice": (
        "continuous listening mode active",
        "wake word detection enabled",
        "voice_shutdown_complete",
    ),
    "charlie.telegram_bot": ("telegram bot polling started",),
    "charlie.browser": ("browser controller launched", "browser controller shut down"),
    "charlie.tools": ("executing tool '",),
    "charlie.fastpaths": ("executing fast-path",),
}


def parse_log_level(value: str | None, default: int) -> int:
    """Parse a logging level without allowing bad env values to break startup."""
    if not value:
        return default
    normalized = value.strip().upper()
    if normalized.isdigit():
        numeric = int(normalized)
        return numeric if 0 <= numeric <= logging.CRITICAL else default
    return logging.getLevelNamesMapping().get(normalized, default)


def env_flag(name: str, default: bool = False) -> bool:
    """Read a conventional boolean environment flag."""
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _is_operational_info(record: logging.LogRecord) -> bool:
    name = record.name.casefold()
    message = record.getMessage().casefold()
    for logger_name, markers in _OPERATIONAL_INFO_MARKERS.items():
        if name == logger_name or name.startswith(f"{logger_name}."):
            return any(marker in message for marker in markers)
    return False


class ConsolePolicyFilter(logging.Filter):
    """Keep normal console output operational while retaining file detail."""

    def __init__(self, minimum_level: int, *, diagnostics: bool = False) -> None:
        super().__init__()
        self.minimum_level = minimum_level
        self.diagnostics = diagnostics

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno < self.minimum_level:
            return False
        if self.diagnostics:
            return True
        if record.levelno >= logging.WARNING:
            return True
        return record.levelno == logging.INFO and _is_operational_info(record)


class ConciseConsoleFormatter(logging.Formatter):
    """Render exception summaries without leaking a traceback to the console."""

    def formatException(self, exc_info) -> str:  # noqa: N802 - logging API name
        exc_type, exc_value, _ = exc_info
        summary = f"{exc_type.__name__}: {exc_value}" if exc_value else exc_type.__name__
        return redact_sensitive_text(summary)

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        if record.exc_info:
            message = f"{message} — {self.formatException(record.exc_info)}"
        prefix = f"{record.levelname} " if record.levelno >= logging.WARNING or record.exc_info else ""
        return redact_sensitive_text(f"{prefix}{message}")


class RedactingFormatter(logging.Formatter):
    """Preserve normal file formatting while redacting the complete rendering."""

    def format(self, record: logging.LogRecord) -> str:
        return redact_sensitive_text(super().format(record))

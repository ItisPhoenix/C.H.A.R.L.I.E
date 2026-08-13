"""Logging filter that prevents credentials from reaching local logs."""

import logging
import re

_AUTHORIZATION_RE = re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)([^\s,]+)")
_BOT_TOKEN_RE = re.compile(r"(?i)(/bot)([^/\s]+)")
_QUERY_SECRET_RE = re.compile(r"(?i)([?&](?:api[_-]?key|token|key|secret)=)([^&\s]+)")
_ASSIGNED_SECRET_RE = re.compile(r'(?i)(\b(?:api[_-]?key|token|secret|password)\s*[:=]\s*)([^\s,}\]"]+)')
_REDACTED = "[REDACTED]"


def redact_sensitive_text(message: str) -> str:
    """Return log-safe text without bearer, bot, or query-string credentials."""
    message = _AUTHORIZATION_RE.sub(rf"\1{_REDACTED}", message)
    message = _BOT_TOKEN_RE.sub(rf"\1{_REDACTED}", message)
    message = _QUERY_SECRET_RE.sub(rf"\1{_REDACTED}", message)
    return _ASSIGNED_SECRET_RE.sub(rf"\1{_REDACTED}", message)


class SensitiveDataFilter(logging.Filter):
    """Redact formatted log-record messages before handlers emit them."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_sensitive_text(record.getMessage())
        record.args = ()
        return True

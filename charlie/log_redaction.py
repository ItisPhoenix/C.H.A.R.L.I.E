"""Credential scrubbing: one canonical detector, one log-safe redactor.

This module is the single source of truth for "does this text contain a
credential or regulated identifier Charlie must not persist or emit". Both the
log/audit/error surface (`redact_sensitive_text`) and the durable-memory write
boundary (`contains_sensitive_secret`) resolve here, so the two paths cannot
drift apart again.
"""

import logging
import re
from typing import Iterable, Pattern, Tuple

_REDACTED = "[REDACTED]"

# --------------------------------------------------------------------------
# Rule table. Every entry is (compiled pattern, group index holding the
# context to preserve). Group 1 of each pattern always captures the text that
# must survive redaction; the remainder of the match is the secret.
# --------------------------------------------------------------------------

_SECRET_RULES: Tuple[Tuple[Pattern[str], int], ...] = (
    # --- Pre-existing log-path rules (unchanged semantics) ---
    # "Authorization: Bearer <tok>" and a bare "Bearer <tok>".
    (re.compile(r"(?i)((?:authorization\s*[:=]\s*)?bearer\s+)([^\s,]+)"), 1),
    # Legacy Telegram form: "/bot<token>".
    (re.compile(r"(?i)(/bot)([^/\s]+)"), 1),
    # Query strings: "?api_key=<tok>&x=1".
    # The negative lookahead makes this rule idempotent: without it, a value this
    # rule has *already* replaced with "[REDACTED]" is re-matched by the rule
    # below, whose value class stops before "]" and so emits "[REDACTED]]".
    (
        re.compile(
            r"(?i)([?&](?:api[_-]?key|token|key|secret|password|auth[_-]?token|"
            r"access[_-]?token)=)(?!\[REDACTED])[^&\s]+"
        ),
        1,
    ),
    # Assignment form: "api_key=<tok>", "password: <tok>".
    # Same lookahead, for the same reason: redaction must be a fixed point.
    (
        re.compile(
            r'(?i)(\b(?:api[_-]?key|token|secret|password|private[_-]?key|auth[_-]?token|'
            r'access[_-]?token|client[_-]?secret|session[_-]?token)\s*[:=]\s*)'
            r'(?!\[REDACTED])[^\s,}\]"]+'
        ),
        1,
    ),
    # Labelled credentials the memory boundary already refused to persist.
    # "&" is excluded from the value so a query string is not swallowed whole,
    # and an already-redacted value is left alone so redaction is idempotent.
    (
        re.compile(
            r"(?i)\b(?:password|passcode|api[\s_-]?key|access[\s_-]?token|refresh[\s_-]?token|"
            r"client[\s_-]?secret|secret|private[\s_-]?key|credential)\b\s*(?:is|:|=)\s*"
            r"(?!\[REDACTED\])[^\s&,}\]%\"]+"
        ),
        0,
    ),
    # --- Real credential formats ---
    # OpenAI-style keys: sk-..., sk-proj-..., sk-ant-..., sk-or-...
    (re.compile(r"\bsk-(?:proj-|ant-|or-|live-|test-)?[A-Za-z0-9_\-]{16,}"), 0),
    # AWS access key ids (AKIA) and temporary/session ids (ASIA).
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), 0),
    # Google API keys.
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"), 0),
    # Slack tokens (bot/app/user/refresh).
    (re.compile(r"\bxox[abprse]-[A-Za-z0-9\-]{10,}"), 0),
    # Telegram bot tokens: <digits>:<token>.
    (re.compile(r"(?<![\w])\d{8,12}:[A-Za-z0-9_\-]{30,}"), 0),
    # Hugging Face access tokens.
    (re.compile(r"\bhf_[A-Za-z0-9]{20,}"), 0),
    # PEM blocks, public and private. Paired block first, then an unterminated
    # header whose body continues to end of line.
    (
        re.compile(
            r"(?s)-----BEGIN [A-Z0-9 ]*KEY-----.*?-----END [A-Z0-9 ]*KEY-----"
        ),
        0,
    ),
    (re.compile(r"-----BEGIN [A-Z0-9 ]*KEY-----[^\n]*"), 0),
    # Bare US SSN, digits-only neighbour guard.
    (re.compile(r"(?<![\d\-])\d{3}-\d{2}-\d{4}(?![\d\-])"), 0),
    # --- Regulated identifiers the memory boundary already refused ---
    (re.compile(r"(?i)\b(?:aadhaar|aadhar|uidai)\b.{0,24}\b\d{4}[ -]?\d{4}[ -]?\d{4}\b"), 0),
    (re.compile(r"(?i)\bPAN\b.{0,16}\b[A-Z]{5}\d{4}[A-Z]\b"), 0),
    (
        re.compile(r"(?i)\bSSN\b.{0,16}\b\d{3}-\d{2}-\d{4}\b"),
        0,
    ),
    (re.compile(r"(?i)\bpassport(?:\s+(?:number|no\.?))?\b.{0,16}\b[A-Z]\d{7}\b"), 0),
    # Digit runs long enough to be a payment card; Luhn-validated below so
    # ordinary order/reference numbers are not treated as credentials.
    (re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)"), 0),
)


def _luhn_valid(candidate: str) -> bool:
    digits = [int(digit) for digit in re.sub(r"\D", "", candidate)]
    if not 13 <= len(digits) <= 19:
        return False
    checksum = sum(
        (digit * 2 - 9 if digit * 2 > 9 else digit * 2) if index % 2 else digit
        for index, digit in enumerate(reversed(digits))
    )
    return checksum % 10 == 0


def _card_matches(text: str) -> Iterable[re.Match]:
    last = _SECRET_RULES[-1][0]
    return (
        match
        for match in last.finditer(text)
        if _luhn_valid(match.group(0))
    )


def _has_card(text: str) -> bool:
    return next(_card_matches(text), None) is not None


def contains_sensitive_secret(*values: str) -> bool:
    """Canonical credential/regulated-identifier predicate.

    Contract:
    - Accepts one or more strings; ``None`` and empty values are ignored.
    - Returns ``True`` if *any* value contains a credential, private key, or
      regulated identifier Charlie must refuse to persist or log: labelled
      secrets, bearer/bot/query credentials, OpenAI-style ``sk-`` keys, AWS
      ``AKIA``/``ASIA`` ids, Google ``AIza`` keys, Slack ``xox[abprse]-``
      tokens, Telegram ``<digits>:<token>`` bot tokens, Hugging Face ``hf_``
      tokens, PEM ``* KEY`` blocks (public and private), US SSN (bare or
      labelled), Aadhaar/PAN/passport ids, and Luhn-valid payment-card numbers.
    - Returns ``False`` for benign text, including long digit strings that are
      not Luhn-valid.
    - This is the only detector both the memory write path and the log path
      must call, so their coverage cannot diverge.
    """
    text = " ".join(value for value in values if value)
    if not text:
        return False
    if any(pattern.search(text) for pattern, _ in _SECRET_RULES[:-1]):
        return True
    return _has_card(text)


def redact_sensitive_text(message: str) -> str:
    """Return log-safe text with every detected secret replaced by ``[REDACTED]``.

    Built from the same rule table as :func:`contains_sensitive_secret`, so
    anything the canonical helper refuses to persist is also scrubbed here.
    """
    if not message:
        return message
    for pattern, keep_group in _SECRET_RULES[:-1]:
        if keep_group:
            message = pattern.sub(rf"\g<{keep_group}>{_REDACTED}", message)
        else:
            message = pattern.sub(_REDACTED, message)
    for match in reversed(list(_card_matches(message))):
        message = (
            message[: match.start()] + _REDACTED + message[match.end() :]
        )
    return message


class SensitiveDataFilter(logging.Filter):
    """Redact formatted log-record messages before handlers emit them."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_sensitive_text(record.getMessage())
        record.args = ()
        return True


__all__ = [
    "SensitiveDataFilter",
    "contains_sensitive_secret",
    "redact_sensitive_text",
]

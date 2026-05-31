import json
import logging
import os
import re
import sys
from logging.handlers import RotatingFileHandler


# Single source of truth for credential redaction. Each entry is a
# (compiled_pattern, replacement) pair that masks a credential value while
# preserving the surrounding key/label so logs stay readable.
_CREDENTIAL_PATTERNS = [
    (re.compile(r'(?i)(api[_-]?key|token|password|secret|pwd)["\s:=]+[a-zA-Z0-9_\-\.]{8,}', re.IGNORECASE), r"\1: [REDACTED]"),
    (re.compile(r'(?i)(authorization:\s*bearer\s+)[a-zA-Z0-9_\-\.]+', re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r'(?i)(telegram_token|TELEGRAM_TOKEN)["\s:=]+[a-zA-Z0-9:_\-]+'), r"\1=[REDACTED]"),
    (re.compile(r'(?i)(NIM_API_KEY|nim_api_key)["\s:=]+[a-zA-Z0-9_\-\.]+'), r"\1=[REDACTED]"),
]

# Filesystem-path patterns redact local usernames from paths. These are applied
# only on the logging path (not by redact_secrets, which targets credentials).
_PATH_PATTERNS = [
    (re.compile(r'C:\\Users\\[^\\]+\\'), r'C:\\Users\\[USER]\\'),
    (re.compile(r'/home/[^/]+/'), r'/home/[USER]/'),
]


def _apply_patterns(text, patterns):
    """Apply a list of (pattern, replacement) pairs to ``text`` in order."""
    for pattern, replacement in patterns:
        text = pattern.sub(replacement, text)
    return text


def redact_secrets(text: str) -> str:
    """Mask credential patterns (API key, token, password, secret, bearer auth,
    NIM_API_KEY, TELEGRAM_TOKEN) in ``text``.

    This is a pure, side-effect-free helper (no logging, no I/O) and the single
    source of truth for credential redaction. Strings without secrets are
    returned unchanged. Non-string input is returned as-is so callers can pass
    arbitrary log arguments safely.
    """
    if not isinstance(text, str):
        return text
    return _apply_patterns(text, _CREDENTIAL_PATTERNS)


class SensitiveDataFilter(logging.Filter):
    """Redacts sensitive patterns from log records before they reach handlers."""

    # Reuse the shared credential patterns (via redact_secrets) plus path
    # patterns; no regex list is duplicated.
    _SENSITIVE_PATTERNS = _CREDENTIAL_PATTERNS + _PATH_PATTERNS

    @staticmethod
    def _scrub(text: str) -> str:
        """Redact credentials (shared helper) then local filesystem paths."""
        return _apply_patterns(redact_secrets(text), _PATH_PATTERNS)

    def filter(self, record):
        if hasattr(record, 'msg') and isinstance(record.msg, str):
            record.msg = self._scrub(record.msg)
        if hasattr(record, 'args') and record.args:
            new_args = []
            for arg in (record.args if isinstance(record.args, tuple) else [record.args]):
                if isinstance(arg, str):
                    arg = self._scrub(arg)
                new_args.append(arg)
            record.args = tuple(new_args)
        return True


class StructuredLogger(logging.Logger):
    def _log(
        self,
        level,
        msg,
        args,
        exc_info=None,
        extra=None,
        stack_info=False,
        stacklevel=1,
        **kwargs,
    ):
        if kwargs:
            if extra is None:
                extra = {}
            reserved = {
                "args",
                "asctime",
                "created",
                "exc_info",
                "filename",
                "funcName",
                "levelname",
                "levelno",
                "lineno",
                "module",
                "msecs",
                "msg",
                "name",
                "pathname",
                "process",
                "processName",
                "relativeCreated",
                "stack_info",
                "thread",
                "threadName",
            }
            for k, v in kwargs.items():
                if k in reserved:
                    extra[f"arg_{k}"] = v
                else:
                    extra[k] = v
        super()._log(level, msg, args, exc_info, extra, stack_info, stacklevel)


# Set the logger class at module level to avoid redundant global mutations
logging.setLoggerClass(StructuredLogger)


class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_data = {
            "timestamp": self.formatTime(record),
            "name": record.name,
            "level": record.levelname,
            "message": record.getMessage(),
        }
        # Add 'extra' fields (those not in reserved list)
        reserved = {
            "args",
            "asctime",
            "created",
            "exc_info",
            "filename",
            "funcName",
            "levelname",
            "levelno",
            "lineno",
            "module",
            "msecs",
            "msg",
            "name",
            "pathname",
            "process",
            "processName",
            "relativeCreated",
            "stack_info",
            "thread",
            "threadName",
        }
        for k, v in record.__dict__.items():
            if k not in reserved and not k.startswith("_"):
                try:
                    # Ensure value is JSON serializable
                    json.dumps(v)
                    log_data[k] = v
                except (TypeError, OverflowError):
                    log_data[k] = str(v)

        # Capture traceback if present
        if record.exc_info:
            log_data["exc_text"] = self.formatException(record.exc_info)
        elif record.stack_info:
            log_data["exc_text"] = self.formatStack(record.stack_info)
        else:
            log_data["exc_text"] = None

        try:
            return json.dumps(log_data)
        except Exception as e:
            return f"{log_data['timestamp']} - {log_data['name']} - {log_data['level']} - {log_data['message']} (JSON Fail: {e})"


class HumanFormatter(logging.Formatter):
    def format(self, record):
        msg = record.getMessage()

        # --- PRETTY PATTERNS ---
        if "stt_result" in msg:
            return f"   SIR: {msg.split('| SIR: ')[-1]}"
        if "speak_vocal" in msg:
            return f"  CHAR: {msg.split('| CHARLIE: ')[-1]}"
        if "brain_thought" in msg:
            return f" THOUGHT: {msg.split('content=')[-1] if 'content=' in msg else msg}"
        if "chain_started" in msg:
            return f"   GOAL: {msg.split('goal=')[-1].split(' | ')[0]}"
        if "tool_complete" in msg:
            return f"   TOOL: ✅ {msg.split('tool=')[-1]}"
        if "factual_tool_fast_return" in msg:
            return f"   TOOL: ⚡ Instant: {msg.split('tool=')[-1]}"
        if "simple_llm_call_failed" in msg:
            return f"  ERROR: ❌ Simple LLM Fail | {msg.split(' | ')[-1]}"
        if "audio_health_check" in msg:
            return f" HEALTH: {msg.split(' | ')[-1]}"
        if "wake_triggered" in msg:
            return "   WAKE: ✨ Listening..."

        return f"{record.levelname}: {msg}"


def get_logger(name):
    """Returns a structured logger for the Charlie system."""
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    sensitive_filter = SensitiveDataFilter()
    logger.addFilter(sensitive_filter)

    if not logger.handlers:
        os.makedirs("logs", exist_ok=True)
        file_handler = RotatingFileHandler(
            "logs/charlie.log", maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(JsonFormatter())
        logger.addHandler(file_handler)

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(HumanFormatter())
        console_handler.setLevel(logging.INFO)
        # Force UTF-8 encoding for console to handle special characters
        if hasattr(console_handler.stream, 'reconfigure'):
            try:
                console_handler.stream.reconfigure(encoding='utf-8', errors='replace')
            except Exception:
                pass
        logger.addHandler(console_handler)

    return logger


# Suppress common noisy warnings at source
import warnings  # noqa: E402

warnings.filterwarnings("ignore", category=FutureWarning, module="torch.cuda")
warnings.filterwarnings("ignore", message=".*pynvml package is deprecated.*")

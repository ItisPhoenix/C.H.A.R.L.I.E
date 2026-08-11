"""Error classification: a small, closed taxonomy so a turn failure gets a concise, non-technical
message instead of a raw traceback -- full details still go to the logger, never to a surface,
voice, or Telegram reply.
"""

from enum import StrEnum
from typing import Tuple

import httpx


class ErrorClass(StrEnum):
    RECOVERABLE = "recoverable"
    RETRYABLE = "retryable"
    USER_ACTION_REQUIRED = "user_action_required"
    DEPENDENCY_FAILURE = "dependency_failure"
    PERMISSION_REQUIRED = "permission_required"
    CRITICAL = "critical"


# RETRYABLE wording is the product-approved cloud-failure phrasing -- do not paraphrase.
_MESSAGES = {
    ErrorClass.RECOVERABLE: "Sorry, something went wrong on my end. Try again?",
    ErrorClass.RETRYABLE: "I can't reach my reasoning service right now. Local functions are still available.",
    ErrorClass.USER_ACTION_REQUIRED: "I need something from you to continue.",
    ErrorClass.DEPENDENCY_FAILURE: "Something I depend on isn't responding right now.",
    ErrorClass.PERMISSION_REQUIRED: "I don't have permission to do that.",
    ErrorClass.CRITICAL: "Something went seriously wrong and I need to stop here.",
}

_RETRYABLE_TYPES = (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.PoolTimeout, TimeoutError)


def classify_exception(exc: BaseException) -> Tuple[ErrorClass, str]:
    """Pure classification, no I/O -- returns (class, a concise user-facing message)."""
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in (401, 403):
            error_class = ErrorClass.PERMISSION_REQUIRED
        elif status >= 500:
            error_class = ErrorClass.DEPENDENCY_FAILURE
        else:
            error_class = ErrorClass.USER_ACTION_REQUIRED
    elif isinstance(exc, _RETRYABLE_TYPES):
        error_class = ErrorClass.RETRYABLE
    elif isinstance(exc, PermissionError):
        error_class = ErrorClass.PERMISSION_REQUIRED
    elif isinstance(exc, FileNotFoundError):
        error_class = ErrorClass.USER_ACTION_REQUIRED
    else:
        error_class = ErrorClass.RECOVERABLE
    return error_class, _MESSAGES[error_class]

"""Owner-approval bridge for self-extension proposals.

The self-extension seam in :mod:`charlie.tools` is deliberately synchronous: it is
reached from executor threads that have no running event loop of their own. The
canonical approval owner (:meth:`Brain.request_tool_approval`) is asynchronous.
Bridging the two by blocking the caller's thread is only safe when the caller is
*not* the event loop thread -- blocking the loop thread would deadlock the very
loop that has to deliver the owner's answer.

So the bridge distinguishes the two cases:

* off the loop thread: submit the approval to the running loop and wait for the
  owner's decision;
* on the loop thread: refuse. A fail-closed refusal is strictly better than a
  frozen interpreter, and the proposal is re-runnable once the owner path is
  reachable.

The bridge can only ever return the binding it was shown, and only when the owner
approved. It cannot widen the grant: :mod:`charlie.self_extension.orchestrator`
independently recomputes the digest from the argv it reviews.

Two hazards are closed here rather than at the call sites:

* ``concurrent.futures.Future.result(timeout=)`` never cancels the coroutine it was
  waiting on, so an abandoned owner coroutine keeps holding the single global
  approval slot forever.  :func:`resolve_approval_timeout` bounds the bridge
  strictly below the owner's own wait and the bridge cancels on timeout.
* The approval must be raised on the channel that can actually answer it, and the
  reason must not be able to structure the owner's prompt.
  :class:`ApprovalChannelRegistry` and :func:`sanitize_approval_reason` serve
  those, so an unanswerable or hostile request fails closed instead of parking.

Evidence class: TEST/MOCK.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import math
import re
import threading
from typing import Any, Awaitable, Callable, Dict, Optional

from charlie.log_redaction import redact_sensitive_text

__all__ = [
    "is_on_loop_thread",
    "build_self_extension_approval_callback",
    "resolve_approval_timeout",
    "sanitize_approval_reason",
    "ApprovalChannelRegistry",
]

# charlie.core._TOOL_APPROVAL_TIMEOUT_SEC / _TELEGRAM_TOOL_APPROVAL_TIMEOUT_SEC.
# They are mirrored here (read-only) rather than imported so this module keeps no
# dependency on the Brain, and the numbers cannot drift apart silently: the
# derived bridge wait is always strictly below the owner's.
_OWNER_APPROVAL_WAIT_SEC = 45.0
_TELEGRAM_APPROVAL_WAIT_SEC = 120.0
_BRIDGE_WAIT_MARGIN = 0.8
_MAX_BRIDGE_WAIT_SEC = _OWNER_APPROVAL_WAIT_SEC
_MIN_BRIDGE_WAIT_SEC = 1.0


def is_on_loop_thread() -> bool:
    """True when the calling thread is already running an asyncio loop.

    ``RuntimeError`` from ``get_running_loop`` means there is no loop on this
    thread, which is the executor-thread case the bridge can actually serve.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _owner_wait_seconds(owner_timeout: Optional[float]) -> float:
    """Seconds the owner will keep the approval parked, best effort.

    ``None`` is the background-task shape (``approval_timeout=None``): the owner
    parks indefinitely, so the bridge must supply its own bound and treat the
    shortest wait any owner can impose as the ceiling.
    """
    if owner_timeout is None:
        return _OWNER_APPROVAL_WAIT_SEC
    try:
        value = float(owner_timeout)
    except (TypeError, ValueError):
        return _OWNER_APPROVAL_WAIT_SEC
    if not math.isfinite(value) or value <= 0:
        return _OWNER_APPROVAL_WAIT_SEC
    return min(value, _TELEGRAM_APPROVAL_WAIT_SEC)


def resolve_approval_timeout(
    timeout: Optional[float] = None,
    *,
    owner_timeout: Optional[float] = None,
) -> float:
    """Return the bridge's wait, always strictly shorter than the owner's own.

    If the bridge outlived the owner, the owner would time out (or park forever)
    while this thread still held a decision that can no longer arrive, and the
    approval slot would stay occupied for the whole window.  The bridge therefore
    gives up first, cancels the owner coroutine, and lets the owner's ``finally``
    clear the global slot.
    """
    derived = min(
        _owner_wait_seconds(owner_timeout) * _BRIDGE_WAIT_MARGIN,
        _MAX_BRIDGE_WAIT_SEC,
    )
    derived = max(derived, _MIN_BRIDGE_WAIT_SEC)
    if timeout is None:
        return derived
    try:
        requested = float(timeout)
    except (TypeError, ValueError):
        return derived
    if not math.isfinite(requested) or requested <= 0:
        return derived
    return min(requested, derived)


# Control, format, zero-width and bidi characters. None of these can appear in a
# legitimate policy sentence, and each one can forge structure in the owner's
# prompt: a new line, an ANSI erase, or a right-to-left override that hides text.
_CONTROL_AND_INVISIBLE = re.compile(
    "[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f"
    "\u00ad\u061c\u180e\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]"
)
_WHITESPACE_RUN = re.compile(r"\s+")
# An affirmative that occupies a whole sentence position: at the start of the
# reason, or right after a sentence boundary, and ending that sentence. The
# owner's question ends "Yes or no?", so this is the shape an injected argv uses
# to answer it. An affirmative used as an ordinary word inside a sentence ("review
# and approve") is not that shape and stays readable.
_STANDALONE_AFFIRMATION = re.compile(
    r"(?i)(?:^|(?<=[.!?]\s))"
    r"(?:yes|yeah|yep|yup|okay|ok|sure|affirmative"
    r"|approved|approval|approve"
    r"|allowed|allow"
    r"|confirmed|confirm"
    r"|authorized|authorize|authorised|authorise"
    r"|do it|go ahead|proceed)"
    r"\s*[.!?]?(?=\s|$)"
)
_QUARANTINE = "[quarantined: untrusted affirmative]"
_MAX_REASON_LEN = 400
# A forged line break is a sentence boundary to whoever reads the prompt, so it is
# normalised to real sentence punctuation *before* the affirmative quarantine and
# only removed afterwards. Stripping it first would destroy the very boundary that
# makes an injected "Yes." detectable.
_FORGED_LINE_BREAK = re.compile(r"[\r\n\v\f\x1c-\x1e\x85\u2028\u2029]+")


def sanitize_approval_reason(text: Any, *, max_length: int = _MAX_REASON_LEN) -> str:
    """Return a reason that cannot structure or answer the owner's prompt.

    The reason is untrusted: it is built from an LLM-planned request, and the
    owner channel for ``self_extension_proposal`` has no hardened preview to fall
    back on.  In order:

    1. credentials are redacted with the canonical redactor;
    2. forged line breaks become real sentence punctuation, so an injected
       affirmative is visible as one;
    3. an affirmative sitting in a sentence position is quarantined, so an
       injected "Yes." cannot answer the "Yes or no?" the owner is asked;
    4. control, format, zero-width and bidi characters are removed, so the text
       cannot forge a line break, an ANSI sequence, or a hidden run;
    5. whitespace runs collapse and the result is length-bounded.

    Returns ``""`` for empty or unusable input, which callers treat as "no reason
    to show" and therefore refuse.
    """
    if text is None:
        return ""
    raw = text if isinstance(text, str) else str(text)
    if not raw:
        return ""
    cleaned = redact_sensitive_text(raw)
    cleaned = _FORGED_LINE_BREAK.sub(". ", cleaned)
    cleaned = _STANDALONE_AFFIRMATION.sub(_QUARANTINE, cleaned)
    cleaned = _CONTROL_AND_INVISIBLE.sub("", cleaned)
    cleaned = _WHITESPACE_RUN.sub(" ", cleaned).strip()
    if not cleaned:
        return ""
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length].rstrip()
    return cleaned


class ApprovalChannelRegistry:
    """Which turn channels are currently able to answer an approval.

    Main owns the turn lifecycle, so it is the only component that knows the
    channel of the turn a tool call belongs to.  The approval seam is synchronous
    and carries no turn identity, so the registry answers one question for the
    bridge: is there exactly one answerable channel right now?

    Anything else -- no turn, or two turns on different channels -- resolves to
    ``None``.  ``None`` means "do not prompt": raising an approval nobody can
    answer parks the single global approval slot for the whole timeout and
    declines every other gated tool call in the meantime.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._channels: Dict[str, str] = {}

    def begin(self, turn_id: Any, channel: Any) -> None:
        """Record that *turn_id* is being served on *channel*."""
        key = str(turn_id or "")
        value = str(channel or "")
        if not key or not value:
            return
        with self._lock:
            self._channels[key] = value

    def end(self, turn_id: Any) -> None:
        """Release *turn_id*; safe to call for a turn that never began."""
        key = str(turn_id or "")
        if not key:
            return
        with self._lock:
            self._channels.pop(key, None)

    def resolve(self) -> Optional[str]:
        """The one channel that can answer, or ``None`` when it is not knowable."""
        with self._lock:
            distinct = set(self._channels.values())
        if len(distinct) == 1:
            return next(iter(distinct))
        return None


def build_self_extension_approval_callback(
    request_decision: Callable[[dict[str, Any]], Awaitable[Optional[str]]],
    *,
    loop: Optional[asyncio.AbstractEventLoop] = None,
    timeout: Optional[float] = None,
    owner_timeout: Optional[float] = None,
) -> Callable[[dict[str, Any]], Optional[str]]:
    """Return a synchronous approval callback backed by the async owner path.

    ``request_decision`` receives the payload the seam hands us (including the exact
    argv and the ``approval_binding`` it was shown) and must await the owner's
    decision, returning the granted binding or ``None``.

    ``owner_timeout`` is the owner's own approval wait.  It is used to derive a
    bridge wait that is strictly shorter, so the owner always outlives this
    thread and its ``finally`` always clears the global approval slot.

    On timeout, on owner refusal, and when called from the loop thread, the result
    is ``None``: no approval, no execution.
    """
    wait = resolve_approval_timeout(timeout, owner_timeout=owner_timeout)

    def _callback(payload: dict[str, Any]) -> Optional[str]:
        if is_on_loop_thread():
            # Blocking here would wait on a loop that cannot run until we return.
            return None
        target = loop
        if target is None:
            try:
                target = asyncio.get_event_loop_policy().get_event_loop()
            except Exception:
                return None
        if target is None or target.is_closed() or not target.is_running():
            return None
        future = asyncio.run_coroutine_threadsafe(request_decision(dict(payload)), target)
        try:
            return future.result(timeout=wait)
        except concurrent.futures.TimeoutError:
            # Future.result(timeout=) never cancels on its own, so without this the
            # coroutine keeps awaiting inside the owner, still holding
            # pending_tool_approvals[request_id] and _active_tool_approval_id.
            future.cancel()
            return None
        except Exception:
            future.cancel()
            return None

    return _callback

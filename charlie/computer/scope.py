"""Per-task ownership of a bounded Cua browser runtime.

A Cua manifest is written once and pinned to a runtime for that runtime's whole life,
and origins are compiled into it. So the unit of ownership cannot be "the process" or
even "the browser session" -- it has to be the task, because only the task knows which
origins it is allowed to reach.

This module owns that unit:

* the declared origin set, built from the task's actual destinations plus explicitly
  declared workflow redirects;
* one warm bounded runtime, one isolated browser profile and one session label;
* the cleanup lifecycle.

Origin discipline
-----------------
Origins are never widened from page content. A redirect or a link to an origin outside
the declared set is reported as a scope boundary, not silently followed and not
silently added. Widening requires the caller to authorise it explicitly, which means a
new runtime, which means the previous verified result is handed back rather than
replayed into.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Iterable, Optional
from urllib.parse import urlsplit

from .manifest import DEFAULT_ORIGINS

__all__ = [
    "ScopeBoundary",
    "BrowserTaskScope",
    "origin_of",
    "build_origins",
]


@dataclass(frozen=True)
class ScopeBoundary:
    """A destination the task is not authorised to reach."""

    origin: str
    reason: str
    authorised_origins: tuple[str, ...] = ()

    @property
    def summary(self) -> str:
        return (
            f"'{self.origin}' is outside this task's approved browser scope "
            f"({self.reason}); approved: {', '.join(self.authorised_origins) or 'none'}"
        )

    @property
    def origins(self) -> tuple[str, ...]:
        return self.authorised_origins


def ScopeBoundaryLike(origin: str, reason: str, approved: tuple[str, ...] = ()):
    """Build a boundary for a case where no destination could be observed."""
    return ScopeBoundary(origin=origin, reason=reason, authorised_origins=approved)


def origin_of(url: str) -> Optional[str]:
    """Reduce a URL to ``scheme://host[:port]``, or None when it has no origin.

    Opaque schemes such as ``about:blank`` carry no authority, so they are kept in
    their own form: they are not network origins and must not be compared against one.
    """
    if not url:
        return None
    parts = urlsplit(url.strip())
    if not parts.scheme:
        return None
    if parts.scheme not in ("http", "https"):
        return f"{parts.scheme}:"
    if not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}"


def build_origins(
    destinations: Iterable[str], workflow_redirects: Iterable[str] = ()
) -> tuple[str, ...]:
    """Build the approved origin set for one task.

    ``destinations`` are the URLs the task intends to visit. ``workflow_redirects`` are
    origins the caller already knows a workflow may reach -- they must be declared, not
    discovered. Anything else is out of scope until authorised.
    """
    origins: list[str] = list(DEFAULT_ORIGINS)
    for url in list(destinations) + list(workflow_redirects):
        origin = origin_of(url)
        if origin and origin not in origins:
            origins.append(origin)
    return tuple(origins)


class BrowserTaskScope:
    """One task's bounded browser runtime, profile, session and cleanup."""

    def __init__(
        self,
        destinations: Iterable[str] = (),
        *,
        workflow_redirects: Iterable[str] = (),
        session: Optional[str] = None,
    ) -> None:
        self._origins = build_origins(destinations, workflow_redirects)
        self._session = session
        self._browser = None
        self.cleanup_report: Optional[dict[str, object]] = None
        self._lock = threading.Lock()
        self._closed = False
        # Set when cancellation closes admission. No further input may be dispatched;
        # reads already in flight are allowed to finish so their result is not lost.
        self._admission_closed = False
        # The last result this task verified. Kept so a scope refusal can hand back
        # real state instead of forcing the caller to redo the work.
        self.last_verified: Optional[object] = None

    @property
    def origins(self) -> tuple[str, ...]:
        return self._origins

    @property
    def session(self) -> Optional[str]:
        return self._session

    def check(self, url: str) -> Optional[ScopeBoundary]:
        """Return a boundary when ``url`` is not already authorised, else None."""
        origin = origin_of(url)
        if origin is None or origin in self._origins:
            return None
        return ScopeBoundary(
            origin=origin,
            reason="origin was not declared for this task",
            authorised_origins=self._origins,
        )

    def close_admission(self) -> None:
        """Stop accepting further input for this task.

        Cancellation uses this so nothing else is dispatched after the user asked to
        stop. It deliberately does not pretend an in-flight native input has been
        undone: a mutation already handed to the driver may have landed, which is why
        callers must keep the outcome uncertain rather than assume either result.
        """
        self._admission_closed = True

    @property
    def admission_open(self) -> bool:
        return not self._admission_closed

    def require_admission(self) -> None:
        """Raise when input may no longer be dispatched."""
        if self._admission_closed:
            raise PermissionError("this browser task no longer accepts input")

    def browser(self):
        """Return this task's warm browser, starting it on first use."""
        if self._closed:
            raise RuntimeError("this browser task scope is already closed")
        with self._lock:
            if self._browser is not None:
                return self._browser
            from .backend import get_cua_browser

            browser = get_cua_browser(
                origins=list(self._origins), session=self._session
            )
            # The runtime is process-wide, so its session label is authoritative for
            # every call in this task. Record it so later boundaries can name it.
            self._session = getattr(browser, "session", self._session)
            self._browser = browser
            return self._browser

    def record_verified(self, result: object) -> None:
        """Remember the last independently verified result for this task."""
        self.last_verified = result

    def close(self) -> None:
        """Release the browser and end the session. Safe to call more than once."""
        with self._lock:
            browser = self._browser
            self._browser = None
            self._closed = True
            # Closing the scope also closes admission, so nothing can be dispatched
            # against a runtime that is being torn down.
            self._admission_closed = True
        if browser is None:
            return
        try:
            result = browser.end_session()
            self.cleanup_report = getattr(browser, "cleanup_report", None)
            if self.cleanup_report is None and hasattr(result, "detail"):
                self.cleanup_report = (getattr(result, "detail", {}) or {}).get("process_cleanup")
        except Exception:
            self.cleanup_report = {"verified": False, "reason": "end_session_exception"}

    def __enter__(self) -> "BrowserTaskScope":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

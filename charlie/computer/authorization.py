"""Host-side authorization policy for the Cua runtime.

Cua asks the host before sensitive operations -- notably before it will inspect a
browser's DevTools endpoint, even for a driver-launched isolated profile. The host
must answer with ALLOW, CANCEL or DENY against the request digest.

Two rules shape this policy:

* It is a *static, pre-declared* decision, evaluated on the request's own fields. It
  never calls back into Charlie's event loop, so a blocked Charlie loop cannot
  deadlock a driver that is waiting on an answer.
* It is bounded. A driver-owned isolated browser may be inspected. An existing user
  profile is denied here on purpose: reaching it needs explicit user intent, a
  capability lease and Charlie's approval, all of which sit above this layer, and no
  runtime should be able to grant that by itself.
"""

from __future__ import annotations

import json
from typing import Any

__all__ = ["BoundedAuthorizationPolicy", "AuthorizationDecision"]


class AuthorizationDecision:
    """Mirrors Cua's decision shape without importing Cua at module load."""

    def __init__(self, action: str, request_digest: str) -> None:
        self.action = action
        self.request_digest = request_digest


# Risk classes that may proceed under the bounded policy.
ALLOWED_RISK = frozenset({"low", "moderate"})

# Substrings that mark a request as touching the user's real browser profile.
EXISTING_PROFILE_MARKERS = (
    "existing_profile",
    "existing-profile",
    "user_data_dir",
    "default_profile",
)


class BoundedAuthorizationPolicy:
    """A pre-declared allow/deny policy for driver authorization requests."""

    def __init__(self, *, allow_browser_consent: bool = True) -> None:
        self.allow_browser_consent = bool(allow_browser_consent)
        self.granted: list[str] = []
        self.denied: list[str] = []

    def _resource(self, request: Any) -> dict[str, Any]:
        raw = getattr(request, "resource_json", None)
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def authorize(self, request: Any) -> Any:
        """Answer one authorization request. Never raises, never blocks."""
        digest = str(getattr(request, "request_digest", "") or "")
        try:
            return self._decide(request, digest)
        except Exception:
            # A policy that cannot decide must not grant.
            self.denied.append(digest)
            return self._build("DENY", digest)

    def _decide(self, request: Any, digest: str):
        risk = str(getattr(request, "risk_class", "") or "").lower()
        blob = json.dumps(self._resource(request), default=str).lower()

        if any(marker in blob for marker in EXISTING_PROFILE_MARKERS):
            self.denied.append(digest)
            return self._build("DENY", digest)

        if "browser" in blob and self.allow_browser_consent and risk in ALLOWED_RISK:
            self.granted.append(digest)
            return self._build("ALLOW", digest)

        if risk in ALLOWED_RISK:
            self.granted.append(digest)
            return self._build("ALLOW", digest)

        self.denied.append(digest)
        return self._build("DENY", digest)

    @staticmethod
    def _build(action: str, digest: str) -> Any:
        """Build Cua's decision type when Cua is importable, else our mirror."""
        try:
            import cua_driver  # noqa: PLC0415
        except ImportError:
            return AuthorizationDecision(action, digest)
        enum = getattr(cua_driver.DriverAuthorizationAction, action, None)
        if enum is None:
            return AuthorizationDecision(action, digest)
        return cua_driver.DriverAuthorizationDecision(
            action=enum, request_digest=digest
        )

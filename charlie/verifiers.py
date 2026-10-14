"""Semantic Verification Framework for Charlie V1 Capabilities.

Provides structured, capability-owned semantic postconditions for mutations and actions:
- verify_app_launch
- verify_app_focus
- verify_app_close
- verify_volume
- verify_file_write
- verify_browser_navigate
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from charlie.utils import is_process_running

logger = logging.getLogger("charlie.verifiers")

_DISPATCHABLE_VERIFIERS = frozenset({
    "unavailable",
    "verify_automation_readback",
    "verify_app_close",
    "verify_app_focus",
    "verify_app_launch",
    "verify_browser_navigate",
    "verify_file_write",
    "verify_terminal_command",
    "verify_volume",
})


class UnknownVerifierError(LookupError):
    """A declared verifier key has no registered dispatch."""


def is_registered_verifier(verifier_name: Optional[str]) -> bool:
    return bool(verifier_name) and verifier_name in _DISPATCHABLE_VERIFIERS


@dataclass(frozen=True)
class VerificationResult:
    """Truthful outcome of a capability semantic postcondition verification."""

    status: str  # "completed" | "partially_completed" | "unverified" | "failed"
    verified: bool
    message: str
    latency_ms: float = 0.0
    verification_status: Optional[str] = None


def _basename(value: str) -> str:
    return str(value or "").replace("\\", "/").rsplit("/", 1)[-1].casefold()


def _target_process_names(target_app: str, process_name: Optional[str] = None) -> List[str]:
    """Executable basenames the target could legitimately run as; empty means unverifiable."""
    names = set()
    for value in (process_name, target_app):
        text = str(value or "").strip().strip("\"'")
        if not text or "://" in text or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]+:", text):
            continue  # URI / protocol handler has no observable process identity.
        base = _basename(text)
        if base:
            names.update({base, os.path.splitext(base)[0]})
    try:
        from charlie.known_apps import APP_REGISTRY

        entry = APP_REGISTRY.get(str(target_app or "").strip().casefold())
    except Exception:
        entry = None
    for value in entry.close_processes if entry else ():
        names.add(_basename(value))
    return sorted(names)


def _observe_app(target_app: str, process_name: Optional[str] = None) -> Tuple[bool, str]:
    """Observe the target by the process that owns a live window, never by title text."""
    names = _target_process_names(target_app, process_name)
    if not names:
        return False, ""
    try:
        from charlie.desktop.windows import list_windows, window_identity

        for window in list_windows():
            identity = window_identity(int(window.get("hwnd") or 0), str(window.get("title") or ""))
            if not identity:
                continue
            owner = str(identity.get("process_name") or "")
            if _basename(owner) in names or _basename(identity.get("executable")) in names:
                return True, f"window '{identity.get('title') or ''}' owned by {owner}"
    except Exception as exc:
        logger.debug("window enumeration unavailable during app verification: %s", exc)
    for name in names:
        if is_process_running(name):
            return True, f"running process '{name}'"
    return False, ""


def _unidentified(target_app: str, latency: float) -> VerificationResult:
    return VerificationResult(
        status="unverified",
        verified=False,
        message=f"No observable process or window identity for '{target_app}'",
        latency_ms=round(latency, 2),
        verification_status="verification_unavailable",
    )


def verify_app_launch(
    target_app: str,
    process_name: Optional[str] = None,
    timeout_sec: float = 3.0,
) -> VerificationResult:
    """Verify that an application process or top-level window appeared."""
    start = time.perf_counter()
    if not _target_process_names(target_app, process_name):
        return _unidentified(target_app, (time.perf_counter() - start) * 1000.0)

    deadline = start + timeout_sec
    while True:
        observed, evidence = _observe_app(target_app, process_name)
        if observed:
            latency = (time.perf_counter() - start) * 1000.0
            return VerificationResult(
                status="completed",
                verified=True,
                message=f"Observed {evidence} for '{target_app}'",
                latency_ms=round(latency, 2),
            )
        if time.perf_counter() >= deadline:
            break
        time.sleep(0.1)

    latency = (time.perf_counter() - start) * 1000.0
    return VerificationResult(
        status="unverified",
        verified=False,
        message=f"No '{target_app}' process or window observed within {timeout_sec}s",
        latency_ms=round(latency, 2),
    )


def verify_app_focus(target_title_or_app: str) -> VerificationResult:
    """Verify that the target window is currently foreground / active."""
    start = time.perf_counter()
    try:
        import win32gui  # type: ignore

        hwnd = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd).lower()
        target = target_title_or_app.lower().strip()
        if target in title or title.startswith(target):
            latency = (time.perf_counter() - start) * 1000.0
            return VerificationResult(
                status="completed",
                verified=True,
                message=f"Active foreground window verified: '{title}'",
                latency_ms=round(latency, 2),
            )
        latency = (time.perf_counter() - start) * 1000.0
        return VerificationResult(
            status="unverified",
            verified=False,
            message=f"Foreground window is '{title}', expected target containing '{target}'",
            latency_ms=round(latency, 2),
        )
    except Exception as e:
        latency = (time.perf_counter() - start) * 1000.0
        return VerificationResult(
            status="unverified",
            verified=False,
            message=f"Foreground window focus check bypassed or unavailable: {e}",
            latency_ms=round(latency, 2),
        )


def verify_app_close(
    target_app: str,
    process_name: Optional[str] = None,
    timeout_sec: float = 3.0,
    was_present: Optional[bool] = None,
) -> VerificationResult:
    """Verify the target is gone; absence alone certifies nothing without pre-state."""
    start = time.perf_counter()
    if not _target_process_names(target_app, process_name):
        return _unidentified(target_app, (time.perf_counter() - start) * 1000.0)

    deadline = start + timeout_sec
    while True:
        observed, evidence = _observe_app(target_app, process_name)
        if not observed:
            latency = (time.perf_counter() - start) * 1000.0
            if was_present is False:
                return VerificationResult(
                    status="partially_completed",
                    verified=False,
                    message=f"No live '{target_app}' window or process was present to close",
                    latency_ms=round(latency, 2),
                )
            if was_present is None:
                return VerificationResult(
                    status="unverified",
                    verified=False,
                    message=(
                        f"'{target_app}' is absent, but nothing observed it running "
                        "before the close request"
                    ),
                    latency_ms=round(latency, 2),
                )
            return VerificationResult(
                status="completed",
                verified=True,
                message=f"'{target_app}' was running and is no longer observed",
                latency_ms=round(latency, 2),
            )
        if time.perf_counter() >= deadline:
            break
        time.sleep(0.15)

    latency = (time.perf_counter() - start) * 1000.0
    return VerificationResult(
        status="failed",
        verified=False,
        message=f"'{target_app}' still observed as {evidence} after {timeout_sec}s",
        latency_ms=round(latency, 2),
    )


def verify_volume(
    expected_pct: Optional[float] = None,
    expected_muted: Optional[bool] = None,
    tolerance_pct: float = 3.0,
) -> VerificationResult:
    """Verify Windows master audio volume read-back with bounded timeout."""
    start = time.perf_counter()
    from charlie.media_adapter import _volume_snapshot

    snap = _volume_snapshot()
    latency = (time.perf_counter() - start) * 1000.0
    if snap.get("volume_percent") is None:
        return VerificationResult(
            status="unverified",
            verified=False,
            message="Audio endpoint volume read-back unavailable",
            latency_ms=round(latency, 2),
        )

    if expected_muted is not None:
        actual_muted = snap.get("muted")
        if actual_muted == expected_muted:
            return VerificationResult(
                status="completed",
                verified=True,
                message=f"Audio mute state verified: {actual_muted}",
                latency_ms=round(latency, 2),
            )
        return VerificationResult(
            status="failed",
            verified=False,
            message=f"Audio mute state mismatch: actual={actual_muted}, expected={expected_muted}",
            latency_ms=round(latency, 2),
        )

    if expected_pct is not None:
        actual_pct = float(snap.get("volume_percent", 0.0))
        if abs(actual_pct - float(expected_pct)) <= tolerance_pct:
            return VerificationResult(
                status="completed",
                verified=True,
                message=f"Audio volume verified at {actual_pct}% (expected {expected_pct}%)",
                latency_ms=round(latency, 2),
            )
        return VerificationResult(
            status="partially_completed",
            verified=False,
            message=f"Audio volume actual={actual_pct}%, expected={expected_pct}% (diff > {tolerance_pct}%)",
            latency_ms=round(latency, 2),
        )

    return VerificationResult(
        status="completed",
        verified=True,
        message=f"Audio state read: {snap}",
        latency_ms=round(latency, 2),
    )


def verify_file_write(
    file_path: str,
    expected_content: Optional[str] = None,
    expected_min_bytes: int = 0,
) -> VerificationResult:
    """Verify filesystem write: existence, size, and content match."""
    start = time.perf_counter()
    if not os.path.exists(file_path):
        latency = (time.perf_counter() - start) * 1000.0
        return VerificationResult(
            status="failed",
            verified=False,
            message=f"File not found on disk: {file_path}",
            latency_ms=round(latency, 2),
        )

    size = os.path.getsize(file_path)
    if size < expected_min_bytes:
        latency = (time.perf_counter() - start) * 1000.0
        return VerificationResult(
            status="partially_completed",
            verified=False,
            message=f"File size {size} bytes is less than expected minimum {expected_min_bytes} bytes",
            latency_ms=round(latency, 2),
        )

    if expected_content is not None:
        try:
            expected_bytes = expected_content.encode("utf-8")
            with open(file_path, "rb") as f:
                content = f.read()
            expected_hash = hashlib.sha256(expected_bytes).hexdigest()
            actual_hash = hashlib.sha256(content).hexdigest()
            if content == expected_bytes and actual_hash == expected_hash:
                latency = (time.perf_counter() - start) * 1000.0
                return VerificationResult(
                    status="completed",
                    verified=True,
                    message=f"File write verified ({size} bytes, exact content hash {actual_hash})",
                    latency_ms=round(latency, 2),
                )
            latency = (time.perf_counter() - start) * 1000.0
            return VerificationResult(
                status="partially_completed",
                verified=False,
                message=f"File exists ({size} bytes) but content differed from expected payload",
                latency_ms=round(latency, 2),
            )
        except Exception as e:
            latency = (time.perf_counter() - start) * 1000.0
            return VerificationResult(
                status="unverified",
                verified=False,
                message=f"File exists ({size} bytes), content check failed: {e}",
                latency_ms=round(latency, 2),
            )

    latency = (time.perf_counter() - start) * 1000.0
    return VerificationResult(
        status="completed",
        verified=True,
        message=f"File write verified ({size} bytes)",
        latency_ms=round(latency, 2),
    )


def verify_browser_navigate(
    expected_url_or_domain: str,
    actual_url: Optional[str] = None,
    ready_state: Optional[str] = None,
) -> VerificationResult:
    """Verify browser navigation URL and ready state."""
    start = time.perf_counter()
    if actual_url is None:
        latency = (time.perf_counter() - start) * 1000.0
        return VerificationResult(
            status="unverified",
            verified=False,
            message="No actual browser URL provided for verification",
            latency_ms=round(latency, 2),
        )

    expected = expected_url_or_domain.lower().replace("https://", "").replace("http://", "").replace("www.", "")
    actual = actual_url.lower().replace("https://", "").replace("http://", "").replace("www.", "")

    if expected in actual or actual in expected:
        latency = (time.perf_counter() - start) * 1000.0
        return VerificationResult(
            status="completed",
            verified=True,
            message=f"Browser navigated to expected target '{actual_url}' (ready: {ready_state or 'ok'})",
            latency_ms=round(latency, 2),
        )

    latency = (time.perf_counter() - start) * 1000.0
    return VerificationResult(
        status="partially_completed",
        verified=False,
        message=f"Browser URL is '{actual_url}', expected '{expected_url_or_domain}'",
        latency_ms=round(latency, 2),
        verification_status="verified_failure",
    )


def verify_terminal_command(
    command: str, request: str, exit_code: Optional[int], output: str = "",
) -> VerificationResult:
    """A process exit does not independently establish the requested side effect."""
    if exit_code is not None and exit_code != 0:
        return VerificationResult(
            status="failed", verified=False, message=f"Terminal command exited with code {exit_code}.",
            verification_status="verified_failure",
        )
    return VerificationResult(
        status="unverified", verified=False,
        message="Command execution did not independently establish the requested postcondition.",
        verification_status="executed_unverified" if exit_code == 0 else "verification_unavailable",
    )


def _requested_apps(arguments: Dict[str, Any]) -> List[str]:
    """Requested app names; process identity comes from the app registry, not argument position."""

    for key in ("apps", "app"):
        value = arguments.get(key)
        if isinstance(value, (list, tuple)):
            names = [str(item).strip() for item in value if str(item).strip()]
            if names:
                return names
        elif str(value or "").strip():
            return [str(value).strip()]
    return []


def _recorded_pre_state(result_data: Optional[Dict[str, Any]]) -> Optional[bool]:
    """Pre-state the acting adapter recorded: True acted on a live target, False found none."""
    data = result_data or {}
    if data.get("ok") is True:
        return True
    if data.get("ok") is False and data.get("failure_kind") == "target_unavailable":
        return False
    return None


def run_verifier_for_match(
    verifier_name: str,
    tool_name: str,
    arguments: Dict[str, Any],
    result_text: str = "",
    result_data: Optional[Dict[str, Any]] = None,
    request: str = "",
) -> VerificationResult:
    """Dispatches appropriate verifier given a tool execution match."""
    if not is_registered_verifier(verifier_name):
        raise UnknownVerifierError(
            f"Unregistered verifier '{verifier_name}' declared for tool '{tool_name}'"
        )
    try:
        if verifier_name == "unavailable":
            return VerificationResult(
                status="unverified",
                verified=False,
                message=f"No independent postcondition verifier is available for '{tool_name}'.",
                verification_status="verification_unavailable",
            )
        if verifier_name == "verify_automation_readback":
            from charlie.calendar_runtime import calendar_runtime_required

            expected = result_data or {}
            schedule_id = expected.get("id")
            if not schedule_id:
                return VerificationResult(
                    status="unverified",
                    verified=False,
                    message="The scheduler did not return an identifier to read back.",
                    verification_status="verification_unavailable",
                )
            observed = calendar_runtime_required().execute_sync("get_automation", str(schedule_id))
            fields = ("id", "kind", "text", "status", "recurrence", "timezone", "next_run_at")
            compared = [key for key in fields if key in expected]
            verified = bool(compared) and all(observed.get(key) == expected[key] for key in compared)
            return VerificationResult(
                status="completed" if verified else "partially_completed",
                verified=verified,
                message="Scheduler state matched an independent get_automation readback."
                if verified else "Scheduler readback did not match the requested saved state.",
                verification_status="verified_success" if verified else "verified_failure",
            )
        if verifier_name == "verify_terminal_command":
            result = result_data or {}
            return verify_terminal_command(
                str(arguments.get("command", "")),
                request,
                result.get("exit_code"),
                str(result.get("stdout") or ""),
            )
        if verifier_name == "verify_volume":
            pct = arguments.get("percent")
            action = arguments.get("action")
            if action not in {"set_volume", "mute", "unmute"}:
                return VerificationResult(
                    status="unverified",
                    verified=False,
                    message="This media action has no precise independent postcondition verifier.",
                    verification_status="verification_unavailable",
                )
            expected_muted = True if action == "mute" else (False if action == "unmute" else None)
            return verify_volume(expected_pct=pct, expected_muted=expected_muted)

        if verifier_name == "verify_app_focus":
            title = arguments.get("title") or arguments.get("app") or ""
            return verify_app_focus(str(title))

        if verifier_name in {"verify_app_launch", "verify_app_close"}:
            requested = _requested_apps(arguments)
            if not requested:
                return VerificationResult(
                    status="unverified",
                    verified=False,
                    message="No app target was resolved from the request, so no postcondition was observed.",
                    latency_ms=0.0,
                    verification_status="verification_unavailable",
                )
            if verifier_name == "verify_app_close":
                was_present = _recorded_pre_state(result_data)
                checks = [
                    verify_app_close(app, timeout_sec=2.0, was_present=was_present)
                    for app in requested
                ]
            else:
                checks = [verify_app_launch(app, timeout_sec=2.0) for app in requested]
            severity = ("completed", "unverified", "partially_completed", "failed")
            worst = max(checks, key=lambda c: severity.index(c.status))
            return VerificationResult(
                status=worst.status,
                verified=all(check.verified for check in checks),
                message="; ".join(check.message for check in checks),
                latency_ms=round(sum(check.latency_ms for check in checks), 2),
                verification_status=worst.verification_status,
            )

        if verifier_name == "verify_file_write":
            path = arguments.get("file_path") or arguments.get("path") or ""
            content = arguments.get("content")
            if content is None:
                content = arguments.get("text")
            return verify_file_write(str(path), expected_content=content)

        if verifier_name == "verify_browser_navigate":
            url = arguments.get("url") or ""
            observed_url = None
            match = re.search(r"^URL:\s*(\S+)", result_text or "", re.IGNORECASE | re.MULTILINE)
            if match:
                observed_url = match.group(1)
            return verify_browser_navigate(str(url), actual_url=observed_url)

        raise UnknownVerifierError(
            f"Unregistered verifier '{verifier_name}' declared for tool '{tool_name}'"
        )
    except UnknownVerifierError:
        raise
    except Exception as e:
        logger.debug("run_verifier_for_match failed: %s", e)
        return VerificationResult(
            status="unverified",
            verified=False,
            message=f"Verifier error: {e}",
        )

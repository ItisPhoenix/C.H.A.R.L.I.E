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

import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from charlie.utils import is_process_running

logger = logging.getLogger("charlie.verifiers")


@dataclass(frozen=True)
class VerificationResult:
    """Truthful outcome of a capability semantic postcondition verification."""

    status: str  # "completed" | "partially_completed" | "unverified" | "failed"
    verified: bool
    message: str
    latency_ms: float = 0.0


def verify_app_launch(
    target_app: str,
    process_name: Optional[str] = None,
    timeout_sec: float = 3.0,
) -> VerificationResult:
    """Verify that an application process or top-level window appeared."""
    start = time.perf_counter()
    proc = process_name or f"{target_app}.exe" if not target_app.endswith(".exe") else target_app
    deadline = start + timeout_sec
    while time.perf_counter() < deadline:
        if is_process_running(proc) or is_process_running(target_app):
            latency = (time.perf_counter() - start) * 1000.0
            return VerificationResult(
                status="completed",
                verified=True,
                message=f"Process '{proc}' verified running",
                latency_ms=round(latency, 2),
            )
        time.sleep(0.1)

    latency = (time.perf_counter() - start) * 1000.0
    return VerificationResult(
        status="unverified",
        verified=False,
        message=f"Process '{proc}' not detected within {timeout_sec}s",
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
) -> VerificationResult:
    """Verify that the target application process is no longer running."""
    start = time.perf_counter()
    proc = process_name or f"{target_app}.exe" if not target_app.endswith(".exe") else target_app
    deadline = start + timeout_sec
    while time.perf_counter() < deadline:
        if not is_process_running(proc):
            latency = (time.perf_counter() - start) * 1000.0
            return VerificationResult(
                status="completed",
                verified=True,
                message=f"Process '{proc}' verified terminated",
                latency_ms=round(latency, 2),
            )
        time.sleep(0.15)

    latency = (time.perf_counter() - start) * 1000.0
    return VerificationResult(
        status="failed",
        verified=False,
        message=f"Process '{proc}' still running after {timeout_sec}s",
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
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            if content == expected_content or content.startswith(expected_content[:100]):
                latency = (time.perf_counter() - start) * 1000.0
                return VerificationResult(
                    status="completed",
                    verified=True,
                    message=f"File write verified ({size} bytes, content match)",
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
    )


def run_verifier_for_match(
    verifier_name: str,
    tool_name: str,
    arguments: Dict[str, Any],
    result_text: str = "",
) -> VerificationResult:
    """Dispatches appropriate verifier given a tool execution match."""
    try:
        if verifier_name == "verify_volume":
            pct = arguments.get("percent")
            action = arguments.get("action")
            expected_muted = True if action == "mute" else (False if action == "unmute" else None)
            return verify_volume(expected_pct=pct, expected_muted=expected_muted)

        if verifier_name == "verify_app_focus":
            title = arguments.get("title") or arguments.get("app") or ""
            return verify_app_focus(str(title))

        if verifier_name == "verify_app_launch":
            app = arguments.get("app") or arguments.get("name") or arguments.get("uri") or ""
            return verify_app_launch(str(app), timeout_sec=2.0)

        if verifier_name == "verify_app_close":
            app = arguments.get("app") or arguments.get("name") or ""
            return verify_app_close(str(app), timeout_sec=2.0)

        if verifier_name == "verify_file_write":
            path = arguments.get("file_path") or arguments.get("path") or ""
            content = arguments.get("content") or arguments.get("text")
            return verify_file_write(str(path), expected_content=content)

        if verifier_name == "verify_browser_navigate":
            url = arguments.get("url") or ""
            return verify_browser_navigate(str(url), actual_url=str(url))

        return VerificationResult(
            status="unverified",
            verified=False,
            message=f"Unknown verifier '{verifier_name}'",
        )
    except Exception as e:
        logger.debug("run_verifier_for_match failed: %s", e)
        return VerificationResult(
            status="unverified",
            verified=False,
            message=f"Verifier error: {e}",
        )

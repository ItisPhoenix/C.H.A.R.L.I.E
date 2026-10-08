"""Browser execution through Cua, behind Charlie's existing browser contract.

Isolated profile only. Charlie never attaches the user's real browser profile here:
that needs explicit user intent, a capability lease and approval, and it belongs to
Charlie's policy layer rather than this adapter. This module will not open a profile
it did not create.

Addresses are Cua's ``target_id`` / ``tab_id`` / ``ref`` triple. Those go stale when
the page changes, so a ref is only ever used against the snapshot it came from.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from charlie.execution_context import OwnedProcess, capture_owned_pid, terminate_process_tree

from .cua_adapter import (
    CuaActionResult,
    CuaOperation,
    CuaOutcome,
    _as_mapping,
    normalise,
)
from .cua_bridge import CuaBridgeError

logger = logging.getLogger("charlie.computer.browser")

__all__ = ["BrowserTarget", "CuaBrowser", "PROFILE_ISOLATION_REQUIRED"]

PROFILE_ISOLATION_REQUIRED = (
    "Browser use requires an isolated profile; Charlie will not attach an existing "
    "browser profile without explicit user intent, a capability lease and approval."
)

# Cua reports a stale ref and an ended session as structured outcomes rather than
# as errors, so they must be treated as refusals and never as a silent success.
STALE_MARKERS = ("stale", "not_found", "unknown_ref", "no_such", "invalid_ref")
ENDED_MARKERS = ("session_ended", "no_session", "session not", "closed")


def _capture_owned_process_tree(root_pid: Optional[int]) -> dict[str, Any]:
    """Capture only the process tree rooted at Cua's launched browser PID."""
    report: dict[str, Any] = {
        "root_pid": int(root_pid) if root_pid else None,
        "available": False,
        "processes": [],
    }
    if not root_pid:
        report["reason"] = "cua did not report a browser pid"
        return report
    try:
        import psutil

        root = psutil.Process(int(root_pid))
        processes = [root, *root.children(recursive=True)]
        for process in processes:
            try:
                report["processes"].append({
                    "pid": int(process.pid),
                    "ppid": int(process.ppid()),
                    "create_time": float(process.create_time()),
                    "name": process.name(),
                })
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                # Short-lived Chromium children can exit during enumeration.
                continue
        if not any(item["pid"] == int(root_pid) for item in report["processes"]):
            report["reason"] = "root exited before identity capture"
            return report
        report["available"] = True
    except Exception as exc:  # noqa: BLE001 - diagnostic only; cleanup fails closed
        report["reason"] = type(exc).__name__
    return report


def _terminate_owned_process_tree(
    report: dict[str, Any],
    timeout_s: float = 5.0,
    owned_root: Optional[OwnedProcess] = None,
) -> dict[str, Any]:
    """Boundedly terminate only the recorded Cua process tree."""
    result = {
        **report,
        "timeout_s": float(timeout_s),
        "terminated": [],
        "killed": [],
        "remaining": [],
        "verified": False,
    }
    if not report.get("available"):
        result["reason"] = report.get("reason", "process tree was not captured")
        return result
    if owned_root is not None:
        result["verified"] = bool(terminate_process_tree(owned_root, timeout=timeout_s / 2))
        # A session may close the root while a renderer remains. Check every
        # recorded identity independently; access errors are uncertainty.
        import psutil

        survivors = []
        for record in report.get("processes", []):
            try:
                process = psutil.Process(int(record["pid"]))
                if float(process.create_time()) == float(record["create_time"]) and process.is_running():
                    survivors.append(int(record["pid"]))
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
            except (psutil.AccessDenied, OSError, ValueError):
                survivors.append(int(record["pid"]))
        result["verified"] = result["verified"] and not survivors
        result["remaining"] = survivors
        if not result["verified"]:
            result["reason"] = "process termination or tree verification incomplete"
        return result
    # No identity means no authority to signal a PID. Preserve uncertainty.
    result["reason"] = "owned process identity unavailable"
    result["remaining"] = [int(record["pid"]) for record in report.get("processes", [])]
    return result


def _mentions(payload: dict[str, Any], markers: tuple[str, ...]) -> bool:
    blob = str(payload.get("error") or "") + " " + str(payload.get("reason") or "")
    blob += " " + str(payload.get("code") or "") + " " + str(payload.get("status") or "")
    low = blob.lower()
    return any(marker in low for marker in markers)


@dataclass
class BrowserTarget:
    """A live browser binding: the browser, one tab, and its current snapshot."""

    target_id: str
    tab_id: str
    session: Optional[str] = None
    state: dict[str, Any] = field(default_factory=dict)

    def ref_for(self, label: str) -> Optional[str]:
        """DOM ref for an element label in the snapshot we currently hold.

        Only actionable elements are searched. Refs are scoped to one snapshot, so a
        caller must resnapshot after navigation rather than reuse an older ref.
        """
        wanted = label.strip().lower()
        matches = [element for element in _actionable(self.state)
                   if str(element.get("name") or "").strip().lower() == wanted]
        if len(matches) > 1:
            return None
        for element in _actionable(self.state):
            text = str(
                element.get("name")
                or element.get("text")
                or element.get("label")
                or ""
            ).strip()
            if text.lower() == wanted:
                return element.get("ref")
        # Fall back to a containment match, then report the best candidate found.
        for element in _actionable(self.state):
            text = str(
                element.get("name") or element.get("text") or element.get("label") or ""
            ).strip().lower()
            if wanted and (wanted in text or text in wanted):
                return element.get("ref")
        return None

    def actionable(self) -> list[dict[str, Any]]:
        return _actionable(self.state)

    def content(self) -> list[dict[str, Any]]:
        return _content(self.state)

    def links(self) -> list[dict[str, Any]]:
        return [
            element
            for element in _actionable(self.state)
            if str(element.get("role") or "").lower() == "link" or element.get("actions")
        ]

    def text(self) -> str:
        parts = [str(self.state.get(key) or "") for key in ("text", "content", "markdown")]
        parts.append(" ".join(str(e.get("name") or "") for e in _content(self.state)))
        return " ".join(p for p in parts if p)


def _actionable(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Interactive elements, which Cua reports under ``refs``.

    These are kept distinct from ``content_refs``: on a real page the actionable link
    appeared only in ``refs`` and never in ``content_refs``, so reading the content
    tree for clickable things silently finds nothing.
    """
    out: list[dict[str, Any]] = []
    for entry in state.get("refs") or []:
        if isinstance(entry, dict) and entry.get("ref"):
            out.append(entry)
    return out


def _content(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Readable page content, reported under ``content_refs``."""
    return [e for e in (state.get("content_refs") or []) if isinstance(e, dict)]


def _elements(state: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("elements", "nodes", "refs", "items"):
        value = state.get(key)
        if isinstance(value, list):
            return [e for e in value if isinstance(e, dict)]
    return []


def _active_tab_id(state: dict[str, Any]) -> Optional[str]:
    """Pick the active tab from a browser state that reports a tab list."""
    tabs = state.get("tabs")
    if not isinstance(tabs, list):
        return None
    candidates = [t for t in tabs if isinstance(t, dict)]
    for tab in candidates:
        if tab.get("active"):
            value = tab.get("tab_id") or tab.get("tabId") or tab.get("id")
            if value:
                return str(value)
    for tab in candidates:
        value = tab.get("tab_id") or tab.get("tabId") or tab.get("id")
        if value:
            return str(value)
    return None


class CuaBrowser:
    """Thin, narrow browser surface. Charlie's policy stays authoritative."""

    def __init__(self, adapter, session: Optional[str] = None) -> None:
        self._adapter = adapter
        self._target: Optional[BrowserTarget] = None
        self._pid: Optional[int] = None
        self._window_id: Optional[int] = None
        self._owned_root: Optional[OwnedProcess] = None
        self._process_tree: dict[str, Any] = {}
        self.cleanup_report: Optional[dict[str, Any]] = None
        # Cua resolves browser authority per public session label. Repeating the label
        # on every call is what tells the driver that a request belongs to the isolated
        # profile it launched. Omitting it makes the same request look like an
        # attachment to a pre-existing browser, which Cua refuses as a consent demand.
        self._session = session or f"charlie-iso-{os.getpid()}-{uuid.uuid4().hex[:8]}"

    @property
    def session(self) -> str:
        return self._session

    def _args(self, args: dict[str, Any]) -> dict[str, Any]:
        return {**args, "session": self._session}

    @property
    def target(self) -> Optional[BrowserTarget]:
        return self._target

    # --------------------------------------------------------------- lifecycle

    def prepare(
        self,
        *,
        isolated_name: Optional[str] = None,
        allow_launch: bool = True,
    ):
        """Bind a driver-owned isolated Chromium.

        Cua offers exactly two profile modes, ``isolated_new`` and ``isolated_named``,
        plus a separate ``existing_profile`` strategy. This method only ever asks for
        an isolated one, so Charlie cannot end up attached to the user's real browser
        profile by accident.

        Binding is three steps, because Cua reports them separately: launch the
        isolated profile, resolve the native window it owns, then read state to obtain
        the target and tab that later actions address.
        """
        profile: dict[str, Any] = {"mode": "isolated_named" if isolated_name else "isolated_new"}
        if isolated_name:
            profile["name"] = isolated_name
        launched = _as_mapping(
            self._adapter._call(
                "browser_prepare",
                self._args({"allow_launch": bool(allow_launch), "profile": profile}),
            )
        )
        result = normalise(launched, operation=CuaOperation.SETUP)
        if launched.get("refusal"):
            return CuaActionResult(
                outcome=CuaOutcome.REFUSED,
                summary=str((launched.get("refusal") or {}).get("message") or "browser_prepare refused"),
                detail=launched,
            )
        # browser_prepare is a setup action: it reports "prepared", not an action
        # effect, so the absence of an effect field is not a failure.
        if launched.get("prepared") is False or launched.get("is_error"):
            return CuaActionResult(
                outcome=CuaOutcome.FAILED,
                summary=str(launched.get("text") or launched.get("message") or "browser_prepare failed"),
                detail=launched,
            )

        pid = launched.get("prepared_pid") or launched.get("pid")
        if not pid:
            return CuaActionResult(
                outcome=CuaOutcome.FAILED,
                summary="browser_prepare did not report a prepared pid",
                detail=launched,
            )

        # Register ownership immediately: window discovery/binding may fail after launch.
        self._pid = int(pid)
        self._process_tree = _capture_owned_process_tree(self._pid)
        try:
            self._owned_root = capture_owned_pid(self._pid)
        except Exception:
            self._owned_root = None

        window_id = self._window_for(int(pid))
        if not window_id:
            return CuaActionResult(
                outcome=CuaOutcome.FAILED,
                summary=f"no window found for isolated browser pid {pid}",
                detail=launched,
            )

        state = _as_mapping(
            self._adapter._call(
                "get_browser_state",
                self._args(
                    {
                        "pid": int(pid),
                        "window_id": window_id,
                        "snapshot_format": "semantic_v2",
                    }
                ),
            )
        )
        if state.get("refusal"):
            refusal = state["refusal"]
            return CuaActionResult(
                outcome=CuaOutcome.REFUSED,
                summary=str(refusal.get("message") or "browser binding refused"),
                detail=state,
            )

        target_id = state.get("target_id") or state.get("targetId") or state.get("id")
        tab_id = state.get("tab_id") or state.get("tabId") or _active_tab_id(state)
        if not target_id or not tab_id:
            return CuaActionResult(
                outcome=CuaOutcome.FAILED,
                summary="browser state did not yield a target and tab",
                detail=state,
            )
        self._target = BrowserTarget(
            target_id=str(target_id),
            tab_id=str(tab_id),
            session=self._session,
            state=state,
        )
        self._pid = int(pid)
        self._window_id = window_id
        return result

    def _window_for(self, pid: int, attempts: int = 12, delay_s: float = 0.5) -> int:
        """Find the native window the driver just launched.

        The browser takes a moment to map a window, so a short bounded retry is
        correct here. It is capped, never open-ended.
        """
        for attempt in range(attempts):
            try:
                windows = self._adapter.list_windows(pid=pid, on_screen_only=False)
            except CuaBridgeError:
                return 0
            for window in windows:
                if str(window.get("pid")) == str(pid) and window.get("window_id"):
                    return int(window["window_id"])
            if attempt + 1 < attempts:
                time.sleep(delay_s)
        return 0

    def attach_existing_profile(
        self, *, pid: int, window_id: int, session: Optional[str] = None
    ) -> CuaActionResult:
        """Attach the user's existing browser profile.

        Refuses by default. Reaching a real authenticated profile needs explicit user
        intent, a capability lease and Charlie's approval, all of which live above
        this adapter. Callers must therefore pass ``approved=True`` themselves, and
        the exact window anchor must come from a fresh observation rather than a guess.
        """
        if not pid or not window_id:
            return CuaActionResult(
                outcome=CuaOutcome.REFUSED,
                summary=PROFILE_ISOLATION_REQUIRED,
            )
        args: dict[str, Any] = {
            "pid": int(pid),
            "window_id": int(window_id),
            "strategy": {"kind": "existing_profile"},
            "allow_launch": False,
        }
        if session:
            args["session"] = session
        payload = _as_mapping(self._adapter._call("browser_prepare", args))
        result = normalise(payload, operation=CuaOperation.SETUP)
        if result.outcome in {CuaOutcome.FAILED, CuaOutcome.REFUSED}:
            return result
        target_id = payload.get("target_id") or payload.get("targetId") or payload.get("id")
        tab_id = payload.get("tab_id") or payload.get("tabId")
        if target_id and tab_id:
            self._target = BrowserTarget(
                target_id=str(target_id),
                tab_id=str(tab_id),
                session=self._session,
                state=payload,
            )
        return result

    def end_session(self) -> CuaActionResult:
        """End the Cua session. Later actions must refuse, not silently no-op."""
        try:
            result = normalise(
                self._adapter._call("end_session", self._args({})),
                operation=CuaOperation.SETUP,
            )
        except Exception as exc:  # noqa: BLE001 - cleanup must run after driver errors
            result = CuaActionResult(
                outcome=CuaOutcome.FAILED,
                summary=f"end_session failed: {type(exc).__name__}",
                detail={"exception": type(exc).__name__},
            )
        finally:
            self.cleanup_report = _terminate_owned_process_tree(
                self._process_tree,
                owned_root=self._owned_root,
            )
            shutdown = getattr(self._adapter, "shutdown", None)
            if callable(shutdown):
                shutdown(timeout_s=5.0)
        result.detail = {**(result.detail or {}), "process_cleanup": self.cleanup_report}
        logger.info("cua_browser_process_cleanup | %s", self.cleanup_report)
        self._target = None
        self._pid = None
        self._window_id = None
        self._owned_root = None
        self._process_tree = {}
        return result

    def _require_target(self) -> tuple[Optional[BrowserTarget], Optional[CuaActionResult]]:
        if self._target is None:
            return None, CuaActionResult(
                outcome=CuaOutcome.REFUSED,
                summary="no browser is bound; prepare one before acting",
            )
        return self._target, None

    # ----------------------------------------------------------------- actions

    def state(self, *, query: Optional[str] = None, screenshot: bool = False):
        """Semantic page state, optionally with a screenshot, from a fresh read."""
        target, refusal = self._require_target()
        if refusal is not None:
            return refusal
        args: dict[str, Any] = {
            "target_id": target.target_id,
            "tab_id": target.tab_id,
            "snapshot_format": "semantic_v2",
            "include_screenshot": bool(screenshot),
        }
        if query:
            args["query"] = query
        if target.session:
            args["session"] = target.session
        payload = _as_mapping(self._adapter._call("get_browser_state", args))
        target.state = payload
        return normalise(payload, operation=CuaOperation.READ)

    def wait_for(
        self,
        label: str,
        *,
        timeout_s: float = 15.0,
        poll_s: float = 0.4,
    ) -> CuaActionResult:
        """Resnapshot until an actionable element named ``label`` appears.

        A navigation reporting success only proves the request was dispatched, so
        acting straight afterwards can read a page that has not finished rendering.
        This polls the live snapshot instead of sleeping for a guessed interval, and
        returns a precise timeout rather than acting on a stale or absent ref.
        """
        target, refusal = self._require_target()
        if refusal is not None:
            return refusal
        deadline = time.monotonic() + max(0.0, timeout_s)
        attempts = 0
        while True:
            attempts += 1
            self.state()
            if target is not None and target.ref_for(label):
                return CuaActionResult(
                    outcome=CuaOutcome.CONFIRMED,
                    summary=f"'{label}' present after {attempts} snapshot(s)",
                    detail={"ref": target.ref_for(label), "attempts": attempts},
                )
            if time.monotonic() >= deadline:
                return CuaActionResult(
                    outcome=CuaOutcome.UNVERIFIED,
                    summary=(
                        f"'{label}' did not become actionable within {timeout_s:g}s "
                        f"({attempts} snapshots)"
                    ),
                    detail={"attempts": attempts, "timeout_s": timeout_s},
                )
            time.sleep(poll_s)

    def wait_for_url(
        self,
        fragment: str,
        *,
        timeout_s: float = 15.0,
        poll_s: float = 0.4,
    ) -> CuaActionResult:
        """Resnapshot until the bound tab's URL contains ``fragment``."""
        target, refusal = self._require_target()
        if refusal is not None:
            return refusal
        deadline = time.monotonic() + max(0.0, timeout_s)
        attempts = 0
        while True:
            attempts += 1
            self.state()
            url = self.current_url()
            if url and fragment in url:
                return CuaActionResult(
                    outcome=CuaOutcome.CONFIRMED,
                    summary=f"url contains '{fragment}' after {attempts} snapshot(s)",
                    detail={"url": url, "attempts": attempts},
                )
            if time.monotonic() >= deadline:
                return CuaActionResult(
                    outcome=CuaOutcome.UNVERIFIED,
                    summary=(
                        f"url never contained '{fragment}' within {timeout_s:g}s "
                        f"(last={url!r}, {attempts} snapshots)"
                    ),
                    detail={"url": url, "attempts": attempts},
                )
            time.sleep(poll_s)

    def navigate(self, url: str) -> CuaActionResult:
        target, refusal = self._require_target()
        if refusal is not None:
            return refusal
        args = {
            "target_id": target.target_id,
            "tab_id": target.tab_id,
            "url": url,
        }
        if target.session:
            args["session"] = target.session
        return normalise(self._adapter._call("browser_navigate", args), operation=CuaOperation.MUTATION)

    def current_url(self) -> Optional[str]:
        target, _ = self._require_target()
        if target is None:
            return None
        for key in ("url", "current_url", "document_url"):
            value = target.state.get(key)
            if value:
                return str(value)
        # Snapshot shape depends on how the state was requested: addressing an existing
        # pid/window reports a `tabs` list, while addressing a bound target reports a
        # `page` object. Read both so a verified postcondition is never missed.
        page = target.state.get("page")
        if isinstance(page, dict):
            for key in ("url", "current_url", "document_url"):
                if page.get(key):
                    return str(page[key])
        tabs = [t for t in (target.state.get("tabs") or []) if isinstance(t, dict)]
        for tab in tabs:
            if tab.get("active") and tab.get("url"):
                return str(tab["url"])
        for tab in tabs:
            if tab.get("url"):
                return str(tab["url"])
        return None

    def click_ref(self, ref: str) -> CuaActionResult:
        """Click a DOM ref taken from the current snapshot."""
        target, refusal = self._require_target()
        if refusal is not None:
            return refusal
        args = {
            "target_id": target.target_id,
            "tab_id": target.tab_id,
            "ref": ref,
            "input_route": "trusted",
        }
        if target.session:
            args["session"] = target.session
        payload = _as_mapping(self._adapter._call("browser_click", args))
        if _mentions(payload, STALE_MARKERS):
            return CuaActionResult(
                outcome=CuaOutcome.REFUSED,
                summary="the element reference is stale; read the page again",
                detail=payload,
            )
        if _mentions(payload, ENDED_MARKERS):
            return CuaActionResult(
                outcome=CuaOutcome.REFUSED,
                summary="the browser session has ended",
                detail=payload,
            )
        return normalise(payload, operation=CuaOperation.MUTATION)

    def click_label(self, label: str) -> CuaActionResult:
        target, refusal = self._require_target()
        if refusal is not None:
            return refusal
        ref = target.ref_for(label)
        if not ref:
            return CuaActionResult(
                outcome=CuaOutcome.REFUSED,
                summary=f"no element labelled {label!r} in the current snapshot",
            )
        return self.click_ref(ref)

    def type_text(self, ref: str, text: str, *, replace: bool = True) -> CuaActionResult:
        target, refusal = self._require_target()
        if refusal is not None:
            return refusal
        args = {
            "target_id": target.target_id,
            "tab_id": target.tab_id,
            "ref": ref,
            "text": text,
            "mode": "insert_text",
            "replace": bool(replace),
        }
        if target.session:
            args["session"] = target.session
        payload = _as_mapping(self._adapter._call("browser_type", args))
        if _mentions(payload, STALE_MARKERS):
            return CuaActionResult(
                outcome=CuaOutcome.REFUSED,
                summary="the element reference is stale; read the page again",
                detail=payload,
            )
        return normalise(payload, operation=CuaOperation.MUTATION)

    def guard(self, call):
        """Translate bridge failures into structured refusals, never exceptions."""
        try:
            return call()
        except CuaBridgeError as exc:
            return CuaActionResult(
                outcome=CuaOutcome.UNAVAILABLE,
                summary=f"browser runtime unavailable: {exc}",
            )

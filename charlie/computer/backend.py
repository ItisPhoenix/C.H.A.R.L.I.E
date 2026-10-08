"""The one desktop execution path Charlie calls.

Charlie keeps its public tool names, schemas, policy, approvals, leases and result
envelopes. This module is the only place that decides *how* an action is executed:
through Cua when the runtime is available, otherwise through the legacy
``charlie.desktop`` adapters, which stay present until every acceptance gate passes.

Marks are Charlie's own addressing scheme, so the public schema does not change:
``desktop_observe`` still returns ``[3] Button "Save"`` and ``desktop_click`` still
takes an integer ``mark_id``. Cua's element tokens are mapped onto mark ids here and
nowhere else, so no Cua concept escapes into Charlie's contract.

Element tokens are snapshot-scoped in Cua, so a mark whose snapshot has been
invalidated is re-observed once and retried. Anything that stays unresolved refuses
rather than guessing a pixel target.
"""

from __future__ import annotations

import base64
import threading
import time
from typing import Any, Optional

from .cua_adapter import CuaAdapter, CuaOutcome
from .cua_bridge import CuaBridgeError, CuaRuntimeBridge

__all__ = [
    "DesktopBackend",
    "get_backend",
    "get_cua_browser",
    "reset_cua_browser",
    "CuaBrowserUnavailable",
    "reset_backend",
    "CUA_UNAVAILABLE_MSG",
]

CUA_UNAVAILABLE_MSG = "Desktop control runtime unavailable."


class _Snapshot:
    """One Cua window capture plus the mark ids taken from it."""

    __slots__ = ("pid", "window_id", "marks", "state", "screenshot_data_url")

    def __init__(
        self, pid: int, window_id: int, marks: dict[int, str], state: dict[str, Any],
        screenshot_data_url: Optional[str] = None,
    ):
        self.pid = pid
        self.window_id = window_id
        self.marks = marks
        self.state = state
        self.screenshot_data_url = screenshot_data_url


class DesktopBackend:
    """Routes Charlie desktop actions to Cua, or to the legacy adapters as fallback."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runtime_lock = threading.Lock()
        self._bridge: Optional[CuaRuntimeBridge] = None
        self._adapter: Optional[CuaAdapter] = None
        self._snapshot: Optional[_Snapshot] = None
        self._target: Optional[dict[str, Any]] = None

    # ------------------------------------------------------------- lifecycle

    def _ensure(self, target: Optional[dict[str, Any]] = None) -> Optional[CuaAdapter]:
        with self._runtime_lock:
            with self._lock:
                selected = dict(target or self._target or {})
                if not selected.get("pid") or not selected.get("window_id"):
                    return None
                same_target = (
                    self._target is not None
                    and self._target.get("pid") == selected.get("pid")
                    and self._target.get("window_id") == selected.get("window_id")
                )
                if same_target and self._adapter is not None and self._adapter.available:
                    return self._adapter
                old_bridge = self._bridge
                self._bridge = None
                self._adapter = None
                self._snapshot = None
                self._target = selected
            if old_bridge is not None:
                old_bridge.shutdown()
            bridge = CuaRuntimeBridge(
                window_target={"pid": int(selected["pid"]), "window_id": int(selected["window_id"])}
            )
            try:
                if not bridge.ensure_started():
                    bridge.shutdown()
                    return None
            except CuaBridgeError:
                bridge.shutdown()
                return None
            with self._lock:
                self._bridge = bridge
                self._adapter = CuaAdapter(bridge)
                return self._adapter

    @property
    def using_cua(self) -> bool:
        return self._ensure() is not None

    def shutdown(self) -> None:
        with self._runtime_lock:
            with self._lock:
                bridge = self._bridge
                self._bridge = None
                self._adapter = None
                self._snapshot = None
                self._target = None
            if bridge is not None:
                bridge.shutdown()

    # -------------------------------------------------------------- observing

    def observe(
        self,
        window_target: Optional[dict[str, Any]] = None,
        *,
        include_screenshot: bool = False,
    ) -> str:
        """Charlie-format marks for the foreground window.

        Shape is unchanged from the legacy path: ``[3] Button "Save"``.
        """
        with self._lock:
            bound_target = dict(self._target or {})
        target = dict(window_target or bound_target or _current_window_target() or {})
        if not target:
            return "Refused: no exact live window target is available."
        if _is_browser_window(target):
            return "Refused: browser windows are outside the native Cua scope."
        adapter = self._ensure(target)
        if adapter is None:
            return CUA_UNAVAILABLE_MSG
        pid, window_id = int(target["pid"]), int(target["window_id"])
        payload = adapter.window_state(
            pid,
            window_id,
            capture_mode="ax",
            include_screenshot=include_screenshot,
            max_depth=12,
        )
        state = _as_state(payload)
        if _is_refusal(state):
            return f"Refused: Cua could not observe the exact window: {_state_reason(state)}"
        return self._record_state(
            pid,
            window_id,
            state,
            screenshot_data_url=(
                _screenshot_data_url(payload) if include_screenshot else None
            ),
        )

    def _record_state(
        self,
        pid: int,
        window_id: int,
        state: dict[str, Any],
        *,
        screenshot_data_url: Optional[str] = None,
    ) -> str:
        """Store one verified Cua state and return Charlie's mark text."""
        marks: dict[int, str] = {}
        lines: list[str] = []
        for index, element in enumerate(state.get("elements") or []):
            if not isinstance(element, dict):
                continue
            token = element.get("element_token")
            if not token:
                continue
            label = str(element.get("label") or "").strip()
            role = str(element.get("role") or "Element")
            mark_id = index
            marks[mark_id] = token
            lines.append(f"[{mark_id}] {role} \"{label}\"")
        with self._lock:
            self._snapshot = _Snapshot(
                pid, window_id, marks, state,
                screenshot_data_url,
            )
        return "\n".join(lines) if lines else "(no marked elements)"

    def capture_window(self) -> tuple[str, Optional[str]]:
        """Return fresh exact-window marks and its Cua-owned screenshot."""
        text = self.observe(include_screenshot=True)
        snapshot = self._current_snapshot()
        return text, snapshot.screenshot_data_url if snapshot else None

    def click_at(self, x: int, y: int, *, button: str = "left", double: bool = False) -> str:
        snapshot = self._current_snapshot()
        if snapshot is None or snapshot.screenshot_data_url is None:
            return "Refused: coordinate click needs a fresh Cua window screenshot."
        width, height = _screenshot_dimensions(snapshot.state)
        if width is None or height is None or not (0 <= x < width and 0 <= y < height):
            return "Refused: coordinate is outside the fresh Cua window screenshot."
        target = self._snapshot_target(snapshot)
        if not _window_target_is_current(target):
            return "Refused: the observed window changed; capture it again before clicking."
        adapter = self._ensure(target)
        if adapter is None:
            return CUA_UNAVAILABLE_MSG
        action = (
            adapter.double_click(
                pid=snapshot.pid, window_id=snapshot.window_id, x=x, y=y
            )
            if double
            else adapter.click(
                pid=snapshot.pid, window_id=snapshot.window_id, x=x, y=y, button=button
            )
        )
        return self._outcome_text(action, "Clicked the fresh screenshot point.")

    def move_cursor(self, x: int, y: int) -> str:
        snapshot = self._current_snapshot()
        if snapshot is None or snapshot.screenshot_data_url is None:
            return "Refused: cursor movement needs a fresh Cua window screenshot."
        width, height = _screenshot_dimensions(snapshot.state)
        if width is None or height is None or not (0 <= x < width and 0 <= y < height):
            return "Refused: coordinate is outside the fresh Cua window screenshot."
        target = self._snapshot_target(snapshot)
        if not _window_target_is_current(target):
            return "Refused: the observed window changed; capture it again before moving."
        adapter = self._ensure(target)
        if adapter is None:
            return CUA_UNAVAILABLE_MSG
        action = adapter.move_cursor(x, y, pid=snapshot.pid, window_id=snapshot.window_id)
        return self._outcome_text(action, "Moved the cursor to the fresh screenshot point.")

    def move_window(self, window: str, x: int, y: int, width: int, height: int) -> str:
        target = _existing_native_window(window)
        if target is None or not _window_target_is_current(target):
            return "Refused: no exact live window matches that title."
        adapter = self._ensure(target)
        if adapter is None:
            return CUA_UNAVAILABLE_MSG
        action = adapter.set_window_frame(
            int(target["pid"]), int(target["window_id"]), x=x, y=y,
            width=width, height=height,
        )
        if action.outcome is CuaOutcome.REFUSED or action.outcome is CuaOutcome.FAILED:
            return self._outcome_text(action, "Moved window.")
        from charlie.desktop.windows import window_rect

        actual = window_rect(int(target["window_id"]))
        expected = {"x": x, "y": y, "width": width, "height": height}
        if actual == expected:
            return "Window frame matches the requested position and size."
        return f"Executed but not verified: window frame is {actual!r}."

    def manage_window(self, window: str, action: str) -> str:
        target = _existing_native_window(window)
        if target is None or not _window_target_is_current(target):
            return "Refused: no exact live window matches that title."
        if action == "close":
            adapter = self._ensure(target)
            if adapter is None:
                return CUA_UNAVAILABLE_MSG
            result = adapter.invoke_menu(
                int(target["pid"]), int(target["window_id"]), ["System", "Close"]
            )
            if result.outcome in {
                CuaOutcome.REFUSED,
                CuaOutcome.FAILED,
                CuaOutcome.SUSPECTED_NOOP,
            }:
                return self._outcome_text(result, "Closed window.")
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                if not _window_target_is_current(target):
                    return "Closed the exact window and verified it is gone."
                time.sleep(0.05)
            return "Executed but not verified: the exact window is still present."
        menu_item = {"minimize": "Minimize", "maximize": "Maximize", "restore": "Restore"}.get(action)
        if menu_item is None:
            return "Refused: unsupported window action."
        adapter = self._ensure(target)
        if adapter is None:
            return CUA_UNAVAILABLE_MSG
        result = adapter.invoke_menu(
            int(target["pid"]), int(target["window_id"]), ["System", menu_item]
        )
        if result.outcome in {CuaOutcome.REFUSED, CuaOutcome.FAILED, CuaOutcome.SUSPECTED_NOOP}:
            return self._outcome_text(result, f"{action.capitalize()}d window.")
        from charlie.desktop.windows import window_display_state

        state = window_display_state(int(target["window_id"]))
        expected = {"minimize": "minimized", "maximize": "maximized", "restore": "normal"}[action]
        return f"Window {state}." if state == expected else f"Executed but not verified: window state is {state}."

    # ---------------------------------------------------------------- helpers

    def _resolve(self, mark_id: int) -> tuple[Optional[_Snapshot], Optional[str]]:
        """Snapshot and element token for ``mark_id``.

        Both are None when the mark cannot be resolved. A reason string must never
        occupy the token slot: it would be truthy and skip the refusal guard.
        """
        with self._lock:
            snapshot = self._snapshot
        if snapshot is None:
            return None, None
        try:
            wanted = int(mark_id)
        except (TypeError, ValueError):
            return snapshot, None
        return snapshot, snapshot.marks.get(wanted)

    def _refresh(self, mark_id: int) -> tuple[Optional[_Snapshot], Optional[str]]:
        """A stale numeric mark cannot be safely rebound to a new snapshot."""
        return self._resolve(mark_id)

    def _outcome_text(self, result: Any, ok: str) -> str:
        if result.outcome is CuaOutcome.CONFIRMED:
            return ok
        if result.outcome is CuaOutcome.REFUSED:
            detail = result.detail if isinstance(result.detail, dict) else {}
            refusal = detail.get("refusal") if isinstance(detail.get("refusal"), dict) else {}
            error = detail.get("error") if isinstance(detail.get("error"), dict) else {}
            code = refusal.get("code") or error.get("code")
            reason = ": ".join(part for part in (result.summary, str(code or "")) if part)
            return f"Refused by the desktop runtime: {reason or result.outcome.value}."
        if result.outcome in {
            CuaOutcome.PARTIAL,
            CuaOutcome.UNVERIFIED,
            CuaOutcome.EXECUTED_UNVERIFIED,
            CuaOutcome.SUSPECTED_NOOP,
        }:
            return (
                f"Executed but not verified ({result.outcome.value}). {ok}"
            )
        return f"Desktop action failed: {result.summary or result.outcome.value}."

    # ---------------------------------------------------------------- actions

    def click_mark(self, mark_id: int) -> str:
        snapshot, token = self._resolve(mark_id)
        if snapshot is None:
            return "Refused: observe a target before clicking."
        if token is None:
            return "Refused: the target is stale. Observe again before clicking."
        target = self._snapshot_target(snapshot)
        if not _window_target_is_current(target):
            return "Refused: the observed window changed; observe it again before clicking."
        adapter = self._ensure(target)
        if adapter is None:
            return CUA_UNAVAILABLE_MSG
        result = adapter.click(
            element_token=token, pid=snapshot.pid, window_id=snapshot.window_id
        )
        return self._outcome_text(result, f"Clicked element {mark_id}.")

    def type_text(self, mark_id: int, text: str) -> str:
        snapshot, token = self._resolve(mark_id)
        if snapshot is None:
            return "Refused: observe a target before typing."
        if token is None:
            return "Refused: the target is stale. Observe again before typing."
        target = self._snapshot_target(snapshot)
        if not _window_target_is_current(target):
            return "Refused: the observed window changed; observe it again before typing."
        adapter = self._ensure(target)
        if adapter is None:
            return CUA_UNAVAILABLE_MSG
        element = next(
            (
                item
                for item in snapshot.state.get("elements") or []
                if isinstance(item, dict) and item.get("element_index") == int(mark_id)
            ),
            {},
        )
        if "set_value" in (element.get("actions") or []):
            result = adapter.set_value(
                snapshot.pid,
                text,
                element_token=token,
                window_id=snapshot.window_id,
            )
            detail_text = str((result.detail or {}).get("text") or "")
            if (
                result.outcome is CuaOutcome.FAILED
                and "does not implement ValuePattern or RangeValuePattern" in detail_text
            ):
                focused = adapter.click(
                    element_token=token,
                    pid=snapshot.pid,
                    window_id=snapshot.window_id,
                )
                if focused.outcome in {CuaOutcome.REFUSED, CuaOutcome.FAILED}:
                    return self._outcome_text(focused, f"Could not focus element {mark_id}.")
                result = adapter.type_text(
                    text,
                    pid=snapshot.pid,
                    window_id=snapshot.window_id,
                    foreground=True,
                )
            if result.outcome in {CuaOutcome.REFUSED, CuaOutcome.FAILED}:
                return self._outcome_text(result, f"Typed into element {mark_id}.")
            try:
                fresh = _as_state(
                    adapter.window_state(snapshot.pid, snapshot.window_id, capture_mode="ax")
                )
            except Exception:
                fresh = {}
            with self._lock:
                self._snapshot = None
            if _is_refusal(fresh):
                return f"Executed but not verified (fresh UIA readback refused: {_state_reason(fresh)})."
            old_element = element
            matches = [
                item
                for item in fresh.get("elements") or []
                if isinstance(item, dict)
                and item.get("role") == old_element.get("role")
                and item.get("label") == old_element.get("label")
            ]
            if len(matches) == 1 and matches[0].get("value") == text:
                return f"Typed into element {mark_id}; fresh UIA readback matches requested text."
            return "Executed but not verified (fresh UIA readback did not match requested text)."
        else:
            result = adapter.type_text(
                text, element_token=token, pid=snapshot.pid, window_id=snapshot.window_id
            )
        return self._outcome_text(result, f"Typed into element {mark_id}.")

    def key_press(self, keys: str) -> str:
        """Charlie policy and approval have already cleared this chord."""
        snapshot = self._current_snapshot()
        if snapshot is None:
            return "Refused: observe a target before sending keys."
        target = self._snapshot_target(snapshot)
        if not _window_target_is_current(target):
            return "Refused: the observed window changed; observe it again before sending keys."
        adapter = self._ensure(target)
        if adapter is None:
            return CUA_UNAVAILABLE_MSG
        chord = _parse_cua_key_chord(keys)
        if chord is None:
            return "Refused: unsupported or prohibited key chord."
        key, modifiers = chord
        result = adapter.press_key(
            key,
            modifiers=modifiers,
            pid=snapshot.pid,
            window_id=snapshot.window_id,
        )
        return self._outcome_text(result, f"Pressed {keys}.")

    def scroll(self, notches: int) -> str:
        snapshot = self._current_snapshot()
        if snapshot is None:
            return "Refused: observe a target before scrolling."
        target = self._snapshot_target(snapshot)
        if not _window_target_is_current(target):
            return "Refused: the observed window changed; observe it again before scrolling."
        adapter = self._ensure(target)
        if adapter is None:
            return CUA_UNAVAILABLE_MSG
        args: dict[str, Any] = {
            "direction": "down" if notches >= 0 else "up",
            "amount": abs(int(notches)),
        }
        args["pid"] = snapshot.pid
        args["window_id"] = snapshot.window_id
        result = adapter.scroll(**args)
        return self._outcome_text(result, f"Scrolled {notches}.")

    def drag(self, x1: int, y1: int, x2: int, y2: int) -> str:
        snapshot = self._current_snapshot()
        if snapshot is None:
            return "Refused: observe a target before dragging."
        if snapshot.screenshot_data_url is None:
            return "Refused: drag needs coordinates from a fresh Cua window screenshot."
        target = self._snapshot_target(snapshot)
        if not _window_target_is_current(target):
            return "Refused: the observed window changed; observe it again before dragging."
        adapter = self._ensure(target)
        if adapter is None:
            return CUA_UNAVAILABLE_MSG
        args: dict[str, Any] = {"pid": snapshot.pid, "window_id": snapshot.window_id}
        result = adapter.drag(x1, y1, x2, y2, **args)
        return self._outcome_text(result, "Dragged.")

    def open_apps(self, apps: list[str]) -> str:
        """Open or show each app, reusing a running instance instead of relaunching."""
        messages: list[str] = []
        for name in apps:
            messages.append(self.open_app(name))
        return " ".join(messages)

    def open_app(self, apps: Any, commands: Any = None) -> str:
        names = [str(apps)] if isinstance(apps, str) else [str(name) for name in apps]
        commands_list = [commands] if isinstance(commands, str) else commands
        if not names:
            return "Refused: no application was specified."
        from charlie.desktop.apps import launch_apps

        launched = launch_apps(names, commands_list)
        if "could not open" in launched.lower():
            return launched
        target = _existing_native_window(names[-1])
        if target is None:
            return f"Refused: {names[-1]} opened without an exact observable window."
        return self.bind_window_target(target)

    def _current_snapshot(self) -> Optional[_Snapshot]:
        with self._lock:
            return self._snapshot

    def _snapshot_target(self, snapshot: _Snapshot) -> dict[str, Any]:
        with self._lock:
            target = dict(self._target or {})
        if target.get("pid") != snapshot.pid or target.get("window_id") != snapshot.window_id:
            return {"pid": snapshot.pid, "window_id": snapshot.window_id}
        return target

    def bind_window_target(self, target: dict[str, Any]) -> str:
        """Rebuild the bounded runtime around one trusted native HWND."""
        if not _window_target_is_current(target):
            return "Refused: the exact native window target is stale."
        if _is_browser_window(target):
            return "Refused: browser windows are outside the native Cua scope."
        adapter = self._ensure(target)
        if adapter is None:
            return CUA_UNAVAILABLE_MSG
        result = adapter.focus_window(int(target["pid"]), int(target["window_id"]))
        reason = f"{result.summary} {result.detail}".casefold()
        if result.outcome is CuaOutcome.REFUSED and (
            "bounded_resource_outside_manifest" in reason
            or "outside the capability manifest" in reason
        ):
            refreshed = _existing_native_window(str(target.get("title") or ""))
            same_process = bool(
                refreshed
                and refreshed.get("pid") == target.get("pid")
                and refreshed.get("create_time") == target.get("create_time")
                and str(refreshed.get("title") or "").casefold()
                == str(target.get("title") or "").casefold()
                and refreshed.get("window_id") != target.get("window_id")
                and _window_target_is_current(refreshed)
                and not _is_browser_window(refreshed)
            )
            if same_process:
                target = refreshed
                adapter = self._ensure(target)
                if adapter is not None:
                    result = adapter.focus_window(int(target["pid"]), int(target["window_id"]))
        if not result.is_success:
            return f"Refused: {result.summary or result.outcome.value}."
        verified_state = result.detail.get("window_state") if isinstance(result.detail, dict) else None
        if isinstance(verified_state, dict) and verified_state.get("elements") is not None:
            self._record_state(int(target["pid"]), int(target["window_id"]), verified_state)
        else:
            observed = self.observe(window_target=target)
            if observed.startswith("Refused:"):
                return observed
        note_focus(int(target["pid"]), int(target["window_id"]))
        return "Opened the app and confirmed its exact window through bounded Cua."


_FOCUS_LOCK = threading.Lock()
_LAST_FOCUSED: dict[str, Any] = {}


def note_focus(pid: int, window_id: Optional[int]) -> None:
    """Record the window Charlie brought forward, so observe() can find it again."""
    with _FOCUS_LOCK:
        _LAST_FOCUSED["pid"] = pid
        _LAST_FOCUSED["window_id"] = window_id


def _existing_native_window(name: str) -> Optional[dict[str, Any]]:
    """Find a visible app window before bounded Cua's pid-scoped lookup.

    Bounded Cua intentionally rejects global window enumeration. Native enumeration
    supplies the exact pid/hwnd, after which Cua still performs the authorized
    focus and state verification.
    """
    if not name:
        return None
    try:
        from charlie.desktop.windows import find_window_identity

        return find_window_identity(name)
    except Exception:
        return None


def _current_window_target() -> Optional[dict[str, Any]]:
    """Use Charlie's last Cua-verified window, then fresh native inventory."""
    try:
        with _FOCUS_LOCK:
            hinted = dict(_LAST_FOCUSED)
        if hinted.get("pid") and hinted.get("window_id"):
            from charlie.desktop.windows import window_identity

            target = window_identity(int(hinted["window_id"]))
            if target and target.get("pid") == hinted.get("pid"):
                return target
        from charlie.desktop.windows import top_window_identity

        return top_window_identity()
    except Exception:
        return None


def _window_target_is_current(target: dict[str, Any]) -> bool:
    try:
        from charlie.desktop.windows import window_target_is_current

        return window_target_is_current(target)
    except Exception:
        return False


def _is_browser_window(target: dict[str, Any]) -> bool:
    from pathlib import Path

    process_name = str(target.get("process_name") or Path(str(target.get("executable") or "")).name)
    return process_name.casefold() in {
        "brave.exe", "chrome.exe", "msedge.exe", "firefox.exe", "opera.exe", "vivaldi.exe"
    }


def _parse_cua_key_chord(value: str) -> Optional[tuple[str, list[str]]]:
    """Translate Charlie's `Ctrl+S` spelling to Cua's separate key/modifiers."""
    aliases = {"control": "ctrl", "ctrl": "ctrl", "shift": "shift", "alt": "alt"}
    parts = [part.strip().casefold() for part in str(value or "").split("+")]
    if not parts or any(not part for part in parts):
        return None
    if any(part in {"win", "windows", "meta", "super", "cmd", "command"} for part in parts):
        return None
    modifiers = []
    for part in parts[:-1]:
        modifier = aliases.get(part)
        if modifier is None or modifier in modifiers:
            return None
        modifiers.append(modifier)
    key = {"enter": "return", "esc": "escape", "del": "delete"}.get(parts[-1], parts[-1])
    if parts[-1] in aliases or not key:
        return None
    if set(modifiers) == {"ctrl", "alt"} and key in {"delete", "del"}:
        return None
    return key, modifiers


def _as_state(payload: Any) -> dict[str, Any]:
    from .cua_adapter import _as_mapping

    return _as_mapping(payload)


def _is_refusal(state: dict[str, Any]) -> bool:
    return bool(
        state.get("is_error")
        or state.get("refusal")
        or state.get("error_code")
        or state.get("status") == "refused"
    )


def _state_reason(state: dict[str, Any]) -> str:
    refusal = state.get("refusal")
    if isinstance(refusal, dict) and refusal.get("message"):
        return str(refusal["message"])
    return str(state.get("text") or state.get("error_code") or state.get("status") or "unknown refusal")


def _screenshot_data_url(payload: Any) -> Optional[str]:
    images = getattr(payload, "images", None)
    if images is None and isinstance(payload, dict):
        images = payload.get("images")
    if not images:
        return None
    image = images[0]
    data = image.get("data") if isinstance(image, dict) else getattr(image, "data", None)
    mime = image.get("mime_type") if isinstance(image, dict) else getattr(image, "mime_type", None)
    if not mime and isinstance(image, dict):
        mime = image.get("mimeType")
    if not mime:
        mime = getattr(image, "mimeType", None)
    if not isinstance(data, (bytes, str)) or not mime:
        return None
    encoded = base64.b64encode(data).decode("ascii") if isinstance(data, bytes) else data
    if encoded.startswith("data:"):
        return encoded
    return f"data:{mime};base64,{encoded}"


def _screenshot_dimensions(state: dict[str, Any]) -> tuple[Optional[int], Optional[int]]:
    width = state.get("screenshot_width")
    height = state.get("screenshot_height")
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        return None, None
    return width, height


_backend: Optional[DesktopBackend] = None
_backend_lock = threading.Lock()


def get_backend() -> DesktopBackend:
    """The process-wide backend. Charlie's tools all resolve through this."""
    global _backend
    with _backend_lock:
        if _backend is None:
            _backend = DesktopBackend()
        return _backend


_browser_lock = threading.Lock()
_browser_session: Optional[Any] = None


def get_cua_browser(origins: Optional[Any] = None, session: Optional[str] = None):
    """Build a fresh, origin-scoped Cua browser owned by the caller.

    This is a second runtime on purpose. Cua rejects an origin-scoped manifest that
    also allows native input tools, so isolated browsing cannot share the native
    runtime that ``get_backend`` owns. Both are bounded; neither falls back.

    ``origins`` is not a hint. Origins are compiled into the manifest, and the manifest
    is fixed for the runtime's life, so the caller must declare the origins it intends
    to reach and anything else stays refused by the runtime. There is deliberately no
    process-wide cache: a cached runtime would carry one task's origin set into the
    next task, which is exactly the silent widening this design avoids.

    The caller owns the result and must close it.
    """
    from .browser import CuaBrowser
    from .cua_adapter import CuaAdapter
    from .cua_bridge import CuaRuntimeBridge
    from .manifest import BROWSER_SCOPE

    bridge = CuaRuntimeBridge(scope=BROWSER_SCOPE, origins=origins)
    if not bridge.start():
        reason = bridge.startup_error
        bridge.shutdown()
        raise CuaBrowserUnavailable(
            f"origin-scoped browser runtime unavailable: {reason}"
        )
    return CuaBrowser(CuaAdapter(bridge), session=session)


class CuaBrowserUnavailable(RuntimeError):
    """The isolated browser runtime could not be started."""


def reset_cua_browser() -> None:
    """Retained for compatibility. There is no process-wide browser runtime any more:
    each task owns its own scope and closes it. Kept so existing callers and tests that
    call this on teardown do not break.
    """
    return None


def reset_backend() -> None:
    """Drop the runtime. Used by tests and by Charlie's teardown."""
    global _backend
    with _backend_lock:
        backend = _backend
        _backend = None
    if backend is not None:
        backend.shutdown()

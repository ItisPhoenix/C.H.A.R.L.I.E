"""The one desktop execution path Charlie calls.

Charlie keeps its public tool names, schemas, policy, approvals, leases and result
envelopes. This module is the only place that decides *how* an action is executed:
through the authorized Cua runtime. Unavailable or refused control stays refused;
legacy ``charlie.desktop`` helpers do not provide a parallel physical executor.

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
import logging
import threading
import time
from typing import Any, Optional

from .cua_adapter import CuaAdapter, CuaOutcome
from .cua_bridge import CuaBridgeError, CuaBridgeTimeout, CuaBridgeUnavailable, CuaRuntimeBridge

logger = logging.getLogger("charlie.computer.backend")

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
    """Owns Charlie's authorized Cua desktop execution and observations."""

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
                old_bridge = self._bridge
                idle_expired = bool(
                    same_target and old_bridge is not None and getattr(old_bridge, "is_idle_expired", False)
                )
                if idle_expired and not _window_target_is_current(selected):
                    self._bridge = None
                    self._adapter = None
                    self._snapshot = None
                    self._target = None
                    target_expired = True
                else:
                    target_expired = False
                if (
                    not target_expired
                    and same_target
                    and not idle_expired
                    and self._adapter is not None
                    and self._adapter.available
                ):
                    return self._adapter
                self._bridge = None
                self._adapter = None
                self._snapshot = None
                if not target_expired:
                    self._target = selected
            if old_bridge is not None:
                if not old_bridge.shutdown():
                    with self._lock:
                        self._bridge = old_bridge
                    logger.warning("Previous bounded Cua runtime did not finish shutdown")
                    return None
            if target_expired:
                logger.info("Bounded Cua idle runtime expired after its exact window target disappeared")
                return None
            if idle_expired:
                logger.info("Renewing idle bounded Cua runtime for its still-live exact window target")
            bridge = CuaRuntimeBridge(
                window_target={"pid": int(selected["pid"]), "window_id": int(selected["window_id"])}
            )
            try:
                if not bridge.ensure_started():
                    if not bridge.shutdown():
                        with self._lock:
                            self._bridge = bridge
                            self._target = selected
                        logger.warning("Failed bounded Cua startup left cleanup incomplete")
                    return None
            except CuaBridgeError:
                if not bridge.shutdown():
                    with self._lock:
                        self._bridge = bridge
                        self._target = selected
                    logger.warning("Failed bounded Cua startup left cleanup incomplete")
                return None
            with self._lock:
                self._bridge = bridge
                self._adapter = CuaAdapter(bridge)
                return self._adapter

    @property
    def using_cua(self) -> bool:
        return self._ensure() is not None

    def shutdown(self) -> bool:
        with self._runtime_lock:
            with self._lock:
                bridge = self._bridge
                self._snapshot = None
                self._target = None
            if bridge is None:
                with self._lock:
                    self._adapter = None
                return True
            stopped = bridge.shutdown()
            if stopped:
                with self._lock:
                    if self._bridge is bridge:
                        self._bridge = None
                        self._adapter = None
            return stopped

    def focus_overlay_rect(self, window_hint: Optional[str] = None) -> Optional[dict[str, int]]:
        """Return bounds only for the same live native target desktop control uses."""
        if window_hint is not None:
            target = _existing_native_window(window_hint)
        else:
            with self._lock:
                target = dict(self._target or {}) or None
            target = target or _current_window_target()
        if not target or not _window_target_is_current(target) or _is_browser_window(target):
            return None
        try:
            from charlie.desktop.windows import window_rect

            return window_rect(int(target["window_id"]))
        except (KeyError, TypeError, ValueError, OSError):
            return None

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
            self._snapshot = None
        target = dict(window_target or bound_target or _current_window_target() or {})
        if not target:
            return "Refused: no exact live window target is available."
        if _is_browser_window(target):
            return "Refused: browser windows are outside the native Cua scope."
        adapter = self._ensure(target)
        if adapter is None:
            return CUA_UNAVAILABLE_MSG
        pid, window_id = int(target["pid"]), int(target["window_id"])
        max_depth = 24 if str(target.get("title") or "").casefold().startswith("save as") else 12
        payload = adapter.window_state(
            pid,
            window_id,
            capture_mode="ax",
            include_screenshot=include_screenshot,
            max_depth=max_depth,
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
        if any(line.endswith('Edit "File name:"') for line in lines) and any(
            line.endswith('Button "Save"') for line in lines
        ):
            return "\n".join(
                line for line in lines
                if line.endswith('Edit "File name:"') or line.endswith('Button "Save"')
            )
        return "\n".join(lines) if lines else "(no marked elements)"

    def capture_window(self) -> tuple[str, Optional[str]]:
        """Return fresh exact-window marks and its Cua-owned screenshot."""
        text = self.observe(include_screenshot=True)
        snapshot = self._current_snapshot()
        return text, snapshot.screenshot_data_url if snapshot else None

    def read_screen(self) -> str:
        """Read text only from a fresh, authorized Cua window screenshot."""
        from charlie.desktop import ocr

        text, image = self.capture_window()
        if text.startswith(("Refused:", CUA_UNAVAILABLE_MSG)):
            return text
        if not ocr.config.desktop_ocr_enabled or not ocr.OCR_AVAILABLE:
            return f"{text}\nOCR is unavailable; I could not read the screen text."
        if not image:
            return f"{text}\nScreen text is unverified: Cua did not provide a window screenshot."
        started = time.perf_counter()
        try:
            if not image.startswith("data:image/") or ";base64," not in image:
                raise ValueError("Unsupported window screenshot encoding")
            pixels = base64.b64decode(image.split(";base64,", 1)[1], validate=True)
            elements = ocr.ocr_marks(pixels)
        except Exception:
            logger.warning("Cua window OCR failed", exc_info=True)
            return f"{text}\nScreen text is unverified: OCR could not read the captured window."
        finally:
            logger.info(
                "pipeline_stage | stage=desktop_ocr | latency_ms=%.1f",
                (time.perf_counter() - started) * 1000,
            )
        if not elements:
            return f"{text}\nOCR did not recognize reliable text in this window."
        logger.info("Cua window OCR recognized %s text regions", len(elements))
        return text + "\n[OCR TEXT - observation only]\n" + "\n".join(
            element.name for element in elements
        )

    def click_at(self, x: int, y: int, *, button: str = "left", double: bool = False) -> str:
        snapshot = self._current_snapshot()
        if snapshot is None or snapshot.screenshot_data_url is None:
            return "Refused: coordinate click needs a fresh Cua window screenshot."
        capture_id = snapshot.state.get("snapshot_id")
        if not isinstance(capture_id, str) or not capture_id:
            return "Refused: Cua did not provide a capture identity for this screenshot."
        width, height = _screenshot_dimensions(snapshot.state)
        if width is None or height is None or not (0 <= x < width and 0 <= y < height):
            return "Refused: coordinate is outside the fresh Cua window screenshot."
        target = self._snapshot_target(snapshot)
        if not _window_target_is_current(target):
            return "Refused: the observed window changed; capture it again before clicking."
        adapter = self._ensure(target)
        if adapter is None:
            return CUA_UNAVAILABLE_MSG
        self._invalidate_snapshot()
        action = adapter.click(
            pid=snapshot.pid, window_id=snapshot.window_id, x=x, y=y,
            button=button, click_count=2 if double else 1, capture_id=capture_id,
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
        self._invalidate_snapshot()
        action = adapter.move_cursor(x, y, pid=snapshot.pid, window_id=snapshot.window_id)
        return self._outcome_text(action, "Moved the cursor to the fresh screenshot point.")

    def move_window(self, window: str, x: int, y: int, width: int, height: int) -> str:
        target = _existing_native_window(window)
        if target is None or not _window_target_is_current(target):
            return "Refused: no exact live window matches that title."
        adapter = self._ensure(target)
        if adapter is None:
            return CUA_UNAVAILABLE_MSG
        self._invalidate_snapshot()
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
        return self._manage_window_target(target, action)

    def _manage_window_target(self, target: dict[str, Any], action: str) -> str:
        if not _window_target_is_current(target):
            return "Refused: the exact window target is stale."
        if action == "close":
            adapter = self._ensure(target)
            if adapter is None:
                return CUA_UNAVAILABLE_MSG
            self._invalidate_snapshot()
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
        self._invalidate_snapshot()
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
            return f"Executed but not verified ({result.outcome.value})."
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
        self._invalidate_snapshot()
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
        self._invalidate_snapshot()
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
                selected_all = adapter.press_key(
                    "a",
                    modifiers=["ctrl"],
                    pid=snapshot.pid,
                    window_id=snapshot.window_id,
                )
                if selected_all.outcome in {CuaOutcome.REFUSED, CuaOutcome.FAILED}:
                    return self._outcome_text(selected_all, f"Could not replace text in element {mark_id}.")
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
            try:
                # Typing a value invalidates its mark snapshot; refresh the exact foreground
                # window before a follow-up chord such as Ctrl+S.
                self.observe()
            except Exception:
                logger.debug("Fresh desktop observation failed before key input", exc_info=True)
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
        self._invalidate_snapshot()
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
        self._invalidate_snapshot()
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
        self._invalidate_snapshot()
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
        commands = [commands] if isinstance(commands, str) else commands or []
        if not names:
            return "Refused: no application was specified."
        results = []
        for index, name in enumerate(names):
            hint = str(commands[index]) if index < len(commands) and commands[index] else None
            entry = _app_entry(name)
            if hint and not _process_hint_matches_app(name, hint, entry):
                results.append(f"Refused: the process hint did not match {name}.")
                continue
            targets = _native_app_windows(name, [hint] if hint else None)
            if len(targets) != 1:
                results.append(
                    f"I couldn't focus {name}; no unique live app window was found. "
                    "This desktop control can only focus apps that are already running."
                )
                continue
            try:
                results.append(self.bind_window_target(targets[0]))
            except CuaBridgeUnavailable:
                results.append(CUA_UNAVAILABLE_MSG)
            except CuaBridgeTimeout:
                results.append("Executed but not verified (Cua focus timed out).")
            except CuaBridgeError:
                results.append("I couldn't confirm the requested app window through desktop control.")
        if all(result.startswith("Opened the app and confirmed") for result in results):
            return "Opened the app and confirmed its exact window through bounded Cua."
        return "I couldn't open every requested app. " + " ".join(results)

    def close_apps(
        self, apps: list[str], process_hints: Optional[list[str]] = None
    ) -> tuple[str, bool, str | None]:
        """Close one unambiguous exact window per app through Cua."""
        results: list[str] = []
        verified = True
        failure_kind: str | None = None
        for index, name in enumerate(apps):
            hint = process_hints[index] if process_hints and index < len(process_hints) else None
            entry = _app_entry(name)
            if hint and not _process_hint_matches_app(name, hint, entry):
                results.append(f"Could not close {name}: its process hint did not match the app.")
                verified = False
                failure_kind = failure_kind or "invalid_process_hint"
                continue
            targets = _native_app_windows(name, [hint] if hint else None)
            if len(targets) != 1:
                why = "no exact live window" if not targets else "multiple matching windows"
                results.append(f"Could not close {name}: {why}.")
                verified = False
                failure_kind = failure_kind or ("target_unavailable" if not targets else "ambiguous_target")
                continue
            target = targets[0]
            try:
                result = self._manage_window_target(target, "close")
            except CuaBridgeUnavailable:
                result = CUA_UNAVAILABLE_MSG
            except CuaBridgeTimeout:
                result = "Executed but not verified (Cua close timed out)."
            except CuaBridgeError:
                result = "I couldn't confirm the close action through desktop control."
            closed = result == "Closed the exact window and verified it is gone."
            results.append(
                f"Closed the selected window for {name} and verified it is gone."
                if closed else f"{name}: {result}"
            )
            verified = verified and closed
            if not closed:
                if failure_kind is None:
                    if result == CUA_UNAVAILABLE_MSG:
                        failure_kind = "unavailable"
                    elif result.startswith(("Refused", "Refused by")):
                        failure_kind = "target_unavailable"
                    elif result.startswith("Desktop action failed"):
                        failure_kind = "cua_failed"
                    else:
                        failure_kind = "cua_window_unverified"
        if any("Executed but not verified" in result for result in results):
            failure_kind = "cua_window_unverified"
        elif any(result.startswith("Desktop action failed") for result in results):
            failure_kind = "cua_failed"
        return " ".join(results), verified, failure_kind

    def _current_snapshot(self) -> Optional[_Snapshot]:
        with self._lock:
            return self._snapshot

    def _invalidate_snapshot(self) -> None:
        with self._lock:
            self._snapshot = None

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


def _native_app_windows(
    name: str, process_hints: Optional[list[str]] = None
) -> list[dict[str, Any]]:
    """Resolve visible windows by exact process identity or exact configured title."""
    if not name:
        return []
    try:
        from charlie.desktop import windows
        entry = _app_entry(name)
        processes = {
            _process_basename(value).casefold()
            for value in (process_hints or (entry.close_processes if entry else ()))
            if value
        }
        titles = {name.strip().casefold()}
        if entry:
            titles.update(title.casefold() for title in entry.close_window_titles)
        matches = []
        for item in windows.list_windows():
            title = str(item.get("title") or "")
            hwnd = int(item.get("hwnd") or 0)
            if not hwnd:
                continue
            identity = windows.window_identity(hwnd, title)
            if not identity or _is_browser_window(identity):
                continue
            process_name = _process_basename(str(identity.get("process_name") or "")).casefold()
            executable = _process_basename(str(identity.get("executable") or "")).casefold()
            if title.casefold() in titles or processes.intersection({process_name, executable}):
                if _window_target_is_current(identity):
                    matches.append(identity)
        return matches
    except Exception:
        return []


def _existing_native_window(name: str) -> Optional[dict[str, Any]]:
    matches = _native_app_windows(name)
    return matches[0] if len(matches) == 1 else None


def _app_entry(name: str) -> Any:
    from charlie.known_apps import APP_REGISTRY

    return APP_REGISTRY.get(name.strip().casefold())


def _process_basename(value: str) -> str:
    """Windows process basename without interpreting a hint as an executable path."""
    return value.replace("\\", "/").rsplit("/", 1)[-1]


def _process_hint_matches_app(name: str, hint: str, entry: Any) -> bool:
    basename = _process_basename(hint).casefold()
    if entry:
        return basename in {value.casefold() for value in entry.close_processes}
    stem = basename[:-4] if basename.endswith(".exe") else basename
    return stem == name.strip().casefold()


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


def reset_backend() -> bool:
    """Drop the runtime. Used by tests and by Charlie's teardown."""
    global _backend
    with _backend_lock:
        backend = _backend
    if backend is None:
        return True
    stopped = backend.shutdown()
    if stopped:
        with _backend_lock:
            if _backend is backend:
                _backend = None
    return stopped

"""Narrow Cua adapter behind Charlie's capability layer.

Charlie keeps its tool names, schemas, policy, approval, lease, result-envelope and
verification contracts. This module is the only place that knows Cua exists. It
deliberately does **not** expose Cua's 57-tool catalogue to the model: each Charlie
tool maps to one narrow, named Cua call built here, and Cua's own arguments are never
accepted from a model message.

Two rules shape the mapping:

* Cua's confirmation is *evidence*, never an outcome. ``ActionEffect`` and
  ``VerificationStatus`` are mapped explicitly, and anything that is not positively
  confirmed -- ``UNVERIFIABLE``, ``REFUSED``, ``SUSPECTED_NOOP``, ``UNKNOWN`` -- can
  never become success. Charlie's verifier remains authoritative.
* Charlie policy, approval and lease checks all run *before* :meth:`CuaAdapter`
  is reached. The driver is configured bounded at construction; there is no
  authorization callback routed back into Charlie's loop.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from .cua_bridge import CuaBridgeError, CuaRuntimeBridge

__all__ = [
    "CuaOutcome",
    "CuaActionResult",
    "CuaAdapter",
    "FOREGROUND_ACTIVATION_BLOCKED",
]

# Charlie-side structured refusal. Distinct from "failed": Windows refused to grant
# foreground activation, which is a truthful answer rather than an error to retry.
FOREGROUND_ACTIVATION_BLOCKED = "foreground_activation_blocked"


class CuaOutcome(str, Enum):
    """How a Cua action actually landed, in Charlie's vocabulary."""

    CONFIRMED = "confirmed"
    PARTIAL = "partial"
    REFUSED = "refused"
    SUSPECTED_NOOP = "suspected_noop"
    UNVERIFIED = "unverified"
    # Dispatched, but the requested postcondition has not been observed yet.
    EXECUTED_UNVERIFIED = "executed_unverified"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


# Cua ActionEffect -> Charlie outcome. UNVERIFIABLE maps to UNVERIFIED, never to
# CONFIRMED: an action Cua could not confirm is an action Charlie has not verified.
_EFFECT_MAP = {
    "confirmed": CuaOutcome.CONFIRMED,
    "partial": CuaOutcome.PARTIAL,
    "refused": CuaOutcome.REFUSED,
    "suspected_noop": CuaOutcome.SUSPECTED_NOOP,
    "unverifiable": CuaOutcome.UNVERIFIED,
}

# Cua VerificationStatus -> Charlie outcome.
_VERIFICATION_MAP = {
    "satisfied": CuaOutcome.CONFIRMED,
    "unsatisfied": CuaOutcome.FAILED,
    "unknown": CuaOutcome.UNVERIFIED,
}

class CuaOperation(str, Enum):
    """What kind of work a call did, which decides how its result may be read.

    The distinction matters because a browser or window tool answers some calls with a
    plain ``status="ok"`` and no action effect. That is enough to say a *read*
    succeeded, but for a *mutation* it only proves the request was dispatched.
    """

    READ = "read"
    MUTATION = "mutation"
    SETUP = "setup"


# Outcomes that must never be reported as success, whatever else is true.
NEVER_SUCCESS = frozenset(
    {
        CuaOutcome.REFUSED,
        CuaOutcome.SUSPECTED_NOOP,
        CuaOutcome.UNVERIFIED,
        CuaOutcome.EXECUTED_UNVERIFIED,
        CuaOutcome.FAILED,
        CuaOutcome.UNAVAILABLE,
    }
)


@dataclass
class CuaActionResult:
    """One Cua result, normalised. Never a raw pass-through to the model."""

    outcome: CuaOutcome
    summary: str = ""
    detail: dict[str, Any] = field(default_factory=dict)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    escalation: Optional[dict[str, Any]] = None
    raw_effect: Optional[str] = None
    raw_verification: Optional[str] = None

    @property
    def is_success(self) -> bool:
        """Only a positively confirmed, positively verified result is a success."""
        return self.outcome is CuaOutcome.CONFIRMED

    @property
    def executed_unverified(self) -> bool:
        """True when the action may have landed but Charlie has not confirmed it."""
        return self.outcome in {
            CuaOutcome.PARTIAL,
            CuaOutcome.UNVERIFIED,
            CuaOutcome.SUSPECTED_NOOP,
        }


def _enum_value(value: Any) -> str:
    if value is None:
        return ""
    return str(getattr(value, "value", value) or "").strip().lower()


def _loads(value: Any) -> Optional[dict[str, Any]]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _content_blocks(payload: Any) -> Optional[dict[str, Any]]:
    """MCP-style ``content`` block lists, where the payload hides in a text block."""
    if isinstance(payload, (list, tuple)):
        blocks = list(payload)
    elif isinstance(payload, dict) and isinstance(payload.get("content"), (list, tuple)):
        blocks = list(payload["content"])
    else:
        return None
    for block in blocks:
        if not isinstance(block, dict):
            continue
        for key in ("structured_json", "raw_json", "json", "data"):
            got = _loads(block.get(key))
            if got is not None:
                return got
        text = block.get("text")
        got = _loads(text)
        if got is not None:
            return got
    return None


def _as_mapping(value: Any) -> dict[str, Any]:
    # Cua results arrive as typed uniffi objects, or with the payload nested in
# structured_json / raw_json / content blocks. Read every carrier before giving up,
# otherwise a host that returned thirteen windows looks like it returned none.
# An unrecognised shape returns {} and callers must treat that as unknown.
    if value is None:
        return {}
    if isinstance(value, dict):
        for key in ("structured_json", "raw_json"):
            got = _loads(value.get(key))
            if got is not None:
                merged = dict(got)
                for extra in ("text", "images", "error", "is_error", "action",
                              "verification", "degraded", "error_code"):
                    if extra in value:
                        merged.setdefault(extra, value[extra])
                return merged
        blocks = _content_blocks(value)
        if blocks is not None:
            return blocks
        return value
    blocks = _content_blocks(value)
    if blocks is not None:
        return blocks
    for attr in ("to_dict", "model_dump", "as_dict"):
        fn = getattr(value, attr, None)
        if callable(fn):
            try:
                out = _as_mapping(fn())
            except Exception:
                continue
            if out:
                return out
    raw = getattr(value, "__dict__", None)
    if isinstance(raw, dict) and raw:
        fields = {k: v for k, v in raw.items() if not k.startswith("_")}
        # Recurse so a typed ToolResult carrying the payload in structured_json is
        # unwrapped exactly like the plain-dict path.
        unwrapped = _as_mapping(fields)
        return unwrapped or fields
    slots = getattr(type(value), "__slots__", None)
    if slots:
        out = {}
        for name in slots:
            if hasattr(value, name):
                out[name] = getattr(value, name)
        if out:
            return out
    return {}


def editor_token(state: dict[str, Any]) -> Optional[str]:
    """Token for the main text editor of a document window.

    A window can expose several ``Document`` elements, so the editor is identified by
    its label rather than by being the first match.
    """
    fallback = None
    for element in state.get("elements") or []:
        if not isinstance(element, dict):
            continue
        label = str(element.get("label") or "")
        if str(element.get("role")) != "Document":
            continue
        if "editor" in label.lower() or "text" in label.lower():
            return element.get("element_token")
        if fallback is None and "set_value" in (element.get("actions") or []):
            fallback = element.get("element_token")
    return fallback


def token_for(state: dict[str, Any], label: str) -> Optional[str]:
    """Element token for ``label`` in a freshly captured window state.

    Tokens are snapshot-scoped: a click invalidates the snapshot it came from, so
    reusing one across an action group silently clicks the wrong control. Capture
    again between actions rather than caching tokens.
    """
    for element in state.get("elements") or []:
        if not isinstance(element, dict):
            continue
        if str(element.get("label") or "").strip() == label:
            return element.get("element_token")
    return None


def display_text(state: dict[str, Any]) -> Optional[str]:
    """Read a calculator-style display value out of a window state."""
    for element in state.get("elements") or []:
        if isinstance(element, dict) and str(element.get("label") or "").startswith("Display"):
            return element.get("label")
    return None


def _hwnd(value: Any) -> Optional[int]:
    """Cua reports window handles as decimal ints or as ``0x`` hex strings."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text, 16) if text.lower().startswith("0x") else int(text, 10)
    except ValueError:
        return None


def _launched_pid(payload: dict[str, Any]) -> Optional[int]:
    """The pid Cua reported for the process it launched."""
    pid = payload.get("pid")
    if isinstance(pid, int) and pid > 0:
        return pid
    for window in payload.get("windows") or []:
        if isinstance(window, dict):
            value = window.get("pid")
            if isinstance(value, int) and value > 0:
                return value
    return None


def _launched_window(payload: dict[str, Any]) -> Optional[dict[str, Any]]:
    """The window Cua reported for the process it launched.

    Cua puts ``pid`` at the payload top level, not on each window entry.
    """
    windows = payload.get("windows")
    if not isinstance(windows, list):
        return None
    top_pid = payload.get("pid")
    for window in windows:
        if not isinstance(window, dict) or not window.get("window_id"):
            continue
        info = _window_info(window)
        if not info.get("pid") and isinstance(top_pid, int):
            info["pid"] = top_pid
        return info
    return None


def _window_matches(window: dict[str, Any], wanted: str) -> bool:
    """True when a window plausibly belongs to the requested app.

    Matching covers both the process name and the window title, because several
    Windows apps do not run under their own name -- Calculator runs inside
    ApplicationFrameHost.exe, so a process-name match alone would never find it.
    A minimised window still counts: reusing it is correct, it just needs raising.
    """
    needle = wanted.strip().lower().replace(".exe", "")
    if not needle:
        return False
    for key in ("app_name", "name", "title"):
        value = str(window.get(key) or "").lower().replace(".exe", "")
        if value and (needle in value or value in needle):
            return True
    return False


def _window_info(window: Any) -> dict[str, Any]:
    """Flatten a typed ``WindowInfo`` into the shape the adapter reasons about."""
    if isinstance(window, dict):
        return dict(window)
    return {
        "window_id": getattr(window, "window_id", None),
        "pid": getattr(window, "pid", None),
        "app_name": getattr(window, "app_name", None),
        "name": getattr(window, "app_name", None),
        "title": getattr(window, "title", None),
        "is_on_screen": getattr(window, "is_on_screen", None),
        "minimized": getattr(window, "minimized", None),
        "bounds": _as_mapping(getattr(window, "bounds", None)),
    }


def normalise(payload: Any, *, operation: CuaOperation = CuaOperation.MUTATION) -> CuaActionResult:
    """Map any Cua result shape onto Charlie's outcome vocabulary.

    Effect is consulted first; a negative verification downgrades a confirmed effect
    to ``UNVERIFIED`` rather than letting a positive effect mask an unproven state.

    ``operation`` decides how a bare ``status="ok"`` is read. A read that returned
    usable content is a success. A mutation that returned ``ok`` has only been
    dispatched, so it stays ``EXECUTED_UNVERIFIED`` until a postcondition is observed
    independently. Payloads that are malformed, unrecognised or empty stay unknown:
    absence of an error is not evidence of an effect.
    """
    data = _as_mapping(payload)
    if not data:
        return CuaActionResult(
            outcome=CuaOutcome.UNVERIFIED,
            summary="empty or unreadable Cua payload",
            detail={"payload": payload},
        )

    effect = _enum_value(data.get("effect"))
    verification = _enum_value(data.get("verification_status")) or _enum_value(
        data.get("verification")
    )

    outcome = _EFFECT_MAP.get(effect)
    if outcome is None and not effect:
        # No effect field at all: a bare status may still describe the result.
        outcome = _status_ok_outcome(data, operation)
        if outcome is not None:
            effect = "status_ok"
    if outcome is None:
        # No recognisable effect and no usable status: never assume success.
        outcome = CuaOutcome.FAILED
        effect = effect or "absent"

    if verification:
        mapped = _VERIFICATION_MAP.get(verification)
        if mapped is not None:
            if mapped is CuaOutcome.UNVERIFIED and outcome is CuaOutcome.CONFIRMED:
                # Cua confirmed the input landed but not the requested state.
                outcome = CuaOutcome.UNVERIFIED
            elif mapped is CuaOutcome.FAILED:
                outcome = CuaOutcome.FAILED

    error = data.get("error")
    if error and outcome is CuaOutcome.CONFIRMED:
        outcome = CuaOutcome.FAILED

    return CuaActionResult(
        outcome=outcome,
        summary=str(data.get("summary") or "")[:400],
        detail=data,
        evidence=list(data.get("evidence") or []),
        escalation=_as_mapping(data.get("escalation")) or None,
        raw_effect=effect or None,
        raw_verification=verification or None,
    )


# Cua reports a plain success as status="ok" for reads, mutations and setup steps.
_STATUS_OK = "ok"


def _status_ok_outcome(data: dict[str, Any], operation: CuaOperation) -> Optional[CuaOutcome]:
    """Read a bare ``status="ok"`` according to what the call was doing.

    Returns None when the payload does not clearly report success, so the caller can
    fall back to treating it as unknown rather than guessing.
    """
    status = _enum_value(data.get("status"))
    if status == "refused":
        return CuaOutcome.REFUSED
    if status != _STATUS_OK:
        return None
    if data.get("is_error") or data.get("isError"):
        return CuaOutcome.FAILED
    # A refusal always wins over a success-looking status.
    if data.get("refusal") or _enum_value(data.get("status")) == "refused":
        return CuaOutcome.REFUSED

    if operation is CuaOperation.MUTATION:
        # Dispatch is not proof. The caller must observe the postcondition.
        return CuaOutcome.EXECUTED_UNVERIFIED
    if operation is CuaOperation.READ:
        # A read only counts when it actually returned something to read.
        if _read_has_content(data):
            return CuaOutcome.CONFIRMED
        return CuaOutcome.UNVERIFIED
    # Setup: prepared/launched with no error is the whole postcondition.
    return CuaOutcome.CONFIRMED


def _read_has_content(data: dict[str, Any]) -> bool:
    for key in ("text", "content", "markdown", "value"):
        if data.get(key):
            return True
    for key in ("elements", "tabs", "nodes", "items", "images", "windows"):
        if isinstance(data.get(key), list) and data[key]:
            return True
    return False


class CuaAdapter:
    """Charlie-facing Cua surface. One instance per process."""

    def __init__(self, bridge: CuaRuntimeBridge) -> None:
        self._bridge = bridge

    @property
    def available(self) -> bool:
        return self._bridge.is_available

    def ensure_started(self) -> bool:
        return self._bridge.ensure_started()

    def shutdown(self, *, timeout_s: float = 10.0) -> None:
        self._bridge.shutdown(timeout_s=timeout_s)

    def _call(self, tool: str, arguments: dict[str, Any], *, timeout_s: float | None = None):
        """Invoke one Cua tool. The model never reaches this with a free-form name."""
        return self._bridge.call_tool(
            tool, json.dumps(arguments), timeout_s=timeout_s
        )

    # ------------------------------------------------------------------ read-only

    def list_windows(self, *, pid: Optional[int] = None, on_screen_only: bool = True):
        """Windows on this host. Typed SDK first, generic call as the fallback."""
        try:
            import cua_driver  # noqa: PLC0415
        except ImportError:
            payload = self._call(
                "list_windows",
                {"on_screen_only": on_screen_only, **({"pid": pid} if pid else {})},
            )
            return self._windows(payload)
        try:
            out = self._bridge.call_typed(
                lambda driver: driver.list_windows(
                    cua_driver.ListWindowsInput(pid=pid, on_screen_only=on_screen_only)
                )
            )
        except (CuaBridgeError, AttributeError, TypeError):
            payload = self._call(
                "list_windows",
                {"on_screen_only": on_screen_only, **({"pid": pid} if pid else {})},
            )
            return self._windows(payload)
        windows = getattr(out, "windows", None)
        if isinstance(windows, list):
            return [_window_info(w) for w in windows]
        return self._windows(_as_mapping(out))

    def desktop_state(self, *, include_screenshot: bool = False):
        return self._call(
            "get_desktop_state",
            {"include_screenshot": include_screenshot, "include_accessibility_tree": True},
        )

    def window_state(
        self,
        pid: int,
        window_id: int,
        *,
        capture_mode: str = "ax",
        include_screenshot: bool = False,
        query: Optional[str] = None,
        max_depth: Optional[int] = None,
    ):
        """Structured UIA state for one window. The screenshot stays opt-in."""
        args: dict[str, Any] = {
            "pid": pid,
            "window_id": window_id,
            "capture_mode": capture_mode,
            "include_screenshot": include_screenshot,
            "include_accessibility_tree": True,
        }
        if query:
            args["query"] = query
        if max_depth:
            args["max_depth"] = max_depth
        return self._call("get_window_state", args)

    # ------------------------------------------------------------------ lifecycle

    def launch_app(self, *, name: str | None = None, path: str | None = None):
        args: dict[str, Any] = {}
        if name:
            args["name"] = name
        if path:
            args["path"] = path
        return self._call("launch_app", args)

    def bring_to_front(self, pid: int, *, window_id: Optional[int] = None) -> CuaActionResult:
        """Explicit foreground activation. Distinct from "the process exists".

        Returns a normalised result like every other action; the raw payload stays
        on ``.detail`` because the foreground proof lives there.
        """
        args: dict[str, Any] = {"pid": int(pid)}
        if window_id is not None:
            args["window_id"] = int(window_id)
        payload = _as_mapping(self._call("bring_to_front", args))
        result = normalise(payload)
        # bring_to_front reports no ``effect``. Its success signal is the handle it
        # moved landing in front, so derive the outcome from that instead of letting
        # the absent field read as failure.
        if not payload.get("effect"):
            now = _hwnd(payload.get("now_fg_hwnd"))
            target = _hwnd(payload.get("target_hwnd"))
            landed = bool(payload.get("landed_on_target"))
            if landed and (not now or not target or now == target):
                result.outcome = CuaOutcome.CONFIRMED
                result.raw_effect = "landed_on_target"
            elif landed:
                result.outcome = CuaOutcome.UNVERIFIED
                result.raw_effect = "landed_on_target_mismatch"
            else:
                result.outcome = CuaOutcome.REFUSED
                result.raw_effect = "not_foreground"
        merged = dict(payload)
        merged.update(result.detail)
        result.detail = merged
        return result

    def open_and_focus(
        self,
        *,
        name: str | None = None,
        path: str | None = None,
        pid: Optional[int] = None,
        title_contains: Optional[str] = None,
        timeout_s: float = 15.0,
    ) -> CuaActionResult:
        """Foreground sequence: launch -> list_windows -> bring_to_front -> verify.

        launch_app reports no ``effect`` (it is a launch, not a foreground action), so
        it is judged on error fields and on whether it identified a process.
        """
        if pid is None and (name or path):
            # Reuse first. Launching an app that is already running is what made
            # "open" relaunch on every call, which then looked like a failure and
            # invited a retry.
            existing = self._find_window_by_app(name or path)
            if existing is not None:
                return self._focus_window(existing)
            launched = _as_mapping(self.launch_app(name=name, path=path))
            if launched.get("is_error") or launched.get("error_code"):
                return CuaActionResult(
                    outcome=CuaOutcome.FAILED,
                    summary="launch was refused by the driver",
                    detail=launched,
                )
            pid = _launched_pid(launched)
            if pid is None:
                return CuaActionResult(
                    outcome=CuaOutcome.FAILED,
                    summary="launch did not identify a process",
                    detail=launched,
                )
            # The launch payload can name the window directly; prefer it.
            launched_window = _launched_window(launched)
            if launched_window is not None and title_contains is None:
                if (
                    title_contains is None
                    or title_contains.lower()
                    in str(launched_window.get("title") or "").lower()
                ):
                    return self._focus_window(launched_window)

        window = self._find_window(pid=pid, title_contains=title_contains)
        if window is None:
            return CuaActionResult(
                outcome=CuaOutcome.FAILED,
                summary="no window matched after launch",
                detail={"pid": pid},
            )
        return self._focus_window(window)

    def focus_window(self, pid: int, window_id: int) -> CuaActionResult:
        """Focus the exact window a trusted native observer already selected."""
        if int(pid) <= 0 or int(window_id) <= 0:
            return CuaActionResult(
                outcome=CuaOutcome.REFUSED,
                summary="focus requires an exact positive pid and window_id",
            )
        return self._focus_window({"pid": int(pid), "window_id": int(window_id)})

    def _focus_window(self, window: dict[str, Any]) -> CuaActionResult:
        wpid = window.get("pid")
        wid = window.get("window_id")
        if not wpid:
            return CuaActionResult(
                outcome=CuaOutcome.FAILED, summary="window has no pid"
            )

        front = self.bring_to_front(
            int(wpid), window_id=int(wid) if wid is not None else None
        )
        front_payload = dict(front.detail)
        if front.outcome is CuaOutcome.REFUSED:
            return CuaActionResult(
                outcome=CuaOutcome.REFUSED,
                summary=front.summary or FOREGROUND_ACTIVATION_BLOCKED,
                detail=front_payload,
                raw_effect=front.raw_effect,
            )

# Foreground proof comes from the driver that did the activation: it reports the
        # handle it moved and the handle that ended up in front. Charlie's own
        # GetForegroundWindow is unusable here because Charlie may run in a different
        # window station, where that API reports an unrelated window.
        landed = bool(front_payload.get("landed_on_target"))
        now_hwnd = _hwnd(front_payload.get("now_fg_hwnd"))
        target_hwnd = _hwnd(front_payload.get("target_hwnd")) or (
            int(wid) if wid is not None else None
        )
        if not landed or (now_hwnd and target_hwnd and now_hwnd != target_hwnd):
            return CuaActionResult(
                outcome=CuaOutcome.REFUSED,
                summary=FOREGROUND_ACTIVATION_BLOCKED,
                detail={
                    "pid": int(wpid),
                    "window_id": wid,
                    "landed_on_target": landed,
                    "now_fg_hwnd": now_hwnd,
                    "target_hwnd": target_hwnd,
                },
            )

        # Fresh observation of the window, so the caller also gets the live state.
        state = _as_mapping(
            self.window_state(
                int(wpid), int(wid) if wid is not None else 0, max_depth=4
            )
        )
        if (
            state.get("is_error")
            or state.get("refusal")
            or state.get("error_code")
            or state.get("status") == "refused"
        ):
            refusal = state.get("refusal")
            reason = (
                refusal.get("message")
                if isinstance(refusal, dict)
                else state.get("text") or state.get("error_code") or "window state was refused"
            )
            return CuaActionResult(
                outcome=CuaOutcome.REFUSED,
                summary=f"fresh window state was refused: {reason}",
                detail={"pid": int(wpid), "window_id": int(wid), "window_state": state},
                raw_effect=front.raw_effect,
            )
        return CuaActionResult(
            outcome=CuaOutcome.CONFIRMED,
            summary="window brought to foreground and confirmed by handle comparison",
            detail={
                "pid": int(wpid),
                "window_id": wid,
                "now_fg_hwnd": now_hwnd,
                "target_hwnd": target_hwnd,
                "window_state": state,
            },
            raw_effect=front.raw_effect,
        )

    def _find_window_by_app(self, wanted: str) -> Optional[dict[str, Any]]:
        """An existing window for ``wanted``, or None when the app is not running."""
        try:
            windows = self.list_windows(on_screen_only=False)
        except Exception:
            # Bounded manifests may intentionally refuse global enumeration. The
            # launch path below still returns a verified refusal or target window.
            return None
        for window in windows:
            if _window_matches(window, wanted):
                return window
        return None

    def _find_window(
        self, *, pid: Optional[int], title_contains: Optional[str]
    ) -> Optional[dict[str, Any]]:
        """Locate a concrete window. Never guesses an id."""
        payload = self.list_windows(pid=pid)
        for window in self._windows(payload):
            if pid is not None and int(window.get("pid") or 0) != int(pid):
                continue
            if title_contains:
                title = str(window.get("title") or "")
                if title_contains.lower() not in title.lower():
                    continue
            return window
        return None

    @staticmethod
    def _windows(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [_window_info(item) for item in payload]
        data = _as_mapping(payload)
        for key in ("windows", "items", "result"):
            value = data.get(key)
            if isinstance(value, list):
                return [_window_info(w) for w in value]
        return []

    @staticmethod
    def _as_state(payload: Any) -> dict[str, Any]:
        return _as_mapping(payload)

    @staticmethod
    def _is_foreground(state: dict[str, Any]) -> bool:
        """Foreground proof from a fresh observation.

        Prefers an explicit flag when the host reports one, and otherwise requires a
        window handle. Deliberately conservative: absent evidence is not foreground.
        """
        for key in ("is_foreground", "foreground", "is_active", "active", "is_front"):
            if key in state:
                return bool(state.get(key))
        handle = state.get("window_id") or state.get("hwnd")
        return handle not in (None, 0, "0")

    # -------------------------------------------------------------------- actions

    def click(
        self,
        *,
        pid: Optional[int] = None,
        window_id: Optional[int] = None,
        element_token: Optional[str] = None,
        x: Optional[float] = None,
        y: Optional[float] = None,
        foreground: bool = True,
        button: str = "left",
        click_count: int = 1,
    ) -> CuaActionResult:
        """Element token first; pixel coordinates only when UIA cannot express it."""
        if not element_token and (x is None or y is None):
            return CuaActionResult(
                outcome=CuaOutcome.REFUSED,
                summary="click needs an element token or an explicit pixel target",
            )
        args: dict[str, Any] = {
            "delivery_mode": "foreground" if foreground else "background",
            "button": button,
        }
        if element_token:
            args["element_token"] = element_token
        else:
            args["x"] = float(x)
            args["y"] = float(y)
        if pid is not None:
            args["pid"] = int(pid)
        if window_id is not None:
            args["window_id"] = int(window_id)
        return normalise(self._call("click", args))

    def move_cursor(
        self, x: float, y: float, *, pid: int, window_id: int
    ) -> CuaActionResult:
        return normalise(self._call("move_cursor", {
            "x": float(x), "y": float(y), "pid": int(pid), "window_id": int(window_id),
            "delivery_mode": "foreground",
        }))

    def double_click(
        self,
        *,
        pid: int,
        window_id: int,
        element_token: Optional[str] = None,
        x: Optional[float] = None,
        y: Optional[float] = None,
    ) -> CuaActionResult:
        args: dict[str, Any] = {"pid": int(pid), "window_id": int(window_id)}
        if element_token:
            args["element_token"] = element_token
        elif x is not None and y is not None:
            args.update(x=float(x), y=float(y))
        else:
            return CuaActionResult(outcome=CuaOutcome.REFUSED, summary="double-click needs an exact target")
        return normalise(self._call("double_click", args))

    def set_window_frame(
        self, pid: int, window_id: int, *, x: int, y: int, width: int, height: int
    ) -> CuaActionResult:
        return normalise(self._call("set_window_frame", {
            "pid": int(pid), "window_id": int(window_id), "x": int(x), "y": int(y),
            "width": int(width), "height": int(height),
        }))

    def type_text(
        self,
        text: str,
        *,
        pid: Optional[int] = None,
        window_id: Optional[int] = None,
        element_token: Optional[str] = None,
        foreground: bool = True,
    ) -> CuaActionResult:
        args: dict[str, Any] = {
            "text": text,
            "delivery_mode": "foreground" if foreground else "background",
        }
        if element_token:
            args["element_token"] = element_token
        if pid is not None:
            args["pid"] = int(pid)
        if window_id is not None:
            args["window_id"] = int(window_id)
        return normalise(self._call("type_text", args))

    def set_value(
        self, pid: int, value: str, *, element_token: Optional[str] = None,
        window_id: Optional[int] = None, foreground: bool = True,
    ) -> CuaActionResult:
        args: dict[str, Any] = {
            "pid": int(pid),
            "value": value,
            "delivery_mode": "foreground" if foreground else "background",
        }
        if element_token:
            args["element_token"] = element_token
        if window_id is not None:
            args["window_id"] = int(window_id)
        return normalise(self._call("set_value", args))

    def press_key(
        self,
        key: str,
        *,
        modifiers: Optional[list[str]] = None,
        pid: Optional[int] = None,
        window_id: Optional[int] = None,
        foreground: bool = True,
    ) -> CuaActionResult:
        """Charlie policy/approval has already cleared this chord by the time we are here."""
        args: dict[str, Any] = {
            "key": key,
            "delivery_mode": "foreground" if foreground else "background",
        }
        if modifiers:
            args["modifiers"] = list(modifiers)
        if pid is not None:
            args["pid"] = int(pid)
        if window_id is not None:
            args["window_id"] = int(window_id)
        return normalise(self._call("press_key", args))

    def scroll(
        self,
        *,
        direction: str = "down",
        amount: int = 3,
        pid: Optional[int] = None,
        window_id: Optional[int] = None,
        foreground: bool = True,
    ) -> CuaActionResult:
        args: dict[str, Any] = {
            "direction": direction,
            "amount": int(amount),
            "delivery_mode": "foreground" if foreground else "background",
        }
        if pid is not None:
            args["pid"] = int(pid)
        if window_id is not None:
            args["window_id"] = int(window_id)
        return normalise(self._call("scroll", args))

    def drag(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        *,
        pid: Optional[int] = None,
        window_id: Optional[int] = None,
    ) -> CuaActionResult:
        """Pixel drag. Requires a caller-supplied fresh capture."""
        args: dict[str, Any] = {
            "from_x": float(x1),
            "from_y": float(y1),
            "to_x": float(x2),
            "to_y": float(y2),
        }
        if pid is not None:
            args["pid"] = int(pid)
        if window_id is not None:
            args["window_id"] = int(window_id)
        return normalise(self._call("drag", args))

    def invoke_menu(
        self, pid: int, window_id: int, path: list[str]
    ) -> CuaActionResult:
        """Menu route, e.g. Notepad File -> Save when a background chord is unsupported."""
        return normalise(
            self._call(
                "invoke_menu",
                {"pid": int(pid), "window_id": int(window_id), "path": list(path)},
            )
        )

    def verify_state(
        self,
        pid: int,
        window_id: int,
        expect: list[dict[str, Any]],
        *,
        timeout_ms: int = 5000,
        stable_samples: int = 2,
    ) -> CuaActionResult:
        return normalise(
            self._call(
                "verify_state",
                {
                    "pid": int(pid),
                    "window_id": int(window_id),
                    "expect": list(expect),
                    "timeout_ms": int(timeout_ms),
                    "stable_samples": int(stable_samples),
                },
            )
        )

    def health(self):
        try:
            return self._call("health_report", {})
        except CuaBridgeError:
            return None

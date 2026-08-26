"""C.H.A.R.L.I.E desktop companion.

Pet V2 keeps the companion as an IPC presentation client.  Semantic runtime
state comes from ``charlie_state``; interaction state only chooses motion and
never replaces the authoritative core state machine.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

import zmq

QT_AVAILABLE = False
QT_IMPORT_ERROR: ImportError | None = None
_QT_IMPORT_ERRORS: list[ImportError] = []

try:
    from PySide6.QtCore import QPoint, QRectF, Qt, QTimer, Signal
    from PySide6.QtGui import (
        QAction,
        QColor,
        QCursor,
        QFont,
        QFontMetrics,
        QGuiApplication,
        QImage,
        QMouseEvent,
        QPainter,
        QPen,
        QRegion,
    )
    from PySide6.QtWidgets import QApplication, QMenu, QWidget
    QT_AVAILABLE = True
except ImportError as exc:
    _QT_IMPORT_ERRORS.append(exc)
    try:
        from PyQt6.QtCore import QPoint, QRectF, Qt, QTimer
        from PyQt6.QtCore import pyqtSignal as Signal
        from PyQt6.QtGui import (
            QAction,
            QColor,
            QCursor,
            QFont,
            QFontMetrics,
            QGuiApplication,
            QImage,
            QMouseEvent,
            QPainter,
            QPen,
            QRegion,
        )
        from PyQt6.QtWidgets import QApplication, QMenu, QWidget
        QT_AVAILABLE = True
    except ImportError as exc:
        _QT_IMPORT_ERRORS.append(exc)
        QT_IMPORT_ERROR = ImportError(
            "Neither PySide6 nor PyQt6 is available: "
            + "; ".join(str(error) for error in _QT_IMPORT_ERRORS)
        )

if not QT_AVAILABLE:
    # Keep this module importable on installations without the optional HUD
    # extra.  The entrypoint exits before constructing a window; these two
    # placeholders only prevent class declarations from masking the real
    # dependency error with ``NameError: QWidget is not defined``.
    QWidget = object

    class _FallbackPoint:
        def __init__(self, x: float, y: float) -> None:
            self._x = x
            self._y = y

        def x(self) -> float:
            return self._x

        def y(self) -> float:
            return self._y

    class QRectF:
        """Small geometry fallback so pure layout helpers remain testable without Qt."""

        def __init__(self, x: float = 0, y: float = 0, width: float = 0, height: float = 0) -> None:
            self._x = x
            self._y = y
            self._width = width
            self._height = height

        def left(self) -> float:
            return self._x

        def top(self) -> float:
            return self._y

        def right(self) -> float:
            return self._x + self._width

        def bottom(self) -> float:
            return self._y + self._height

        def width(self) -> float:
            return self._width

        def height(self) -> float:
            return self._height

        def center(self) -> _FallbackPoint:
            return _FallbackPoint(self._x + self._width / 2, self._y + self._height / 2)

        def isNull(self) -> bool:
            return self._width == 0 and self._height == 0

    def Signal(*_args: object, **_kwargs: object) -> None:
        return None

from charlie.config import config
from charlie.ipc import DEFAULT_COMMAND_PORT, DEFAULT_EVENT_PORT
from charlie.utils import json_loads

logger = logging.getLogger("charlie.pet")

LOGICAL_WIDTH = 220
LOGICAL_HEIGHT = 230
EXPANDED_LOGICAL_HEIGHT = 340
MIN_SCALE = 0.5
MAX_SCALE = 2.0
DRAG_THRESHOLD = 7
EDGE_SNAP_THRESHOLD = 28
EDGE_GAP = 14
CAPTION_TTL_SECONDS = 5.0
DEBUG_LAYOUT = False

CORE_STATES = frozenset(
    ("idle", "listening", "thinking", "speaking", "working", "waiting", "attention", "completed", "error")
)
INTERACTION_STATES = frozenset(("normal", "hover", "pressed", "dragging", "landing", "ptt", "menu"))

STATE_TITLES = {
    "idle": "Ready",
    "listening": "Listening",
    "thinking": "Thinking",
    "speaking": "Speaking",
    "working": "Working",
    "waiting": "Approval needed",
    "attention": "Needs attention",
    "completed": "Complete",
    "error": "Error",
}
STATE_COLORS = {
    "idle": "#6d7c90",
    "listening": "#42d9ff",
    "thinking": "#a78bfa",
    "speaking": "#62e6b5",
    "working": "#ffb454",
    "waiting": "#f7ca62",
    "attention": "#ff9f5a",
    "completed": "#76e3a7",
    "error": "#ff6b7d",
}


@dataclass(frozen=True)
class ScreenInfo:
    """Monitor geometry used by pure layout/persistence helpers."""

    name: str
    left: int
    top: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height


@dataclass(frozen=True)
class PositionRecord:
    x: int
    y: int
    scale: float = 1.0
    screen: str = ""
    normalized_x: float = 1.0
    normalized_y: float = 1.0
    anchor: str = "free"
    captions: bool = True
    cursor_tracking: bool = True


def clamp_position(x: int, y: int, window_width: int, window_height: int, screen: ScreenInfo) -> tuple[int, int]:
    """Clamp a top-left position to one monitor's available geometry."""
    max_x = max(screen.left, screen.right - window_width)
    max_y = max(screen.top, screen.bottom - window_height)
    return max(screen.left, min(x, max_x)), max(screen.top, min(y, max_y))


def detect_anchor(
    x: int,
    y: int,
    window_width: int,
    window_height: int,
    screen: ScreenInfo,
    threshold: int = EDGE_SNAP_THRESHOLD,
) -> str:
    """Return nearest edge/corner only when the window is actually close to it."""
    near_left = abs(x - screen.left) <= threshold
    near_right = abs(screen.right - (x + window_width)) <= threshold
    near_top = abs(y - screen.top) <= threshold
    near_bottom = abs(screen.bottom - (y + window_height)) <= threshold
    horizontal = "left" if near_left else "right" if near_right else ""
    vertical = "top" if near_top else "bottom" if near_bottom else ""
    return f"{vertical}_{horizontal}" if vertical and horizontal else vertical or horizontal or "free"


def snapped_position(
    x: int,
    y: int,
    window_width: int,
    window_height: int,
    screen: ScreenInfo,
    threshold: int = EDGE_SNAP_THRESHOLD,
    gap: int = EDGE_GAP,
) -> tuple[tuple[int, int], str]:
    """Soft-snap eligible edges; leave free placement untouched."""
    anchor = detect_anchor(x, y, window_width, window_height, screen, threshold)
    if anchor == "free":
        return clamp_position(x, y, window_width, window_height, screen), anchor
    left = screen.left + gap
    right = screen.right - window_width - gap
    top = screen.top + gap
    bottom = screen.bottom - window_height - gap
    nx = left if "left" in anchor else right if "right" in anchor else x
    ny = top if "top" in anchor else bottom if "bottom" in anchor else y
    return clamp_position(nx, ny, window_width, window_height, screen), anchor


def activity_orientation(anchor: str) -> str:
    """Choose inward-facing tray direction from the saved/observed edge."""
    if "right" in anchor:
        return "left"
    if "left" in anchor:
        return "right"
    if "top" in anchor:
        return "down"
    return "up"  # bottom and free default to a tray above the companion


def choose_saved_screen(record: PositionRecord, screens: Iterable[ScreenInfo]) -> ScreenInfo:
    """Select named monitor, then nearest monitor, then first available monitor."""
    available = list(screens)
    if not available:
        raise ValueError("at least one screen is required")
    for screen in available:
        if record.screen and screen.name == record.screen:
            return screen
    center_x = record.x + 60
    center_y = record.y + 60
    return min(
        available,
        key=lambda s: (
            max(s.left - center_x, 0, center_x - s.right) ** 2 + max(s.top - center_y, 0, center_y - s.bottom) ** 2
        ),
    )


def load_position_record(path: Path, default_scale: float = 1.0) -> PositionRecord:
    """Load v2 position data and migrate the old x/y/scale shape safely."""
    try:
        data = json_loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    raw_scale = data.get("scale", default_scale)
    scale = float(raw_scale) if isinstance(raw_scale, (int, float)) else default_scale
    scale = max(MIN_SCALE, min(MAX_SCALE, scale))
    x = data.get("x", 0)
    y = data.get("y", 0)
    return PositionRecord(
        x=int(x) if isinstance(x, (int, float)) else 0,
        y=int(y) if isinstance(y, (int, float)) else 0,
        scale=scale,
        screen=str(data.get("screen", "")),
        normalized_x=float(data.get("normalized_x", 1.0))
        if isinstance(data.get("normalized_x", 1.0), (int, float))
        else 1.0,
        normalized_y=float(data.get("normalized_y", 1.0))
        if isinstance(data.get("normalized_y", 1.0), (int, float))
        else 1.0,
        anchor=str(data.get("anchor", "free"))
        if data.get("anchor", "free")
        in {"free", "left", "right", "top", "bottom", "top_left", "top_right", "bottom_left", "bottom_right"}
        else "free",
        captions=bool(data.get("captions", True)),
        cursor_tracking=bool(data.get("cursor_tracking", True)),
    )


def position_record_json(record: PositionRecord) -> dict:
    return {
        "version": 2,
        "x": record.x,
        "y": record.y,
        "scale": record.scale,
        "screen": record.screen,
        "normalized_x": record.normalized_x,
        "normalized_y": record.normalized_y,
        "anchor": record.anchor,
        "captions": record.captions,
        "cursor_tracking": record.cursor_tracking,
    }


@dataclass(frozen=True)
class SpriteFrame:
    """Procedural sprite frame parameters; no external artwork is bundled."""

    name: str
    bob: float = 0.0
    lean: float = 0.0
    eye_x: float = 0.0
    eye_y: float = 0.0
    eye_open: float = 1.0
    glow: float = 1.0
    mouth: float = 0.0


@dataclass(frozen=True)
class AnimationClip:
    name: str
    frames: tuple[SpriteFrame, ...]
    fps: float
    loop: bool = True


def _frames(name: str, values: tuple[SpriteFrame, ...], fps: float, loop: bool = True) -> AnimationClip:
    return AnimationClip(name, values, fps, loop)


_BASE = SpriteFrame("base")

CHARLIE_ATLAS_PATH = Path(__file__).with_name("assets") / "charlie_pet_atlas.png"
CHARLIE_ATLAS_COLUMNS = 5
CHARLIE_ATLAS_ROWS = 4

# The atlas is original CHARLIE artwork.  Clips intentionally reuse related
# poses as short loops so every semantic state has visible body movement while
# keeping one loaded image and one stable rendering path.
SPRITE_CLIP_FRAMES: dict[str, tuple[int, ...]] = {
    "idle": (0, 0, 4, 0),
    "idle_blink": (1, 0),
    "idle_look": (2, 3),
    "hover": (4, 5, 4),
    "pressed": (6, 1),
    "drag_left": (10, 11),
    "drag_right": (11, 10),
    "drag_up": (4, 5),
    "drag_down": (5, 4),
    "landing": (12, 17, 0),
    "listening": (7, 5),
    "thinking": (8, 2),
    "speaking": (13, 16),
    "working": (14, 9),
    "waiting": (15, 1),
    "attention": (9, 17),
    "completed": (17, 18, 0),
    "error": (19, 10),
    "ptt_listening": (7, 5),
}
ANIMATION_CLIPS = {
    "idle": _frames("idle", (_BASE, SpriteFrame("breath", bob=-1.5, glow=1.04)), 3.0),
    "idle_blink": _frames("idle_blink", (SpriteFrame("blink", eye_open=0.05), SpriteFrame("open")), 8.0, False),
    "idle_look": _frames("idle_look", (SpriteFrame("look_l", eye_x=-4), SpriteFrame("look_r", eye_x=4)), 2.0),
    "hover": _frames(
        "hover",
        (SpriteFrame("anticipate", bob=3, lean=-2), SpriteFrame("rise", bob=-5), SpriteFrame("settle", bob=-1)),
        10.0,
        False,
    ),
    "pressed": _frames("pressed", (SpriteFrame("press", bob=3, glow=0.9),), 12.0),
    "drag_left": _frames(
        "drag_left", (SpriteFrame("left1", lean=-8, eye_x=-3), SpriteFrame("left2", lean=-12, bob=-2, eye_x=-4)), 12.0
    ),
    "drag_right": _frames(
        "drag_right", (SpriteFrame("right1", lean=8, eye_x=3), SpriteFrame("right2", lean=12, bob=-2, eye_x=4)), 12.0
    ),
    "drag_up": _frames("drag_up", (SpriteFrame("up1", lean=-3, bob=-4), SpriteFrame("up2", lean=3, bob=-6)), 12.0),
    "drag_down": _frames(
        "drag_down", (SpriteFrame("down1", lean=3, bob=3), SpriteFrame("down2", lean=-3, bob=5)), 12.0
    ),
    "landing": _frames(
        "landing", (SpriteFrame("land1", bob=-5), SpriteFrame("land2", bob=4), SpriteFrame("land3")), 12.0, False
    ),
    "listening": _frames(
        "listening",
        (SpriteFrame("listen1", eye_open=1.1, glow=1.12), SpriteFrame("listen2", eye_open=1.2, glow=1.2)),
        5.0,
    ),
    "thinking": _frames(
        "thinking", (SpriteFrame("think_l", eye_x=-4, mouth=-1), SpriteFrame("think_r", eye_x=4, mouth=-1)), 2.5
    ),
    "speaking": _frames("speaking", (SpriteFrame("speak1", mouth=1), SpriteFrame("speak2", mouth=4, bob=-1)), 8.0),
    "working": _frames("working", (SpriteFrame("work1", glow=1.0), SpriteFrame("work2", eye_x=2, glow=1.08)), 5.0),
    "waiting": _frames("waiting", (SpriteFrame("wait", eye_y=2, glow=0.92),), 2.0),
    "attention": _frames(
        "attention", (SpriteFrame("attention1", glow=1.2), SpriteFrame("attention2", bob=-2, glow=1.35)), 5.0
    ),
    "completed": _frames(
        "completed", (SpriteFrame("done1", bob=-3, glow=1.25), SpriteFrame("done2", bob=2, glow=1.05)), 7.0, False
    ),
    "error": _frames(
        "error", (SpriteFrame("error1", lean=-3, glow=1.15), SpriteFrame("error2", lean=3, glow=1.15)), 8.0
    ),
    "ptt_listening": _frames(
        "ptt_listening", (SpriteFrame("ptt1", eye_open=1.1, glow=1.2), SpriteFrame("ptt2", mouth=2, glow=1.3)), 8.0
    ),
}


def resolve_animation_clip(semantic: str, interaction: str = "normal", ptt: bool = False) -> str:
    """Deterministic interaction-over-semantic resolver."""
    if interaction in {"dragging", "landing", "pressed", "hover"}:
        if interaction == "dragging":
            return "drag_right"
        return interaction
    if ptt:
        return "ptt_listening"
    return semantic if semantic in ANIMATION_CLIPS else "idle"


class PetAnimator:
    """Small frame clock. It owns motion only; semantic state remains external."""

    def __init__(self) -> None:
        self.semantic = "idle"
        self.interaction = "normal"
        self.ptt = False
        self.clip_name = "idle"
        self.frame_index = 0
        self.elapsed = 0.0
        self._last_personality = time.monotonic()

    @property
    def frame(self) -> SpriteFrame:
        return ANIMATION_CLIPS[self.clip_name].frames[self.frame_index]

    @property
    def sprite_index(self) -> int:
        frames = SPRITE_CLIP_FRAMES.get(self.clip_name, SPRITE_CLIP_FRAMES["idle"])
        return frames[self.frame_index % len(frames)]

    def set_semantic(self, state: str) -> None:
        if state in CORE_STATES:
            self.semantic = state
            self._select_clip()

    def set_interaction(self, interaction: str) -> None:
        if interaction in INTERACTION_STATES:
            self.interaction = interaction
            self._select_clip()

    def set_ptt(self, active: bool) -> None:
        self.ptt = active
        self._select_clip()

    def drag_clip(self, dx: float, dy: float) -> None:
        if abs(dx) >= abs(dy):
            self.clip_name = "drag_right" if dx >= 0 else "drag_left"
        else:
            self.clip_name = "drag_down" if dy >= 0 else "drag_up"
        self.interaction = "dragging"
        self.elapsed = 0.0

    def tick(self, dt: float) -> bool:
        self.elapsed += max(0.0, dt)
        clip = ANIMATION_CLIPS[self.clip_name]
        changed = False
        frame = min(len(clip.frames) - 1, int(self.elapsed * clip.fps))
        if frame != self.frame_index:
            self.frame_index = frame
            changed = True
        if clip.loop and len(clip.frames) > 1 and self.elapsed * clip.fps >= len(clip.frames):
            self.elapsed %= len(clip.frames) / clip.fps
            self.frame_index = int(self.elapsed * clip.fps)
            changed = True
        elif not clip.loop and self.elapsed >= len(clip.frames) / clip.fps:
            if self.interaction in {"landing", "pressed"}:
                self.interaction = "normal"
                self._select_clip()
                changed = True
            elif self.clip_name in {"idle_blink", "idle_look", "completed"}:
                self.clip_name = self.semantic if self.semantic not in {"completed", "error"} else "idle"
                self.frame_index = 0
                self.elapsed = 0.0
                changed = True
        return changed

    def refresh_interval_ms(self) -> int:
        if self.interaction == "dragging" or self.ptt:
            return 16
        if self.interaction in {"hover", "pressed", "landing"} or self.semantic in {"speaking", "attention", "error"}:
            return 33
        if self.semantic == "idle":
            return 80
        return 50

    def maybe_personality(self, now: float) -> None:
        if self.semantic != "idle" or self.interaction != "normal":
            return
        if now - self._last_personality >= 7.0:
            self.clip_name = "idle_blink" if int(now) % 2 else "idle_look"
            self.elapsed = 0.0
            self.frame_index = 0
            self._last_personality = now

    def _select_clip(self) -> None:
        selected = resolve_animation_clip(self.semantic, self.interaction, self.ptt)
        if selected != self.clip_name:
            self.clip_name = selected
            self.frame_index = 0
            self.elapsed = 0.0


@dataclass(frozen=True)
class LayoutSnapshot:
    window_width: float
    window_height: float
    body: QRectF
    title: QRectF
    activity: QRectF
    badge: QRectF
    ptt: QRectF
    approve: QRectF
    reject: QRectF
    orientation: str


class PetLayoutEngine:
    """Single logical coordinate system shared by rendering, hit testing, and mask."""

    def __init__(self, width: float = 320.0, height: float = EXPANDED_LOGICAL_HEIGHT) -> None:
        self.width = width
        self.height = height

    def calculate(self, anchor: str, expanded: bool, approval: bool) -> LayoutSnapshot:
        orientation = activity_orientation(anchor)
        vertical = orientation in {"up", "down"}
        if vertical:
            window_width = 220.0
            window_height = float(EXPANDED_LOGICAL_HEIGHT if expanded else LOGICAL_HEIGHT)
            body_y = 166 if expanded and orientation == "up" else 8 if orientation == "down" else 72
            body = QRectF(52, body_y, 116, 116)
            header_y = 8 if orientation == "up" else body.bottom() + 8
            header = QRectF(10, header_y, 200, 54)
            tray = QRectF(10, header_y, 200, 150) if expanded else QRectF()
        else:
            window_width = 320.0
            window_height = 300.0 if expanded else 220.0
            panel_x = 8.0 if orientation == "left" else 136.0
            body_x = 206.0 if orientation == "left" else 14.0
            header = QRectF(panel_x, 8, 176, 54)
            tray = QRectF(panel_x, 8, 176, 150) if expanded else QRectF()
            body = QRectF(body_x, 68, 116, 116)
        controls_y = body.bottom() + 8
        if vertical and orientation == "down":
            controls_y = header.bottom() + 8
        badge = QRectF(body.center().x() - 34, controls_y, 28, 28)
        ptt = QRectF(body.center().x() + 6, controls_y, 28, 28)
        title = header
        activity = tray
        approve = QRectF(activity.left() + 12, activity.bottom() - 38, 70, 26) if approval and expanded else QRectF()
        reject = QRectF(activity.left() + 94, activity.bottom() - 38, 70, 26) if approval and expanded else QRectF()
        return LayoutSnapshot(
            window_width, window_height, body, title, activity, badge, ptt, approve, reject, orientation
        )

    @staticmethod
    def hit_regions(layout: LayoutSnapshot, controls_visible: bool, expanded: bool) -> tuple[QRectF, ...]:
        regions = [layout.body, layout.ptt]
        if controls_visible:
            regions.append(layout.badge)
            regions.append(layout.title)
        if expanded:
            regions.append(layout.activity)
            if not layout.approve.isNull():
                regions.extend((layout.approve, layout.reject))
        return tuple(regions)


@dataclass
class ApprovalState:
    request_id: str
    title: str
    reason: str


class PetActivityModel:
    """IPC-fed, bounded companion-side activity projection."""

    def __init__(self) -> None:
        self.tasks: dict[str, dict] = {}
        self.approval: Optional[ApprovalState] = None
        self.alert: str = ""
        self.completion: str = ""
        self._updated: dict[str, float] = {}

    @property
    def active_count(self) -> int:
        return sum(1 for task in self.tasks.values() if task.get("status") in {"planning", "running", "paused"})

    def apply(self, event: dict) -> None:
        etype = event.get("type", "")
        payload = event.get("payload") or {}
        now = time.monotonic()
        if etype in {"background_task", "task_snapshot"}:
            rows = payload.get("tasks") if etype == "task_snapshot" else [payload]
            if etype == "task_snapshot":
                self.tasks.clear()
                self._updated.clear()
            if isinstance(rows, list):
                for row in rows:
                    if isinstance(row, dict) and row.get("id"):
                        self.tasks[str(row["id"])] = dict(row)
                        self._updated[str(row["id"])] = now
                        if row.get("status") in {"done", "cancelled", "failed"}:
                            self.completion = str(row.get("title") or row.get("text") or "Task finished")
        elif etype == "tool_approval_request":
            request_id = payload.get("request_id")
            if request_id:
                self.approval = ApprovalState(
                    str(request_id),
                    str(payload.get("tool_name") or "Approval needed"),
                    str(payload.get("reason") or "A decision is required"),
                )
        elif etype in {"presentation_intent", "presentation_update"}:
            content = payload.get("content")
            if payload.get("kind") == "attention" and isinstance(content, dict) and content.get("request_id"):
                self.approval = ApprovalState(
                    str(content["request_id"]),
                    str(payload.get("title") or "Approval needed"),
                    str(payload.get("summary") or content.get("reason") or "A decision is required"),
                )
        elif etype in {"tool_approval_resolved", "presentation_dismiss"} and payload.get(
            "request_id", payload.get("id")
        ):
            resolved_id = payload.get("request_id", payload.get("id"))
            if self.approval and str(resolved_id) == self.approval.request_id:
                self.approval = None
        elif etype == "alert" and payload.get("severity") in {"warning", "error"}:
            self.alert = str(payload.get("message") or "Something needs attention")
        elif etype == "result_stored":
            self.completion = str(payload.get("summary") or "Task finished")
        self.prune(now)

    def prune(self, now: Optional[float] = None) -> None:
        now = now if now is not None else time.monotonic()
        for task_id in list(self.tasks):
            task = self.tasks[task_id]
            if task.get("status") in {"done", "cancelled", "failed"} and now - self._updated.get(task_id, now) > 4.0:
                self.tasks.pop(task_id, None)
                self._updated.pop(task_id, None)

    def visible_tasks(self, limit: int = 4) -> list[dict]:
        return list(self.tasks.values())[-limit:]


class PetCommandClient:
    """Persistent fire-and-forget PUSH client; reconnect-safe and UI-thread friendly."""

    def __init__(self, port: int = DEFAULT_COMMAND_PORT) -> None:
        self._port = port
        self._ctx: Optional[zmq.Context] = None
        self._socket = None
        self._lock = threading.Lock()

    def send(self, command_type: str, payload: Optional[dict] = None) -> bool:
        with self._lock:
            try:
                if self._ctx is None:
                    self._ctx = zmq.Context()
                    self._socket = self._ctx.socket(zmq.PUSH)
                    self._socket.setsockopt(zmq.LINGER, 0)
                    self._socket.connect(f"tcp://127.0.0.1:{self._port}")
                self._socket.send_string(json.dumps({"type": command_type, "payload": payload or {}}), zmq.NOBLOCK)
                return True
            except (zmq.ZMQError, OSError):
                logger.debug("Pet command unavailable: %s", command_type)
                return False

    def close(self) -> None:
        with self._lock:
            if self._socket is not None:
                self._socket.close(linger=0)
            if self._ctx is not None:
                self._ctx.term()
            self._socket = None
            self._ctx = None


class PetEventBridge:
    """Background SUB bridge with retry and explicit connection health."""

    def __init__(
        self, on_event: Callable[[dict], None], on_connection: Callable[[bool], None], port: int = DEFAULT_EVENT_PORT
    ) -> None:
        self._on_event = on_event
        self._on_connection = on_connection
        self._port = port
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="CharliePetEvents", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def _run(self) -> None:
        ctx = zmq.Context()
        sock = None
        connected = False
        last_event_at = time.monotonic()
        try:
            while not self._stop.is_set():
                try:
                    if sock is None:
                        sock = ctx.socket(zmq.SUB)
                        sock.setsockopt(zmq.SUBSCRIBE, b"")
                        sock.setsockopt(zmq.RCVTIMEO, 500)
                        sock.connect(f"tcp://127.0.0.1:{self._port}")
                    raw = sock.recv_string()
                    last_event_at = time.monotonic()
                    if not connected:
                        connected = True
                        self._on_connection(True)
                    event = json.loads(raw)
                    if isinstance(event, dict):
                        self._on_event(event)
                except zmq.Again:
                    if connected and time.monotonic() - last_event_at > 8.0:
                        connected = False
                        self._on_connection(False)
                except (zmq.ZMQError, OSError, ValueError):
                    if connected:
                        connected = False
                        self._on_connection(False)
                    if sock is not None:
                        sock.close(linger=0)
                        sock = None
                    self._stop.wait(0.5)
        finally:
            if connected:
                self._on_connection(False)
            if sock is not None:
                sock.close(linger=0)
            ctx.term()


class PetWindow(QWidget):
    state_changed = Signal(str)
    caption_changed = Signal(str)
    workspace_surface_changed = Signal(bool)
    event_received = Signal(object)
    connection_changed = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self._pet_scale = max(MIN_SCALE, min(MAX_SCALE, float(getattr(config, "pet_scale", 1.0))))
        self._position_path = Path(getattr(config, "pet_position_path", "pet_position.json"))
        self._captions_enabled = bool(getattr(config, "pet_captions", True))
        self._cursor_tracking = bool(getattr(config, "pet_cursor_tracking", True))
        self._edge_snap = bool(getattr(config, "pet_edge_snap", True))
        self._anchor = "bottom_right"
        self._semantic_state = "idle"
        self._controls_alpha = 0.0
        self._caption_alpha = 0.0
        self._caption_until = 0.0
        self._caption_desc = ""
        self._speech_buffer = ""
        self._audio_level = 0.0
        self._last_tick = time.monotonic()
        self._last_mask_signature: Optional[tuple] = None
        self._drag_offset: Optional[QPoint] = None
        self._press_global: Optional[QPoint] = None
        self._last_drag_global: Optional[QPoint] = None
        self._dragged = False
        self._ptt_pressed = False
        self._activity_expanded = False
        self._connected = False
        self._active_workspaces: set[str] = set()
        self._cursor_eye_x = 0.0
        self._cursor_eye_y = 0.0
        self._pre_workspace_pos: Optional[QPoint] = None
        self._relocate_target: Optional[QPoint] = None
        self._layout_engine = PetLayoutEngine()
        self._layout = self._layout_engine.calculate(self._anchor, False, False)
        self._animator = PetAnimator()
        self._sprite_atlas = QImage(str(CHARLIE_ATLAS_PATH))
        if self._sprite_atlas.isNull():
            logger.error("CHARLIE sprite atlas could not be loaded from %s", CHARLIE_ATLAS_PATH)
        self._activity = PetActivityModel()
        self._commands = PetCommandClient()
        self._restore_position()
        self._layout = self._layout_engine.calculate(self._anchor, False, False)
        self._resize_to_layout()
        self.state_changed.connect(self._on_state_changed)
        self.caption_changed.connect(self._on_caption_changed)
        self.workspace_surface_changed.connect(self._on_workspace_surface_changed)
        self.event_received.connect(self.ingest_event)
        self.connection_changed.connect(self.set_connection)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(self._animator.refresh_interval_ms())
        self._show_caption("Core unavailable")

    def _current_screen(self) -> Optional[ScreenInfo]:
        screen = QGuiApplication.screenAt(self.frameGeometry().center())
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        if screen is None:
            return None
        rect = screen.availableGeometry()
        return ScreenInfo(screen.name(), rect.left(), rect.top(), rect.width(), rect.height())

    def _all_screens(self) -> list[ScreenInfo]:
        return [
            ScreenInfo(
                s.name(),
                s.availableGeometry().left(),
                s.availableGeometry().top(),
                s.availableGeometry().width(),
                s.availableGeometry().height(),
            )
            for s in QGuiApplication.screens()
        ]

    def _restore_position(self) -> None:
        record = load_position_record(self._position_path, self._pet_scale)
        self._pet_scale = record.scale
        self._captions_enabled = record.captions
        self._cursor_tracking = record.cursor_tracking
        screens = self._all_screens()
        if not screens:
            return
        screen = choose_saved_screen(record, screens)
        width = int(LOGICAL_WIDTH * self._pet_scale)
        height = int(LOGICAL_HEIGHT * self._pet_scale)
        if (
            record.screen
            and record.screen == screen.name
            and 0 <= record.normalized_x <= 1
            and 0 <= record.normalized_y <= 1
        ):
            x = screen.left + int((screen.width - width) * record.normalized_x)
            y = screen.top + int((screen.height - height) * record.normalized_y)
        elif record.x or record.y:
            x, y = record.x, record.y
        else:
            x, y = screen.right - width - 24, screen.bottom - height - 24
        x, y = clamp_position(x, y, width, height, screen)
        self.move(x, y)
        self._anchor = detect_anchor(x, y, width, height, screen)

    def _save_position(self) -> None:
        screen = self._current_screen()
        if screen is None:
            return
        width = max(1, self.width())
        height = max(1, self.height())
        x, y = clamp_position(self.x(), self.y(), width, height, screen)
        record = PositionRecord(
            x=x,
            y=y,
            scale=self._pet_scale,
            screen=screen.name,
            normalized_x=(x - screen.left) / max(1, screen.width - width),
            normalized_y=(y - screen.top) / max(1, screen.height - height),
            anchor=detect_anchor(x, y, width, height, screen),
            captions=self._captions_enabled,
            cursor_tracking=self._cursor_tracking,
        )
        try:
            self._position_path.write_text(json.dumps(position_record_json(record), indent=2), encoding="utf-8")
        except OSError:
            logger.warning("Could not save Pet position", exc_info=True)

    def _refresh_layout(self) -> None:
        self._layout = self._layout_engine.calculate(
            self._anchor, self._activity_expanded, self._activity.approval is not None
        )
        self._resize_to_layout()
        self._last_mask_signature = None
        self._apply_click_mask()
        self.update()

    def _resize_to_layout(self) -> None:
        width = max(1, int(round(self._layout.window_width * self._pet_scale)))
        height = max(1, int(round(self._layout.window_height * self._pet_scale)))
        if self.width() == width and self.height() == height:
            return
        old_right = self.x() + self.width()
        old_bottom = self.y() + self.height()
        self.resize(width, height)
        x = self.x()
        y = self.y()
        if "right" in self._anchor:
            x = old_right - width
        if "bottom" in self._anchor:
            y = old_bottom - height
        screen = self._current_screen()
        if screen is not None:
            x, y = clamp_position(x, y, width, height, screen)
        self.move(x, y)

    def _set_scale(self, scale: float) -> None:
        self._pet_scale = max(MIN_SCALE, min(MAX_SCALE, scale))
        self._layout = self._layout_engine.calculate(
            self._anchor, self._activity_expanded, self._activity.approval is not None
        )
        self._resize_to_layout()
        self._save_position()
        self._last_mask_signature = None
        self.update()

    def _toggle_activity(self) -> None:
        self._activity_expanded = not self._activity_expanded
        self._refresh_layout()

    def _tick(self) -> None:
        now = time.monotonic()
        dt = min(0.2, now - self._last_tick)
        self._last_tick = now
        self._animator.maybe_personality(now)
        changed = self._animator.tick(dt)
        self._activity.prune(now)
        self._controls_alpha = (
            min(1.0, self._controls_alpha + dt * 8) if self.underMouse() else max(0.0, self._controls_alpha - dt * 5)
        )
        if self._caption_until and now > self._caption_until:
            self._caption_alpha = max(0.0, self._caption_alpha - dt * 2.5)
        else:
            self._caption_alpha = min(1.0, self._caption_alpha + dt * 5)
        if self._relocate_target is not None and not self._dragged:
            current = self.pos()
            target = self._relocate_target
            step = 1.0 - math.exp(-dt * 8)
            next_pos = QPoint(
                int(current.x() + (target.x() - current.x()) * step),
                int(current.y() + (target.y() - current.y()) * step),
            )
            self.move(next_pos)
            if (next_pos - target).manhattanLength() < 2:
                self.move(target)
                self._relocate_target = None
        if (
            self._cursor_tracking
            and self._animator.interaction != "dragging"
            and self._semantic_state not in {"error", "completed"}
        ):
            local = self.mapFromGlobal(QCursor.pos())
            point_x = local.x() / self._pet_scale
            point_y = local.y() / self._pet_scale
            center = self._layout.body.center()
            dx = point_x - center.x()
            dy = point_y - center.y()
            radius = math.hypot(dx, dy)
            if radius <= 170:
                self._cursor_eye_x = max(-5.0, min(5.0, dx / 18.0))
                self._cursor_eye_y = max(-3.0, min(3.0, dy / 24.0))
            else:
                self._cursor_eye_x *= 0.8
                self._cursor_eye_y *= 0.8
        self._timer.setInterval(self._animator.refresh_interval_ms())
        if changed or self._caption_alpha > 0 or self._controls_alpha > 0 or self._relocate_target is not None:
            self.update()

    def _on_state_changed(self, state: str) -> None:
        if state not in CORE_STATES:
            return
        self._semantic_state = state
        self._animator.set_semantic(state)
        self._show_caption(STATE_TITLES[state])
        self._refresh_layout()

    def _on_caption_changed(self, desc: str) -> None:
        self._caption_desc = desc
        self._show_caption(desc)
        self.update()

    def _show_caption(self, desc: str) -> None:
        self._caption_until = time.monotonic() + CAPTION_TTL_SECONDS if desc else 0.0
        self._caption_alpha = 1.0 if desc else 0.0

    def _on_workspace_surface_changed(self, active: bool) -> None:
        if active:
            if self._pre_workspace_pos is None:
                self._pre_workspace_pos = self.pos()
            screen = self._current_screen()
            if screen:
                target, anchor = snapped_position(
                    screen.left, screen.top, self.width(), self.height(), screen, threshold=10, gap=18
                )
                self._anchor = anchor if anchor != "free" else "top_left"
                self._relocate_target = QPoint(*target)
                self._refresh_layout()
        elif self._pre_workspace_pos is not None:
            self._relocate_target = self._pre_workspace_pos
            self._pre_workspace_pos = None

    def _apply_click_mask(self) -> None:
        layout = self._layout
        regions = PetLayoutEngine.hit_regions(
            layout, self._controls_alpha > 0.05 or self._activity_expanded, self._activity_expanded
        )
        signature = (
            self._pet_scale,
            self._activity_expanded,
            self._activity.approval is not None,
            tuple((r.x(), r.y(), r.width(), r.height()) for r in regions),
        )
        if signature == self._last_mask_signature:
            return
        mask = QRegion()
        for rect in regions:
            mask = mask.united(
                QRegion(
                    int(rect.x() * self._pet_scale),
                    int(rect.y() * self._pet_scale),
                    int(rect.width() * self._pet_scale),
                    int(rect.height() * self._pet_scale),
                )
            )
        self.setMask(mask)
        self._last_mask_signature = signature

    def paintEvent(self, _event) -> None:
        self._apply_click_mask()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.scale(self._pet_scale, self._pet_scale)
        frame = self._animator.frame
        color = QColor(STATE_COLORS.get(self._semantic_state, STATE_COLORS["idle"]))
        body = self._layout.body.translated(0, frame.bob)
        self._draw_sprite(painter, body, frame, color)
        self._draw_controls(painter, color)
        if self._captions_enabled and not self._activity_expanded:
            self._draw_identity(painter, color)
        if self._activity_expanded:
            self._draw_activity(painter, color)
        if DEBUG_LAYOUT:
            self._draw_debug_layout(painter)

    def _draw_debug_layout(self, painter: QPainter) -> None:
        painter.save()
        painter.setPen(QPen(QColor(255, 80, 120, 220), 1.0, Qt.DashLine))
        painter.drawRect(QRectF(0, 0, self.width() / self._pet_scale, self.height() / self._pet_scale))
        painter.setPen(QPen(QColor(70, 220, 255, 220), 1.0))
        painter.drawRect(self._layout.body)
        painter.setPen(QPen(QColor(255, 190, 70, 220), 1.0))
        painter.drawRect(self._layout.title)
        for rect in (
            self._layout.badge,
            self._layout.ptt,
            self._layout.activity,
            self._layout.approve,
            self._layout.reject,
        ):
            if not rect.isNull():
                painter.drawRect(rect)
        painter.restore()

    def _draw_sprite(self, painter: QPainter, body: QRectF, frame: SpriteFrame, color: QColor) -> None:
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
        painter.translate(body.center())
        painter.rotate(frame.lean)
        painter.translate(-body.center())
        if not self._sprite_atlas.isNull():
            frame_width = self._sprite_atlas.width() / CHARLIE_ATLAS_COLUMNS
            frame_height = self._sprite_atlas.height() / CHARLIE_ATLAS_ROWS
            column = self._animator.sprite_index % CHARLIE_ATLAS_COLUMNS
            row = self._animator.sprite_index // CHARLIE_ATLAS_COLUMNS
            source = QRectF(column * frame_width, row * frame_height, frame_width, frame_height)
            pulse = self._audio_level * 2.5 if self._semantic_state in {"listening", "speaking"} else 0.0
            target = body.adjusted(-pulse, -pulse, pulse, pulse)
            painter.drawImage(target, self._sprite_atlas, source)
        painter.restore()

    def _draw_identity(self, painter: QPainter, color: QColor) -> None:
        rect = self._layout.title
        alpha = int(225 * max(self._caption_alpha, 0.32))
        painter.setBrush(QColor(8, 15, 25, 236))
        painter.setPen(QPen(QColor(64, 103, 121, 190), 1.0))
        painter.drawRoundedRect(rect, 9, 9)
        painter.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 105), 1.0))
        painter.drawLine(rect.left() + 10, rect.bottom() - 2, rect.right() - 10, rect.bottom() - 2)
        painter.setFont(QFont("Segoe UI", 9, QFont.Bold))
        painter.setPen(QColor(234, 244, 255, alpha))
        painter.drawText(rect.adjusted(12, 4, -12, -27), Qt.AlignLeft | Qt.AlignVCenter, "CHARLIE")
        if self._caption_desc and self._caption_alpha > 0:
            painter.setFont(QFont("Segoe UI", 8))
            painter.setPen(QColor(164, 187, 209, alpha))
            painter.drawText(
                rect.adjusted(12, 25, -12, -5),
                Qt.AlignLeft | Qt.AlignVCenter,
                _elide_text(
                    self._caption_desc, QFontMetrics(QFont("Segoe UI", 8)).horizontalAdvance, int(rect.width() - 24)
                ),
            )

    def _draw_controls(self, painter: QPainter, color: QColor) -> None:
        alpha = int(220 * max(self._controls_alpha, 0.35 if self._activity_expanded else 0.0))
        for rect, label in (
            (self._layout.badge, "•" if self._activity.active_count == 0 else str(self._activity.active_count)),
            (self._layout.ptt, "MIC"),
        ):
            painter.setBrush(QColor(8, 15, 25, alpha))
            painter.setPen(QPen(QColor(76, 118, 135, alpha), 1.0))
            painter.drawEllipse(rect)
            painter.setFont(QFont("Segoe UI", 7 if label == "MIC" else 12, QFont.Bold))
            painter.setPen(QColor(228, 241, 255, alpha))
            painter.drawText(rect, Qt.AlignCenter, label)

    def _draw_activity(self, painter: QPainter, color: QColor) -> None:
        rect = self._layout.activity
        painter.setBrush(QColor(8, 15, 25, 245))
        painter.setPen(QPen(QColor(64, 103, 121, 205), 1.0))
        painter.drawRoundedRect(rect, 10, 10)
        painter.setFont(QFont("Segoe UI", 9, QFont.Bold))
        painter.setPen(QColor(236, 246, 255, 235))
        painter.drawText(rect.adjusted(12, 7, -12, -rect.height() + 34), Qt.AlignLeft | Qt.AlignVCenter, "CHARLIE")
        painter.setFont(QFont("Segoe UI", 8))
        painter.setPen(QColor(164, 187, 209, 225))
        painter.drawText(
            rect.adjusted(12, 26, -12, -rect.height() + 54),
            Qt.AlignLeft | Qt.AlignVCenter,
            _elide_text(
                self._caption_desc or STATE_TITLES.get(self._semantic_state, "Ready"), lambda s: len(s) * 5, 176
            ),
        )
        content = rect.adjusted(12, 58, -12, -10)
        painter.setFont(QFont("Segoe UI", 8, QFont.Bold))
        painter.setPen(QColor(236, 246, 255, 230))
        if self._activity.approval:
            painter.drawText(
                content.adjusted(0, 0, 0, -42),
                Qt.TextWordWrap,
                self._activity.approval.title
                + "\n"
                + _elide_text(self._activity.approval.reason, lambda s: len(s) * 5, int(content.width())),
            )
            for button, label in ((self._layout.approve, "Approve"), (self._layout.reject, "Decline")):
                painter.setBrush(QColor(37, 62, 78, 230))
                painter.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 170), 1))
                painter.drawRoundedRect(button, 8, 8)
                painter.setPen(QColor(240, 248, 255, 235))
                painter.drawText(button, Qt.AlignCenter, label)
            return
        rows = self._activity.visible_tasks()
        if not rows:
            painter.drawText(content, Qt.AlignLeft | Qt.AlignVCenter, "No active work")
            return
        for index, task in enumerate(rows):
            y = content.top() + index * 22
            painter.setPen(QColor(200, 220, 235, 220))
            title = str(task.get("title") or task.get("text") or "Task")
            painter.drawText(
                QRectF(rect.left() + 12, y, rect.width() - 24, 17),
                Qt.AlignLeft | Qt.AlignVCenter,
                _elide_text(title, lambda s: len(s) * 5, int(rect.width() - 24)),
            )
            painter.setPen(QColor(130, 160, 180, 190))
            painter.drawText(
                QRectF(rect.left() + 12, y + 14, rect.width() - 24, 14),
                Qt.AlignLeft,
                str(task.get("status", "running")),
            )

    def _contains(self, rect: QRectF, pos) -> bool:
        return rect.contains(pos / self._pet_scale)

    def enterEvent(self, event) -> None:
        self._controls_alpha = 1.0
        if self._animator.interaction == "normal":
            self._animator.set_interaction("hover")
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        if not self._dragged and not self._ptt_pressed:
            self._animator.set_interaction("normal")
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.RightButton:
            self._show_context_menu(event.globalPosition().toPoint())
            return
        if event.button() != Qt.LeftButton:
            return
        pos = event.position()
        if self._activity.approval and self._activity_expanded and self._contains(self._layout.approve, pos):
            self._commands.send("tool_approve", {"request_id": self._activity.approval.request_id})
            return
        if self._activity.approval and self._activity_expanded and self._contains(self._layout.reject, pos):
            self._commands.send("tool_reject", {"request_id": self._activity.approval.request_id})
            return
        if self._contains(self._layout.ptt, pos):
            self._ptt_pressed = True
            self._animator.set_ptt(True)
            self._commands.send("ptt_start")
            self._show_caption("Listening...")
            self.update()
            return
        if self._contains(self._layout.badge, pos):
            self._toggle_activity()
            return
        if self._contains(self._layout.body, pos):
            self._press_global = event.globalPosition().toPoint()
            self._last_drag_global = self._press_global
            self._drag_offset = self._press_global - self.pos()
            self._dragged = False
            self._animator.set_interaction("pressed")

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is None or self._press_global is None:
            return
        current = event.globalPosition().toPoint()
        delta = current - self._press_global
        if not self._dragged and delta.manhattanLength() >= DRAG_THRESHOLD:
            self._dragged = True
            self._animator.set_interaction("dragging")
        if self._dragged:
            step = current - (self._last_drag_global or current)
            self._animator.drag_clip(step.x(), step.y())
            self.move(current - self._drag_offset)
            screen = self._current_screen()
            if screen:
                next_anchor = detect_anchor(self.x(), self.y(), self.width(), self.height(), screen)
                if next_anchor != self._anchor:
                    self._anchor = next_anchor
                    self._refresh_layout()
        self._last_drag_global = current

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.LeftButton:
            return
        if self._ptt_pressed:
            self._ptt_pressed = False
            self._animator.set_ptt(False)
            self._commands.send("ptt_stop")
            self.update()
            return
        if self._drag_offset is None:
            return
        if self._dragged:
            screen = self._current_screen()
            if screen:
                if self._edge_snap:
                    target, self._anchor = snapped_position(self.x(), self.y(), self.width(), self.height(), screen)
                else:
                    target = clamp_position(self.x(), self.y(), self.width(), self.height(), screen)
                    self._anchor = detect_anchor(*target, self.width(), self.height(), screen)
                self._relocate_target = QPoint(*target)
            self._animator.set_interaction("landing")
            self._save_position()
        else:
            self._toggle_activity()
        self._drag_offset = None
        self._press_global = None
        self._last_drag_global = None
        self._dragged = False

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton and self._contains(self._layout.body, event.position()):
            self._commands.send("hud_invoke")

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.ControlModifier:
            self._set_scale(self._pet_scale + event.angleDelta().y() / 1200.0)
            event.accept()
            return
        event.ignore()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape and self._ptt_pressed:
            self._ptt_pressed = False
            self._animator.set_ptt(False)
            self._commands.send("ptt_cancel")
            self.update()
            return
        super().keyPressEvent(event)

    def _show_context_menu(self, global_pos: QPoint) -> None:
        menu = QMenu(self)
        captions = QAction("Captions ✓" if self._captions_enabled else "Captions", self)
        tracking = QAction("Cursor tracking ✓" if self._cursor_tracking else "Cursor tracking", self)
        reset = QAction("Reset position", self)
        sizes = menu.addMenu("Size")
        small = sizes.addAction("Small")
        medium = sizes.addAction("Medium")
        large = sizes.addAction("Large")
        menu.addAction(captions)
        menu.addAction(tracking)
        menu.addSeparator()
        menu.addAction(reset)
        close = menu.addAction("Close Pet")
        chosen = menu.exec(global_pos)
        if chosen is captions:
            self._captions_enabled = not self._captions_enabled
            self._save_position()
            self.update()
        elif chosen is tracking:
            self._cursor_tracking = not self._cursor_tracking
            self._save_position()
        elif chosen is reset:
            screen = self._current_screen()
            if screen:
                self.move(screen.right - self.width() - 24, screen.bottom - self.height() - 24)
                self._anchor = "bottom_right"
                self._save_position()
                self._refresh_layout()
        elif chosen is small:
            self._set_scale(0.75)
        elif chosen is medium:
            self._set_scale(1.0)
        elif chosen is large:
            self._set_scale(1.35)
        elif chosen is close:
            self.close()

    def ingest_event(self, event: dict) -> None:
        self._activity.apply(event)
        etype = event.get("type", "")
        payload = event.get("payload") or {}
        state = _map_event_to_state(event)
        if state:
            self.state_changed.emit(state)
        desc = _map_event_to_caption_desc(event)
        if desc is not None:
            self.caption_changed.emit(desc)
        if etype == "token":
            self._speech_buffer += str(payload.get("text", ""))
            sentence = _extract_last_sentence(self._speech_buffer)
            if sentence:
                self.caption_changed.emit(sentence)
        elif etype in {"speaking_stop", "response_done"}:
            self._speech_buffer = ""
        elif etype == "audio_level":
            raw = max(0.0, min(1.0, float(payload.get("level", 0.0))))
            self._audio_level = self._audio_level * 0.75 + raw * 0.25
        elif etype in {"ptt_start", "ptt_listening"}:
            self._animator.set_ptt(True)
        elif etype in {"ptt_stop", "ptt_cancel"}:
            self._animator.set_ptt(False)
        elif etype in {"presentation_intent", "presentation_update", "presentation_dismiss"}:
            workspace_active = _track_workspace_presentation(self._active_workspaces, event)
            if workspace_active is not None:
                self.workspace_surface_changed.emit(workspace_active)
        self._refresh_layout()

    def set_connection(self, connected: bool) -> None:
        self._connected = connected
        if not connected:
            self._show_caption("Core unavailable")
        elif self._caption_desc == "Core unavailable":
            self._caption_desc = ""
            self._caption_until = 0.0
            self._caption_alpha = 0.0
        self.update()

    def closeEvent(self, event) -> None:
        self._save_position()
        self._commands.close()
        event.accept()


def _map_event_to_state(event: dict) -> Optional[str]:
    if event.get("type") != "charlie_state":
        return None
    state = (event.get("payload") or {}).get("state")
    return state if state in CORE_STATES else None


def _state_caption_title(state: str) -> str:
    return STATE_TITLES.get(state, state.capitalize() if state else "")


def _map_event_to_caption_desc(event: dict) -> Optional[str]:
    etype = event.get("type", "")
    payload = event.get("payload") or {}
    if etype in ("vad_start", "wake_word", "ptt_start"):
        return "I'm paying attention"
    if etype == "thinking":
        return "Processing request..."
    if etype in ("speaking_stop", "response_done"):
        return ""
    if etype in ("tool_approval_request", "extension_pending", "recovery_proposal"):
        return str(payload.get("reason") or "Waiting on a decision").strip()
    if etype == "alert" and payload.get("severity") in ("warning", "error"):
        return str(payload.get("message") or "Something needs a look").strip()
    return None


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _extract_last_sentence(buffer: str) -> str:
    buffer = buffer.strip()
    return _SENTENCE_SPLIT_RE.split(buffer)[-1].strip() if buffer else ""


def _elide_text(text: str, width_fn: Callable[[str], int], max_width: int) -> str:
    if not text or width_fn(text) <= max_width:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        candidate = text[:mid].rstrip() + "..."
        if width_fn(candidate) <= max_width:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + "..." if lo else "..."


def _track_workspace_presentation(active_ids: set, event: dict) -> Optional[bool]:
    etype = event.get("type")
    payload = event.get("payload") or {}
    presentation_id = payload.get("id")
    if (
        etype in {"presentation_intent", "presentation_update"}
        and payload.get("kind") == "workspace"
        and presentation_id
    ):
        was_empty = not active_ids
        active_ids.add(presentation_id)
        return True if was_empty else None
    if etype == "presentation_dismiss" and presentation_id in active_ids:
        active_ids.discard(presentation_id)
        return False if not active_ids else None
    return None


def _sub_loop(window: PetWindow, stop_event: threading.Event) -> None:
    """Compatibility wrapper retained for callers/tests from Pet V1."""
    bridge = PetEventBridge(window.event_received.emit, window.connection_changed.emit)
    bridge.start()
    while not stop_event.wait(0.25):
        pass
    bridge.stop()


def main() -> None:
    if not QT_AVAILABLE:
        reason = str(QT_IMPORT_ERROR) if QT_IMPORT_ERROR else "Qt binding unavailable"
        logger.warning("Pet companion unavailable: install the optional HUD dependency (%s)", reason)
        return

    app = QApplication([])
    window = PetWindow()
    window.show()
    hotkey_listener = None
    try:
        # HUD invocation belongs to the always-available pet companion now that
        # the React HUD has no separate Qt shell process.
        from charlie.hud.invocation import start_hotkey_listener

        hotkey_listener = start_hotkey_listener(config.hud_invoke_hotkey)
    except Exception:
        logger.warning("HUD invocation hotkey unavailable", exc_info=True)
    stop_event = threading.Event()
    bridge = PetEventBridge(window.event_received.emit, window.connection_changed.emit)
    bridge.start()
    ready_file = os.getenv("CHARLIE_COMPANION_READY_FILE")
    if ready_file:
        try:
            Path(ready_file).write_text("ready\n", encoding="utf-8")
        except OSError:
            logger.warning("Unable to publish companion readiness", exc_info=True)
    try:
        app.exec()
    finally:
        stop_event.set()
        bridge.stop()
        if hotkey_listener is not None:
            hotkey_listener.stop()

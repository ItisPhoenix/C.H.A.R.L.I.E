"""Floating desktop pet: frameless always-on-top EMO-style orb reflecting live voice state."""

import json
import logging
import math
import re
import threading
from pathlib import Path
from typing import Callable, List, Optional

import zmq
from PySide6.QtCore import QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QFontMetrics,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
    QRegion,
)
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from charlie.config import config
from charlie.ipc import DEFAULT_COMMAND_PORT, DEFAULT_EVENT_PORT
from charlie.utils import json_loads

logger = logging.getLogger("charlie.pet")

_WIDTH = 400
_HEIGHT = 240
_DRAG_THRESHOLD = 6
_POSITION_PATH = Path(getattr(config, "pet_position_path", "pet_position.json"))
_PULSE_INTERVAL_MS = 30
_MIN_SCALE = 0.5
_MAX_SCALE = 2.0
_WHEEL_SCALE_STEP = 1200.0
_CAPTION_PILL_HEIGHT = 46
_CAPTION_MAX_WIDTH = 320
_CAPTION_MIN_WIDTH = 90
_CAPTION_PADDING_X = 14
_CAPTION_MARGIN = 10

_STATE_TITLES = {
    "idle": "Idle",
    "listening": "Listening",
    "thinking": "Thinking",
    "speaking": "Speaking",
    "working": "Working",
    "waiting": "Waiting",
    "attention": "Needs attention",
    "completed": "Done",
    "error": "Error",
}

_STATE_COLORS = {
    "idle": "#4b5563",
    "listening": "#06b6d4",
    "thinking": "#a855f7",
    "speaking": "#10b981",
    "working": "#f59e0b",
    "waiting": "#64748b",
    "attention": "#ef4444",
    "completed": "#22c55e",
    "error": "#dc2626",
}
# Eyes stay cyan to match EMO aesthetic, but we can modulate alpha
_EYE_COLOR = "#00ffff"
_EYE_GLOW_ALPHA = {
    "idle": 150,
    "listening": 255,
    "thinking": 200,
    "speaking": 220,
    "working": 200,
    "waiting": 120,
    "attention": 255,
    "completed": 220,
    "error": 255,
}

# Only 4 states have hand-drawn eye shapes; the other 5 borrow the closest one, own color/pulse instead.
_EYE_SHAPE_FOR_STATE = {
    "idle": "idle",
    "listening": "listening",
    "thinking": "thinking",
    "speaking": "speaking",
    "working": "thinking",
    "waiting": "idle",
    "attention": "listening",
    "completed": "speaking",
    "error": "idle",
}

_BOUNCE_AMPLITUDE_PX = {
    "idle": 2.0,
    "listening": 4.0,
    "thinking": 1.5,
    "speaking": 3.0,
    "working": 1.5,
    "waiting": 1.0,
    "attention": 5.0,
    "completed": 3.0,
    "error": 1.0,
}
_PULSE_SPEED = {
    "idle": 0.04,
    "listening": 0.1,
    "thinking": 0.15,
    "speaking": 0.2,
    "working": 0.15,
    "waiting": 0.03,
    "attention": 0.3,
    "completed": 0.2,
    "error": 0.05,
}
_BLINK_PERIOD_TICKS = 120
_BLINK_DURATION_TICKS = 5

def _send_summon_command() -> None:
    """Click-to-summon: same hud_invoke command hud/invocation.py's hotkey sends, context-sensitive server-side."""
    ctx = zmq.Context()
    sock = ctx.socket(zmq.PUSH)
    sock.connect(f"tcp://127.0.0.1:{DEFAULT_COMMAND_PORT}")
    try:
        sock.send_string(json.dumps({"type": "hud_invoke", "payload": {}}))
    except Exception:
        logger.warning("Failed to send summon command", exc_info=True)
    finally:
        sock.close(linger=0)
        ctx.term()


def _track_workspace_surface(active_ids: set, event: dict) -> Optional[bool]:
    """Update active_ids in place for a surface_spawn/dismiss event; return an edge-triggered emit value."""
    etype = event.get("type")
    payload = event.get("payload") or {}
    sid = payload.get("surface_id")
    if etype == "surface_spawn" and payload.get("presentation") == "workspace" and sid:
        was_empty = not active_ids
        active_ids.add(sid)
        return True if was_empty else None
    if etype == "surface_dismiss" and sid in active_ids:
        active_ids.discard(sid)
        return False if not active_ids else None
    return None


class PetWindow(QWidget):
    state_changed = Signal(str)
    caption_changed = Signal(str) # content line only -- title is derived from state
    workspace_surface_changed = Signal(bool)

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)

        self._pet_scale = getattr(config, "pet_scale", 1.0)
        self.resize(int(_WIDTH * self._pet_scale), int(_HEIGHT * self._pet_scale))
        self.setFocusPolicy(Qt.StrongFocus)
        self._state = "idle"
        self._resizing = False

        self._captions_enabled = True
        self._caption_title = ""
        self._caption_desc = ""

        # Fade variables
        self._caption_alpha = 0.0
        self._caption_visible = False
        self._caption_fade_dir = 0 # 1=in, -1=out
        self._caption_time_left = 0

        self._phase = 0.0
        self._tick_count = 0
        self._drag_origin: Optional[QPoint] = None
        self._press_pos: Optional[QPoint] = None
        self._dragged = False

        self._card_x = 0
        self._card_y = 0
        self._card_w = 0
        self._card_h = 0
        self._chevron_rect = QRectF()

        self._pre_workspace_pos: Optional[QPoint] = None

        self.state_changed.connect(self._on_state_changed)
        self.caption_changed.connect(self._on_caption_changed)
        self.workspace_surface_changed.connect(self._on_workspace_surface_changed)
        self._restore_position()

        self._pulse_timer = QTimer(self)
        self._pulse_timer.timeout.connect(self._tick)
        self._pulse_timer.start(_PULSE_INTERVAL_MS)

    def _set_resizing(self, value: bool) -> None:
        """Enter/exit wheel-resize mode; persist the final scale on exit."""
        self._resizing = value
        if value:
            self.setFocus()
        else:
            self._save_position()
        self.update()

    def _reset_position(self) -> None:
        screen = QApplication.primaryScreen().availableGeometry()
        default_x = screen.right() - int(_WIDTH * self._pet_scale) - 24
        default_y = screen.bottom() - int(_HEIGHT * self._pet_scale) - 24
        self.move(default_x, default_y)
        self._save_position()

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        resize_action = QAction("Stop Resizing" if self._resizing else "Resize", self)
        captions_action = QAction("Captions: On" if self._captions_enabled else "Captions: Off", self)
        reset_action = QAction("Reset Position", self)
        quit_action = QAction("Quit", self)
        for action in (resize_action, captions_action, reset_action):
            menu.addAction(action)
        menu.addSeparator()
        menu.addAction(quit_action)

        chosen = menu.exec(event.globalPos())
        if chosen is resize_action:
            self._set_resizing(not self._resizing)
        elif chosen is captions_action:
            self._captions_enabled = not self._captions_enabled
            self.update()
        elif chosen is reset_action:
            self._reset_position()
        elif chosen is quit_action:
            QApplication.quit()

    def wheelEvent(self, event):
        if not self._resizing:
            event.ignore()
            return
        delta = event.angleDelta().y() / _WHEEL_SCALE_STEP
        self._pet_scale = min(_MAX_SCALE, max(_MIN_SCALE, self._pet_scale + delta))
        self.resize(int(_WIDTH * self._pet_scale), int(_HEIGHT * self._pet_scale))
        self._save_position() # persist every tick so whatever size the wheel lands on is the new default
        self.update()
        event.accept()

    def keyPressEvent(self, event):
        if self._resizing and event.key() == Qt.Key_Escape:
            self._set_resizing(False)
            return
        super().keyPressEvent(event)

    def _on_state_changed(self, state: str):
        self._state = state
        self._caption_title = _STATE_TITLES.get(state, state.capitalize() if state else "")
        self._caption_visible = True
        self._caption_fade_dir = 1
        self._caption_time_left = 5000 // _PULSE_INTERVAL_MS # 5 seconds
        self.update()

    def _on_caption_changed(self, desc: str):
        if not desc:
            # Clear / fade out
            self._caption_time_left = 0
        else:
            self._caption_desc = desc
            self._caption_visible = True
            self._caption_fade_dir = 1
            self._caption_time_left = 5000 // _PULSE_INTERVAL_MS # 5 seconds
        self.update()

    def _on_workspace_surface_changed(self, active: bool):
        screen = QApplication.primaryScreen().availableGeometry()
        if active:
            self._pre_workspace_pos = self.pos()
            self.move(screen.left() + 24, screen.top() + 24)
        elif self._pre_workspace_pos is not None:
            self.move(self._pre_workspace_pos)
            self._pre_workspace_pos = None

    def _tick(self):
        self._phase += _PULSE_SPEED.get(self._state, 0.04)
        self._tick_count += 1

        # Caption fade logic
        if self._caption_time_left > 0:
            self._caption_time_left -= 1
            if self._caption_time_left == 0:
                self._caption_fade_dir = -1 # Start fading out

        if self._caption_fade_dir == 1:
            self._caption_alpha = min(255.0, self._caption_alpha + 15)
        elif self._caption_fade_dir == -1:
            self._caption_alpha = max(0.0, self._caption_alpha - 10)
            if self._caption_alpha == 0:
                self._caption_visible = False
                self._caption_fade_dir = 0

        self.update()

    def _is_blinking(self) -> bool:
        return (self._tick_count % _BLINK_PERIOD_TICKS) < _BLINK_DURATION_TICKS

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.scale(self._pet_scale, self._pet_scale)

        # Coordinates for EMO head (anchored right)
        pet_w = 120
        pet_h = 100
        cx = _WIDTH - pet_w - 20

        bounce = int(_BOUNCE_AMPLITUDE_PX.get(self._state, 2.0) * math.sin(self._phase))
        cy = 120 + bounce

        self._draw_pet(painter, cx, cy, pet_w, pet_h)
        self._draw_chevron(painter, cx, cy, pet_w, pet_h)

        if self._resizing:
            self._draw_resize_ring(painter, cx, cy, pet_w, pet_h)

        if self._captions_enabled and self._caption_alpha > 0:
            self._draw_caption_bubble(painter, cx, cy, pet_w)

        self._apply_click_mask()

    def _draw_resize_ring(self, painter: QPainter, cx: float, cy: float, w: float, h: float):
        ring = QRectF(cx - 34, cy - 34, w + 68, h + 68)
        painter.setPen(QPen(QColor("#38bdf8"), 3, Qt.DashLine))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(ring, 20, 20)

    def _mask_regions(self) -> List[QRectF]:
        """Un-scaled click-through rects (pet body, caption pill, chevron); scaled once by the caller."""
        pet_w, pet_h = 120, 100
        cx = _WIDTH - pet_w - 20
        bounce = int(_BOUNCE_AMPLITUDE_PX.get(self._state, 2.0) * math.sin(self._phase))
        cy = 120 + bounce
        pad = 34 if self._resizing else 30
        regions = [QRectF(cx - pad, cy - pad, pet_w + 2 * pad, pet_h + 2 * pad)]
        if self._captions_enabled and self._caption_alpha > 0 and self._card_w > 0:
            regions.append(QRectF(
                self._card_x - 10, self._card_y - 10, self._card_w + 20, self._card_h + 20,
            ))
        chevron = self._chevron_rect
        regions.append(QRectF(
            chevron.x() - 5, chevron.y() - 5, chevron.width() + 10, chevron.height() + 10,
        ))
        return regions

    def _apply_click_mask(self) -> None:
        s = self._pet_scale
        mask = QRegion()
        for r in self._mask_regions():
            mask = mask | QRegion(int(r.x() * s), int(r.y() * s), int(r.width() * s), int(r.height() * s))
        self.setMask(mask)

    def _draw_chevron(self, painter: QPainter, pet_cx: float, pet_cy: float, pet_w: float, pet_h: float):
        # Right of the pet, below the headphone -- clear of the caption band above and within canvas bounds
        ch_x = pet_cx + pet_w + 5
        ch_y = pet_cy + pet_h - 5
        self._chevron_rect = QRectF(ch_x - 12, ch_y - 12, 24, 24)

        # Background circle
        alpha = 180 if self._captions_enabled else 80
        state_hex = _STATE_COLORS.get(self._state, _STATE_COLORS["idle"])
        base_c = QColor(state_hex)
        painter.setBrush(QColor(30, 30, 30, alpha))
        painter.setPen(QPen(QColor(base_c.red(), base_c.green(), base_c.blue(), alpha), 1.5))
        painter.drawEllipse(self._chevron_rect)

        # Chevron icon
        painter.setPen(QPen(QColor(255, 255, 255, 200), 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        path = QPainterPath()
        if self._captions_enabled:
            # Pointing right (to hide)
            path.moveTo(ch_x - 3, ch_y - 4)
            path.lineTo(ch_x + 3, ch_y)
            path.lineTo(ch_x - 3, ch_y + 4)
        else:
            # Pointing left (to show)
            path.moveTo(ch_x + 3, ch_y - 4)
            path.lineTo(ch_x - 3, ch_y)
            path.lineTo(ch_x + 3, ch_y + 4)
        painter.drawPath(path)

    def _draw_pet(self, painter: QPainter, cx: float, cy: float, w: float, h: float):
        state_hex = _STATE_COLORS.get(self._state, _STATE_COLORS["idle"])
        pet_c = QColor(state_hex)

        # 1. Base Drop Shadow / Glow
        painter.setPen(Qt.NoPen)
        glow_alpha = _EYE_GLOW_ALPHA.get(self._state, 150) // 4
        glow_grad = QRadialGradient(cx + w/2, cy + h/2, w*0.8)
        glow_grad.setColorAt(0, QColor(pet_c.red(), pet_c.green(), pet_c.blue(), glow_alpha))
        glow_grad.setColorAt(1, QColor(0, 0, 0, 0))
        painter.setBrush(glow_grad)
        painter.drawRoundedRect(cx - 20, cy - 20, w + 40, h + 40, 40, 40)

        # 2. Headphones (Pill shapes on sides)
        hp_w = 20
        hp_h = 60
        hp_y = cy + (h - hp_h) / 2

        # Left HP
        hp_left_rect = QRectF(cx - hp_w + 5, hp_y, hp_w, hp_h)
        painter.setBrush(QColor("#1a1a1a"))
        painter.drawRoundedRect(hp_left_rect, hp_w/2, hp_w/2)
        # Left HP Neon Ring
        painter.setPen(QPen(QColor(pet_c.red(), pet_c.green(), pet_c.blue(), 100), 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(hp_left_rect.adjusted(4, 8, -4, -8), (hp_w-8)/2, (hp_w-8)/2)

        # Right HP
        hp_right_rect = QRectF(cx + w - 5, hp_y, hp_w, hp_h)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#1a1a1a"))
        painter.drawRoundedRect(hp_right_rect, hp_w/2, hp_w/2)
        # Right HP Neon Ring
        painter.setPen(QPen(QColor(pet_c.red(), pet_c.green(), pet_c.blue(), 100), 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(hp_right_rect.adjusted(4, 8, -4, -8), (hp_w-8)/2, (hp_w-8)/2)

        # 3. Face Screen (Main Body)
        painter.setPen(QPen(QColor("#333333"), 2)) # subtle bevel border
        screen_grad = QLinearGradient(cx, cy, cx, cy + h)
        screen_grad.setColorAt(0, QColor("#151515"))
        screen_grad.setColorAt(1, QColor("#050505"))
        painter.setBrush(screen_grad)

        face_path = QPainterPath()
        face_path.addRoundedRect(cx, cy, w, h, 24, 24)
        painter.drawPath(face_path)

        # Apply clipping so face contents don't overflow
        painter.save()
        painter.setClipPath(face_path)

        # 4. Eyes
        eye_w = 26
        eye_h = 32
        eye_y = cy + 30
        eye_space = 20

        left_eye_x = cx + w/2 - eye_space/2 - eye_w
        right_eye_x = cx + w/2 + eye_space/2

        eye_color = QColor(pet_c.red(), pet_c.green(), pet_c.blue(), _EYE_GLOW_ALPHA.get(self._state, 200))
        painter.setPen(Qt.NoPen)
        painter.setBrush(eye_color)

        eye_shape = _EYE_SHAPE_FOR_STATE.get(self._state, "idle")
        if self._is_blinking():
            eye_y += eye_h / 2 - 2
            eye_h = 4
            painter.drawRoundedRect(left_eye_x, eye_y, eye_w, eye_h, 2, 2)
            painter.drawRoundedRect(right_eye_x, eye_y, eye_w, eye_h, 2, 2)
        elif eye_shape == "thinking":
            # Squint and look around (scanning)
            eye_w = 32
            eye_h = 24
            eye_y += 4
            shift_x = 12 * math.sin(self._phase * 1.5)
            shift_y = 6 * math.cos(self._phase * 2.0)
            left_eye_x += shift_x
            right_eye_x += shift_x
            eye_y += shift_y
            painter.drawRoundedRect(left_eye_x, eye_y, eye_w, eye_h, 8, 8)
            painter.drawRoundedRect(right_eye_x, eye_y, eye_w, eye_h, 8, 8)
        elif eye_shape == "listening":
            # Widen and angle
            eye_w = 30
            eye_h = 40
            eye_y -= 4
            painter.save()
            painter.translate(left_eye_x + eye_w/2, eye_y + eye_h/2)
            painter.rotate(-15)
            painter.drawRoundedRect(-eye_w/2, -eye_h/2, eye_w, eye_h, 10, 10)
            painter.restore()

            painter.save()
            painter.translate(right_eye_x + eye_w/2, eye_y + eye_h/2)
            painter.rotate(15)
            painter.drawRoundedRect(-eye_w/2, -eye_h/2, eye_w, eye_h, 10, 10)
            painter.restore()
        elif eye_shape == "speaking":
            # Happy bounce / arches
            mod = 4 * math.sin(self._phase * 3)
            eye_h = 28 - mod
            eye_y += mod

            path_l = QPainterPath()
            path_l.moveTo(left_eye_x, eye_y + eye_h)
            path_l.quadTo(left_eye_x + eye_w/2, eye_y - eye_h/2, left_eye_x + eye_w, eye_y + eye_h)

            path_r = QPainterPath()
            path_r.moveTo(right_eye_x, eye_y + eye_h)
            path_r.quadTo(right_eye_x + eye_w/2, eye_y - eye_h/2, right_eye_x + eye_w, eye_y + eye_h)

            painter.setPen(QPen(eye_color, 8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path_l)
            painter.drawPath(path_r)
        else:
            # Idle
            painter.drawRoundedRect(left_eye_x, eye_y, eye_w, eye_h, 8, 8)
            painter.drawRoundedRect(right_eye_x, eye_y, eye_w, eye_h, 8, 8)

        painter.restore()

    def _draw_caption_bubble(self, painter: QPainter, pet_cx: float, pet_cy: float, pet_w: float):
        title = self._caption_title
        desc = self._caption_desc
        if not title and not desc:
            self._card_w = 0
            self._card_h = 0
            return

        alpha = int(self._caption_alpha)
        title_font = QFont("Segoe UI", 9, QFont.Bold)
        desc_font = QFont("Segoe UI", 9)
        title_metrics = QFontMetrics(title_font)
        desc_metrics = QFontMetrics(desc_font)
        elided_title = _elide_text(title, title_metrics.horizontalAdvance, _CAPTION_MAX_WIDTH)
        elided_desc = _elide_text(desc, desc_metrics.horizontalAdvance, _CAPTION_MAX_WIDTH) if desc else ""
        text_w = max(
            title_metrics.horizontalAdvance(elided_title),
            desc_metrics.horizontalAdvance(elided_desc) if elided_desc else 0,
        )

        card_h = _CAPTION_PILL_HEIGHT
        card_w = max(text_w + 2 * _CAPTION_PADDING_X, _CAPTION_MIN_WIDTH)
        # Right edge anchored near the pet, extends left -- centering on the pet overflowed past _WIDTH.
        right_edge = min(pet_cx + pet_w, _WIDTH - _CAPTION_MARGIN)
        card_x = max(right_edge - card_w, _CAPTION_MARGIN)
        card_y = pet_cy - card_h - 14

        self._card_x = card_x
        self._card_y = card_y
        self._card_w = card_w
        self._card_h = card_h

        state_hex = _STATE_COLORS.get(self._state, _STATE_COLORS["idle"])
        base_c = QColor(state_hex)
        painter.setBrush(QColor(30, 30, 30, int(alpha * 0.9)))
        painter.setPen(QPen(QColor(base_c.red(), base_c.green(), base_c.blue(), int(alpha * 0.6)), 1.5))
        painter.drawRoundedRect(card_x, card_y, card_w, card_h, 14, 14)

        painter.setFont(title_font)
        painter.setPen(QColor(255, 255, 255, alpha))
        painter.drawText(
            card_x + _CAPTION_PADDING_X, card_y + 4, card_w - 2 * _CAPTION_PADDING_X, card_h / 2,
            Qt.AlignLeft | Qt.AlignVCenter, elided_title,
        )
        if elided_desc:
            painter.setFont(desc_font)
            painter.setPen(QColor(200, 200, 200, alpha))
            painter.drawText(
                card_x + _CAPTION_PADDING_X, card_y + card_h / 2, card_w - 2 * _CAPTION_PADDING_X, card_h / 2,
                Qt.AlignLeft | Qt.AlignVCenter, elided_desc,
            )

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            if self._resizing:
                self._set_resizing(False)
                return
            self._drag_origin = event.globalPosition().toPoint() - self.pos()
            self._press_pos = event.globalPosition().toPoint()
            self._dragged = False

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._drag_origin is None:
            return
        current = event.globalPosition().toPoint()
        if (current - self._press_pos).manhattanLength() > _DRAG_THRESHOLD:
            self._dragged = True
        self.move(current - self._drag_origin)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            # Check for chevron click
            click_pos = event.pos() / self._pet_scale
            if self._chevron_rect.contains(click_pos):
                # Toggle captions
                self._captions_enabled = not self._captions_enabled
                self._drag_origin = None
                self._press_pos = None
                self.update()
                return

            if not self._dragged:
                _send_summon_command()
            else:
                self._save_position()
            self._drag_origin = None
            self._press_pos = None

    def _restore_position(self):
        screen = QApplication.primaryScreen().availableGeometry()
        try:
            if _POSITION_PATH.exists():
                data = json_loads(_POSITION_PATH.read_text(encoding="utf-8"))
                scale = data.get("scale")
                if isinstance(scale, (int, float)) and _MIN_SCALE <= scale <= _MAX_SCALE:
                    self._pet_scale = float(scale)
                    self.resize(int(_WIDTH * self._pet_scale), int(_HEIGHT * self._pet_scale))
                x = min(max(int(data["x"]), screen.left()), screen.right() - int(_WIDTH * self._pet_scale))
                y = min(max(int(data["y"]), screen.top()), screen.bottom() - int(_HEIGHT * self._pet_scale))
                self.move(x, y)
                return
        except Exception as e:
            logger.debug("No usable saved pet position, using default: %s", e)
        default_x = screen.right() - int(_WIDTH * self._pet_scale) - 24
        default_y = screen.bottom() - int(_HEIGHT * self._pet_scale) - 24
        self.move(default_x, default_y)

    def _save_position(self):
        try:
            _POSITION_PATH.write_text(
                json.dumps({"x": self.x(), "y": self.y(), "scale": self._pet_scale}), encoding="utf-8"
            )
        except Exception as e:
            logger.warning("Failed to save pet position: %s", e, exc_info=True)


_ALL_CORE_STATES = frozenset(_STATE_COLORS)


def _map_event_to_state(event: dict) -> Optional[str]:
    """Map the authoritative charlie_state event straight through -- all 9 CoreState values render."""
    if event.get("type") != "charlie_state":
        return None
    core_state = (event.get("payload") or {}).get("state")
    return core_state if core_state in _ALL_CORE_STATES else None


def _state_caption_title(state: str) -> str:
    """Human-readable title line for the caption pill -- the pet's current charlie_state, verbatim."""
    return _STATE_TITLES.get(state, state.capitalize() if state else "")


def _map_event_to_caption_desc(event: dict) -> Optional[str]:
    """Content line (line 2) for the caption pill. None means no mapping -- _sub_loop leaves desc untouched.
    speaking_start deliberately has no branch here: it fires once per TTS-flushed sentence chunk
    (many times per reply), and a generic placeholder here would stomp the live token-driven text."""
    etype = event.get("type", "")

    if etype in ("vad_start", "wake_word"):
        return "I'm paying attention"

    if etype == "thinking":
        return "Processing request..."

    if etype in ("speaking_stop", "response_done"):
        return ""

    if etype in ("tool_approval_request", "extension_pending", "recovery_proposal"):
        reason = (event.get("payload") or {}).get("reason", "").strip()
        return reason or "Waiting on a decision"

    if etype == "alert":
        payload = event.get("payload") or {}
        if payload.get("severity") in ("warning", "error"):
            return (payload.get("message") or "Something needs a look").strip()

    return None


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _extract_last_sentence(buffer: str) -> str:
    """Return the last (possibly still-incomplete) sentence of a streamed token buffer."""
    buffer = buffer.strip()
    if not buffer:
        return ""
    return _SENTENCE_SPLIT_RE.split(buffer)[-1].strip()


def _elide_text(text: str, width_fn: Callable[[str], int], max_width: int) -> str:
    """Shrink text with a trailing ellipsis until width_fn(text) <= max_width. Pure, Qt-free."""
    if not text or width_fn(text) <= max_width:
        return text
    ellipsis = "..."
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        candidate = text[:mid].rstrip() + ellipsis
        if width_fn(candidate) <= max_width:
            lo = mid
        else:
            hi = mid - 1
    return (text[:lo].rstrip() + ellipsis) if lo > 0 else ellipsis


def _sub_loop(window: PetWindow, stop_event: threading.Event):
    """Blocking ZeroMQ SUB loop, run on a background thread; pushes state via Qt signal."""
    ctx = zmq.Context()
    sock = ctx.socket(zmq.SUB)
    sock.connect(f"tcp://127.0.0.1:{DEFAULT_EVENT_PORT}")
    sock.setsockopt(zmq.SUBSCRIBE, b"")
    sock.setsockopt(zmq.RCVTIMEO, 500)
    active_workspaces: set = set()
    speech_buffer = ""
    try:
        while not stop_event.is_set():
            try:
                raw = sock.recv_string()
            except zmq.Again:
                continue
            except Exception as e:
                logger.debug("Pet event recv error: %s", e)
                continue
            try:
                event = json.loads(raw)
            except Exception:
                continue

            etype = event.get("type", "")
            if etype == "speaking_start":
                speech_buffer = ""
            elif etype == "token":
                speech_buffer += (event.get("payload") or {}).get("text", "")
                sentence = _extract_last_sentence(speech_buffer)
                if sentence:
                    window.caption_changed.emit(sentence)
            elif etype in ("speaking_stop", "response_done"):
                speech_buffer = ""

            state = _map_event_to_state(event)
            if state is not None:
                window.state_changed.emit(state)

            desc = _map_event_to_caption_desc(event)
            if desc is not None:
                window.caption_changed.emit(desc)

            workspace_active = _track_workspace_surface(active_workspaces, event)
            if workspace_active is not None:
                window.workspace_surface_changed.emit(workspace_active)
    finally:
        sock.close(linger=0)
        ctx.term()


def main():
    app = QApplication([])
    window = PetWindow()
    window.show()

    stop_event = threading.Event()
    sub_thread = threading.Thread(
        target=_sub_loop, args=(window, stop_event), daemon=True
    )
    sub_thread.start()

    # Let Qt's event loop tick even with no native events, so app.quit() etc. stay responsive.
    keepalive = QTimer()
    keepalive.timeout.connect(lambda: None)
    keepalive.start(250)

    try:
        app.exec()
    finally:
        stop_event.set()

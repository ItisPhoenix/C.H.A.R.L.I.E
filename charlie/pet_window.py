"""Floating desktop pet: frameless always-on-top orb reflecting live voice state."""

import json
import logging
import math
import threading
import webbrowser
from pathlib import Path
from typing import Optional

import zmq
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import QApplication, QWidget

from charlie.config import config
from charlie.ipc import DEFAULT_EVENT_PORT
from charlie.utils import json_loads

logger = logging.getLogger("charlie.pet")

_DESIGN_WIDTH = 240
_DESIGN_HEIGHT = 420
_SCALE = 0.6
_WIDTH = round(_DESIGN_WIDTH * _SCALE)
_HEIGHT = round(_DESIGN_HEIGHT * _SCALE)
_CHAR_TOP = 130
_DRAG_THRESHOLD = 6
_POSITION_PATH = Path(config.pet_position_path)
_PULSE_INTERVAL_MS = 40
_BODY_COLOR = "#f5f6f8"
_OUTLINE_COLOR = "#2a2e36"
_VISOR_BG = "#0b0c10"
_ACCENT_COLOR = "#7ed6c8"
_PLATE_COLOR = "#e7e9ec"
_BUBBLE_BG = "#14151b"
_BUBBLE_BG_ALPHA = 234
_BUBBLE_TEXT = "#e7e8ec"
_CAPTION_MAX_CHARS = 160

_STATE_COLORS = {
    "idle": "#4b5563",
    "listening": "#06b6d4",
    "thinking": "#a855f7",
    "speaking": "#10b981",
}
# Eyes stay lit at all times; only the glow strength dips at idle so the pet never looks dead.
_EYE_COLORS = {
    "idle": "#22d3ee",
    "listening": "#22d3ee",
    "thinking": "#a855f7",
    "speaking": "#10b981",
}
_EYE_GLOW_ALPHA = {
    "idle": 90,
    "listening": 170,
    "thinking": 170,
    "speaking": 170,
}
# Idle bobs gently in place; active states bounce more noticeably.
_BOUNCE_AMPLITUDE_PX = {
    "idle": 1.5,
    "listening": 2.5,
    "thinking": 3.5,
    "speaking": 2.5,
}
_PULSE_SPEED = {
    "idle": 0.05,
    "listening": 0.12,
    "thinking": 0.18,
    "speaking": 0.14,
}
# Waving arm: base angle plus a swing amplitude/speed, faster while active.
_WAVE_BASE_ANGLE = -52
_WAVE_AMPLITUDE = {
    "idle": 4,
    "listening": 12,
    "thinking": 8,
    "speaking": 12,
}
_WAVE_SPEED = {
    "idle": 0.04,
    "listening": 0.16,
    "thinking": 0.10,
    "speaking": 0.16,
}
_BLINK_PERIOD_TICKS = 90
_BLINK_DURATION_TICKS = 4


class PetWindow(QWidget):
    state_changed = Signal(str)
    caption_changed = Signal(str)

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(_WIDTH, _HEIGHT)
        self._state = "idle"
        self._caption = ""
        self._phase = 0.0
        self._wave_phase = 0.0
        self._tick_count = 0
        self._drag_origin: Optional[QPoint] = None
        self._press_pos: Optional[QPoint] = None
        self._dragged = False
        self.state_changed.connect(self._on_state_changed)
        self.caption_changed.connect(self._on_caption_changed)
        self._restore_position()

        self._pulse_timer = QTimer(self)
        self._pulse_timer.timeout.connect(self._tick)
        self._pulse_timer.start(_PULSE_INTERVAL_MS)

    def _on_state_changed(self, state: str):
        self._state = state
        self.update()

    def _on_caption_changed(self, caption: str):
        self._caption = caption
        self.update()

    def _tick(self):
        self._phase += _PULSE_SPEED.get(self._state, 0.05)
        self._wave_phase += _WAVE_SPEED.get(self._state, 0.04)
        self._tick_count += 1
        self.update()

    def _is_blinking(self) -> bool:
        return (self._tick_count % _BLINK_PERIOD_TICKS) < _BLINK_DURATION_TICKS

    def _silhouette(self, cx: float, top: float) -> QPainterPath:
        head = QPainterPath()
        head.addEllipse(QRectF(cx - 62, top, 124, 124))
        body = QPainterPath()
        body.addRoundedRect(QRectF(cx - 58, top + 98, 116, 150), 58, 58)
        return head.united(body)

    def _paddle_path(self, length: float, width: float) -> QPainterPath:
        path = QPainterPath()
        path.addRoundedRect(QRectF(-width / 2, -length / 2, width, length), width / 2, width / 2)
        return path

    def _draw_paddle_arm(self, painter: QPainter, origin: QPointF, angle: float, outline: QPen):
        painter.save()
        painter.translate(origin)
        painter.rotate(angle)
        painter.setPen(outline)
        painter.setBrush(QColor(_ACCENT_COLOR))
        painter.drawPath(self._paddle_path(90, 30))
        painter.setBrush(QColor(_BODY_COLOR))
        painter.setPen(Qt.NoPen)
        painter.translate(4, -2)
        painter.drawPath(self._paddle_path(82, 20))
        painter.restore()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        color = QColor(_EYE_COLORS.get(self._state, _EYE_COLORS["idle"]))
        glow_alpha = _EYE_GLOW_ALPHA.get(self._state, 90)
        bounce_amp = _BOUNCE_AMPLITUDE_PX.get(self._state, 1.5)
        bounce = bounce_amp * math.sin(self._phase)
        wave_amp = _WAVE_AMPLITUDE.get(self._state, 4)
        wave_angle = _WAVE_BASE_ANGLE + wave_amp * math.sin(self._wave_phase)

        if self._caption:
            self._draw_caption_bubble(painter)

        # Character is drawn in a fixed design-space canvas, then scaled down to the widget's actual size.
        painter.save()
        painter.scale(_SCALE, _SCALE)

        cx = _DESIGN_WIDTH / 2
        top = _CHAR_TOP + bounce

        outline = QPen(QColor(_OUTLINE_COLOR))
        outline.setWidthF(2.4)

        # Base plate, anchored to the character's own top so bounce doesn't detach it from the ground.
        painter.setPen(outline)
        painter.setBrush(QColor(_PLATE_COLOR))
        painter.drawEllipse(QPointF(cx, top + 244), 78, 15)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 30))
        painter.drawEllipse(QPointF(cx, top + 249), 70, 9)

        # Resting arm, pushed well past the torso edge so it isn't swallowed by the body silhouette.
        self._draw_paddle_arm(painter, QPointF(cx + 70, top + 196), 6, outline)
        # Waving arm swings on its own phase/speed, faster while listening or speaking.
        self._draw_paddle_arm(painter, QPointF(cx - 54, top + 172), wave_angle, outline)

        # Torso + head, one seamless silhouette (no boxy head, no visible neck seam).
        silhouette = self._silhouette(cx, top)
        shade = QRadialGradient(QPointF(cx, top + 70), 190)
        shade.setColorAt(0.0, QColor(_BODY_COLOR).lighter(106))
        shade.setColorAt(1.0, QColor(_BODY_COLOR).darker(110))
        painter.setPen(outline)
        painter.setBrush(shade)
        painter.drawPath(silhouette)

        # Ears: small, mostly-white nubs at cheek level, just a faint shadow inside -- not full dark discs.
        for dx in (-61, 61):
            painter.setPen(outline)
            painter.setBrush(QColor(_BODY_COLOR))
            painter.drawEllipse(QPointF(cx + dx, top + 92), 8, 11)
            painter.setPen(Qt.NoPen)
            shadow = QColor(_OUTLINE_COLOR)
            shadow.setAlpha(70)
            painter.setBrush(shadow)
            painter.drawEllipse(QPointF(cx + dx, top + 92), 3, 5)

        # Teal cap: a wide rounded band clipped to the top of the head circle.
        cap_rect = QPainterPath()
        cap_rect.addRoundedRect(QRectF(cx - 34, top, 68, 30), 15, 15)
        head_circle = QPainterPath()
        head_circle.addEllipse(QRectF(cx - 62, top, 124, 124))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(_ACCENT_COLOR))
        painter.drawPath(cap_rect.intersected(head_circle))

        # Face visor with glowing eyes, color-coded to the live voice state.
        visor = QRectF(cx - 48, top + 32, 96, 62)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(_VISOR_BG))
        painter.drawRoundedRect(visor, 28, 28)

        blinking = self._is_blinking()
        eye_h = 3.0 if blinking else 28.0
        eye_y = visor.center().y() - eye_h / 2
        for dx in (-22, 22):
            center = QPointF(cx + dx, visor.center().y())
            if not blinking:
                glow = QRadialGradient(center, 26)
                glow_c = QColor(color)
                glow_c.setAlpha(glow_alpha)
                glow.setColorAt(0.0, glow_c)
                edge_c = QColor(color)
                edge_c.setAlpha(0)
                glow.setColorAt(1.0, edge_c)
                painter.setBrush(glow)
                painter.setPen(Qt.NoPen)
                painter.drawEllipse(center, 26, 26)
            painter.setBrush(color)
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(QRectF(center.x() - 9, eye_y, 18, eye_h), 9, 9)

        painter.restore()

    def _draw_caption_bubble(self, painter: QPainter):
        font = QFont("Segoe UI")
        font.setPointSize(9)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        bubble_width = _WIDTH - 16
        text_rect = metrics.boundingRect(
            0, 0, bubble_width - 20, 300, Qt.TextWordWrap, self._caption
        )
        bubble_height = min(text_rect.height() + 20, 64)
        bubble_rect = QRectF(8, 6, bubble_width, bubble_height)
        bubble_color = QColor(_BUBBLE_BG)
        bubble_color.setAlpha(_BUBBLE_BG_ALPHA)
        painter.setPen(Qt.NoPen)
        painter.setBrush(bubble_color)
        painter.drawRoundedRect(bubble_rect, 12, 12)
        painter.setPen(QColor(_BUBBLE_TEXT))
        painter.drawText(bubble_rect.adjusted(10, 8, -10, -8), Qt.TextWordWrap, self._caption)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
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
            if not self._dragged:
                self._open_dashboard()
            else:
                self._save_position()
            self._drag_origin = None
            self._press_pos = None

    def _open_dashboard(self):
        url = f"http://{config.charlie_host}:{config.charlie_port}"
        try:
            webbrowser.open(url)
        except Exception as e:
            logger.warning("Failed to open dashboard: %s", e, exc_info=True)

    def _restore_position(self):
        try:
            if _POSITION_PATH.exists():
                data = json_loads(_POSITION_PATH.read_text(encoding="utf-8"))
                self.move(int(data["x"]), int(data["y"]))
                return
        except Exception as e:
            logger.debug("No usable saved pet position, using default: %s", e)
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(screen.right() - _WIDTH - 24, screen.bottom() - _HEIGHT - 24)

    def _save_position(self):
        try:
            _POSITION_PATH.write_text(
                json.dumps({"x": self.x(), "y": self.y()}), encoding="utf-8"
            )
        except Exception as e:
            logger.warning("Failed to save pet position: %s", e, exc_info=True)


def _map_event_to_state(event_type: str) -> Optional[str]:
    """Map an EventBus event type to a pet visual state, or None if irrelevant."""
    if event_type in ("vad_start", "wake_word"):
        return "listening"
    if event_type == "thinking":
        return "thinking"
    if event_type == "speaking_start":
        return "speaking"
    if event_type in ("speaking_stop", "response_done"):
        return "idle"
    return None


def _map_event_to_caption(event: dict) -> Optional[str]:
    """Map an EventBus event to caption-bubble text, or None if irrelevant."""
    etype = event.get("type", "")
    if etype in ("vad_start", "wake_word"):
        return "Listening..."
    if etype == "thinking":
        return "Thinking..."
    if etype == "speaking_start":
        text = (event.get("payload") or {}).get("text", "").strip()
        if len(text) > _CAPTION_MAX_CHARS:
            text = text[:_CAPTION_MAX_CHARS].rstrip() + "..."
        return text or "Speaking..."
    if etype in ("speaking_stop", "response_done"):
        return ""
    return None


def _sub_loop(window: PetWindow, stop_event: threading.Event):
    """Blocking ZeroMQ SUB loop, run on a background thread; pushes state via Qt signal."""
    ctx = zmq.Context()
    sock = ctx.socket(zmq.SUB)
    sock.connect(f"tcp://127.0.0.1:{DEFAULT_EVENT_PORT}")
    sock.setsockopt(zmq.SUBSCRIBE, b"")
    sock.setsockopt(zmq.RCVTIMEO, 500)
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
            state = _map_event_to_state(event.get("type", ""))
            if state is not None:
                window.state_changed.emit(state)
            caption = _map_event_to_caption(event)
            if caption is not None:
                window.caption_changed.emit(caption)
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

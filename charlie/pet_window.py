"""Floating desktop pet: frameless always-on-top EMO-style orb reflecting live voice state."""

import json
import logging
import math
import threading
import webbrowser
from pathlib import Path
from typing import Optional

import zmq
from PySide6.QtCore import QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QMouseEvent, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import QApplication, QWidget

from charlie.config import config
from charlie.ipc import DEFAULT_EVENT_PORT
from charlie.utils import json_loads

logger = logging.getLogger("charlie.pet")

_WIDTH = 380
_HEIGHT = 280
_DRAG_THRESHOLD = 6
_POSITION_PATH = Path(config.pet_position_path)
_PULSE_INTERVAL_MS = 30
_CAPTION_MAX_CHARS = 200

# Eyes stay cyan to match EMO aesthetic, but we can modulate alpha
_EYE_COLOR = "#00ffff"
_EYE_GLOW_ALPHA = {
    "idle": 150,
    "listening": 255,
    "thinking": 200,
    "speaking": 220,
}

_BOUNCE_AMPLITUDE_PX = {
    "idle": 2.0,
    "listening": 4.0,
    "thinking": 1.5,
    "speaking": 3.0,
}
_PULSE_SPEED = {
    "idle": 0.04,
    "listening": 0.1,
    "thinking": 0.15,
    "speaking": 0.2,
}
_BLINK_PERIOD_TICKS = 120
_BLINK_DURATION_TICKS = 5

class PetWindow(QWidget):
    state_changed = Signal(str)
    caption_changed = Signal(str, str) # title, desc

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(_WIDTH, _HEIGHT)
        self._state = "idle"
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
        self.state_changed.connect(self._on_state_changed)
        self.caption_changed.connect(self._on_caption_changed)
        self._restore_position()

        self._pulse_timer = QTimer(self)
        self._pulse_timer.timeout.connect(self._tick)
        self._pulse_timer.start(_PULSE_INTERVAL_MS)

    def _on_state_changed(self, state: str):
        self._state = state
        self.update()

    def _on_caption_changed(self, title: str, desc: str):
        if not title and not desc:
            # Clear / fade out
            self._caption_time_left = 0
        else:
            self._caption_title = title
            self._caption_desc = desc
            self._caption_visible = True
            self._caption_fade_dir = 1
            self._caption_time_left = 5000 // _PULSE_INTERVAL_MS # 5 seconds
        self.update()

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

        # Coordinates for EMO head
        pet_w = 76
        pet_h = 64
        cx = (_WIDTH - pet_w) / 2

        bounce = int(_BOUNCE_AMPLITUDE_PX.get(self._state, 2.0) * math.sin(self._phase))
        cy = 150 + bounce

        self._draw_pet(painter, cx, cy, pet_w, pet_h)

        if self._caption_alpha > 0:
            self._draw_caption_bubble(painter)

    def _draw_pet(self, painter: QPainter, cx: float, cy: float, w: float, h: float):
        # 1. Base Drop Shadow / Glow
        painter.setPen(Qt.NoPen)
        glow_alpha = _EYE_GLOW_ALPHA.get(self._state, 150) // 4
        glow_grad = QRadialGradient(cx + w/2, cy + h/2, w*0.8)
        glow_grad.setColorAt(0, QColor(0, 255, 255, glow_alpha))
        glow_grad.setColorAt(1, QColor(0, 0, 0, 0))
        painter.setBrush(glow_grad)
        painter.drawRoundedRect(cx - 20, cy - 20, w + 40, h + 40, 40, 40)

        # 2. Headphones (Pill shapes on sides)
        hp_w = 12
        hp_h = 38
        hp_y = cy + (h - hp_h) / 2

        # Left HP
        hp_left_rect = QRectF(cx - hp_w + 5, hp_y, hp_w, hp_h)
        painter.setBrush(QColor("#1a1a1a"))
        painter.drawRoundedRect(hp_left_rect, hp_w/2, hp_w/2)
        # Left HP Neon Ring
        painter.setPen(QPen(QColor(0, 255, 255, int(self._caption_alpha if self._state != "idle" else 100)), 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(hp_left_rect.adjusted(4, 8, -4, -8), (hp_w-8)/2, (hp_w-8)/2)

        # Right HP
        hp_right_rect = QRectF(cx + w - 5, hp_y, hp_w, hp_h)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#1a1a1a"))
        painter.drawRoundedRect(hp_right_rect, hp_w/2, hp_w/2)
        # Right HP Neon Ring
        painter.setPen(QPen(QColor(0, 255, 255, int(self._caption_alpha if self._state != "idle" else 100)), 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(hp_right_rect.adjusted(4, 8, -4, -8), (hp_w-8)/2, (hp_w-8)/2)

        # 3. Face Screen (Main Body)
        painter.setPen(QPen(QColor("#333333"), 2)) # subtle bevel border
        screen_grad = QLinearGradient(cx, cy, cx, cy + h)
        screen_grad.setColorAt(0, QColor("#151515"))
        screen_grad.setColorAt(1, QColor("#050505"))
        painter.setBrush(screen_grad)
        painter.drawRoundedRect(cx, cy, w, h, 24, 24)

        # 4. Eyes
        # Calculate eye geometry based on state
        eye_w = 16
        eye_h = 20
        eye_y = cy + 20
        eye_space = 12

        left_eye_x = cx + w/2 - eye_space/2 - eye_w
        right_eye_x = cx + w/2 + eye_space/2

        if self._is_blinking():
            eye_y += eye_h / 2 - 2
            eye_h = 4
        elif self._state == "thinking":
            # Squint and look around
            eye_h = 14
            eye_y += 4
            shift = 6 * math.sin(self._phase * 1.5)
            left_eye_x += shift
            right_eye_x += shift
        elif self._state == "speaking":
            # Happy bounce / arches
            mod = 2 * math.sin(self._phase * 3)
            eye_h = 16 - mod
            eye_y += mod
        elif self._state == "listening":
            # Widen
            eye_h = 24
            eye_y -= 4

        eye_color = QColor(0, 255, 255, _EYE_GLOW_ALPHA.get(self._state, 200))
        painter.setPen(Qt.NoPen)
        painter.setBrush(eye_color)

        if self._state == "speaking" and not self._is_blinking():
            # Draw arch eyes for speaking ^ ^
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
            # Draw standard rounded rect eyes
            painter.drawRoundedRect(left_eye_x, eye_y, eye_w, eye_h, 8, 8)
            painter.drawRoundedRect(right_eye_x, eye_y, eye_w, eye_h, 8, 8)

    def _draw_caption_bubble(self, painter: QPainter):
        alpha = int(self._caption_alpha)

        card_w = _WIDTH - 20
        card_h = 56
        card_x = 10
        card_y = 20

        # Dark rounded rect with subtle grey border
        painter.setBrush(QColor(30, 30, 30, int(alpha * 0.9)))
        painter.setPen(QPen(QColor(100, 100, 100, int(alpha * 0.6)), 1.5))
        painter.drawRoundedRect(card_x, card_y, card_w, card_h, 16, 16)

        # Circular button around chevron on the right
        chevron_x = card_x + card_w - 20
        chevron_y = card_y + card_h / 2

        # Circle background
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(60, 60, 60, int(alpha * 0.8)))
        painter.drawEllipse(QPoint(int(chevron_x), int(chevron_y)), 12, 12)

        # Chevron arrow (down)
        painter.setPen(QPen(QColor(255, 255, 255, alpha), 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        path = QPainterPath()
        path.moveTo(chevron_x - 4, chevron_y - 2)
        path.lineTo(chevron_x, chevron_y + 3)
        path.lineTo(chevron_x + 4, chevron_y - 2)
        painter.drawPath(path)

        # Text Metrics
        painter.setPen(QColor(255, 255, 255, alpha))

        # Title (Bold)
        title_font = QFont("Segoe UI", 10, QFont.Bold)
        painter.setFont(title_font)
        painter.drawText(card_x + 16, card_y + 6, card_w - 40, 24, Qt.AlignLeft | Qt.AlignVCenter, self._caption_title)

        # Description (Regular)
        desc_font = QFont("Segoe UI", 9)
        painter.setFont(desc_font)
        painter.setPen(QColor(180, 180, 180, alpha))
        painter.drawText(card_x + 16, card_y + 26, card_w - 40, 24, Qt.AlignLeft | Qt.AlignVCenter, self._caption_desc)


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
                # Click on the caption or pet opens dashboard
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
    if event_type in ("thinking", "thinking_update"):
        return "thinking"
    if event_type == "speaking_start":
        return "speaking"
    if event_type in ("speaking_stop", "response_done"):
        return "idle"
    return None


def _map_event_to_caption(event: dict) -> tuple[Optional[str], Optional[str]]:
    """Map an EventBus event to caption bubble (title, desc), or (None, None) if irrelevant."""
    etype = event.get("type", "")

    if etype in ("vad_start", "wake_word"):
        return ("Listening...", "I'm paying attention")

    if etype == "thinking":
        return ("Thinking...", "Processing request...")

    if etype == "thinking_update":
        text = (event.get("payload") or {}).get("text", "").strip()
        if len(text) > _CAPTION_MAX_CHARS:
            text = text[:_CAPTION_MAX_CHARS].rstrip() + "..."
        # We don't get the tool name separately in this payload, just the text.
        # But we can try to split by colon if present.
        if ": " in text:
            parts = text.split(": ", 1)
            title = parts[0]
            desc = parts[1]
            return (title, desc)
        return ("Thinking...", text or "Processing request...")

    if etype == "speaking_start":
        text = (event.get("payload") or {}).get("text", "").strip()
        if len(text) > _CAPTION_MAX_CHARS:
            text = text[:_CAPTION_MAX_CHARS].rstrip() + "..."
        return ("Speaking", text or "Responding...")

    if etype in ("speaking_stop", "response_done"):
        return (None, None)

    return (None, None)


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

            title, desc = _map_event_to_caption(event)
            if title is not None:
                window.caption_changed.emit(title, desc)
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

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
from PySide6.QtGui import QRegion

_WIDTH = 420
_HEIGHT = 160
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
    "sleeping": 0.02,
    "listening": 0.1,
    "thinking": 0.15,
    "searching": 0.15,
    "reading": 0.15,
    "speaking": 0.2,
    "error": 0.2,
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
        self._caption_phase = 0.0 # for bouncy spring popup
        
        # Dimensions saved for mask
        self._card_x = 0
        self._card_y = 0
        self._card_w = 0
        self._card_h = 0

        self._phase = 0.0
        self._tick_count = 0
        self._idle_ticks = 0
        self._pet_color = config.pet_color or "#00ffff"
        
        self._drag_origin: Optional[QPoint] = None
        self._press_pos: Optional[QPoint] = None
        self._dragged = False
        self._chevron_rect = QRectF()
        self.state_changed.connect(self._on_state_changed)
        self.caption_changed.connect(self._on_caption_changed)
        self._restore_position()

        self._pulse_timer = QTimer(self)
        self._pulse_timer.timeout.connect(self._tick)
        self._pulse_timer.start(_PULSE_INTERVAL_MS)
        
        self._color_poll_timer = QTimer(self)
        self._color_poll_timer.timeout.connect(self._poll_color)
        self._color_poll_timer.start(5000)

    def _poll_color(self):
        try:
            env_path = Path(".env")
            if env_path.exists():
                for line in env_path.read_text().splitlines():
                    if line.startswith("PET_COLOR="):
                        val = line.split("=", 1)[1].strip(' "\'')
                        if val:
                            self._pet_color = val
        except Exception:
            pass

    def _on_state_changed(self, state: str):
        if self._state != state:
            self._state = state
            self._idle_ticks = 0
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
            self._caption_alpha = min(255.0, self._caption_alpha + 25)
            self._caption_phase += 0.2
        elif self._caption_fade_dir == -1:
            self._caption_alpha = max(0.0, self._caption_alpha - 15)
            self._caption_phase -= 0.15
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
        pet_w = 64
        pet_h = 54
        cx = _WIDTH - pet_w - 20

        bounce = int(_BOUNCE_AMPLITUDE_PX.get(self._state, 2.0) * math.sin(self._phase))
        cy = _HEIGHT - pet_h - 20 + bounce

        self._draw_pet(painter, cx, cy, pet_w, pet_h)

        if self._caption_alpha > 0:
            self._draw_caption_bubble(painter, cx, cy, pet_w, pet_h)

        # Update click-through mask
        mask = QRegion(int(cx - 30), int(cy - 30), int(pet_w + 60), int(pet_h + 60))
        if self._caption_alpha > 0:
            mask = mask | QRegion(int(self._card_x - 10), int(self._card_y - 10), int(self._card_w + 20), int(self._card_h + 20))
        self.setMask(mask)
        
        self._idle_ticks += 1
        if self._idle_ticks > (60 * 1000) // _PULSE_INTERVAL_MS and self._state == "idle":
            self._state = "sleeping"

    def _draw_pet(self, painter: QPainter, cx: float, cy: float, w: float, h: float):
        pet_c = QColor(self._pet_color)
        
        # 1. Base Drop Shadow / Glow
        painter.setPen(Qt.NoPen)
        glow_alpha = _EYE_GLOW_ALPHA.get(self._state, 150) // 4
        glow_grad = QRadialGradient(cx + w/2, cy + h/2, w*0.8)
        glow_grad.setColorAt(0, QColor(pet_c.red(), pet_c.green(), pet_c.blue(), glow_alpha))
        glow_grad.setColorAt(1, QColor(0, 0, 0, 0))
        painter.setBrush(glow_grad)
        painter.drawRoundedRect(cx - 20, cy - 20, w + 40, h + 40, 40, 40)

        # 2. Headphones (Pill shapes on sides)
        hp_w = 10
        hp_h = 32
        hp_y = cy + (h - hp_h) / 2

        # Left HP
        hp_left_rect = QRectF(cx - hp_w + 5, hp_y, hp_w, hp_h)
        painter.setBrush(QColor("#1a1a1a"))
        painter.drawRoundedRect(hp_left_rect, hp_w/2, hp_w/2)
        # Left HP Neon Ring
        ring_c = QColor(pet_c.red(), pet_c.green(), pet_c.blue(), int(self._caption_alpha if self._state != "idle" else 100))
        painter.setPen(QPen(ring_c, 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(hp_left_rect.adjusted(4, 8, -4, -8), (hp_w-8)/2, (hp_w-8)/2)

        # Right HP
        hp_right_rect = QRectF(cx + w - 5, hp_y, hp_w, hp_h)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#1a1a1a"))
        painter.drawRoundedRect(hp_right_rect, hp_w/2, hp_w/2)
        # Right HP Neon Ring
        painter.setPen(QPen(ring_c, 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(hp_right_rect.adjusted(4, 8, -4, -8), (hp_w-8)/2, (hp_w-8)/2)

        # 2b. Headphone Top Strap
        painter.setPen(QPen(QColor("#2a2a2a"), 8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        strap_path = QPainterPath()
        strap_path.moveTo(cx + 4, hp_y + 6)
        strap_path.quadTo(cx + w/2, cy - 12, cx + w - 4, hp_y + 6)
        painter.drawPath(strap_path)

        # 3. Face Screen (Main Body)
        painter.setPen(QPen(QColor("#333333"), 2)) # subtle bevel border
        screen_grad = QLinearGradient(cx, cy, cx, cy + h)
        screen_grad.setColorAt(0, QColor("#151515"))
        screen_grad.setColorAt(1, QColor("#050505"))
        painter.setBrush(screen_grad)
        painter.drawRoundedRect(cx, cy, w, h, 24, 24)

        # 4. Eyes
        # Calculate eye geometry based on state
        eye_w = 14
        eye_h = 16
        eye_y = cy + 18
        eye_space = 10

        left_eye_x = cx + w/2 - eye_space/2 - eye_w
        right_eye_x = cx + w/2 + eye_space/2

        # Mouse Tracking Offset
        cursor_pos = self.mapFromGlobal(QCursor.pos())
        dx = cursor_pos.x() - (cx + w/2)
        dy = cursor_pos.y() - (cy + h/2)
        dist = math.hypot(dx, dy)
        max_dist = 5.0
        if dist > max_dist:
            dx = (dx / dist) * max_dist
            dy = (dy / dist) * max_dist
            
        left_eye_x += dx
        right_eye_x += dx
        eye_y += dy

        eye_w_left = eye_w
        eye_h_left = eye_h
        eye_w_right = eye_w
        eye_h_right = eye_h
        eye_angle = 0

        if self._is_blinking():
            eye_y += eye_h / 2 - 2
            eye_h_left = eye_h_right = 4
        elif self._state == "sleeping":
            eye_y += eye_h / 2 - 2
            eye_h_left = eye_h_right = 3
            # Floating Zzzs
            painter.setPen(QColor(255, 255, 255, 120))
            painter.setFont(QFont("Segoe UI", 10, QFont.Bold))
            z_y = cy - 10 - 15 * math.sin(self._phase * 0.5)
            z_x = cx + w - 10 + 5 * math.cos(self._phase * 0.5)
            painter.drawText(int(z_x), int(z_y), "Z")
            z_y2 = cy - 25 - 15 * math.sin(self._phase * 0.5 + 1)
            z_x2 = cx + w + 5 + 5 * math.cos(self._phase * 0.5 + 1)
            painter.drawText(int(z_x2), int(z_y2), "z")
        elif self._state in ("thinking", "searching", "reading"):
            # Spin in circles
            eye_h_left = eye_h_right = 8
            eye_w_left = eye_w_right = 8
            eye_y += 4
            spin = 4
            left_eye_x += spin * math.cos(self._phase * 4)
            right_eye_x += spin * math.cos(self._phase * 4)
            eye_y += spin * math.sin(self._phase * 4)
        elif self._state == "listening":
            # Puppy tilt
            eye_h_left = eye_h_right = 20
            eye_y -= 4
            eye_angle = 15 # degrees
        elif self._state == "error":
            eye_h_left = 18
            eye_w_left = 18
            eye_h_right = 8
            eye_w_right = 8
            shake = 4 * math.sin(self._phase * 15)
            left_eye_x += shake
            right_eye_x += shake

        eye_color = QColor(pet_c.red(), pet_c.green(), pet_c.blue(), _EYE_GLOW_ALPHA.get(self._state, 200))
        painter.setPen(Qt.NoPen)
        painter.setBrush(eye_color)

        if self._state == "speaking" and not self._is_blinking():
            # 3 vertical soundwave bars per eye
            bar_w = 4
            spacing = 2
            for i in range(3):
                bh = 6 + 8 * math.sin(self._phase * 8 + i)
                if bh < 4: bh = 4
                bx = left_eye_x + i * (bar_w + spacing)
                painter.drawRoundedRect(bx, eye_y + (16 - bh)/2, bar_w, bh, 2, 2)
            for i in range(3):
                bh = 6 + 8 * math.sin(self._phase * 8 + i + 1)
                if bh < 4: bh = 4
                bx = right_eye_x + i * (bar_w + spacing)
                painter.drawRoundedRect(bx, eye_y + (16 - bh)/2, bar_w, bh, 2, 2)
        else:
            # Draw standard rounded rect eyes with optional tilt
            painter.save()
            painter.translate(left_eye_x + eye_w_left/2, eye_y + eye_h_left/2)
            painter.rotate(eye_angle)
            painter.drawRoundedRect(-eye_w_left/2, -eye_h_left/2, eye_w_left, eye_h_left, 6, 6)
            painter.restore()
            
            painter.save()
            painter.translate(right_eye_x + eye_w_right/2, eye_y + eye_h_right/2)
            painter.rotate(-eye_angle)
            painter.drawRoundedRect(-eye_w_right/2, -eye_h_right/2, eye_w_right, eye_h_right, 6, 6)
            painter.restore()

        # Context Props
        if self._state == "reading":
            # Draw reading glasses
            painter.setPen(QPen(QColor(200, 200, 200, 200), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(left_eye_x - 4, eye_y - 4, eye_w_left + 8, eye_h_left + 8, 2, 2)
            painter.drawRoundedRect(right_eye_x - 4, eye_y - 4, eye_w_right + 8, eye_h_right + 8, 2, 2)
            painter.drawLine(left_eye_x + eye_w_left + 4, eye_y + eye_h_left/2, right_eye_x - 4, eye_y + eye_h_right/2)
        elif self._state == "searching":
            # Draw magnifying glass over right eye
            painter.setPen(QPen(QColor(200, 200, 200, 200), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(right_eye_x - 6, eye_y - 6, eye_w_right + 12, eye_h_right + 12)
            painter.drawLine(right_eye_x + eye_w_right + 2, eye_y + eye_h_right + 2, right_eye_x + eye_w_right + 12, eye_y + eye_h_right + 12)

    def _draw_caption_bubble(self, painter: QPainter, cx: float, cy: float, w: float, h: float):
        alpha = int(self._caption_alpha)

        # Bouncy spring popup animation
        spring = min(1.0, math.sin(min(self._caption_phase, math.pi/2) * 1.5))
        if self._caption_fade_dir == -1:
            spring = self._caption_alpha / 255.0

        card_w = (cx + w - 20) * spring
        card_h = 48 * spring
        card_x = 10 + (cx + w - 20 - card_w) / 2
        card_y = cy - card_h - 10
        
        self._card_x = card_x
        self._card_y = card_y
        self._card_w = card_w
        self._card_h = card_h

        # Dark rounded rect with subtle border matches pet_color
        pet_c = QColor(self._pet_color)
        painter.setBrush(QColor(30, 30, 30, int(alpha * 0.9)))
        painter.setPen(QPen(QColor(pet_c.red(), pet_c.green(), pet_c.blue(), int(alpha * 0.6)), 1.5))
        painter.drawRoundedRect(card_x, card_y, card_w, card_h, 24, 24)

        # Chevron button INSIDE the right edge of the caption
        chevron_cx = card_x + card_w - 20
        chevron_cy = card_y + card_h / 2
        self._chevron_rect = QRectF(chevron_cx - 12, chevron_cy - 12, 24, 24)

        # Circle background for chevron inside caption
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(60, 60, 60, int(alpha * 0.8)))
        painter.drawEllipse(self._chevron_rect)

        # Chevron arrow (down)
        painter.setPen(QPen(QColor(255, 255, 255, alpha), 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        path = QPainterPath()
        path.moveTo(chevron_cx - 4, chevron_cy - 2)
        path.lineTo(chevron_cx, chevron_cy + 3)
        path.lineTo(chevron_cx + 4, chevron_cy - 2)
        painter.drawPath(path)

        # Text Metrics
        painter.setPen(QColor(255, 255, 255, alpha))

        # Title (Bold)
        title_font = QFont("Segoe UI", 9, QFont.Bold)
        painter.setFont(title_font)
        painter.drawText(card_x + 16, card_y + 4, card_w - 32, 20, Qt.AlignLeft | Qt.AlignVCenter, self._caption_title)

        # Description (Regular)
        desc_font = QFont("Segoe UI", 8)
        painter.setFont(desc_font)
        painter.setPen(QColor(150, 150, 150, alpha))
        painter.drawText(card_x + 16, card_y + 24, card_w - 32, 20, Qt.AlignLeft | Qt.AlignVCenter, self._caption_desc)


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
                if self._chevron_rect.contains(event.position()):
                    # Clicked the chevron -> show caption thing or dashboard
                    self._open_dashboard()
                else:
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


def _map_event_to_state(event_type: str, event: dict) -> Optional[str]:
    """Map an EventBus event type to a pet visual state, or None if irrelevant."""
    if event_type in ("vad_start", "wake_word"):
        return "listening"
    if event_type in ("thinking", "thinking_update"):
        text = (event.get("payload") or {}).get("text", "").lower()
        if "search" in text or "web" in text:
            return "searching"
        if "read" in text or "file" in text or "code" in text or "edit" in text:
            return "reading"
        return "thinking"
    if event_type == "speaking_start":
        return "speaking"
    if event_type in ("speaking_stop", "response_done"):
        return "idle"
    if event_type == "error":
        return "error"
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

            state = _map_event_to_state(event.get("type", ""), event)
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

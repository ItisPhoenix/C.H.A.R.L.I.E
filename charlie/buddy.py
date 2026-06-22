"""Charlie Buddy — Glass Orb holographic character widget.

A frameless, transparent, always-on-top Qt widget that renders Charlie
as a translucent energy sphere with a mature, sophisticated face, ambient
glow, mode ring, audio visualizer, speech bubble, stance icons, and
orbiting energy particles.
"""

import json
import logging
import math
import os
import time
import random
from datetime import datetime, date
from enum import Enum
from typing import Optional

try:
    from PySide6.QtWidgets import QWidget, QMenu, QApplication
    from PySide6.QtCore import Qt, QTimer, QPointF, QRectF
    from PySide6.QtGui import (
        QPainter, QPen, QBrush, QRadialGradient,
        QColor, QFont, QPainterPath, QCursor,
    )
    HAS_PYSIDE6 = True
except ImportError:
    HAS_PYSIDE6 = False

if not HAS_PYSIDE6:
    raise ImportError("PySide6 is required for the buddy widget. Install with: uv add PySide6")

logger = logging.getLogger("charlie.buddy")

# Optional pynput for global hotkey
try:
    from pynput import keyboard as _pynput_keyboard
    HAS_PYNPUT = True
except ImportError:
    HAS_PYNPUT = False
    logger.debug("pynput not available — global hotkey disabled")


# ── State machine ──────────────────────────────────────────────────────────

class BuddyState(Enum):
    IDLE = "idle"
    GREETING = "greeting"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    SLEEPING = "sleeping"
    STANCE_POSE = "stance_pose"
    PROACTIVE = "proactive"


# ── Emotion → orb color mapping ────────────────────────────────────────────

_ORB_COLORS: dict[str, dict[str, QColor]] = {
    "neutral":    {"core": QColor("#4a6fa5"), "glow": QColor("#6b8fc7")},
    "energetic":  {"core": QColor("#00d4ff"), "glow": QColor("#33e0ff")},
    "calm":       {"core": QColor("#4caf8c"), "glow": QColor("#6fcfa6")},
    "frustrated": {"core": QColor("#ff6b35"), "glow": QColor("#ff8c5e")},
    "sad":        {"core": QColor("#9c7cb0"), "glow": QColor("#b99dcd")},
}


# ── Emotion + state → expression ───────────────────────────────────────────

_EXPRESSION_MAP: dict[tuple[str, str], str] = {
    ("neutral", "idle"): "neutral",
    ("neutral", "speaking"): "engaged",
    ("energetic", "idle"): "happy",
    ("energetic", "speaking"): "excited",
    ("calm", "idle"): "peaceful",
    ("calm", "speaking"): "gentle",
    ("frustrated", "idle"): "annoyed",
    ("frustrated", "speaking"): "intense",
    ("sad", "idle"): "downcast",
    ("sad", "speaking"): "melancholy",
    ("neutral", "thinking"): "pensive",
    ("neutral", "listening"): "attentive",
    ("neutral", "sleeping"): "sleepy",
    ("neutral", "stance_pose"): "confident",
    ("energetic", "listening"): "excited",
    ("energetic", "thinking"): "pensive",
    ("calm", "listening"): "attentive",
    ("calm", "thinking"): "pensive",
    ("frustrated", "listening"): "annoyed",
    ("frustrated", "thinking"): "intense",
    ("sad", "listening"): "downcast",
    ("sad", "thinking"): "melancholy",
    ("energetic", "greeting"): "happy",
    ("calm", "greeting"): "peaceful",
    ("frustrated", "greeting"): "neutral",
    ("sad", "greeting"): "neutral",
    ("neutral", "greeting"): "happy",
    ("neutral", "proactive"): "neutral",
    ("energetic", "proactive"): "excited",
    ("calm", "proactive"): "gentle",
    ("frustrated", "proactive"): "neutral",
    ("sad", "proactive"): "neutral",
}


# ── Time-of-day modifiers ──────────────────────────────────────────────────

def _tod_modifiers(hour: int) -> tuple[float, float]:
    """Return (brightness_mult, speed_mult) for current hour."""
    if 6 <= hour < 12:
        return (1.05, 1.1)  # morning
    if 12 <= hour < 18:
        return (0.95, 1.0)  # afternoon
    if 18 <= hour < 22:
        return (0.85, 0.85)  # evening
    return (0.8, 0.7)  # 22:00–05:59

# ── Stance → pose name ─────────────────────────────────────────────────────

STANCE_MAP: dict[str, str] = {
    "ai_hype": "skeptical",
    "privacy": "concerned",
    "open_source": "approving",
    "automation": "passionate",
    "big_tech": "snarky",
}


# ── State → target size ────────────────────────────────────────────────────

_STATE_SIZE: dict[str, float] = {
    "idle": 80,
    "greeting": 85,
    "listening": 85,
    "thinking": 75,
    "speaking": 90,
    "sleeping": 65,
    "stance_pose": 85,
    "proactive": 82,
}

# ── Particle data class ────────────────────────────────────────────────────

class _Particle:
    """A single orbiting energy orb particle."""
    __slots__ = ("angle", "radius", "speed", "life", "max_life", "trail")

    def __init__(self, base_radius: float) -> None:
        self.angle = random.uniform(0, 2 * math.pi)
        self.radius = base_radius + random.uniform(8, 18)
        self.speed = random.uniform(1.5, 3.5)
        self.life = 1.5
        self.max_life = 1.5
        self.trail: list[tuple[float, float, float]] = []  # (x, y, alpha)

    def update(self, dt: float, audio_rms: float) -> None:
        self.angle += self.speed * dt * (1.0 + audio_rms * 4.0)
        self.life -= dt
        # Store trail position
        x = math.cos(self.angle) * self.radius
        y = math.sin(self.angle) * self.radius
        self.trail.append((x, y, self.life / self.max_life))
        if len(self.trail) > 4:
            self.trail.pop(0)

    @property
    def alive(self) -> bool:
        return self.life > 0


# ═══════════════════════════════════════════════════════════════════════════
# CharlieBuddy — Glass Orb
# ═══════════════════════════════════════════════════════════════════════════

class CharlieBuddy(QWidget):
    """The glass-orb holographic buddy character."""

    def __init__(self, bridge: object = None, voice: object = None,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self.voice = voice

        # Window setup — solid, interactive, multi-monitor capable
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setFixedSize(300, 300)

        # ── State ───────────────────────────────────────────────────
        self.state: BuddyState = BuddyState.IDLE
        self.emotional_state: str = "neutral"
        self.stance_pose: Optional[str] = None
        self.audio_rms: float = 0.0

        # ── Animation ───────────────────────────────────────────────
        self._time: float = 0.0
        self._pupil_offset: QPointF = QPointF(0, 0)
        self._expression: str = "neutral"

        # Spring physics for size
        self._size: float = 40.0  # start small for grow-in
        self._target_size: float = 80.0
        self._size_vel: float = 0.0

        # Float offset
        self._float_offset: float = 0.0

        # ── Eyelid system ──────────────────────────────────────────
        self._blink_phase: float = 0.0  # 0=none, 1=blinking
        self._blink_timer: float = 0.0
        self._blink_interval: float = 4.0

        # ── Particles ──────────────────────────────────────────────
        self._particles: list[_Particle] = []

        # ── Sleep ───────────────────────────────────────────────────
        self._sleep_threshold: float = 300.0
        self._last_mouse_time: float = time.time()

        # ── Time of day ─────────────────────────────────────────────
        self._hour: int = datetime.now().hour
        self._tod_bright: float = 1.0
        self._tod_speed: float = 1.0

        # ── Personalization ─────────────────────────────────────────
        self._user_name: str = "friend"
        self._last_greeting_day: Optional[date] = None
        self._screen_context: str = "unknown"
        # ── Idle fidget ──────────────────────────────────────────
        self._idle_time: float = 0.0
        self._fidget_saccade: QPointF = QPointF(0, 0)
        self._fidget_head_tilt: float = 0.0
        self._is_fidgeting: bool = False

        # ── Startup greeting ─────────────────────────────────────
        self._startup_greeting_shown: bool = False

        # ── Proactive ───────────────────────────────────────────────
        self._proactive_text: str = ""
        self._proactive_timer: float = 0.0
        self._proactive_scale: float = 0.0  # 0→1 spring for bubble

        # ── Greeting ────────────────────────────────────────────────
        self._greeting_text: str = ""
        self._greeting_timer: float = 0.0

        # ── Stance icon ─────────────────────────────────────────────
        self._stance_icon_timer: float = 0.0

        # ── Drag ────────────────────────────────────────────────────
        self._drag_offset: Optional[QPointF] = None

        # ── Stance revert ───────────────────────────────────────────
        self._stance_timer: Optional[QTimer] = None

        # ── Startup animation ───────────────────────────────────────
        self._startup_alpha: float = 0.0
        self._startup_scale: float = 0.2
        self._startup_done: bool = False

        # ── Global hotkey ───────────────────────────────────────────
        self._hotkey_listener: Optional[object] = None
        self._init_hotkey()

        # ── Timers ──────────────────────────────────────────────────
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._tick)
        self._anim_timer.start(33)  # ~30fps

        self._pupil_timer = QTimer(self)
        self._pupil_timer.timeout.connect(self._update_pupils)
        self._pupil_timer.start(50)  # 20fps

        self._tod_timer = QTimer(self)
        self._tod_timer.timeout.connect(self._update_tod)
        self._tod_timer.start(60000)
        # ── Emotional persistence ────────────────────────────────
        self._state_path: str = os.path.join("charlie", "data", "buddy_state.json")
        self._buddy_state: dict = self._load_state()
        # Restore last emotion from saved state
        saved_emotion = self._buddy_state.get("last_emotion", "neutral")
        if saved_emotion:
            self.emotional_state = saved_emotion
            self._update_expression()
        # State save timer (every 30s)
        self._save_timer = QTimer(self)
        self._save_timer.timeout.connect(self._save_state)
        self._save_timer.start(30000)

        # ── Bridge connections ──────────────────────────────────────
        self._connect_bridge()

    # ── Global hotkey (pynput) ─────────────────────────────────────────────

    def _init_hotkey(self) -> None:
        """Start pynput listener for Ctrl+Shift+C if available."""
        if not HAS_PYNPUT:
            return
        try:
            def _on_activate():
                QTimer.singleShot(0, self._activation_callback)


            hotkey = _pynput_keyboard.HotKey(
                _pynput_keyboard.HotKey.parse('<ctrl>+<shift>+c'),
                _on_activate,
            )

            import threading

            def _listener_thread():
                with _pynput_keyboard.Listener(
                    on_press=lambda k: hotkey.press(
                        _pynput_keyboard.Listener._current_listener
                        if hasattr(_pynput_keyboard.Listener, '_current_listener')
                        else None
                    ),
                    on_release=lambda k: hotkey.release(
                        _pynput_keyboard.Listener._current_listener
                        if hasattr(_pynput_keyboard.Listener, '_current_listener')
                        else None
                    ),
                ) as listener:
                    self._hotkey_listener = listener
                    listener.join()

            t = threading.Thread(target=_listener_thread, daemon=True, name="pynput-hotkey")
            t.start()
            logger.info("Global hotkey registered: Ctrl+Shift+C")
        except Exception as e:
            logger.warning(f"Failed to register global hotkey: {e}")

    def _activation_callback(self) -> None:
        """Handle Ctrl+Shift+C activation — same as click."""
        if hasattr(self, '_on_activation'):
            self._on_activation()

    # ── Timer callbacks ─────────────────────────────────────────────────────

    def _tick(self) -> None:
        """Main 30fps animation tick."""
        dt = 0.033
        self._time += 0.033 * self._tod_speed

        # Startup animation
        if not self._startup_done:
            self._startup_alpha = min(1.0, self._startup_alpha + dt * 1.67)
            self._startup_scale = min(1.0, self._startup_scale + dt * 1.67)
            if self._startup_alpha >= 1.0:
                self._startup_done = True
                # Trigger startup greeting once
                if not self._startup_greeting_shown:
                    self._startup_greeting_shown = True
                    hour = datetime.now().hour
                    if 6 <= hour <= 12:
                        greeting = f"Good morning, {self._user_name}!"
                    elif hour < 18:
                        greeting = f"Good afternoon, {self._user_name}!"
                    else:
                        greeting = f"Good evening, {self._user_name}!"
                    self._greeting_text = greeting
                    self._greeting_timer = 4.0

        # Sleep inactivity tracking
        if self.state != BuddyState.SLEEPING:
            if time.time() - self._last_mouse_time > self._sleep_threshold:
                self._enter_sleep()

        # Idle fidget (after 10s of no mouse movement)
        if self.state == BuddyState.IDLE:
            mouse_idle = time.time() - self._last_mouse_time
            if mouse_idle > 10.0:
                self._is_fidgeting = True
                # Subtle saccade drift (random eye wander)
                self._fidget_saccade = QPointF(
                    self._fidget_saccade.x() * 0.98 + random.uniform(-0.3, 0.3) * 0.02,
                    self._fidget_saccade.y() * 0.98 + random.uniform(-0.2, 0.2) * 0.02,
                )
                # Clamp saccade to 2px
                sx = max(-2.0, min(2.0, self._fidget_saccade.x()))
                sy = max(-2.0, min(2.0, self._fidget_saccade.y()))
                self._fidget_saccade = QPointF(sx, sy)
                # Subtle head tilt
                self._fidget_head_tilt = math.sin(self._time * 0.7) * 2.0
            else:
                was_fidgeting = self._is_fidgeting
                self._is_fidgeting = False
                self._fidget_saccade = QPointF(0, 0)
                self._fidget_head_tilt = 0.0
                if was_fidgeting:
                    self._update_expression()
        else:
            was_fidgeting = self._is_fidgeting
            self._is_fidgeting = False
            self._fidget_saccade = QPointF(0, 0)
            self._fidget_head_tilt = 0.0
            if was_fidgeting:
                self._update_expression()
        # Override expression to bored/sleepy when fidgeting
        if self._is_fidgeting:
            self._expression = "sleepy"

        # Eyelid blink
        if self._blink_phase > 0:
            self._blink_phase -= dt * 6.0  # 150ms blink
            if self._blink_phase <= 0:
                self._blink_phase = 0
        if self.state != BuddyState.SLEEPING:
            self._blink_timer += dt
            if self._blink_timer >= self._blink_interval:
                self._blink_phase = 1.0
                self._blink_timer = 0.0
                self._blink_interval = 3.0 + random.uniform(1.0, 3.0)

        # Spring physics for size
        target = self._target_size * self._startup_scale
        spring_k, damp = 0.1, 0.8
        self._size_vel += (target - self._size) * spring_k
        self._size_vel *= damp
        self._size += self._size_vel

        # Float offset (gentle bobbing)
        self._float_offset = math.sin(self._time * 1.5) * 4.0

        # Proactive text spring
        if self._proactive_text:
            self._proactive_timer -= dt
            self._proactive_scale = min(1.0, self._proactive_scale + dt * 5.0)
            if self._proactive_timer <= 0:
                self._proactive_text = ""
                self._proactive_scale = 0.0
        else:
            self._proactive_scale = 0.0

        # Greeting fade
        if self._greeting_text:
            self._greeting_timer -= dt
            if self._greeting_timer <= 0:
                self._greeting_text = ""

        # Stance icon timer
        if self._stance_icon_timer > 0:
            self._stance_icon_timer -= dt

        # Particles
        self._update_particles(dt)

        self.update()

    def _update_particles(self, dt: float) -> None:
        """Update and manage energy orb particles."""
        # Spawn particles when speaking or thinking
        if self.state in (BuddyState.SPEAKING, BuddyState.THINKING):
            if len(self._particles) < 6 and random.random() < 0.15:
                self._particles.append(_Particle(self._size / 2))
        elif self.state == BuddyState.IDLE:
            # Fade out particles when idle
            pass

        # Update existing
        for p in self._particles:
            p.update(dt, self.audio_rms if self.state == BuddyState.SPEAKING else 0)

        # Remove dead
        self._particles = [p for p in self._particles if p.alive]

        # Force fade if not speaking/thinking
        if self.state not in (BuddyState.SPEAKING, BuddyState.THINKING):
            for p in self._particles:
                p.life = min(p.life, 0.3)

    # ── Pupil tracking ──────────────────────────────────────────────────────

    def _update_pupils(self) -> None:
        """Track mouse cursor for pupil movement, blend fidget saccade when idle."""
        if self.state == BuddyState.SLEEPING:
            self._pupil_offset = QPointF(0, 0)
            return
        cursor = QCursor.pos()
        center = self.mapToGlobal(QPointF(self.width() / 2, self.height() / 2))
        dx = cursor.x() - center.x()
        dy = cursor.y() - center.y()
        dist = math.sqrt(dx * dx + dy * dy)
        if dist > 0:
            max_offset = 6.0
            nx = dx / dist * min(max_offset, dist * 0.012)
            ny = dy / dist * min(max_offset, dist * 0.012)
            new_off = QPointF(nx, ny)
            # When fidgeting, blend mouse tracking with saccade drift
            if self._is_fidgeting:
                blend = 0.15  # mostly saccade, slight mouse tracking
                new_off = QPointF(
                    new_off.x() * blend + self._fidget_saccade.x() * (1.0 - blend),
                    new_off.y() * blend + self._fidget_saccade.y() * (1.0 - blend),
                )
            self._pupil_offset = QPointF(
                self._pupil_offset.x() * 0.7 + new_off.x() * 0.3,
                self._pupil_offset.y() * 0.7 + new_off.y() * 0.3,
            )
            self._last_mouse_time = time.time()
        else:
            # Relax toward center when no cursor
            self._pupil_offset = QPointF(
                self._pupil_offset.x() * 0.85,
                self._pupil_offset.y() * 0.85,
            )

    def _update_tod(self) -> None:
        self._hour = datetime.now().hour
        self._tod_bright, self._tod_speed = _tod_modifiers(self._hour)
        night = self._hour >= 22 or self._hour < 6
        self._sleep_threshold = 120.0 if night else 300.0

    # ── Sleep ───────────────────────────────────────────────────────────────

    def _enter_sleep(self) -> None:
        self.state = BuddyState.SLEEPING
        self._target_size = _STATE_SIZE["sleeping"]
        self._update_expression()
        logger.info("Buddy entering sleep")

    def _exit_sleep(self) -> None:
        self.state = BuddyState.IDLE
        self._target_size = _STATE_SIZE["idle"]
        self._update_expression()
        today = date.today()
        if self._last_greeting_day != today:
            self._last_greeting_day = today
            hour = datetime.now().hour
            if 6 <= hour <= 12:
                greeting = f"Good morning, {self._user_name}."
            elif hour < 18:
                greeting = f"Good afternoon, {self._user_name}."
            else:
                greeting = f"Good evening, {self._user_name}."
            self._greeting_text = greeting
            self._greeting_timer = 4.0
            if hasattr(self, '_on_greeting'):
                self._on_greeting(greeting)

    # ── State management ─────────────────────────────────────────────────────

    def set_state(self, state: BuddyState) -> None:
        self.state = state
        self._target_size = _STATE_SIZE.get(state.value, 80)
        self._update_expression()

    def set_emotional_state(self, state: str) -> None:
        self.emotional_state = state
        self._update_expression()

    def set_stance(self, stance_key: str) -> None:
        pose = STANCE_MAP.get(stance_key)
        if pose:
            self.stance_pose = pose
            self._stance_icon_timer = 2.5
            self.set_state(BuddyState.STANCE_POSE)
            if self._stance_timer:
                self._stance_timer.stop()
            self._stance_timer = QTimer(self)
            self._stance_timer.setSingleShot(True)
            self._stance_timer.timeout.connect(self._revert_stance)
            self._stance_timer.start(2500)

    def _revert_stance(self) -> None:
        self.stance_pose = None
        self.set_state(BuddyState.IDLE)

    def set_audio_rms(self, rms: float) -> None:
        self.audio_rms = min(1.0, max(0.0, rms))

    def record_voice_interaction(self) -> None:
        """Track a voice interaction for persistence."""
        self._buddy_state["total_interactions"] = self._buddy_state.get("total_interactions", 0) + 1
        self._buddy_state["last_interaction"] = datetime.now().isoformat()

    def set_screen_context(self, title: str) -> None:
        self._screen_context = title

    def set_user_name(self, name: str) -> None:
        self._user_name = name

    def show_proactive(self, text: str) -> None:
        self._proactive_text = text
        self._proactive_timer = 3.0
        self._proactive_scale = 0.0
        self.set_state(BuddyState.PROACTIVE)
        QTimer.singleShot(3000, lambda: self.set_state(BuddyState.IDLE))

    def _update_expression(self) -> None:
        key = (self.emotional_state, self.state.value)
        self._expression = _EXPRESSION_MAP.get(key, "neutral")

    # ═══════════════════════════════════════════════════════════════════════
    # PAINTING
    # ═══════════════════════════════════════════════════════════════════════

    def paintEvent(self, event) -> None:  # noqa: N802 — Qt naming
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        alpha = self._startup_alpha if self._startup_done is False else 1.0

        cx = self.width() / 2
        cy = self.height() / 2 + self._float_offset
        radius = self._size / 2

        # 0. Drop shadow
        self._draw_shadow(p, cx, cy, radius)

        # 1. Ambient glow (behind orb)
        self._draw_glow(p, cx, cy, radius)

        # 2. Glass orb body
        self._draw_body(p, cx, cy, radius)

        # 3. Mode ring
        self._draw_mode_indicator(p, cx, cy, radius)

        # 4. Particles (behind face for depth)
        self._draw_particles(p, cx, cy, radius)

        # 5. Face
        self._draw_face(p, cx, cy, radius)

        # 6. Audio visualizer (waveform trail)
        self._draw_audio_visualizer(p, cx, cy, radius)

        # 7. Stance icon
        if self.stance_pose and self._stance_icon_timer > 0:
            self._draw_stance_icon(p, cx, cy, radius)

        # 8. Proactive speech bubble
        if self._proactive_text and self._proactive_scale > 0.01:
            self._draw_speech_bubble(p, cx, cy, radius)

        # 9. Greeting text
        if self._greeting_text:
            self._draw_greeting(p, cx, cy, radius)

        # 10. Sleep Zzz
        if self.state == BuddyState.SLEEPING:
            self._draw_sleep_zzz(p, cx, cy, radius)

        # Startup alpha overlay
        if not self._startup_done:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(0, 0, 0, int(255 * (1.0 - alpha)))))
            p.drawRect(QRectF(0, 0, self.width(), self.height()))

        p.end()

    # ── Shadow ──────────────────────────────────────────────────────────────

    def _draw_shadow(self, p: QPainter, cx: float, cy: float, radius: float) -> None:
        """Draw drop shadow beneath orb."""
        shadow_y = cy + radius + 2
        shadow_r = radius * 0.6
        grad = QRadialGradient(cx, shadow_y, shadow_r)
        grad.setColorAt(0.0, QColor(0, 0, 0, 70))
        grad.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(grad))
        p.drawEllipse(QRectF(cx - shadow_r, shadow_y - shadow_r * 0.3,
                             shadow_r * 2, shadow_r * 0.6))

    # ── Glow ────────────────────────────────────────────────────────────────

    def _draw_glow(self, p: QPainter, cx: float, cy: float, radius: float) -> None:
        """Layered ambient glow: inner soft disc + outer halo."""
        colors = _ORB_COLORS.get(self.emotional_state, _ORB_COLORS["neutral"])
        glow_color = QColor(colors["glow"])

        # Inner glow
        inner_r = radius + 8
        inner_alpha = int((30 + self.audio_rms * 50) * self._tod_bright)
        inner_color = QColor(glow_color)
        inner_color.setAlpha(inner_alpha)
        grad = QRadialGradient(cx, cy, inner_r)
        grad.setColorAt(0.7, QColor(0, 0, 0, 0))
        grad.setColorAt(1.0, inner_color)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(grad))
        p.drawEllipse(QRectF(cx - inner_r, cy - inner_r, inner_r * 2, inner_r * 2))

        # Outer halo
        halo_r = radius + 15 + math.sin(self._time) * 3
        halo_alpha = int((15 + self.audio_rms * 35) * self._tod_bright)
        halo_color = QColor(glow_color)
        halo_color.setAlpha(halo_alpha)
        grad2 = QRadialGradient(cx, cy, halo_r)
        grad2.setColorAt(0.8, QColor(0, 0, 0, 0))
        grad2.setColorAt(1.0, halo_color)
        p.setBrush(QBrush(grad2))
        p.drawEllipse(QRectF(cx - halo_r, cy - halo_r, halo_r * 2, halo_r * 2))

    # ── Glass Orb Body ──────────────────────────────────────────────────────

    def _draw_body(self, p: QPainter, cx: float, cy: float, radius: float) -> None:
        """Translucent glass orb with core gradient, specular, and rim light."""
        colors = _ORB_COLORS.get(self.emotional_state, _ORB_COLORS["neutral"])

        # Breathing + speaking pulse
        breath = radius + math.sin(self._time * 2.0) * 1.5
        if self.state == BuddyState.SPEAKING:
            breath += self.audio_rms * 6.0

        # Step 1: Outer glass shell
        shell_grad = QRadialGradient(cx - breath * 0.25, cy - breath * 0.3, breath * 1.1)
        shell_top = QColor(220, 230, 255, int(45 * self._tod_bright))
        shell_bot = QColor(colors["core"].red(), colors["core"].green(), colors["core"].blue(),
                           int(25 * self._tod_bright))
        shell_grad.setColorAt(0.0, shell_top)
        shell_grad.setColorAt(0.7, shell_bot)
        shell_grad.setColorAt(1.0, QColor(colors["core"].red(), colors["core"].green(),
                                          colors["core"].blue(), int(15 * self._tod_bright)))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(shell_grad))
        p.drawEllipse(QRectF(cx - breath, cy - breath, breath * 2, breath * 2))

        # Step 2: Inner core (pulsing)
        core_r = breath * 0.65
        core_pulse = core_r + math.sin(self._time * 3.0) * 2.0
        core_color = QColor(colors["core"])
        core_color.setAlpha(int(140 * self._tod_bright))
        core_grad = QRadialGradient(cx, cy, core_pulse)
        core_grad.setColorAt(0.0, core_color)
        core_grad.setColorAt(0.6, QColor(colors["core"].red(), colors["core"].green(),
                                         colors["core"].blue(), int(40 * self._tod_bright)))
        core_grad.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setBrush(QBrush(core_grad))
        p.drawEllipse(QRectF(cx - core_pulse, cy - core_pulse,
                             core_pulse * 2, core_pulse * 2))

        # Step 3: Specular highlight (white crescent top-left)
        spec_x = cx - breath * 0.3
        spec_y = cy - breath * 0.35
        spec_r = breath * 0.35
        spec_grad = QRadialGradient(spec_x, spec_y, spec_r)
        spec_grad.setColorAt(0.0, QColor(255, 255, 255, int(100 * self._tod_bright)))
        spec_grad.setColorAt(0.5, QColor(255, 255, 255, int(30 * self._tod_bright)))
        spec_grad.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setBrush(QBrush(spec_grad))
        p.drawEllipse(QRectF(spec_x - spec_r, spec_y - spec_r,
                             spec_r * 2, spec_r * 1.4))

        # Step 4: Rim light at bottom edge
        rim_y = cy + breath * 0.85
        rim_r = breath * 0.4
        rim_color = QColor(colors["glow"])
        rim_color.setAlpha(int(40 * self._tod_bright))
        rim_grad = QRadialGradient(cx, rim_y, rim_r)
        rim_grad.setColorAt(0.0, rim_color)
        rim_grad.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setBrush(QBrush(rim_grad))
        p.drawEllipse(QRectF(cx - rim_r, rim_y - rim_r, rim_r * 2, rim_r * 2))

    # ── Face ────────────────────────────────────────────────────────────────

    def _draw_face(self, p: QPainter, cx: float, cy: float, radius: float) -> None:
        """Mature face with oval eyes, eyelids, curved eyebrows, and Bézier mouth."""
        eye_sep = radius * 0.35
        eye_y = cy - radius * 0.15
        eye_w = radius * 0.20
        eye_h = radius * 0.28

        # ── Eyelid system ──────────────────────────────────────────
        lid_close = 0.0
        if self.state == BuddyState.SLEEPING:
            lid_close = 1.0
        elif self._blink_phase > 0:
            # Blink: 0→1→0
            t = self._blink_phase
            if t > 0.5:
                lid_close = (t - 0.5) * 2.0  # closing
            else:
                lid_close = t * 2.0  # opening
            lid_close = min(1.0, max(0.0, lid_close))

        # ── Eyes (oval sclera with gradient) ───────────────────────
        for side in (-1, 1):
            ex = cx + side * eye_sep
            # Oval sclera (tilted 5° outward)
            p.save()
            p.translate(ex, eye_y)
            p.rotate(side * -5)
            # Sclera gradient for depth
            sclera_grad = QRadialGradient(0, 0, max(eye_w, eye_h))
            sclera_grad.setColorAt(0.0, QColor(255, 255, 255))
            sclera_grad.setColorAt(1.0, QColor(220, 225, 235))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(sclera_grad))
            p.drawEllipse(QRectF(-eye_w, -eye_h, eye_w * 2, eye_h * 2))
            p.restore()

        # ── Pupils ─────────────────────────────────────────────────
        if lid_close < 0.85:
            pupil_r = eye_w * 0.5
            for side in (-1, 1):
                ex = cx + side * eye_sep + self._pupil_offset.x()
                ey = eye_y + self._pupil_offset.y()
                # Dark pupil
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(QColor("#1a1a2e")))
                p.drawEllipse(QRectF(ex - pupil_r, ey - pupil_r,
                                     pupil_r * 2, pupil_r * 2))
                # Highlight dot
                hr = pupil_r * 0.25
                p.setBrush(QBrush(QColor(255, 255, 255, 200)))
                p.drawEllipse(QRectF(ex - pupil_r * 0.35 - hr,
                                     ey - pupil_r * 0.35 - hr,
                                     hr * 2, hr * 2))

        # ── Eyelids (arc covering top of eye) ─────────────────────
        if lid_close > 0.01:
            lid_color = QColor(20, 20, 40)  # dark orb-surface color
            lid_h = eye_h * 2 * lid_close
            for side in (-1, 1):
                ex = cx + side * eye_sep
                # Draw filled rect from top covering portion of eye
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(lid_color))
                top = eye_y - eye_h
                p.drawRect(QRectF(ex - eye_w - 1, top, eye_w * 2 + 2, lid_h))

        # ── Eyebrows (curved paths) ───────────────────────────────
        brow_y = eye_y - eye_h - radius * 0.08
        brow_w = eye_w * 0.9
        expr = self._expression

        brow_pen = QPen(QColor(255, 255, 255, 180), 2.2,
                        Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(brow_pen)
        p.setBrush(Qt.BrushStyle.NoBrush)

        for side in (-1, 1):
            ex = cx + side * eye_sep
            brow_path = QPainterPath()
            brow_path.moveTo(ex - brow_w, brow_y)

            # Default arch
            arch_y = brow_y - radius * 0.04

            if expr in ("annoyed", "intense"):
                # V-shape (closer together, angled down inward)
                arch_y = brow_y + radius * 0.02
                offset = side * radius * 0.03
                brow_path.cubicTo(
                    QPointF(ex - brow_w * 0.5, arch_y + offset),
                    QPointF(ex + brow_w * 0.3, arch_y + offset),
                    QPointF(ex + brow_w, brow_y - offset * 2),
                )
            elif expr in ("pensive",):
                # One raised, one lowered
                if side == -1:
                    arch_y = brow_y - radius * 0.08
                else:
                    arch_y = brow_y + radius * 0.04
                brow_path.cubicTo(
                    QPointF(ex - brow_w * 0.4, arch_y),
                    QPointF(ex + brow_w * 0.3, arch_y),
                    QPointF(ex + brow_w, brow_y + (arch_y - brow_y) * 0.5),
                )
            elif expr in ("happy", "excited", "peaceful", "gentle"):
                # Raised, gently arched up
                arch_y = brow_y - radius * 0.07
                brow_path.cubicTo(
                    QPointF(ex - brow_w * 0.4, arch_y),
                    QPointF(ex + brow_w * 0.3, arch_y),
                    QPointF(ex + brow_w, brow_y),
                )
            elif expr == "sleepy":
                arch_y = brow_y - radius * 0.02
                brow_path.cubicTo(
                    QPointF(ex - brow_w * 0.4, arch_y),
                    QPointF(ex + brow_w * 0.3, arch_y),
                    QPointF(ex + brow_w, brow_y),
                )
            else:
                # Neutral: slight natural arch
                brow_path.cubicTo(
                    QPointF(ex - brow_w * 0.4, arch_y),
                    QPointF(ex + brow_w * 0.3, arch_y),
                    QPointF(ex + brow_w, brow_y),
                )

            p.drawPath(brow_path)

        # ── Mouth (Bézier curve, opens when speaking) ──────────────
        mouth_y = cy + radius * 0.28
        mouth_w = radius * 0.30
        mouth_path = QPainterPath()
        mouth_path.moveTo(cx - mouth_w, mouth_y)

        ctrl_y = mouth_y
        if expr in ("happy", "excited", "peaceful", "gentle", "confident"):
            ctrl_y = mouth_y + radius * 0.10  # smile
        elif expr in ("annoyed", "intense", "downcast", "melancholy"):
            ctrl_y = mouth_y - radius * 0.06  # frown
        elif expr in ("engaged", "attentive"):
            ctrl_y = mouth_y + radius * 0.04  # slight smile
        elif expr == "sleepy":
            ctrl_y = mouth_y + radius * 0.02
        elif self.state == BuddyState.SPEAKING:
            # Mouth opens vertically proportional to audio_rms
            open_amt = self.audio_rms * radius * 0.18
            ctrl_y = mouth_y + open_amt

        mouth_path.cubicTo(
            QPointF(cx - mouth_w * 0.5, ctrl_y),
            QPointF(cx + mouth_w * 0.5, ctrl_y),
            QPointF(cx + mouth_w, mouth_y),
        )
        mouth_pen = QPen(QColor(255, 255, 255, 200), max(1.5, radius * 0.025),
                         Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(mouth_pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(mouth_path)

        # Inner mouth (darker) when speaking
        if self.state == BuddyState.SPEAKING and self.audio_rms > 0.15:
            inner_open = self.audio_rms * radius * 0.10
            inner_pen = QPen(QColor(60, 20, 30, 150), 1.0)
            p.setPen(inner_pen)
            inner_path = QPainterPath()
            inner_path.moveTo(cx - mouth_w * 0.6, mouth_y + 1)
            inner_path.cubicTo(
                QPointF(cx - mouth_w * 0.3, mouth_y + inner_open),
                QPointF(cx + mouth_w * 0.3, mouth_y + inner_open),
                QPointF(cx + mouth_w * 0.6, mouth_y + 1),
            )
            p.drawPath(inner_path)

    # ── Mode Ring Indicator ─────────────────────────────────────────────────

    def _draw_mode_indicator(self, p: QPainter, cx: float, cy: float, radius: float) -> None:
        """Thin ring outside the orb indicating state."""
        ring_r = radius + 5
        ring_w = 1.5

        pen_color: QColor
        pen_style = Qt.PenStyle.SolidLine

        if self.state == BuddyState.IDLE:
            # Dim crescent, slowly rotating
            pen_color = QColor(42, 42, 74, 80)
        elif self.state == BuddyState.LISTENING:
            # Pulsing green
            pulse = abs(math.sin(self._time * 4.0))  # 500ms pulse
            pen_color = QColor(76, 175, 80, int(120 + 135 * pulse))
        elif self.state == BuddyState.THINKING:
            # Amber dashed, rotating
            pen_color = QColor(255, 152, 0, 180)
            pen_style = Qt.PenStyle.DashLine
        elif self.state == BuddyState.SPEAKING:
            # Bright white ring with 8 segments lighting with audio_rms
            self._draw_speaking_ring(p, cx, cy, ring_r)
            return
        elif self.state == BuddyState.SLEEPING:
            pen_color = QColor(33, 150, 243, 60)
        else:
            pen_color = QColor(42, 42, 74, 60)

        pen = QPen(pen_color, ring_w, pen_style, Qt.PenCapStyle.RoundCap)
        if pen_style == Qt.PenStyle.DashLine:
            pen.setDashPattern([4, 4])
            # Rotate dash pattern for thinking
            pen.setDashOffset(int(self._time * 360) % 16)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(cx - ring_r, cy - ring_r, ring_r * 2, ring_r * 2))

    def _draw_speaking_ring(self, p: QPainter, cx: float, cy: float, ring_r: float) -> None:
        """Draw 8 radial segments lighting with audio_rms."""
        segments = 8
        seg_angle = 360.0 / segments
        for i in range(segments):
            base_angle = i * seg_angle + self._time * 60  # slow rotation
            intensity = max(0.3, self.audio_rms)
            alpha = int(80 + 175 * intensity * (0.7 + 0.3 * math.sin(
                self._time * 8 + i * 0.8)))
            pen = QPen(QColor(255, 255, 255, alpha), 2.0,
                       Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawArc(QRectF(cx - ring_r, cy - ring_r, ring_r * 2, ring_r * 2),
                      int(-base_angle * 16), int(-seg_angle * 0.6 * 16))

    # ── Audio Visualizer (Waveform Trail) ───────────────────────────────────

    def _draw_audio_visualizer(self, p: QPainter, cx: float, cy: float, radius: float) -> None:
        """Flowing sine wave emanating from the orb, active only when speaking."""
        if self.state != BuddyState.SPEAKING or self.audio_rms < 0.05:
            return

        amplitude = self.audio_rms * 12
        wave_len = 40.0

        for layer in range(3):
            layer_alpha = int((80 - layer * 20) * self._tod_bright * self.audio_rms)
            if layer_alpha <= 0:
                continue
            layer_r = radius + 10 + layer * 12
            pen = QPen(QColor(180, 200, 255, layer_alpha), 1.2)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)

            path = QPainterPath()
            first = True
            for deg in range(0, 361, 4):
                rad = math.radians(deg)
                # Sine wave offset from base radius
                wave = math.sin(rad * (2 * math.pi / wave_len) * layer_r + self._time * 6) * amplitude
                r = layer_r + wave
                x = cx + math.cos(rad) * r
                y = cy + math.sin(rad) * r
                if first:
                    path.moveTo(x, y)
                    first = False
                else:
                    path.lineTo(x, y)
            p.drawPath(path)

    # ── Particles ───────────────────────────────────────────────────────────

    def _draw_particles(self, p: QPainter, cx: float, cy: float, radius: float) -> None:
        """Draw orbiting energy orb particles with trails."""
        if not self._particles:
            return

        colors = _ORB_COLORS.get(self.emotional_state, _ORB_COLORS["neutral"])
        base_color = QColor(colors["glow"])

        for particle in self._particles:
            fade = particle.life / particle.max_life
            if fade <= 0:
                continue

            # Draw trail
            for i, (tx, ty, t_alpha) in enumerate(particle.trail):
                trail_fade = t_alpha * fade * 0.4
                trail_r = 2.0 - i * 0.4
                if trail_r < 0.5:
                    trail_r = 0.5
                trail_color = QColor(base_color)
                trail_color.setAlpha(int(trail_fade * 150))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(trail_color))
                px = cx + tx
                py = cy + ty
                p.drawEllipse(QRectF(px - trail_r, py - trail_r,
                                     trail_r * 2, trail_r * 2))

            # Draw particle
            px = cx + math.cos(particle.angle) * particle.radius
            py = cy + math.sin(particle.angle) * particle.radius
            p_r = 3.0 * fade
            p_color = QColor(base_color)
            p_color.setAlpha(int(fade * 200))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(p_color))
            p.drawEllipse(QRectF(px - p_r, py - p_r, p_r * 2, p_r * 2))

    # ── Stance Icon ─────────────────────────────────────────────────────────

    def _draw_stance_icon(self, p: QPainter, cx: float, cy: float, radius: float) -> None:
        """Draw mini 20x20 icon offset right of orb."""
        icon_x = cx + radius + 12
        icon_y = cy - 10
        fade = min(1.0, self._stance_icon_timer / 0.5)  # fade in first 0.5s
        alpha = int(200 * fade)

        pen = QPen(QColor(200, 200, 220, alpha), 1.8,
                   Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)

        pose = self.stance_pose
        if pose == "skeptical":
            # Magnifying glass
            p.drawEllipse(QRectF(icon_x - 5, icon_y - 5, 10, 10))
            p.drawLine(QPointF(icon_x + 3, icon_y + 3),
                       QPointF(icon_x + 8, icon_y + 8))
        elif pose == "concerned":
            # Shield
            shield_path = QPainterPath()
            shield_path.moveTo(icon_x, icon_y - 6)
            shield_path.lineTo(icon_x + 6, icon_y - 3)
            shield_path.lineTo(icon_x + 5, icon_y + 3)
            shield_path.cubicTo(
                QPointF(icon_x + 3, icon_y + 7),
                QPointF(icon_x - 3, icon_y + 7),
                QPointF(icon_x - 5, icon_y + 3),
            )
            shield_path.lineTo(icon_x - 6, icon_y - 3)
            shield_path.closeSubpath()
            p.drawPath(shield_path)
        elif pose == "approving":
            # Thumbs up
            p.drawLine(QPointF(icon_x, icon_y + 4), QPointF(icon_x, icon_y - 4))
            p.drawLine(QPointF(icon_x - 3, icon_y - 1), QPointF(icon_x + 3, icon_y - 1))
            p.drawEllipse(QRectF(icon_x - 1, icon_y - 7, 3, 4))
        elif pose == "passionate":
            # Flame spark
            flame = QPainterPath()
            flame.moveTo(icon_x, icon_y - 6)
            flame.cubicTo(
                QPointF(icon_x + 5, icon_y - 2),
                QPointF(icon_x + 3, icon_y + 4),
                QPointF(icon_x, icon_y + 6),
            )
            flame.cubicTo(
                QPointF(icon_x - 3, icon_y + 4),
                QPointF(icon_x - 5, icon_y - 2),
                QPointF(icon_x, icon_y - 6),
            )
            p.drawPath(flame)
        elif pose == "snarky":
            # Winking eye
            p.drawEllipse(QRectF(icon_x - 5, icon_y - 3, 10, 6))
            # Wink line
            wink_pen = QPen(QColor(200, 200, 220, alpha), 2.0)
            p.setPen(wink_pen)
            p.drawLine(QPointF(icon_x + 2, icon_y),
                       QPointF(icon_x + 6, icon_y - 1))
            p.drawLine(QPointF(icon_x + 6, icon_y - 1),
                       QPointF(icon_x + 10, icon_y))

    # ── Speech Bubble ───────────────────────────────────────────────────────

    def _draw_speech_bubble(self, p: QPainter, cx: float, cy: float, radius: float) -> None:
        """Frosted glass bubble above the orb."""
        text = self._proactive_text
        if not text:
            return

        scale = self._proactive_scale
        p.setFont(QFont("Segoe UI", 9))
        fm = p.fontMetrics()
        text_rect = fm.boundingRect(text)
        bw = text_rect.width() + 24
        bh = text_rect.height() + 14
        bx = cx - bw / 2
        by = cy - radius - bh - 20

        # Scale transform
        p.save()
        p.translate(cx, by + bh / 2)
        p.scale(scale, scale)
        p.translate(-cx, -(by + bh / 2))

        # Background (frosted dark)
        p.setPen(QPen(QColor(74, 111, 165, 100), 1))
        p.setBrush(QBrush(QColor(26, 26, 46, 215)))
        p.drawRoundedRect(QRectF(bx, by, bw, bh), 12, 12)

        # Small triangle tail
        tail_path = QPainterPath()
        tail_path.moveTo(cx - 5, by + bh)
        tail_path.lineTo(cx, by + bh + 10)
        tail_path.lineTo(cx + 5, by + bh)
        tail_path.closeSubpath()
        p.setBrush(QBrush(QColor(26, 26, 46, 215)))
        p.setPen(QPen(QColor(74, 111, 165, 100), 1))
        p.drawPath(tail_path)

        # Text
        p.setPen(QPen(QColor(230, 230, 230)))
        p.drawText(QRectF(bx, by, bw, bh), Qt.AlignmentFlag.AlignCenter, text)

        p.restore()

    # ── Greeting ────────────────────────────────────────────────────────────

    def _draw_greeting(self, p: QPainter, cx: float, cy: float, radius: float) -> None:
        """Draw greeting text below the orb."""
        p.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        alpha = int(255 * min(1.0, self._greeting_timer / 1.0))
        p.setPen(QPen(QColor(200, 200, 220, alpha)))
        p.drawText(QRectF(cx - 100, cy + radius + 25, 200, 30),
                   Qt.AlignmentFlag.AlignCenter, self._greeting_text)

    # ── Sleep Zzz ───────────────────────────────────────────────────────────

    def _draw_sleep_zzz(self, p: QPainter, cx: float, cy: float, radius: float) -> None:
        """Floating 'Zzz' for sleeping state."""
        p.setFont(QFont("Arial", max(8, int(radius * 0.3)), QFont.Weight.Bold))
        z_alpha = int(150 + 50 * math.sin(self._time * 2))
        p.setPen(QPen(QColor(180, 180, 220, z_alpha)))
        for i, size in enumerate([10, 14, 18]):
            offset_y = -radius * 0.3 - i * radius * 0.25
            offset_x = radius * 0.3 + i * radius * 0.15
            p.drawText(QRectF(cx + offset_x, cy + offset_y, 30, 20),
                       Qt.AlignmentFlag.AlignCenter, "z" if i == 0 else "Z")

    # ═══════════════════════════════════════════════════════════════════════
    # INTERACTION
    # ═══════════════════════════════════════════════════════════════════════

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self._last_mouse_time = time.time()
        self._is_fidgeting = False
        self._fidget_saccade = QPointF(0, 0)
        self._fidget_head_tilt = 0.0
        if event.button() == Qt.MouseButton.LeftButton:
            if self.state == BuddyState.SLEEPING:
                self._exit_sleep()
                return
            if self.state == BuddyState.PROACTIVE:
                self._proactive_text = ""
                self._proactive_scale = 0.0
                self.set_state(BuddyState.LISTENING)
                if hasattr(self, '_on_activation'):
                    self._on_activation()
                return
            if self.state == BuddyState.IDLE:
                self.set_state(BuddyState.LISTENING)
                if hasattr(self, '_on_activation'):
                    self._on_activation()
            elif self.state == BuddyState.LISTENING:
                self.set_state(BuddyState.IDLE)
                if hasattr(self, '_on_deactivation'):
                    self._on_deactivation()
            elif self.state == BuddyState.SPEAKING:
                if self.voice and hasattr(self.voice, 'stop_tts'):
                    self.voice.stop_tts()
                self.set_state(BuddyState.LISTENING)
            self._drag_offset = event.position()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_offset is not None:
            new_pos = event.globalPosition() - self._drag_offset
            self.move(int(new_pos.x()), int(new_pos.y()))
            self._last_mouse_time = time.time()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_offset = None

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        """Toggle dashboard."""
        if hasattr(self, '_on_double_click'):
            self._on_double_click()

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background-color: #1a1a2e; color: #e0e0e0; border: 1px solid #2a2a4a; }
            QMenu::item:selected { background-color: #4a6fa5; }
        """)
        act_dashboard = menu.addAction("Open Dashboard")
        act_mute = menu.addAction("Mute Microphone")
        menu.addSeparator()
        act_quit = menu.addAction("Quit Charlie")
        action = menu.exec(event.globalPos())
        if action == act_dashboard:
            if hasattr(self, '_on_double_click'):
                self._on_double_click()
        elif action == act_mute:
            if self.voice and hasattr(self.voice, 'stop'):
                self.voice.stop()
        elif action == act_quit:
            QApplication.quit()

    def enterEvent(self, event) -> None:  # noqa: N802
        self.setToolTip(f"State: {self.state.value} | Emotion: {self.emotional_state}\nScreen: {self._screen_context[:50]}")

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.setToolTip("")

    # ── Bridge wiring ────────────────────────────────────────────────────────

    def _connect_bridge(self) -> None:
        if not self.bridge:
            return
        bridge = self.bridge
        if hasattr(bridge, "emotional_state_changed") and hasattr(bridge.emotional_state_changed, "connect"):
            bridge.emotional_state_changed.connect(self.set_emotional_state)
        if hasattr(bridge, "audio_level") and hasattr(bridge.audio_level, "connect"):
            bridge.audio_level.connect(self.set_audio_rms)
        if hasattr(bridge, "stance_expressed") and hasattr(bridge.stance_expressed, "connect"):
            bridge.stance_expressed.connect(self.set_stance)
        if hasattr(bridge, "screen_context_changed") and hasattr(bridge.screen_context_changed, "connect"):
            bridge.screen_context_changed.connect(self.set_screen_context)
        if hasattr(bridge, "screen_category_changed") and hasattr(bridge.screen_category_changed, "connect"):
            bridge.screen_category_changed.connect(self.set_screen_category)
        if hasattr(bridge, "proactive_remark") and hasattr(bridge.proactive_remark, "connect"):
            bridge.proactive_remark.connect(self.show_proactive)
        if hasattr(bridge, "greeting_ready") and hasattr(bridge.greeting_ready, "connect"):
            bridge.greeting_ready.connect(lambda t: setattr(self, '_greeting_text', t) or setattr(self, '_greeting_timer', 4.0))

    def set_activation_callback(self, cb) -> None:
        self._on_activation = cb

    def set_deactivation_callback(self, cb) -> None:
        self._on_deactivation = cb

    def set_double_click_callback(self, cb) -> None:
        self._on_double_click = cb

    def set_greeting_callback(self, cb) -> None:
        self._on_greeting = cb

    # ── Emotional persistence ────────────────────────────────────────────────

    def _load_state(self) -> dict:
        """Load buddy state from disk."""
        default = {
            "session_count": 0,
            "total_interactions": 0,
            "last_emotion": "neutral",
            "last_stance": None,
            "last_interaction": None,
            "favorite_topics": [],
        }
        try:
            if os.path.exists(self._state_path):
                with open(self._state_path, "r") as f:
                    saved = json.load(f)
                default.update(saved)
                logger.info(f"Buddy state loaded: session #{default['session_count']}")
        except Exception as e:
            logger.warning(f"Buddy state load failed: {e}")
        # Always increment session count
        default["session_count"] = default.get("session_count", 0) + 1
        return default

    def _save_state(self) -> None:
        """Persist buddy state to disk."""
        try:
            os.makedirs(os.path.dirname(self._state_path), exist_ok=True)
            self._buddy_state["last_emotion"] = self.emotional_state
            self._buddy_state["last_stance"] = self.stance_pose
            self._buddy_state["last_interaction"] = datetime.now().isoformat()
            with open(self._state_path, "w") as f:
                json.dump(self._buddy_state, f, indent=2)
        except Exception as e:
            logger.warning(f"Buddy state save failed: {e}")

    def closeEvent(self, event) -> None:  # noqa: N802
        """Save state on close."""
        self._save_state()
        super().closeEvent(event)

    def set_screen_category(self, category: str) -> None:
        """React to screen category changes (from widget_bridge signal)."""
        self._screen_context = category

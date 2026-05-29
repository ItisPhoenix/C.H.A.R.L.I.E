import logging
import time
from collections import deque

from charlie.perception.world_model import WorldModel

logger = logging.getLogger("charlie.intelligence.frustration")

class FrustrationDetector:
    """
    Analyzes system events to detect user frustration.
    Tracks error repetition and rapid window switching.
    """
    def __init__(self, world_model: WorldModel):
        self.world = world_model
        # Store last 10 errors and window switches with timestamps
        self.error_history = deque(maxlen=10)
        self.window_history = deque(maxlen=20)
        self.last_decay_time = time.time()
        self.last_switch_alert = 0.0

    def process_error(self, error_text: str):
        """Called when an error is detected (e.g. by Vision Sentinel or logs)."""
        if not error_text: return

        now = time.time()
        self.error_history.append((now, error_text))

        # Check for ≥ 3 similar errors in last 90s
        recent_errors = [text for t, text in self.error_history if now - t < 90]
        if recent_errors.count(error_text) >= 3:
            logger.warning(f"frustration_detected | repeated_error | {error_text}")
            self.world.frustration_score = min(1.0, self.world.frustration_score + 0.3)
            self.world.last_error_text = error_text
            self.world.error_count_last_60s = len(recent_errors)

    def process_window_switch(self, window_title: str):
        """Called by ACE when the active window changes."""
        if not window_title: return

        now = time.time()
        # Avoid double-counting rapid polls of same window
        if self.window_history and self.window_history[-1][1] == window_title:
            return

        self.window_history.append((now, window_title))

        # Check for rapid switching: > 5 switches in 30s
        recent_switches = [title for t, title in self.window_history if now - t < 30]
        if len(recent_switches) > 5 and now - self.last_switch_alert > 30:
            logger.warning("frustration_detected | rapid_window_switching")
            self.world.frustration_score = min(1.0, self.world.frustration_score + 0.2)
            self.last_switch_alert = now

    def update(self):
        """Periodic maintenance: decay frustration score over time."""
        now = time.time()
        # Decay: 0.1 per minute (0.00167 per second)
        elapsed = now - self.last_decay_time
        if elapsed > 10:  # Update every 10s
            decay_amount = (elapsed / 60.0) * 0.1
            self.world.frustration_score = max(0.0, self.world.frustration_score - decay_amount)
            self.last_decay_time = now

            # Update error count in world model based on last 60s
            recent_errors = [t for t, _ in self.error_history if now - t < 60]
            self.world.error_count_last_60s = len(recent_errors)

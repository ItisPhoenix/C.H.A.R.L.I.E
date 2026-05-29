"""
C.H.A.R.L.I.E. — Activity Sentinel
Monitors user interaction patterns to detect frustration and proactive help triggers.
"""

import contextlib
import os
import threading
import time

from pynput import keyboard, mouse

from charlie.utils.logger import get_logger

logger = get_logger("ActivitySentinel")

@contextlib.contextmanager
def silence_stderr():
    """Redirects stderr to os.devnull. Useful for muffling driver logs (VCAMDS)."""
    if os.name != 'nt':
        yield
        return

    try:
        # Save original stderr
        orig_stderr = os.dup(2)
        null_fd = os.open('NUL', os.O_WRONLY)
        os.dup2(null_fd, 2)
        try:
            yield
        finally:
            os.dup2(orig_stderr, 2)
            os.close(null_fd)
            os.close(orig_stderr)
    except Exception:
        yield

class ActivitySentinel:
    def __init__(self, brain_task_q, status_q, heartbeat):
        self.brain_task_q = brain_task_q
        self.status_q = status_q
        self.heartbeat = heartbeat
        self.running = True

        self.click_history = []  # [(timestamp, x, y)]
        self.scroll_history = [] # [(timestamp, dy)]
        self.frustration_score = 0.0
        self.last_proactive_event = 0.0
        self.startup_time = time.time()
        self.grace_period = 60.0  # Ignore frustration for first 60s
        self._lock = threading.Lock()
        self._memory = None
        self._poll_interval = 2.0

        # Input listeners
        self.mouse_listener = mouse.Listener(on_click=self._on_click, on_scroll=self._on_scroll)
        self.kb_listener = keyboard.Listener(on_press=self._on_key)

        # Vision settings
        self.face_cascade = None
        self.cap = None
        self.presence_detected = False

        try:
            import cv2
            cascade_path = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
            self.face_cascade = cv2.CascadeClassifier(cascade_path)
        except Exception as e:
            logger.debug(f"cv2_face_cascade_init_skipped | {e}")

    def _on_click(self, x, y, button, pressed):
        if pressed:
            now = time.time()
            with self._lock:
                self.click_history.append((now, x, y))
            self._analyze_behavior()

    def _on_scroll(self, x, y, dx, dy):
        now = time.time()
        with self._lock:
            self.scroll_history.append((now, dy))
        self._analyze_behavior()

    def _on_key(self, key):
        # Detect rapid Escape key presses or similar frustration markers
        pass

    def _analyze_behavior(self):
        now = time.time()
        if now - self.startup_time < self.grace_period:
            return

        with self._lock:
            # Prune older than 15s
            self.click_history = [c for c in self.click_history if now - c[0] < 15]
            self.scroll_history = [s for s in self.scroll_history if now - s[0] < 15]

            click_count = len(self.click_history)
            scroll_count = len(self.scroll_history)

        # 1. Rapid repetitive clicks (UI Stuck/Frustration) - Threshold: 12 clicks in 15s
        if click_count >= 12:
            with self._lock:
                avg_x = sum(c[1] for c in self.click_history) / click_count
                avg_y = sum(c[2] for c in self.click_history) / click_count
                variance = sum((c[1]-avg_x)**2 + (c[2]-avg_y)**2 for c in self.click_history) / click_count

            if variance < 2500: # Clicks within 50px radius
                self.frustration_score += 0.15 # Reduced increment
                logger.debug(f"behavior_alert | repetitive_clicks | variance={variance:.1f}")

        # 2. Hyper-scrolling - Threshold: 40 scroll events in 15s
        if scroll_count > 40:
            self.frustration_score += 0.10
            if not getattr(self, "_scroll_alerted", False):
                logger.debug("behavior_alert | aggressive_scrolling")
                self._scroll_alerted = True
        else:
            self._scroll_alerted = False

        if self.frustration_score >= 1.0:
            self._trigger_proactive_help("behavioral_frustration")

    def _trigger_proactive_help(self, reason):
        now = time.time()
        if now - self.last_proactive_event < 60: # Limit to once per minute
            return

        self.last_proactive_event = now
        self.frustration_score = 0.0

        # IMPORTANT: Clear history to prevent immediate re-trigger from same window
        with self._lock:
            self.click_history = []
            self.scroll_history = []

        logger.info(f"proactive_help_triggered | reason={reason}")
        self.brain_task_q.put({
            "type": "PROACTIVE_HELP",
            "content": reason
        })

    def _vision_presence_check(self):
        """Background thread for low-frequency face detection."""
        try:
            import cv2
        except ImportError:
            logger.warning("vision_presence_check_skipped | opencv-python not installed")
            return

        try:
            if not self.face_cascade:
                cascade_path = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
                self.face_cascade = cv2.CascadeClassifier(cascade_path)

            # Use CAP_DSHOW and silence driver noise (VCAMDS/NBX hive)
            with silence_stderr():
                self.cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)

            if not self.cap.isOpened():
                logger.warning("vision_cap_failed | no_camera_found")
                return

            while self.running:
                ret, frame = self.cap.read()
                if not ret:
                    time.sleep(2)
                    continue

                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                faces = self.face_cascade.detectMultiScale(gray, 1.3, 5)

                prev_presence = self.presence_detected
                self.presence_detected = len(faces) > 0

                if self.presence_detected and not prev_presence:
                    logger.info("user_presence_detected | face_found")
                    self.status_q.put({"type": "PRESENCE", "content": True})
                elif not self.presence_detected and prev_presence:
                    logger.info("user_presence_lost")
                    self.status_q.put({"type": "PRESENCE", "content": False})

                time.sleep(1) # Check every 1 second to save CPU
        except Exception as e:
            logger.error(f"vision_loop_error | {e}", exc_info=True)
        finally:
            if self.cap:
                try:
                    self.cap.release()
                except Exception:
                    pass

    def stop(self):
        """Gracefully shut down all listeners and loops."""
        logger.info("activity_sentinel_stopping")
        self.running = False
        if self.mouse_listener:
            self.mouse_listener.stop()
        if self.kb_listener:
            self.kb_listener.stop()

    def run(self):
        logger.info("activity_sentinel_started")
        try:
            self.mouse_listener.start()
            self.kb_listener.start()

            vision_thread = threading.Thread(target=self._vision_presence_check, daemon=True)
            vision_thread.start()

            while self.running:
                # Heartbeat to Phoenix Supervisor
                self.heartbeat.value = time.time()

                # Slow decay of frustration over time
                self.frustration_score = max(0, self.frustration_score - 0.05)
                time.sleep(self._poll_interval)
        except Exception as e:
            logger.exception(f"activity_sentinel_runtime_error | {e}")
        finally:
            self.stop()

    def set_memory(self, memory) -> None:
        """Set the MemoryCoordinator for activity logging."""
        self._memory = memory

    def log_activity_event(self, event_type: str, details: str) -> None:
        """Log an activity event to episodic memory via MemoryCoordinator."""
        if hasattr(self, '_memory') and self._memory:
            try:
                self._memory.episodic.store_episode(
                    f"activity_{int(time.time())}",
                    f"[{event_type}] {details}",
                    {"type": "activity", "event": event_type},
                )
            except Exception as e:
                logger.debug("activity_log_failed | error=%s", e)

    def set_poll_interval(self, seconds: float) -> None:
        """Set the polling interval for activity monitoring."""
        self._poll_interval = max(1.0, min(seconds, 60.0))
        logger.info("poll_interval_set | seconds=%.1f", self._poll_interval)

    def check_frustration(self, recent_errors: list[str]) -> bool:
        """Check if recent activity suggests user frustration.

        Returns True if frustration detected (rapid clicks, repeated errors, etc.)
        """
        # Check for rapid error repetition
        if len(recent_errors) >= 3:
            # If same error appears 3+ times, user is likely frustrated
            unique_errors = set(recent_errors)
            if len(unique_errors) < len(recent_errors) * 0.5:
                logger.info("frustration_detected | repeated_errors=%d", len(recent_errors))
                return True
        return False

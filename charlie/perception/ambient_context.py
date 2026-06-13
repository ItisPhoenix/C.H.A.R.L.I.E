import logging
import threading
import time
from typing import Optional

try:
    import pygetwindow as gw
except ImportError:
    gw = None  # type: ignore

from charlie.intelligence.pattern_tracker import PatternTracker
from charlie.intelligence.task_inferrer import TaskInferrer
from charlie.perception.idle import get_idle_watcher
from charlie.perception.world_model import WorldModel

logger = logging.getLogger("charlie.perception.ace")


class AmbientContextEngine:
    """
    ACE: Ambient Context Engine.
    Monitors the user's environment (windows, idle time, apps) and updates WorldModel.
    """

    def __init__(self, world_model: WorldModel):
        self.world = world_model
        self.inferrer = TaskInferrer()
        self.tracker = PatternTracker()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_active_window = ""
        self._last_pattern_log = 0.0

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        logger.info("ace_ignited")

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)
        logger.info("ace_halted")

    def _monitor_loop(self):
        while not self._stop_event.is_set():
            try:
                self._update_state()
            except Exception as e:
                logger.error(f"ace_update_failed | {e}")
            time.sleep(2)

    def _update_state(self):
        if gw is None:
            return
        # 1. Windows & App Info
        try:
            active_win = gw.getActiveWindow()
            if active_win:
                title = active_win.title
                self.world.active_app = self._infer_app_name(title)
                self.world.active_file = self._infer_file_name(title)

                if title != self._last_active_window:
                    self.detector.process_window_switch(title)
                    self._last_active_window = title

            # Fetch top window titles for context
            from charlie.utils.system import get_visible_window_titles

            self.world.open_windows = get_visible_window_titles()
        except Exception as e:
            logger.debug(f"ace_window_scan_fail | {e}")

        # 2. Idle Time (read from centralized IdleWatcher)
        self.world.user_idle_seconds = get_idle_watcher().get_idle_duration()

        # 3. Frustration Decay
        self.detector.update()

        # 4. Task Inference
        self.world.current_task_inferred = self.inferrer.get_task_summary(
            self.world.active_app, self.world.open_windows, self.world.user_idle_seconds
        )

        # 5. Pattern Tracking (every 5 min)
        now = time.time()
        if now - self._last_pattern_log > 300:
            self.tracker.log_event(self.world.active_app, self.world.active_file, self.world.current_task_inferred)
            self._last_pattern_log = now

        # 6. Timestamp
        self.world.last_updated = now

    def _infer_app_name(self, title: str) -> str:
        if "Visual Studio Code" in title:
            return "VS Code"
        if "Chrome" in title:
            return "Chrome"
        if "Discord" in title:
            return "Discord"
        if "Spotify" in title:
            return "Spotify"
        if "Terminal" in title or "PowerShell" in title:
            return "Terminal"
        # Fallback: take last part of title if it contains " - "
        if " - " in title:
            return title.split(" - ")[-1].strip()
        return title[:30]

    def _infer_file_name(self, title: str) -> Optional[str]:
        # Heuristic for VS Code, Notepad++, etc.
        if " - " in title:
            parts = title.split(" - ")
            if len(parts) > 1:
                potential_file = parts[0].strip()
                if "." in potential_file:
                    return potential_file
        return None

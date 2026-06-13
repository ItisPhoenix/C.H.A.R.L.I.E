import logging
import time
from typing import List

logger = logging.getLogger("charlie.intelligence.task_inferrer")

TASK_SIGNATURES = {
    "Development": ["code", "visual studio", "py", "git", "terminal", "powershell", "cmd", "github", "stack overflow"],
    "Research": ["chrome", "edge", "firefox", "browser", "search", "arxiv", "wiki", "documentation"],
    "Entertainment": ["spotify", "vlc", "youtube", "netflix", "twitch", "game", "steam"],
    "Communication": ["discord", "slack", "teams", "zoom", "meet", "outlook", "gmail", "telegram"],
    "Design": ["photoshop", "figma", "canvas", "blender", "inkscape"],
    "Writing": ["word", "notepad", "obsidian", "overleaf", "latex"],
}


class TaskInferrer:
    """
    TaskInferrer: Inferred Task Detection.
    Maps system activity to high-level tasks and tracks their duration.
    """

    def __init__(self):
        self.current_task = "Idle"
        self.task_start_time = time.time()
        self.last_active_app = ""

    def infer_task(self, active_app: str, window_titles: List[str], idle_sec: float) -> str:
        """
        Returns inferred task label based on app and window context.
        """
        if idle_sec > 600:
            return "Away"
        if idle_sec > 60:
            return "Idle"

        app_lower = active_app.lower()
        titles_lower = " ".join(window_titles).lower()
        context = f"{app_lower} {titles_lower}"

        for task, keywords in TASK_SIGNATURES.items():
            if any(k in context for k in keywords):
                return task

        return "General"

    def get_task_summary(self, active_app: str, window_titles: List[str], idle_sec: float) -> str:
        """
        Returns string: "TASK: Category · Duration"
        """
        new_task = self.infer_task(active_app, window_titles, idle_sec)

        if new_task != self.current_task:
            duration = int(time.time() - self.task_start_time)
            if duration > 10:
                logger.info(f"task_transition | {self.current_task} -> {new_task} | lasted={duration}s")
            self.current_task = new_task
            self.task_start_time = time.time()

        elapsed_min = int((time.time() - self.task_start_time) / 60)
        return f"{self.current_task} · {elapsed_min} min"

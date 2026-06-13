import time
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class WorldModel:
    """
    Central state for Charlie's perception of the user's environment.
    Updated by AmbientContextEngine (ACE).
    """

    active_app: str = "Unknown"
    active_file: Optional[str] = None
    open_windows: List[str] = field(default_factory=list)
    error_count_last_60s: int = 0
    last_error_text: str = ""
    user_idle_seconds: float = 0.0
    current_task_inferred: str = "Idle"
    frustration_score: float = 0.0  # 0.0 to 1.0
    last_updated: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "active_app": self.active_app,
            "active_file": self.active_file,
            "open_windows": self.open_windows,
            "error_count": self.error_count_last_60s,
            "idle_sec": round(self.user_idle_seconds, 1),
            "task": self.current_task_inferred,
            "frustration": round(self.frustration_score, 2),
            "updated": self.last_updated,
        }

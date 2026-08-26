"""Safe, typed runtime health for Charlie subsystems."""

import re
from enum import StrEnum
from typing import Dict, Optional, Tuple


class HealthStatus(StrEnum):
    """Public state of one independently-started subsystem."""

    STARTING = "starting"
    RUNNING = "running"
    DEGRADED = "degraded"
    STOPPED = "stopped"
    DISABLED = "disabled"


_PUBLIC_DETAILS: Dict[HealthStatus, str] = {
    HealthStatus.STARTING: "Starting",
    HealthStatus.RUNNING: "Running",
    HealthStatus.DEGRADED: "Unavailable",
    HealthStatus.STOPPED: "Stopped",
    HealthStatus.DISABLED: "Disabled",
}


class HealthRegistry:
    """In-memory health snapshot that never exposes exception text."""

    def __init__(self, names: Tuple[str, ...]) -> None:
        self._health: Dict[str, HealthStatus] = {name: HealthStatus.DISABLED for name in names}
        self._public_details: Dict[str, str] = {}

    def set(
        self,
        name: str,
        status: HealthStatus,
        detail: Optional[str] = None,
        *,
        public_detail: Optional[str] = None,
    ) -> None:
        """Record one transition; only explicitly safe public detail is exposed."""
        if name not in self._health:
            raise ValueError(f"Unknown subsystem: {name}")
        self._health[name] = status
        if public_detail is None:
            self._public_details.pop(name, None)
        else:
            safe_detail = re.sub(
                r"(?i)(api[-_ ]?key|token|password|secret)\s*[:=]\s*[^\s,;]+",
                r"\1=redacted",
                str(public_detail),
            ).strip()
            self._public_details[name] = safe_detail[:240] or _PUBLIC_DETAILS[status]

    def snapshot(self) -> Dict[str, Dict[str, str]]:
        """Return a JSON-safe public snapshot."""
        return {
            name: {"status": status.value, "detail": self._public_details.get(name, _PUBLIC_DETAILS[status])}
            for name, status in self._health.items()
        }

    def event(self) -> Dict[str, Dict[str, Dict[str, str]] | str]:
        """Return the typed IPC event for the current public snapshot."""
        return {"type": "subsystem_health", "payload": self.snapshot()}

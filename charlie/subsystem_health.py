"""Safe, typed runtime health for Charlie subsystems."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Dict, Iterable, Optional

RUNTIME_TRUTH_SCHEMA_VERSION = 1


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


_UNSET = object()


class HealthStatus(StrEnum):
    """Public state of one independently-started subsystem."""

    STARTING = "starting"
    RUNNING = "running"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"
    DISABLED = "disabled"
    SHUTTING_DOWN = "shutting_down"
    STOPPED = "stopped"


class RuntimeStatus(StrEnum):
    """Aggregate lifecycle/health state owned by the main runtime."""

    RUNNING = "running"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    SHUTTING_DOWN = "shutting_down"
    STOPPED = "stopped"


_PUBLIC_DETAILS: Dict[HealthStatus, str] = {
    HealthStatus.STARTING: "Starting",
    HealthStatus.RUNNING: "Running",
    HealthStatus.DEGRADED: "Unavailable",
    HealthStatus.UNAVAILABLE: "Unavailable",
    HealthStatus.UNKNOWN: "Unknown",
    HealthStatus.SHUTTING_DOWN: "Shutting down",
    HealthStatus.STOPPED: "Stopped",
    HealthStatus.DISABLED: "Disabled",
}


class HealthRegistry:
    """Main-owned subsystem health and aggregate runtime-truth authority.

    ``snapshot()`` and ``event()`` retain the legacy per-subsystem contract.
    ``runtime_snapshot()`` is the richer revisioned contract consumed across
    process boundaries; no reader is allowed to recompute its aggregate.
    """

    def __init__(self, names: Iterable[str], *, launch_id: str = "") -> None:
        names = tuple(names)
        self._health: Dict[str, HealthStatus] = {name: HealthStatus.DISABLED for name in names}
        self._public_details: Dict[str, str] = {}
        observed_at = _utc_now()
        self._metadata: Dict[str, Dict[str, Any]] = {
            name: {
                "enabled": False,
                "required": False,
                "static_available": None,
                "live_ready": None,
                "evidence_authority": "main_runtime",
                "observed_at": observed_at,
            }
            for name in names
        }
        self._configured: set[str] = set()
        self._launch_id = str(launch_id or "")
        self._revision = 0
        self._observed_at = observed_at
        self._lifecycle: Optional[RuntimeStatus] = None
        self._shutdown_failed = False

    @property
    def launch_id(self) -> str:
        return self._launch_id

    @property
    def revision(self) -> int:
        return self._revision

    def _touch(self, name: Optional[str] = None) -> None:
        self._revision += 1
        self._observed_at = _utc_now()
        if name is not None:
            self._metadata[name]["observed_at"] = self._observed_at

    @staticmethod
    def _inferred_live_ready(status: HealthStatus) -> Optional[bool]:
        if status == HealthStatus.RUNNING:
            return True
        if status in {HealthStatus.DEGRADED, HealthStatus.UNAVAILABLE, HealthStatus.STOPPED, HealthStatus.DISABLED}:
            return False
        return None

    def configure_subsystem(
        self,
        name: str,
        *,
        enabled: bool,
        required: bool = False,
        static_available: Optional[bool] = None,
        evidence_authority: str = "main_runtime",
    ) -> None:
        """Set policy/static metadata without treating static availability as readiness."""
        if name not in self._health:
            raise ValueError(f"Unknown subsystem: {name}")
        metadata = self._metadata[name]
        metadata["enabled"] = bool(enabled)
        metadata["required"] = bool(required)
        metadata["static_available"] = static_available
        metadata["evidence_authority"] = str(evidence_authority or "main_runtime")
        self._configured.add(name)
        if not enabled:
            self._health[name] = HealthStatus.DISABLED
            metadata["live_ready"] = False
            self._public_details.pop(name, None)
        elif self._health[name] == HealthStatus.DISABLED:
            self._health[name] = (
                HealthStatus.UNAVAILABLE if static_available is False else HealthStatus.UNKNOWN
            )
            metadata["live_ready"] = False if static_available is False else None
            self._public_details.pop(name, None)
        self._touch(name)

    configure = configure_subsystem

    def set(
        self,
        name: str,
        status: HealthStatus,
        detail: Optional[str] = None,
        *,
        public_detail: Optional[str] = None,
        live_ready: Any = _UNSET,
        evidence_authority: Optional[str] = None,
    ) -> None:
        """Record one transition; only explicitly safe public detail is exposed."""
        if name not in self._health:
            raise ValueError(f"Unknown subsystem: {name}")
        status = HealthStatus(status)
        if name not in self._configured and status != HealthStatus.DISABLED:
            self._metadata[name]["enabled"] = True
        if live_ready is _UNSET:
            inferred_live_ready = self._inferred_live_ready(status)
        else:
            inferred_live_ready = live_ready
            if status == HealthStatus.RUNNING and live_ready is None:
                status = HealthStatus.UNKNOWN
            elif status == HealthStatus.RUNNING and live_ready is False:
                status = HealthStatus.DEGRADED
        self._health[name] = status
        self._metadata[name]["live_ready"] = inferred_live_ready
        if evidence_authority is not None:
            self._metadata[name]["evidence_authority"] = str(evidence_authority)
        if public_detail is None:
            self._public_details.pop(name, None)
        else:
            safe_detail = re.sub(
                r"(?i)(api[-_ ]?key|token|password|secret)\s*[:=]\s*[^\s,;]+",
                r"\1=redacted",
                str(public_detail),
            ).strip()
            self._public_details[name] = safe_detail[:240] or _PUBLIC_DETAILS[status]
        self._touch(name)

    def set_runtime_lifecycle(self, status: Optional[RuntimeStatus]) -> None:
        """Set main-owned aggregate lifecycle without changing subsystem evidence."""
        self._lifecycle = None if status is None else RuntimeStatus(status)
        if self._lifecycle == RuntimeStatus.STOPPED:
            self._shutdown_failed = False
        self._touch()

    def mark_shutdown_failed(self) -> None:
        """Keep an incomplete shutdown degraded instead of falsely stopped."""
        self._lifecycle = None
        self._shutdown_failed = True
        self._touch()

    def snapshot(self) -> Dict[str, Dict[str, str]]:
        """Return a JSON-safe public snapshot."""
        return {
            name: {"status": status.value, "detail": self._public_details.get(name, _PUBLIC_DETAILS[status])}
            for name, status in self._health.items()
        }

    def runtime_snapshot(self) -> Dict[str, Any]:
        """Return canonical main-owned aggregate truth and evidence metadata."""
        subsystems: Dict[str, Dict[str, Any]] = {}
        for name, status in self._health.items():
            metadata = self._metadata[name]
            subsystems[name] = {
                "enabled": bool(metadata["enabled"]),
                "required": bool(metadata["required"]),
                "static_available": metadata["static_available"],
                "live_ready": metadata["live_ready"],
                "status": status.value,
                "detail": self._public_details.get(name, _PUBLIC_DETAILS[status]),
                "evidence_authority": metadata["evidence_authority"],
                "observed_at": metadata["observed_at"],
            }

        required_failures = [
            name
            for name, item in subsystems.items()
            if item["required"]
            and (
                item["status"] != HealthStatus.RUNNING.value
                or item["live_ready"] is not True
            )
        ]
        if self._lifecycle is not None:
            aggregate_status = self._lifecycle.value
        elif self._shutdown_failed:
            aggregate_status = RuntimeStatus.DEGRADED.value
        elif any(
            subsystems[name]["status"] == HealthStatus.UNAVAILABLE.value
            for name in required_failures
        ):
            aggregate_status = RuntimeStatus.UNAVAILABLE.value
        elif required_failures:
            aggregate_status = RuntimeStatus.DEGRADED.value
        else:
            aggregate_status = RuntimeStatus.RUNNING.value

        return {
            "schema_version": RUNTIME_TRUTH_SCHEMA_VERSION,
            "authority": "main_runtime",
            "launch_id": self._launch_id,
            "revision": self._revision,
            "observed_at": self._observed_at,
            "status": aggregate_status,
            "required_failures": required_failures,
            "subsystems": subsystems,
        }

    def event(self) -> Dict[str, Dict[str, Dict[str, str]] | str]:
        """Return the typed IPC event for the current public snapshot."""
        return {"type": "subsystem_health", "payload": self.snapshot()}

    def runtime_event(self) -> Dict[str, Any]:
        """Return the revisioned IPC event for canonical runtime truth."""
        return {"type": "runtime_truth", "payload": self.runtime_snapshot()}

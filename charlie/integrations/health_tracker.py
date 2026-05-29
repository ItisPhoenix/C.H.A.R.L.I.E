"""
charlie/integrations/health_tracker.py

IntegrationHealthTracker — Tracks health/auth/capabilities of all integrations.
"""

import time
from dataclasses import dataclass, field

from charlie.utils.logger import get_logger

logger = get_logger("HealthTracker")


@dataclass
class IntegrationHealth:
    """Health status of a single integration."""
    name: str
    status: str = "disconnected"  # "connected", "disconnected", "error", "auth_expired"
    last_connected: float | None = None
    last_sync: float | None = None
    last_error: str | None = None
    capabilities: list[str] = field(default_factory=list)
    auth_method: str = "unknown"  # "oauth", "token", "api_key", "none"
    auth_expires: float | None = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status,
            "last_connected": self.last_connected,
            "last_sync": self.last_sync,
            "last_error": self.last_error,
            "capabilities": self.capabilities,
            "auth_method": self.auth_method,
            "auth_expires": self.auth_expires,
        }


class IntegrationHealthTracker:
    """
    Tracks health of all CHARLIE integrations.

    Usage:
        tracker = IntegrationHealthTracker()
        tracker.register(gmail_integration)
        tracker.check_health("Gmail")
        all_health = tracker.get_all_health()
    """

    def __init__(self):
        self._integrations: dict[str, IntegrationHealth] = {}
        self._instances: dict[str, object] = {}

    def register(self, integration, capabilities: list[str] = None, auth_method: str = "unknown"):
        """Register an integration for health tracking."""
        name = getattr(integration, 'name', integration.__class__.__name__)
        self._integrations[name] = IntegrationHealth(
            name=name,
            capabilities=capabilities or ["fetch"],
            auth_method=auth_method,
        )
        self._instances[name] = integration
        logger.info(f"integration_registered | {name}")

    def check_health(self, name: str) -> IntegrationHealth:
        """Check current health of an integration."""
        health = self._integrations.get(name)
        if not health:
            return IntegrationHealth(name=name, status="unknown")

        integration = self._instances.get(name)
        if not integration:
            health.status = "disconnected"
            return health

        try:
            connected = integration.connect()
            if connected:
                health.status = "connected"
                health.last_connected = time.time()
                health.last_error = None
            else:
                health.status = "auth_expired"
        except Exception as e:
            health.status = "error"
            health.last_error = str(e)[:200]
            logger.warning(f"integration_health_error | {name} | {e}")

        return health

    def check_all(self) -> list[IntegrationHealth]:
        """Check health of all registered integrations."""
        results = []
        for name in self._integrations:
            results.append(self.check_health(name))
        return results

    def get_health(self, name: str) -> IntegrationHealth | None:
        """Get cached health status."""
        return self._integrations.get(name)

    def get_all_health(self) -> list[IntegrationHealth]:
        """Get all cached health statuses."""
        return list(self._integrations.values())

    def mark_synced(self, name: str):
        """Mark an integration as recently synced."""
        if name in self._integrations:
            self._integrations[name].last_sync = time.time()

    def mark_error(self, name: str, error: str):
        """Mark an integration as errored."""
        if name in self._integrations:
            self._integrations[name].status = "error"
            self._integrations[name].last_error = error[:200]

    def reconnect(self, name: str) -> bool:
        """Attempt to reconnect an integration."""
        integration = self._instances.get(name)
        if not integration:
            return False

        try:
            if hasattr(integration, 'disconnect'):
                integration.disconnect()
            connected = integration.connect()
            if connected:
                self._integrations[name].status = "connected"
                self._integrations[name].last_connected = time.time()
                self._integrations[name].last_error = None
                logger.info(f"integration_reconnected | {name}")
                return True
        except Exception as e:
            self._integrations[name].status = "error"
            self._integrations[name].last_error = str(e)[:200]
            logger.error(f"integration_reconnect_failed | {name} | {e}")

        return False

    @property
    def registered_count(self) -> int:
        return len(self._integrations)

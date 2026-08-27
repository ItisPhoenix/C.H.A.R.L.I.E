"""Central test-only isolation primitives. Never connect to production Charlie state."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class IsolatedEventBus:
    """In-process command recorder for debug harnesses; no sockets or runtime state."""

    commands: list[dict] = field(default_factory=list)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def send_command(self, command: dict):
        self.commands.append(command)

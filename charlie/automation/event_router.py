"""Event Router — receives events from all sources, classifies, routes."""
from __future__ import annotations

import logging
from typing import Callable

from charlie.automation.models import Event

logger = logging.getLogger("charlie.automation.event_router")


class EventRouter:
    """Routes events to registered subscribers."""

    def __init__(self):
        self._subscribers: dict[str, list[Callable]] = {}

    def subscribe(self, event_type: str, handler: Callable[[Event], None]):
        """Register a handler for an event type. Use '*' for all events."""
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(handler)

    def unsubscribe(self, event_type: str, handler: Callable):
        """Remove a handler for an event type."""
        if event_type in self._subscribers:
            self._subscribers[event_type] = [
                h for h in self._subscribers[event_type] if h is not handler
            ]

    def emit(self, event: Event):
        """Emit an event to all matching subscribers."""
        logger.info(f"event_emitted | type={event.type} | source={event.source}")

        # Type-specific subscribers
        for handler in self._subscribers.get(event.type, []):
            try:
                handler(event)
            except Exception as e:
                logger.error(f"event_handler_failed | type={event.type} | error={e}")

        # Wildcard subscribers
        for handler in self._subscribers.get("*", []):
            try:
                handler(event)
            except Exception as e:
                logger.error(f"wildcard_handler_failed | type={event.type} | error={e}")

    def get_subscribed_types(self) -> list[str]:
        """Return all event types that have subscribers."""
        return [t for t in self._subscribers.keys() if t != "*"]

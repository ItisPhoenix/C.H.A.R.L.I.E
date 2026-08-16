"""Abstract Routing Provider Interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from charlie.geo.models import RouteResult


class RoutingProvider(ABC):
    """Abstract interface for Charlie vehicular and tactical routing providers."""

    @abstractmethod
    async def get_route(
        self,
        start: List[float],  # [lon, lat]
        destination: List[float],  # [lon, lat]
        start_label: str = "Origin",
        destination_label: str = "Destination",
        mode: str = "driving",
    ) -> Optional[RouteResult]:
        """Compute an actionable navigation route between two geographic coordinates."""
        pass

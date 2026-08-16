"""Abstract Geocoding Provider Interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from charlie.geo.models import GeocodingResult


class GeocodingProvider(ABC):
    """Abstract interface for Charlie geocoding providers."""

    @abstractmethod
    async def search(self, query: str, limit: int = 5) -> List[GeocodingResult]:
        """Resolve a place name or address query to geocoding candidates."""
        pass

    @abstractmethod
    async def reverse(self, lat: float, lon: float) -> Optional[GeocodingResult]:
        """Reverse geocode coordinates into a normalized place descriptor."""
        pass

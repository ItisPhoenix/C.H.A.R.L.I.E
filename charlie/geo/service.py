"""Master Geospatial Service for Charlie OS."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from charlie.geo.geocoding import GeocodingProvider, NominatimProvider
from charlie.geo.intelligence import (
    CyberThreatProvider,
    IntelligenceProvider,
    NASAEONETProvider,
    OpenMeteoProvider,
    USGSEarthquakesProvider,
)
from charlie.geo.models import GeocodingResult, LayerDataResult, RouteResult
from charlie.geo.routing import OSRMProvider, RoutingProvider
from charlie.geo.tiles.pmtiles import PMTilesManager

logger = logging.getLogger("charlie.geo.service")


class GeoService:
    """Authoritative backend coordinator for all Charlie geospatial capabilities."""

    def __init__(
        self,
        geocoding_provider: Optional[GeocodingProvider] = None,
        routing_provider: Optional[RoutingProvider] = None,
    ) -> None:
        self.geocoder = geocoding_provider or NominatimProvider()
        self.router = routing_provider or OSRMProvider()
        self.pmtiles = PMTilesManager()

        self._layers: Dict[str, IntelligenceProvider] = {
            "earthquakes": USGSEarthquakesProvider(),
            "wildfires": NASAEONETProvider(),
            "weather": OpenMeteoProvider(),
            "cyber_threats": CyberThreatProvider(),
        }

    async def geocode(self, query: str, limit: int = 5) -> List[GeocodingResult]:
        """Search places or coordinates."""
        try:
            return await self.geocoder.search(query, limit=limit)
        except Exception as e:
            logger.warning(f"Geocoding failed for '{query}': {e}")
            return []

    async def reverse_geocode(self, lat: float, lon: float) -> Optional[GeocodingResult]:
        """Reverse geocode coordinate pair."""
        try:
            return await self.geocoder.reverse(lat, lon)
        except Exception as e:
            logger.warning(f"Reverse geocoding failed for {lat},{lon}: {e}")
            return None

    async def get_route(
        self,
        start: List[float],
        destination: List[float],
        start_label: str = "Origin",
        destination_label: str = "Destination",
        mode: str = "driving",
    ) -> Optional[RouteResult]:
        """Calculate vehicular route."""
        try:
            return await self.router.get_route(
                start=start,
                destination=destination,
                start_label=start_label,
                destination_label=destination_label,
                mode=mode,
            )
        except Exception as e:
            logger.warning(f"Routing failed: {e}")
            return None

    async def get_layer_data(self, layer_id: str) -> LayerDataResult:
        """Fetch normalized intelligence layer features."""
        provider = self._layers.get(layer_id)
        if not provider:
            return LayerDataResult(
                layer_id=layer_id,
                status="unconfigured",
                features=[],
                attribution="Charlie Open Intelligence Index",
                timestamp=time.time(),
                error=f"Layer '{layer_id}' is not configured on this system",
            )

        try:
            return await provider.fetch_data()
        except Exception as e:
            logger.warning(f"Intelligence layer '{layer_id}' fetch failed: {e}")
            return LayerDataResult(
                layer_id=layer_id,
                status="error",
                features=[],
                attribution=provider.attribution,
                timestamp=time.time(),
                error=str(e),
            )

    def get_registered_layers(self) -> List[Dict[str, Any]]:
        """List active and operational backend layers."""
        return [
            {
                "layer_id": lid,
                "attribution": p.attribution,
                "operational": True,
            }
            for lid, p in self._layers.items()
        ]


# Global Singleton
geo_service = GeoService()

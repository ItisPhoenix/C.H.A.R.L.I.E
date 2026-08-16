"""OSRM Routing Provider for Charlie Spatial Intelligence."""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional

import httpx

from charlie.geo.models import RouteResult, RouteStep
from charlie.geo.routing.base import RoutingProvider

logger = logging.getLogger("charlie.geo.routing.osrm")


def calculate_haversine_distance(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Calculate the great-circle distance between two points on the Earth in kilometers."""
    r = 6371.0  # Earth's mean radius in km
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = math.sin(delta_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return r * c


class OSRMProvider(RoutingProvider):
    """OSRM Routing provider supporting both public demo and self-hosted instances.
    
    Zero-fabrication invariant: If real routing fails or is unavailable, this provider
    returns None (or an explicit geodesic measurement if requested). It NEVER generates
    fake road geometry, fake driving duration, or fake turn-by-turn road instructions.
    """

    def __init__(
        self,
        base_url: str = "https://router.project-osrm.org",
        timeout_sec: float = 6.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_sec = timeout_sec

    async def get_route(
        self,
        start: List[float],
        destination: List[float],
        start_label: str = "Origin",
        destination_label: str = "Destination",
        mode: str = "driving",
    ) -> Optional[RouteResult]:
        if len(start) < 2 or len(destination) < 2:
            return None

        start_lon, start_lat = start[0], start[1]
        dest_lon, dest_lat = destination[0], destination[1]

        # 1. Attempt real OSRM routing
        try:
            url = f"{self.base_url}/route/v1/{mode}/{start_lon},{start_lat};{dest_lon},{dest_lat}"
            params = {
                "overview": "full",
                "geometries": "geojson",
                "steps": "true",
            }
            headers = {"User-Agent": "Charlie-Spatial-Engine/1.0 (Autonomous-AI-OS)"}

            async with httpx.AsyncClient(timeout=self.timeout_sec) as client:
                resp = await client.get(url, params=params, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    routes = data.get("routes", [])
                    if routes:
                        primary_route = routes[0]
                        coords = primary_route.get("geometry", {}).get("coordinates", [])
                        distance_meters = float(primary_route.get("distance", 0.0))
                        duration_sec = float(primary_route.get("duration", 0.0))

                        # Parse genuine turn steps from OSRM
                        steps: List[RouteStep] = []
                        legs = primary_route.get("legs", [])
                        for leg in legs:
                            for step_item in leg.get("steps", []):
                                maneuver = step_item.get("maneuver", {})
                                instruction = maneuver.get("type", "proceed")
                                modifier = maneuver.get("modifier")
                                if modifier:
                                    instruction = f"{instruction} {modifier}"
                                name = step_item.get("name")
                                if name:
                                    instruction = f"{instruction} onto {name}"

                                step_dist_km = float(step_item.get("distance", 0)) / 1000.0
                                step_dur_min = float(step_item.get("duration", 0)) / 60.0
                                steps.append(
                                    RouteStep(
                                        instruction=instruction.capitalize(),
                                        distance=f"{step_dist_km:.1f} km",
                                        duration=f"{step_dur_min:.0f} min" if step_dur_min >= 1 else "<1 min",
                                    )
                                )

                        if coords:
                            return RouteResult(
                                start=[start_lon, start_lat],
                                start_label=start_label,
                                destination=[dest_lon, dest_lat],
                                destination_label=destination_label,
                                geometry=coords,
                                distance_km=round(distance_meters / 1000.0, 1),
                                duration_min=round(duration_sec / 60.0, 1),
                                steps=steps,
                                mode=mode,
                                provider="osrm",
                            )
        except Exception as e:
            logger.warning(f"OSRM routing query failed: {e}. Route unavailable.")

        # Real route unavailable: Do NOT fabricate road geometry, driving times, or turn steps.
        return None

    def get_geodesic_measurement(
        self,
        start: List[float],
        destination: List[float],
        start_label: str = "Point A",
        destination_label: str = "Point B",
    ) -> Optional[RouteResult]:
        """Expose an explicit, clearly typed geodesic distance measurement.
        
        Never presents as driving navigation. No turn instructions or fake road curvature.
        """
        if len(start) < 2 or len(destination) < 2:
            return None

        start_lon, start_lat = start[0], start[1]
        dest_lon, dest_lat = destination[0], destination[1]
        distance_km = calculate_haversine_distance(start_lon, start_lat, dest_lon, dest_lat)

        return RouteResult(
            start=[start_lon, start_lat],
            start_label=start_label,
            destination=[dest_lon, dest_lat],
            destination_label=destination_label,
            geometry=[[start_lon, start_lat], [dest_lon, dest_lat]],
            distance_km=round(distance_km, 1),
            duration_min=None,
            steps=[],
            mode="geodesic_measurement",
            provider="geodesic_measurement",
        )

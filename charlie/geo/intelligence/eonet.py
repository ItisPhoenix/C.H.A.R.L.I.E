"""NASA EONET Natural Hazards Intelligence Layer Provider."""

from __future__ import annotations

import logging
import time
from typing import List

import httpx

from charlie.geo.intelligence.base import IntelligenceProvider
from charlie.geo.models import LayerDataResult, MapFeature

logger = logging.getLogger("charlie.geo.intelligence.eonet")

EONET_FEED_URL = "https://eonet.gsfc.nasa.gov/api/v3/events?status=open&limit=60"


class NASAEONETProvider(IntelligenceProvider):
    """Provider for global wildfire and thermal hotspot events from NASA EONET."""

    def __init__(self, cache_ttl_sec: float = 120.0) -> None:
        self.cache_ttl_sec = cache_ttl_sec
        self._last_result: LayerDataResult | None = None

    @property
    def layer_id(self) -> str:
        return "wildfires"

    @property
    def attribution(self) -> str:
        return "NASA Earth Observatory Natural Event Tracker (EONET)"

    async def fetch_data(self) -> LayerDataResult:
        now = time.time()
        if self._last_result and (now - self._last_result.timestamp < self.cache_ttl_sec):
            return self._last_result

        try:
            async with httpx.AsyncClient(timeout=6.0) as client:
                resp = await client.get(
                    EONET_FEED_URL,
                    headers={"User-Agent": "Charlie-Spatial-Engine/1.0 (Autonomous-AI-OS)"},
                )

                if resp.status_code == 200:
                    data = resp.json()
                    events = data.get("events", [])
                    features: List[MapFeature] = []

                    for ev in events:
                        categories = ev.get("categories", [])
                        cat_title = categories[0].get("title", "Hazard") if categories else "Hazard"
                        cat_id = categories[0].get("id", "") if categories else ""

                        # Strict filter: Wildfires layer must contain wildfires & thermal anomalies
                        is_wildfire = (
                            "fire" in cat_title.lower()
                            or "wildfire" in cat_id.lower()
                            or cat_id == "wildfires"
                        )
                        if not is_wildfire:
                            continue

                        geometries = ev.get("geometry", [])
                        if not geometries:
                            continue

                        latest_geom = geometries[-1]
                        coords = latest_geom.get("coordinates", [])
                        if len(coords) < 2:
                            continue

                        lon, lat = float(coords[0]), float(coords[1])
                        title = ev.get("title", "Active Wildfire Complex")
                        ev_date = latest_geom.get("date", "")

                        features.append(
                            MapFeature(
                                id=f"wildfire_{ev.get('id', int(now))}",
                                label=title,
                                category="Wildfire / Thermal Anomaly",
                                description=f"Active wildfire complex tracked by NASA thermal sensors. Recorded: {ev_date}.",
                                coordinates=[lon, lat],
                                severity="high",
                                source="NASA EONET",
                                timestamp=ev_date,
                                properties={"event_id": ev.get("id"), "link": ev.get("link")},
                                color="#ef4444",
                            )
                        )

                    result = LayerDataResult(
                        layer_id=self.layer_id,
                        status="ready",
                        features=features,
                        attribution=self.attribution,
                        timestamp=now,
                    )
                    self._last_result = result
                    return result

        except Exception as e:
            logger.warning(f"NASA EONET wildfires feed fetch failed: {e}")

        if self._last_result:
            return self._last_result

        return LayerDataResult(
            layer_id=self.layer_id,
            status="error",
            features=[],
            attribution=self.attribution,
            timestamp=now,
            error="Could not connect to NASA EONET feed",
        )

"""NASA EONET Natural Hazards Intelligence Layer Provider."""

from __future__ import annotations

import logging
import time
from typing import List

import httpx

from charlie.geo.intelligence.base import IntelligenceProvider
from charlie.geo.models import LayerDataResult, MapFeature

logger = logging.getLogger("charlie.geo.intelligence.eonet")

EONET_FEED_URL = "https://eonet.gsfc.nasa.gov/api/v3/events?status=open&limit=40"


class NASAEONETProvider(IntelligenceProvider):
    """Provider for global natural hazards and environmental events from NASA EONET."""

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
                    headers={"User-Agent": "Charlie-Spatial-Engine/1.0"},
                )

                if resp.status_code == 200:
                    data = resp.json()
                    events = data.get("events", [])
                    features: List[MapFeature] = []

                    for ev in events:
                        geometries = ev.get("geometry", [])
                        if not geometries:
                            continue

                        latest_geom = geometries[-1]
                        coords = latest_geom.get("coordinates", [])
                        if len(coords) < 2:
                            continue

                        lon, lat = float(coords[0]), float(coords[1])
                        title = ev.get("title", "Natural Hazard Event")
                        categories = ev.get("categories", [])
                        cat_title = categories[0].get("title", "Hazard") if categories else "Hazard"
                        ev_date = latest_geom.get("date", "")

                        color = "#f97316"
                        if "fire" in cat_title.lower():
                            color = "#ef4444"
                        elif "storm" in cat_title.lower():
                            color = "#3b82f6"
                        elif "volcano" in cat_title.lower():
                            color = "#dc2626"

                        features.append(
                            MapFeature(
                                id=f"eonet_{ev.get('id', int(now))}",
                                label=title,
                                category=cat_title,
                                description=f"Active {cat_title} event tracked by NASA. Date reported: {ev_date}.",
                                coordinates=[lon, lat],
                                severity="high",
                                source="NASA EONET",
                                timestamp=ev_date,
                                properties={"event_id": ev.get("id"), "link": ev.get("link")},
                                color=color,
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
            logger.warning(f"NASA EONET feed fetch failed: {e}")

        if self._last_result:
            return self._last_result

        return LayerDataResult(
            layer_id=self.layer_id,
            status="error",
            features=[],
            attribution=self.attribution,
            timestamp=now,
            error="Upstream NASA EONET service unavailable",
        )

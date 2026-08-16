"""USGS Real-Time Earthquakes Intelligence Layer Provider."""

from __future__ import annotations

import datetime
import logging
import time
from typing import List

import httpx

from charlie.geo.intelligence.base import IntelligenceProvider
from charlie.geo.models import LayerDataResult, MapFeature

logger = logging.getLogger("charlie.geo.intelligence.usgs")

USGS_FEED_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson"


class USGSEarthquakesProvider(IntelligenceProvider):
    """Provider for real-time seismic events from USGS."""

    def __init__(self, cache_ttl_sec: float = 60.0) -> None:
        self.cache_ttl_sec = cache_ttl_sec
        self._last_result: LayerDataResult | None = None

    @property
    def layer_id(self) -> str:
        return "earthquakes"

    @property
    def attribution(self) -> str:
        return "USGS Earthquake Hazards Program"

    async def fetch_data(self) -> LayerDataResult:
        now = time.time()
        if self._last_result and (now - self._last_result.timestamp < self.cache_ttl_sec):
            return self._last_result

        try:
            async with httpx.AsyncClient(timeout=6.0) as client:
                resp = await client.get(
                    USGS_FEED_URL,
                    headers={"User-Agent": "Charlie-Spatial-Engine/1.0"},
                )

                if resp.status_code == 200:
                    data = resp.json()
                    raw_features = data.get("features", [])
                    features: List[MapFeature] = []

                    for feat in raw_features:
                        props = feat.get("properties", {})
                        geom = feat.get("geometry", {})
                        coords = geom.get("coordinates", [])

                        if len(coords) < 2:
                            continue

                        lon, lat = float(coords[0]), float(coords[1])
                        depth_km = float(coords[2]) if len(coords) > 2 else 10.0
                        mag = float(props.get("mag") or 0.0)
                        place = props.get("place") or "Unknown Region"
                        epoch_ms = props.get("time") or int(now * 1000)
                        time_iso = datetime.datetime.fromtimestamp(
                            epoch_ms / 1000.0, tz=datetime.timezone.utc
                        ).strftime("%Y-%m-%d %H:%M:%S UTC")

                        severity = "normal"
                        color = "#00f0ff"
                        if mag >= 6.0:
                            severity = "critical"
                            color = "#ef4444"
                        elif mag >= 4.5:
                            severity = "high"
                            color = "#f97316"
                        elif mag >= 3.5:
                            severity = "medium"
                            color = "#eab308"

                        features.append(
                            MapFeature(
                                id=f"usgs_{feat.get('id', int(now))}",
                                label=f"M{mag:.1f} — {place}",
                                category="Seismic Event",
                                description=f"Magnitude {mag:.1f} earthquake at {depth_km:.1f}km depth. Occurred at {time_iso}.",
                                coordinates=[lon, lat],
                                severity=severity,
                                source="USGS Real-Time Feed",
                                timestamp=time_iso,
                                properties={
                                    "magnitude": mag,
                                    "depth_km": depth_km,
                                    "tsunami_alert": bool(props.get("tsunami", 0)),
                                    "url": props.get("url"),
                                },
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
            logger.warning(f"USGS earthquake feed fetch failed: {e}")

        # If previous result exists, reuse it gracefully
        if self._last_result:
            return self._last_result

        return LayerDataResult(
            layer_id=self.layer_id,
            status="error",
            features=[],
            attribution=self.attribution,
            timestamp=now,
            error="Upstream USGS service unavailable",
        )

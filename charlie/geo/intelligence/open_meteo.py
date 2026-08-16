"""Open-Meteo Weather Intelligence Layer Provider."""

from __future__ import annotations

import logging
import time
from typing import List

import httpx

from charlie.geo.intelligence.base import IntelligenceProvider
from charlie.geo.models import LayerDataResult, MapFeature

logger = logging.getLogger("charlie.geo.intelligence.open_meteo")

METEOROLOGICAL_HUBS = [
    ("Tokyo Station", 139.6917, 35.6895),
    ("London Heathrow", -0.4543, 51.4700),
    ("New York JFK", -73.7781, 40.6413),
    ("Paris CDG", 2.5479, 49.0097),
    ("Singapore Changi", 103.9915, 1.3644),
    ("Sydney Kingsford", 151.1772, -33.9399),
    ("Dubai International", 55.3657, 25.2532),
    ("Delhi IGI", 77.1000, 28.5562),
    ("San Francisco SFO", -122.3789, 37.6213),
    ("Reykjavik", -21.9426, 64.1466),
]


class OpenMeteoProvider(IntelligenceProvider):
    """Provider for real-time global weather observations."""

    def __init__(self, cache_ttl_sec: float = 120.0) -> None:
        self.cache_ttl_sec = cache_ttl_sec
        self._last_result: LayerDataResult | None = None

    @property
    def layer_id(self) -> str:
        return "weather"

    @property
    def attribution(self) -> str:
        return "Open-Meteo Weather API"

    async def fetch_data(self) -> LayerDataResult:
        now = time.time()
        if self._last_result and (now - self._last_result.timestamp < self.cache_ttl_sec):
            return self._last_result

        features: List[MapFeature] = []

        try:
            # Query in parallel batches for speed
            async with httpx.AsyncClient(timeout=4.0) as client:
                for name, lon, lat in METEOROLOGICAL_HUBS[:6]:
                    url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code"
                    try:
                        resp = await client.get(url, headers={"User-Agent": "Charlie-Spatial-Engine/1.0"})
                        if resp.status_code == 200:
                            data = resp.json()
                            curr = data.get("current", {})
                            temp = curr.get("temperature_2m", 0.0)
                            wind = curr.get("wind_speed_10m", 0.0)
                            hum = curr.get("relative_humidity_2m", 0)

                            features.append(
                                MapFeature(
                                    id=f"weather_{name.lower().replace(' ', '_')}",
                                    label=f"{name}: {temp:.1f}°C",
                                    category="Meteorology",
                                    description=f"Current conditions at {name}: Temperature {temp:.1f}°C, Wind Speed {wind:.1f} km/h, Humidity {hum}%.",
                                    coordinates=[lon, lat],
                                    severity="normal",
                                    source="Open-Meteo",
                                    timestamp=curr.get("time"),
                                    properties={"temperature_c": temp, "wind_kmh": wind, "humidity_pct": hum},
                                    color="#38bdf8",
                                )
                            )
                    except Exception as hub_err:
                        logger.debug(f"Weather hub query failed for {name}: {hub_err}")

            if features:
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
            logger.warning(f"Open-Meteo weather fetch failed: {e}")

        if self._last_result:
            return self._last_result

        return LayerDataResult(
            layer_id=self.layer_id,
            status="error",
            features=[],
            attribution=self.attribution,
            timestamp=now,
            error="Upstream Open-Meteo service unavailable",
        )

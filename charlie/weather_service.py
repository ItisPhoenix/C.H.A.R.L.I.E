"""Location-specific current weather from Open-Meteo."""

from __future__ import annotations

import re
from typing import Any

import httpx

from charlie.geo.service import geo_service

_COORDINATES = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*[,;]\s*(-?\d+(?:\.\d+)?)\s*$")


def _condition_label(code: Any) -> str | None:
    if not isinstance(code, int):
        return None
    if code == 0:
        return "Clear sky"
    if code == 1:
        return "Mostly clear"
    if code == 2:
        return "Partly cloudy"
    if code == 3:
        return "Overcast"
    if code in {45, 48}:
        return "Fog"
    if 51 <= code <= 67 or 80 <= code <= 82:
        return "Rain"
    if 71 <= code <= 77 or code in {85, 86}:
        return "Snow"
    if 95 <= code <= 99:
        return "Thunderstorm"
    return None


async def get_weather(location: str, *, geocoder: Any = None) -> dict[str, Any]:
    """Resolve a place or lat,lon pair, then fetch current conditions at that point."""
    if not isinstance(location, str) or not location.strip():
        raise ValueError("location must be a place name or latitude,longitude")
    query = location.strip()
    match = _COORDINATES.fullmatch(query)
    if match:
        lat, lon = map(float, match.groups())
        if not -90 <= lat <= 90 or not -180 <= lon <= 180:
            raise ValueError("coordinates are outside valid latitude/longitude ranges")
        place = {"name": query, "display_name": query, "coordinates": [lon, lat]}
    else:
        matches = await (geocoder or geo_service).geocode(query, limit=5)
        if not matches:
            raise LookupError(f"Could not resolve location: {query}")
        def normalize(value: str) -> str:
            return " ".join(re.findall(r"[\w]+", value.casefold()))

        requested_tokens = set(normalize(query).split())
        exact_name_matches = [result for result in matches if normalize(result.name) == normalize(query)]
        candidates = [
            result for result in matches
            if set(normalize(result.name).split()).issubset(requested_tokens)
            and requested_tokens.issubset(set(normalize(result.display_name).split()))
        ]
        if len(exact_name_matches) == 1:
            result = exact_name_matches[0]
        elif candidates and len({normalize(result.name) for result in candidates}) == 1:
            city_candidates = [
                candidate for candidate in candidates
                if getattr(candidate, "place_type", None) in {"city", "town", "village", "municipality"}
            ]
            if len(city_candidates) == 1:
                result = city_candidates[0]
            else:
                def scope(place):
                    return tuple(normalize(part) for part in place.display_name.split(",")[1:3])

                scopes = {scope(candidate) for candidate in candidates}
                if len(scopes) > 1:
                    raise LookupError(f"Location is ambiguous; include a state, region, or country: {query}")
                result = city_candidates[0] if city_candidates else candidates[0]
        elif len(candidates) == 1:
            result = candidates[0]
        else:
            raise LookupError(f"Location is ambiguous; include a state, region, or country: {query}")
        if not set(normalize(query).split()).issubset(set(normalize(result.display_name).split())):
            raise LookupError(f"Could not verify resolved location matches: {query}")
        lon, lat = result.coordinates
        place = {
            "name": result.name,
            "display_name": result.display_name,
            "coordinates": [lon, lat],
        }

    async with httpx.AsyncClient(timeout=6.0) as client:
        response = await client.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,wind_speed_10m,weather_code",
                "daily": "sunrise,sunset",
                "timezone": "auto",
            },
            headers={"User-Agent": "Charlie-Weather/1.0"},
        )
        response.raise_for_status()
        data = response.json()
    current = data.get("current")
    if not isinstance(current, dict) or "temperature_2m" not in current:
        raise ValueError("Weather provider returned no current temperature")
    return {
        "location": place,
        "current": current,
        "condition": _condition_label(current.get("weather_code")),
        "daily": data.get("daily", {}),
        "units": data.get("current_units", {}),
        "timezone": data.get("timezone"),
        "source": "Open-Meteo",
    }

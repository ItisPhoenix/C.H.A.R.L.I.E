"""Cyber Threat Intelligence Layer with pluggable IP geolocation enrichment.

Strict Rules:
- IP geolocation is fully abstracted behind the IPGeolocationProvider interface.
- If provider is unconfigured or unavailable, cyber indicators remain non-geographic.
- Never fabricate geographic coordinates.
- Treat URLhaus authentication and feed endpoint availability honestly.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import time
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple

import httpx

from charlie.geo.intelligence.base import IntelligenceProvider
from charlie.geo.models import LayerDataResult, MapFeature

logger = logging.getLogger("charlie.geo.intelligence.cyber")

URLHAUS_RECENT_FEED = "https://urlhaus.abuse.ch/downloads/json_recent/"


class IPGeolocationProvider(ABC):
    """Abstract interface for IP address geolocation lookups."""

    @abstractmethod
    async def geolocate(self, ip: str) -> Optional[List[float]]:
        """Geolocate public IP string to [longitude, latitude], or return None if unresolvable."""
        pass


class NoOpIPGeolocationProvider(IPGeolocationProvider):
    """Default no-op provider when no external IP geolocation service is configured."""

    async def geolocate(self, ip: str) -> Optional[List[float]]:
        return None


class HttpIPGeolocationProvider(IPGeolocationProvider):
    """Configurable HTTP-based IP geolocation provider (e.g. IPInfo, MaxMind, or custom gateway)."""

    def __init__(
        self,
        endpoint_template: str = "https://ipinfo.io/{ip}/geo",
        api_token: Optional[str] = None,
        timeout_sec: float = 3.0,
    ) -> None:
        self.endpoint_template = endpoint_template
        self.api_token = api_token
        self.timeout_sec = timeout_sec

    async def geolocate(self, ip: str) -> Optional[List[float]]:
        try:
            url = self.endpoint_template.format(ip=ip)
            headers = {"User-Agent": "Charlie-Spatial-Engine/1.0"}
            if self.api_token:
                headers["Authorization"] = f"Bearer {self.api_token}"

            async with httpx.AsyncClient(timeout=self.timeout_sec) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    # Handle "loc": "lat,lon" (ipinfo style) or "lat"/"lon" directly
                    if "loc" in data and isinstance(data["loc"], str):
                        parts = data["loc"].split(",")
                        if len(parts) == 2:
                            return [float(parts[1].strip()), float(parts[0].strip())]
                    elif "lat" in data and "lon" in data:
                        return [float(data["lon"]), float(data["lat"])]
                    elif "latitude" in data and "longitude" in data:
                        return [float(data["longitude"]), float(data["latitude"])]
        except Exception as e:
            logger.debug(f"HTTP IP Geolocation lookup failed for {ip}: {e}")
        return None


class OfflineIPGeolocationProvider(IPGeolocationProvider):
    """Static lookup for known local test networks and reference prefixes."""

    def __init__(self, static_ranges: Optional[Dict[str, List[float]]] = None) -> None:
        self.static_ranges = static_ranges or {}

    async def geolocate(self, ip: str) -> Optional[List[float]]:
        return self.static_ranges.get(ip)


class IPGeolocationEnricher:
    """Enriches IP addresses with geographic coordinates without fabricating locations."""

    def __init__(
        self,
        provider: Optional[IPGeolocationProvider] = None,
        cache_ttl_sec: float = 86400.0,
    ) -> None:
        self.provider = provider or NoOpIPGeolocationProvider()
        self.cache_ttl_sec = cache_ttl_sec
        self._cache: Dict[str, Tuple[float, Optional[List[float]]]] = {}

    def is_public_ip(self, ip_str: str) -> bool:
        """Check if string is a valid public IP."""
        try:
            ip_obj = ipaddress.ip_address(ip_str.strip())
            return not (ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_reserved or ip_obj.is_link_local)
        except ValueError:
            return False

    async def resolve_ip(self, ip_str: str) -> Optional[List[float]]:
        """Resolve a public IP to [longitude, latitude].

        Strict rule: Returns None if unresolvable. Never fabricates coordinates.
        """
        if not self.is_public_ip(ip_str):
            return None

        now = time.time()
        if ip_str in self._cache:
            ts, coords = self._cache[ip_str]
            if now - ts < self.cache_ttl_sec:
                return coords

        coords = await self.provider.geolocate(ip_str)
        self._cache[ip_str] = (now, coords)
        return coords


class CyberThreatProvider(IntelligenceProvider):
    """Provider for verified cyber infrastructure and C2 indicators with strict geolocation."""

    def __init__(
        self,
        cache_ttl_sec: float = 300.0,
        api_key: Optional[str] = None,
        feed_url: Optional[str] = None,
        enricher: Optional[IPGeolocationEnricher] = None,
    ) -> None:
        self.cache_ttl_sec = cache_ttl_sec
        self.api_key = api_key or os.environ.get("CHARLIE_URLHAUS_API_KEY")
        self.feed_url = feed_url or os.environ.get("CHARLIE_URLHAUS_FEED_URL") or URLHAUS_RECENT_FEED
        self.enricher = enricher or IPGeolocationEnricher()
        self._last_result: LayerDataResult | None = None

    @property
    def layer_id(self) -> str:
        return "cyber_threats"

    @property
    def attribution(self) -> str:
        return "abuse.ch URLhaus / ThreatFox Threat Intelligence"

    async def fetch_data(self) -> LayerDataResult:
        now = time.time()
        if self._last_result and (now - self._last_result.timestamp < self.cache_ttl_sec):
            return self._last_result

        features: List[MapFeature] = []

        headers = {
            "User-Agent": "Charlie-Spatial-Engine/1.0 (Autonomous-AI-OS)",
        }
        if self.api_key:
            headers["Auth-Key"] = self.api_key

        try:
            async with httpx.AsyncClient(timeout=4.5) as client:
                resp = await client.get(self.feed_url, headers=headers)

                if resp.status_code == 200:
                    data = resp.json()
                    # Inspect recent records
                    for item_id, entries in list(data.items())[:25]:
                        if not isinstance(entries, list) or not entries:
                            continue
                        entry = entries[0]
                        host = entry.get("host", "")
                        threat = entry.get("threat", "malicious_payload")
                        status = entry.get("url_status", "online")
                        date_added = entry.get("dateadded", "")

                        # Attempt IP Geolocation only when host is a public IP
                        # Strict invariant: Unresolvable indicators remain strictly non-geographic
                        coords = await self.enricher.resolve_ip(host)
                        if coords:
                            features.append(
                                MapFeature(
                                    id=f"cyber_{item_id}",
                                    label=f"Threat Host: {host}",
                                    category="Cyber C2 / Malware Host",
                                    description=f"Active threat indicator '{threat}' (Status: {status}). Logged at {date_added}.",
                                    coordinates=coords,
                                    severity="critical" if status == "online" else "medium",
                                    source="abuse.ch URLhaus",
                                    timestamp=date_added,
                                    properties={"host": host, "threat": threat, "url": entry.get("url")},
                                    color="#f43f5e",
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
                elif resp.status_code in (401, 403):
                    # Authentication required
                    return LayerDataResult(
                        layer_id=self.layer_id,
                        status="unconfigured",
                        features=[],
                        attribution=self.attribution,
                        timestamp=now,
                        error="URLhaus API authentication required for live cyber threat feed.",
                    )
                else:
                    logger.warning(f"URLhaus feed query returned status {resp.status_code}")
        except Exception as e:
            logger.warning(f"Cyber threat feed fetch error: {e}")

        if self._last_result:
            return self._last_result

        # Honest unconfigured / unavailable status
        return LayerDataResult(
            layer_id=self.layer_id,
            status="unconfigured",
            features=[],
            attribution=self.attribution,
            timestamp=now,
            error="Live cyber threat feed is unconfigured or unavailable. Configure CHARLIE_URLHAUS_API_KEY in settings.",
        )

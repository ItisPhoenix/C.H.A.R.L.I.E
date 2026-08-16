"""Cyber Threat Intelligence Layer with pluggable IP geolocation enrichment."""

from __future__ import annotations

import ipaddress
import logging
import time
from typing import Dict, List, Optional, Tuple

import httpx

from charlie.geo.intelligence.base import IntelligenceProvider
from charlie.geo.models import LayerDataResult, MapFeature

logger = logging.getLogger("charlie.geo.intelligence.cyber")

URLHAUS_RECENT_FEED = "https://urlhaus.abuse.ch/downloads/json_recent/"


class IPGeolocationEnricher:
    """Enriches IP addresses with geographic coordinates without fabricating locations."""

    def __init__(self, cache_ttl_sec: float = 86400.0) -> None:
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

        # Query lightweight IP geolocation endpoint
        try:
            url = f"http://ip-api.com/json/{ip_str}?fields=status,lat,lon"
            async with httpx.AsyncClient(timeout=2.5) as client:
                resp = await client.get(url, headers={"User-Agent": "Charlie-Spatial-Engine/1.0"})
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("status") == "success":
                        lat = float(data.get("lat", 0.0))
                        lon = float(data.get("lon", 0.0))
                        coords = [lon, lat]
                        self._cache[ip_str] = (now, coords)
                        return coords
        except Exception as e:
            logger.debug(f"IP geolocation lookup failed for {ip_str}: {e}")

        # Mark as None in cache to avoid repetitive failed lookups
        self._cache[ip_str] = (now, None)
        return None


class CyberThreatProvider(IntelligenceProvider):
    """Provider for verified cyber infrastructure and C2 indicators with strict geolocation."""

    def __init__(self, cache_ttl_sec: float = 300.0) -> None:
        self.cache_ttl_sec = cache_ttl_sec
        self._last_result: LayerDataResult | None = None
        self.enricher = IPGeolocationEnricher()

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

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    URLHAUS_RECENT_FEED,
                    headers={"User-Agent": "Charlie-Spatial-Engine/1.0 (Autonomous-AI-OS)"},
                )

                if resp.status_code == 200:
                    data = resp.json()
                    # Inspect recent records
                    for item_id, entries in list(data.items())[:20]:
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

        except Exception as e:
            logger.warning(f"Cyber threat feed fetch failed: {e}")

        if self._last_result:
            return self._last_result

        return LayerDataResult(
            layer_id=self.layer_id,
            status="error",
            features=[],
            attribution=self.attribution,
            timestamp=now,
            error="Could not fetch URLhaus threat feed",
        )

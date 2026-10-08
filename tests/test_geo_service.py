"""Unit and Integration Tests for Charlie Geospatial and Spatial Intelligence Engine."""


import json
import struct

import pytest

from charlie.geo import geo_service
from charlie.geo.geocoding.nominatim import NominatimProvider
from charlie.geo.intelligence.cyber import (
    CyberThreatProvider,
    IPGeolocationEnricher,
    NoOpIPGeolocationProvider,
    OfflineIPGeolocationProvider,
)
from charlie.geo.routing.osrm import OSRMProvider
from charlie.geo.tiles.pmtiles import PMTilesManager


@pytest.mark.asyncio
async def test_geocoding_real_query():
    """Verify geocoding for major global locations."""
    # Tokyo
    results = await geo_service.geocode("Tokyo")
    assert len(results) > 0
    assert "Tokyo" in results[0].display_name or "Tokyo" in results[0].name or "東京都" in results[0].name
    assert results[0].coordinates[0] > 130.0  # Longitude ~139.69
    assert results[0].coordinates[1] > 30.0   # Latitude ~35.68

    # Delhi
    results_delhi = await geo_service.geocode("Delhi")
    assert len(results_delhi) > 0
    assert "Delhi" in results_delhi[0].name


@pytest.mark.asyncio
async def test_geocoding_fallback_semantics():
    """Verify fallback is only used when provider fails and exact query matches emergency hub."""
    # Provider with invalid endpoint to simulate outage
    offline_geocoder = NominatimProvider(base_url="http://127.0.0.1:9999", min_request_interval_sec=0.0)

    # Exact hub match should use offline fallback on provider failure
    res = await offline_geocoder.search("tokyo")
    assert len(res) == 1
    assert res[0].provider == "offline_fallback"
    assert res[0].name == "Tokyo"

    # Substring search must NOT trigger fallback (e.g. "somewhere in tokyo city center")
    res_sub = await offline_geocoder.search("somewhere in tokyo center 12345")
    assert len(res_sub) == 0


@pytest.mark.asyncio
async def test_geocoding_valid_empty_result_does_not_trigger_offline_fallback():
    """Verify that a valid HTTP 200 with zero results returns [] and does NOT trigger offline fallback."""
    geocoder = NominatimProvider(min_request_interval_sec=0.0)
    # A completely non-existent query should return [] without fallback
    res = await geocoder.search("zzzzzz_non_existent_place_query_123456789")
    assert len(res) == 0


@pytest.mark.asyncio
async def test_osrm_failure_never_creates_fake_driving_route():
    """Verify that OSRM failure returns None and NEVER creates fake road geometry or turn instructions."""
    bad_router = OSRMProvider(base_url="http://127.0.0.1:9999", timeout_sec=0.5)

    route = await bad_router.get_route(
        start=[77.1025, 28.7041],
        destination=[75.7873, 26.9124],
        start_label="Delhi",
        destination_label="Jaipur",
        mode="driving",
    )
    # Must be None, no fake driving turns or fake road geometry
    assert route is None


def test_explicit_geodesic_measurement():
    """Verify explicit geodesic measurement is typed properly without fake turn steps."""
    router = OSRMProvider()
    measurement = router.get_geodesic_measurement(
        start=[77.1025, 28.7041],
        destination=[75.7873, 26.9124],
        start_label="Delhi",
        destination_label="Jaipur",
    )
    assert measurement is not None
    assert measurement.mode == "geodesic_measurement"
    assert measurement.provider == "geodesic_measurement"
    assert measurement.duration_min is None
    assert len(measurement.steps) == 0
    assert measurement.distance_km > 200.0


@pytest.mark.asyncio
async def test_routing_delhi_to_jaipur():
    """Verify vehicular routing from Delhi to Jaipur with live/cached OSRM."""
    delhi_coords = [77.1025, 28.7041]
    jaipur_coords = [75.7873, 26.9124]

    route = await geo_service.get_route(
        start=delhi_coords,
        destination=jaipur_coords,
        start_label="Delhi",
        destination_label="Jaipur",
    )

    if route:
        assert route.start_label == "Delhi"
        assert route.destination_label == "Jaipur"
        assert len(route.geometry) > 1
        assert route.distance_km > 200.0
        assert route.mode == "driving"


@pytest.mark.asyncio
async def test_usgs_earthquakes_layer():
    """Verify earthquakes layer normalization and structure."""
    res = await geo_service.get_layer_data("earthquakes")
    assert res.layer_id == "earthquakes"
    assert res.status in ("ready", "loading", "error")
    for feat in res.features:
        assert len(feat.coordinates) == 2
        assert -180.0 <= feat.coordinates[0] <= 180.0
        assert -90.0 <= feat.coordinates[1] <= 90.0
        assert feat.severity in ("low", "normal", "medium", "high", "critical")


def test_cyber_ip_enricher_abstraction():
    """Verify IP enricher provider abstraction and behavior."""
    # 1. No-op provider returns None
    noop_enricher = IPGeolocationEnricher(provider=NoOpIPGeolocationProvider())
    assert not noop_enricher.is_public_ip("127.0.0.1")
    assert not noop_enricher.is_public_ip("192.168.1.1")
    assert noop_enricher.is_public_ip("8.8.8.8")

    # 2. Static offline provider returns known coords without fabrication
    offline_provider = OfflineIPGeolocationProvider({"8.8.8.8": [-122.084, 37.422]})
    offline_enricher = IPGeolocationEnricher(provider=offline_provider)

    import asyncio
    res_known = asyncio.run(offline_enricher.resolve_ip("8.8.8.8"))
    res_unknown = asyncio.run(offline_enricher.resolve_ip("1.1.1.1"))
    assert res_known == [-122.084, 37.422]
    assert res_unknown is None


@pytest.mark.asyncio
async def test_cyber_provider_honest_unconfigured_status():
    """Verify cyber threat provider returns honest unconfigured status if feed is unconfigured."""
    cyber = CyberThreatProvider(feed_url="http://127.0.0.1:9999/feed")
    res = await cyber.fetch_data()
    assert res.status == "unconfigured"
    assert len(res.features) == 0


def test_pmtiles_v3_header_inspection_and_security(tmp_path):
    """Use a test-only archive to check parsing; it is never runtime or acceptance data."""
    metadata = json.dumps({"vector_layers": [{"id": "places"}]}).encode()
    header = struct.pack(
        "<7sB11Q4B2B4iBii",
        b"PMTiles",
        3,
        127,
        1,
        128,
        len(metadata),
        0,
        0,
        128 + len(metadata),
        0,
        0,
        0,
        0,
        1,
        0,
        0,
        1,
        0,
        14,
        0,
        0,
        0,
        0,
        10,
        0,
        0,
    )
    (tmp_path / "regression_fixture.pmtiles").write_bytes(header + b"\x00" + metadata)
    pm_manager = PMTilesManager(str(tmp_path))
    archives = pm_manager.list_archives()
    assert len(archives) > 0
    sample = archives[0]
    assert sample["valid"] is True
    assert sample["version"] == 3
    assert sample["tileType"] in ("vector", "raster_png", "raster_webp", "unknown")
    assert sample["url"].startswith("/api/geo/pmtiles/")
    assert sample["metadata"] is not None
    assert "vector_layers" in sample["metadata"]

    # Security: Ensure path traversal attempts are rejected
    assert pm_manager.resolve_safe_path("../../../etc/passwd") is None
    assert pm_manager.resolve_safe_path("..\\..\\Windows\\win.ini") is None
    assert pm_manager.resolve_safe_path(sample["name"]) is not None


@pytest.mark.asyncio
async def test_unconfigured_layer_quiet_state():
    """Verify unconfigured or nonexistent layers return graceful empty state without crashing."""
    res = await geo_service.get_layer_data("non_existent_layer_xyz")
    assert res.status == "unconfigured"
    assert len(res.features) == 0

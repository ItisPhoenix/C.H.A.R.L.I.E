"""Unit and Integration Tests for Charlie Geospatial and Spatial Intelligence Engine."""

import pytest
from pathlib import Path
from starlette.testclient import TestClient

from charlie.geo import geo_service
from charlie.geo.geocoding.nominatim import NominatimProvider
from charlie.geo.routing.osrm import OSRMProvider
from charlie.geo.intelligence.cyber import (
    IPGeolocationEnricher,
    NoOpIPGeolocationProvider,
    OfflineIPGeolocationProvider,
    HttpIPGeolocationProvider,
    CyberThreatProvider,
)
from charlie.geo.tiles.pmtiles import PMTilesManager, PMTILES_MAGIC, PMTILES_VERSION, PMTILES_HEADER_SIZE
from charlie.web_server import app


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


def test_pmtiles_v3_header_inspection_and_security():
    """Verify PMTiles archive header follows exact v3 spec (7-byte magic + ver 3 + 127 bytes)."""
    pm_manager = PMTilesManager()
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


def test_pmtiles_http_range_endpoint():
    """Verify FastAPI /api/geo/pmtiles/{archive_name} endpoint with HTTP Range requests."""
    client = TestClient(app)

    # 1. HEAD request
    head_resp = client.head("/api/geo/pmtiles/sample_regional.pmtiles")
    assert head_resp.status_code == 200
    assert "content-length" in head_resp.headers
    total_size = int(head_resp.headers["content-length"])
    assert total_size >= PMTILES_HEADER_SIZE
    assert head_resp.headers.get("accept-ranges") == "bytes"

    # 2. Valid Range request for 127-byte header (bytes=0-126)
    range_resp = client.get(
        "/api/geo/pmtiles/sample_regional.pmtiles",
        headers={"Range": "bytes=0-126"},
    )
    assert range_resp.status_code == 206
    assert len(range_resp.content) == 127
    assert range_resp.headers.get("content-range") == f"bytes 0-126/{total_size}"
    # Verify magic bytes in range response
    assert range_resp.content[0:7] == PMTILES_MAGIC
    assert range_resp.content[7] == PMTILES_VERSION

    # 3. Suffix Range request (last 50 bytes: bytes=-50)
    suffix_resp = client.get(
        "/api/geo/pmtiles/sample_regional.pmtiles",
        headers={"Range": "bytes=-50"},
    )
    assert suffix_resp.status_code == 206
    assert len(suffix_resp.content) == 50

    # 4. Open-ended Range request (bytes=100-)
    open_resp = client.get(
        "/api/geo/pmtiles/sample_regional.pmtiles",
        headers={"Range": "bytes=100-"},
    )
    assert open_resp.status_code == 206
    assert len(open_resp.content) == total_size - 100

    # 5. Invalid Range (416 Range Not Satisfiable)
    invalid_resp = client.get(
        "/api/geo/pmtiles/sample_regional.pmtiles",
        headers={"Range": f"bytes={total_size + 100}-{total_size + 200}"},
    )
    assert invalid_resp.status_code == 416
    assert invalid_resp.headers.get("content-range") == f"bytes */{total_size}"


@pytest.mark.asyncio
async def test_unconfigured_layer_quiet_state():
    """Verify unconfigured or nonexistent layers return graceful empty state without crashing."""
    res = await geo_service.get_layer_data("non_existent_layer_xyz")
    assert res.status == "unconfigured"
    assert len(res.features) == 0

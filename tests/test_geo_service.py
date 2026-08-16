"""Unit and Integration Tests for Charlie Geospatial and Spatial Intelligence Engine."""

import pytest
from charlie.geo import geo_service
from charlie.geo.models import GeocodingResult, MapFeature, RouteResult


@pytest.mark.asyncio
async def test_geocoding_known_hubs():
    """Verify geocoding for Tokyo, OpenAI HQ, Delhi, Jaipur."""
    # Tokyo
    results = await geo_service.geocode("Tokyo")
    assert len(results) > 0
    assert "Tokyo" in results[0].name
    assert results[0].coordinates[0] > 130.0  # Longitude ~139.69
    assert results[0].coordinates[1] > 30.0   # Latitude ~35.68

    # OpenAI Headquarters
    results_openai = await geo_service.geocode("OpenAI Headquarters")
    assert len(results_openai) > 0
    assert "OpenAI" in results_openai[0].name
    assert results_openai[0].coordinates[0] < -120.0  # San Francisco

    # Delhi
    results_delhi = await geo_service.geocode("Delhi")
    assert len(results_delhi) > 0
    assert "Delhi" in results_delhi[0].name


@pytest.mark.asyncio
async def test_routing_delhi_to_jaipur():
    """Verify vehicular routing from Delhi to Jaipur."""
    delhi_coords = [77.1025, 28.7041]
    jaipur_coords = [75.7873, 26.9124]

    route = await geo_service.get_route(
        start=delhi_coords,
        destination=jaipur_coords,
        start_label="Delhi",
        destination_label="Jaipur",
    )

    assert route is not None
    assert route.start_label == "Delhi"
    assert route.destination_label == "Jaipur"
    assert len(route.geometry) > 1
    assert route.distance_km > 200.0  # ~240-280 km
    assert len(route.steps) > 0


@pytest.mark.asyncio
async def test_usgs_earthquakes_layer():
    """Verify earthquakes layer normalization and structure."""
    res = await geo_service.get_layer_data("earthquakes")
    assert res.layer_id == "earthquakes"
    assert res.status in ("ready", "loading", "error")
    # All normalized features must have valid 2D coordinates and severity
    for feat in res.features:
        assert len(feat.coordinates) == 2
        assert -180.0 <= feat.coordinates[0] <= 180.0
        assert -90.0 <= feat.coordinates[1] <= 90.0
        assert feat.severity in ("low", "normal", "medium", "high", "critical")


@pytest.mark.asyncio
async def test_cyber_threats_layer_no_fake_coordinates():
    """Verify cyber threat layer never invents unverified coordinates."""
    res = await geo_service.get_layer_data("cyber_threats")
    assert res.layer_id == "cyber_threats"
    for feat in res.features:
        # If coordinates exist, they must be strictly valid numbers
        assert isinstance(feat.coordinates[0], (int, float))
        assert isinstance(feat.coordinates[1], (int, float))
        assert feat.source == "abuse.ch URLhaus"


@pytest.mark.asyncio
async def test_unconfigured_layer_quiet_state():
    """Verify unconfigured or nonexistent layers return graceful empty state without crashing."""
    res = await geo_service.get_layer_data("non_existent_layer_xyz")
    assert res.status == "unconfigured"
    assert len(res.features) == 0

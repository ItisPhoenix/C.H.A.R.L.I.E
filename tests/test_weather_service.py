from types import SimpleNamespace

import pytest

from charlie.weather_service import _condition_label, get_weather


class _Response:
    def raise_for_status(self):
        pass

    def json(self):
        return {
            "current": {"temperature_2m": 31.2, "weather_code": 1},
            "current_units": {"temperature_2m": "°C"},
            "daily": {"sunrise": ["2026-10-10T06:10"], "sunset": ["2026-10-10T17:46"]},
            "timezone": "Asia/Kolkata",
        }


class _Client:
    calls = []

    def __init__(self, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        pass

    async def get(self, url, *, params, **_kwargs):
        self.calls.append((url, params))
        return _Response()


@pytest.mark.asyncio
async def test_weather_uses_exact_resolved_location_coordinates(monkeypatch):
    from charlie import weather_service

    _Client.calls.clear()
    monkeypatch.setattr(weather_service.httpx, "AsyncClient", _Client)
    async def fake_geocode(query, limit):
        assert (query, limit) == ("Kanpur", 5)
        return [SimpleNamespace(name="Kanpur", display_name="Kanpur, Uttar Pradesh, India", coordinates=[80.3319, 26.4499])]

    geocoder = SimpleNamespace(geocode=fake_geocode)
    result = await get_weather("Kanpur", geocoder=geocoder)

    assert result["location"]["name"] == "Kanpur"
    assert result["current"]["temperature_2m"] == 31.2
    assert result["daily"]["sunset"] == ["2026-10-10T17:46"]
    assert result["source"] == "Open-Meteo"
    assert _Client.calls[0][1]["daily"] == "sunrise,sunset"
    assert _Client.calls[0][1]["latitude"] == 26.4499
    assert _Client.calls[0][1]["longitude"] == 80.3319


@pytest.mark.asyncio
async def test_weather_selects_unique_city_when_admin_result_shares_its_name(monkeypatch):
    from charlie import weather_service

    _Client.calls.clear()
    monkeypatch.setattr(weather_service.httpx, "AsyncClient", _Client)
    places = [
        SimpleNamespace(
            name="Kanpur",
            display_name="Kanpur, Kanpur Nagar district, Uttar Pradesh, India",
            coordinates=[80.3319, 26.4499],
            place_type="city",
        ),
        SimpleNamespace(
            name="Kanpur",
            display_name="Kanpur, Uttar Pradesh, India",
            coordinates=[80.3, 26.4],
            place_type="administrative",
        ),
    ]
    result = await get_weather("Kanpur", geocoder=SimpleNamespace(geocode=lambda *_args, **_kwargs: _return(places)))

    assert result["location"]["display_name"] == "Kanpur, Kanpur Nagar district, Uttar Pradesh, India"
    assert _Client.calls[0][1]["latitude"] == 26.4499


@pytest.mark.asyncio
async def test_weather_accepts_exact_coordinates_without_geocoding(monkeypatch):
    from charlie import weather_service

    _Client.calls.clear()
    monkeypatch.setattr(weather_service.httpx, "AsyncClient", _Client)
    result = await get_weather("26.4499, 80.3319")
    assert result["location"]["coordinates"] == [80.3319, 26.4499]
    assert _Client.calls[0][1]["latitude"] == 26.4499
    assert _Client.calls[0][1]["longitude"] == 80.3319


@pytest.mark.asyncio
async def test_weather_rejects_unresolved_places_and_invalid_coordinates():
    geocoder = SimpleNamespace(geocode=lambda *_args, **_kwargs: _empty())
    with pytest.raises(LookupError):
        await get_weather("Unmapped place", geocoder=geocoder)
    with pytest.raises(ValueError):
        await get_weather("95,200")


@pytest.mark.asyncio
async def test_weather_rejects_ambiguous_place_instead_of_using_first_result():
    places = [
        SimpleNamespace(name="Springfield", display_name="Springfield, Illinois, United States", coordinates=[-89.6, 39.8]),
        SimpleNamespace(name="Springfield", display_name="Springfield, Missouri, United States", coordinates=[-93.3, 37.2]),
    ]
    geocoder = SimpleNamespace(geocode=lambda *_args, **_kwargs: _return(places))
    with pytest.raises(LookupError, match="ambiguous"):
        await get_weather("Springfield", geocoder=geocoder)


@pytest.mark.asyncio
async def test_weather_uses_city_record_when_geocoder_also_returns_same_admin_area(monkeypatch):
    from charlie import weather_service

    _Client.calls.clear()
    monkeypatch.setattr(weather_service.httpx, "AsyncClient", _Client)
    places = [
        SimpleNamespace(
            name="Kanpur",
            display_name="Kanpur, Kanpur Nagar, Uttar Pradesh, India",
            place_type="administrative",
            coordinates=[80.3017, 26.4069],
        ),
        SimpleNamespace(
            name="Kanpur",
            display_name="Kanpur, Kanpur Nagar, Uttar Pradesh, 208012, India",
            place_type="city",
            coordinates=[80.3217, 26.4609],
        ),
    ]
    geocoder = SimpleNamespace(geocode=lambda *_args, **_kwargs: _return(places))

    result = await get_weather("Kanpur, Uttar Pradesh India", geocoder=geocoder)

    assert result["location"]["coordinates"] == [80.3217, 26.4609]
    assert _Client.calls[0][1]["latitude"] == 26.4609


@pytest.mark.asyncio
async def test_weather_resolves_duplicate_records_when_full_location_agrees(monkeypatch):
    from charlie import weather_service

    _Client.calls.clear()
    monkeypatch.setattr(weather_service.httpx, "AsyncClient", _Client)
    places = [
        SimpleNamespace(
            name="Kanpur",
            display_name="Kanpur, Kanpur Nagar, Uttar Pradesh, India",
            coordinates=[80.3319, 26.4499],
        ),
        SimpleNamespace(
            name="Kanpur",
            display_name="Kanpur, Kanpur Nagar, Uttar Pradesh, India",
            coordinates=[80.34, 26.46],
        ),
    ]
    geocoder = SimpleNamespace(geocode=lambda *_args, **_kwargs: _return(places))

    result = await get_weather("Kanpur, Uttar Pradesh India", geocoder=geocoder)

    assert result["location"]["name"] == "Kanpur"
    assert _Client.calls[0][1]["latitude"] == 26.4499
    assert _Client.calls[0][1]["longitude"] == 80.3319


async def _empty():
    return []


async def _return(value):
    return value


@pytest.mark.parametrize(
    ("code", "expected"),
    [(0, "Clear sky"), (2, "Partly cloudy"), (61, "Rain"), (71, "Snow"), (95, "Thunderstorm"), (999, None)],
)
def test_weather_code_label_covers_provider_conditions(code, expected):
    assert _condition_label(code) == expected

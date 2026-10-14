from charlie.capabilities import get_capability_index
from charlie.tools import registry


def test_weather_timer_and_stopwatch_tools_are_registered_with_authority_metadata():
    names = {"weather_get", "timer_create", "timer_cancel", "stopwatch_control"}
    assert names <= set(registry.get_tool_names())

    index = get_capability_index()
    assert index.get_operation_domain("timer_create") == "calendar"
    assert index.get_operation_domain("timer_cancel") == "calendar"
    assert index.get_operation("timer_create").required_leases == ("calendar",)
    assert index.get_operation("timer_cancel").required_leases == ("calendar",)
    assert index.get_operation("weather_get").risk_class == "safe"
    assert index.get_operation("weather_get").cacheable is False


def test_timer_structured_tool_results_reuse_automation_verification(monkeypatch):
    from types import SimpleNamespace
    from charlie import calendar_runtime
    from charlie import timer_service

    monkeypatch.setattr(
        timer_service,
        "create_timer",
        lambda delay, text, **_kwargs: {
            "id": "timer-1",
            "kind": "reminder",
            "text": text,
            "status": "active",
            "active_run_status": None,
            "recurrence": "once",
            "next_run_at": "2026-10-10T12:00:00Z",
            "timezone": "Asia/Kolkata",
        },
    )
    monkeypatch.setattr(calendar_runtime, "calendar_runtime_required", lambda: SimpleNamespace(
        execute_sync=lambda method, identity: timer_service.create_timer(300, "Tea is ready"),
    ))
    result = registry.execute_tool_structured(
        "timer_create", {"delay_seconds": 300, "text": "Tea is ready"}
    )
    assert result.structured_data["ok"] is True
    assert result.structured_data["id"] == "timer-1"
    assert result.structured_data["verified"] is True
    assert result.model_text == "Timer set for 5 minutes."


def test_reminder_create_confirmation_hides_schedule_internals(monkeypatch):
    from types import SimpleNamespace

    from charlie import calendar_runtime

    schedule = {
        "id": "internal-schedule-id",
        "kind": "reminder",
        "text": "Water the plants",
        "status": "active",
        "active_run_status": None,
        "recurrence": "once",
        "next_run_at": "2026-10-11T12:00:00Z",
        "timezone": "Asia/Kolkata",
        "delivery_channel": "local",
    }
    monkeypatch.setattr(
        calendar_runtime,
        "calendar_runtime_required",
        lambda: SimpleNamespace(execute_sync=lambda *_args, **_kwargs: schedule),
    )

    result = registry.execute_tool_structured(
        "automation_create",
        {
            "kind": "reminder",
            "text": "Water the plants",
            "first_run_at": "2026-10-11T17:30:00+05:30",
            "recurrence": "once",
        },
    )

    assert result.model_text == "Reminder set: Water the plants."
    assert "internal-schedule-id" not in result.model_text
    assert "active_run_status" not in result.model_text
    assert "next_run_at" not in result.model_text
    assert result.structured_data["id"] == "internal-schedule-id"
    assert result.structured_data["active_run_status"] is None
    assert result.structured_data["recurrence"] == "once"
    assert result.structured_data["verified"] is True


def test_timer_cancel_confirmation_hides_internal_schedule_id(monkeypatch):
    from types import SimpleNamespace
    from charlie import calendar_runtime
    from charlie import timer_service

    schedule_id = "internal-schedule-id"
    monkeypatch.setattr(timer_service, "cancel_timer", lambda _id: {
        "id": schedule_id,
        "kind": "reminder",
        "text": "Tea is ready",
        "status": "cancelled",
    })

    monkeypatch.setattr(calendar_runtime, "calendar_runtime_required", lambda: SimpleNamespace(
        execute_sync=lambda method, identity: timer_service.cancel_timer(identity),
    ))
    result = registry.execute_tool_structured("timer_cancel", {"schedule_id": schedule_id})

    assert result.model_text == "Timer cancelled."
    assert schedule_id not in result.model_text
    assert result.structured_data["id"] == schedule_id
    assert result.structured_data["verified"] is True


def test_stopwatch_result_is_short_and_carries_readback_verification(monkeypatch):
    from charlie import tools

    monkeypatch.setattr(tools, "_stopwatch", None)
    result = registry.execute_tool_structured("stopwatch_control", {"action": "start"})
    assert result.model_text == "Stopwatch started."
    assert result.structured_data["verified"] is True
    assert result.structured_data["verification_status"] == "verified_success"


def test_weather_tool_is_registered_as_structured_tool():
    op = get_capability_index().get_operation("weather_get")
    assert op is not None
    assert op.id == "weather.current.read"
    assert registry.get_tool_param_names("weather_get") == ["location"]


def test_weather_user_text_omits_provider_label(monkeypatch):
    from charlie import weather_service

    async def fake_weather(_location):
        return {
            "location": {"name": "Kanpur", "display_name": "Kanpur, Uttar Pradesh, India"},
            "condition": "Partly cloudy",
            "current": {
                "temperature_2m": 26.0,
                "apparent_temperature": 27.2,
                "relative_humidity_2m": 65,
                "wind_speed_10m": 8,
                "precipitation": 0.2,
                "time": "2026-10-10T18:00",
            },
            "units": {
                "temperature_2m": "°C",
                "wind_speed_10m": "km/h",
                "precipitation": "mm",
            },
            "source": "Open-Meteo",
        }

    monkeypatch.setattr(weather_service, "get_weather", fake_weather)
    result = registry.execute_tool_structured("weather_get", {"location": "Kanpur"})

    assert result.model_text == "In Kanpur, it's 26 °C right now with partly cloudy conditions; it feels like 27.2 °C."
    assert result.data["current"]["relative_humidity_2m"] == 65
    assert "Open-Meteo" not in result.model_text


def test_weather_reply_reports_missing_conditions_without_claiming_provider_failure(monkeypatch):
    from charlie import weather_service

    async def sparse_weather(_location):
        return {
            "location": {"name": "Kanpur", "display_name": "Kanpur, Uttar Pradesh, India"},
            "current": {"temperature_2m": 26, "time": "2026-10-10T18:00"},
            "units": {"temperature_2m": "°C"},
        }

    monkeypatch.setattr(weather_service, "get_weather", sparse_weather)
    result = registry.execute_tool_structured("weather_get", {"location": "Kanpur"})

    assert "26 °C" in result.model_text
    assert result.model_text == "In Kanpur, it's 26 °C right now."
    assert "couldn't find" not in result.model_text.casefold()

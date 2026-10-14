import sys
from types import SimpleNamespace

import pytest

from charlie.fastpaths import (
    FastPathResult,
    _handle_system_diagnostics,
    match_direct_url,
    match_fast_path,
    match_filesystem_basic,
    match_focus_app,
    match_media_volume,
    match_system_telemetry,
    match_system_workspace,
    match_windows_settings,
)


def test_close_picture_in_picture_window_routes_to_desktop_window_control():
    match = match_fast_path("Close the picture in picture mode currently on my screen.")
    assert match is not None
    assert match.tool_name == "desktop_window"
    assert match.arguments == {"window": "picture in picture mode", "action": "close"}


@pytest.mark.parametrize(
    ("query", "widget", "visible"),
    [
        ("Charlie, show the clock again.", "clock", True),
        ("Hide the clock widget.", "clock", False),
        ("Open the music widget.", "music", True),
        ("Close the music card.", "music", False),
        ("Show me the weather widget.", "weather", True),
        ("Close the timer widget.", "timer", False),
        ("Open the stopwatch widget.", "stopwatch", True),
    ],
)
def test_dashboard_widget_visibility_is_a_deterministic_command(query, widget, visible):
    match = match_fast_path(query)

    assert match is not None
    assert match.tool_name == "dashboard_widget_control"
    assert match.arguments == {"widget": widget, "visible": visible}


def test_plain_show_stopwatch_keeps_its_existing_elapsed_time_meaning():
    match = match_fast_path("Show stopwatch.")

    assert match is not None
    assert match.tool_name == "stopwatch_control"
    assert match.arguments == {"action": "read"}


@pytest.mark.parametrize(
    ("query", "seconds", "text"),
    [
        ("Set a 10 second timer named acceptance test.", 10, "acceptance test"),
        ("Start a timer for two minutes.", 120, "Timer finished"),
        ("Set a timer for 1 hour called tea.", 3600, "tea"),
    ],
)
def test_timer_fastpath_parses_duration_and_optional_label(query, seconds, text):
    match = match_fast_path(query)
    assert match is not None
    assert match.tool_name == "timer_create"
    assert match.arguments == {"delay_seconds": seconds, "text": text}


def test_stopwatch_fastpath_accepts_spoken_self_correction():
    match = match_fast_path("Stop that stop watch.")
    assert match is not None
    assert match.tool_name == "stopwatch_control"
    assert match.arguments == {"action": "stop"}


class TestSystemTelemetryMatching:
    def test_full_system_status_admits_system_workspace(self):
        for query in (
            "Show me the full system status.",
            "What's the system status?",
            "What is my machine health?",
        ):
            match = match_fast_path(query)
            assert match is not None
            assert match.intent == "system_workspace"
            assert match.semantic_op_id == "system.workspace.read"
            assert match.tool_name == "system_diagnostics"

    def test_full_system_status_reply_summarizes_the_collected_vitals(self, monkeypatch):
        import sys
        from types import ModuleType, SimpleNamespace

        from charlie.fastpaths import _handle_full_system_status

        fake_psutil = SimpleNamespace(
            cpu_percent=lambda interval: 62.0,
            virtual_memory=lambda: SimpleNamespace(percent=84.0),
            disk_usage=lambda _path: SimpleNamespace(percent=59.0),
            process_iter=lambda _fields: [],
        )
        fake_journal = ModuleType("charlie.task_journal")
        fake_journal.get_task_journal = lambda: SimpleNamespace(list=lambda **_kwargs: [])
        monkeypatch.setitem(sys.modules, "psutil", fake_psutil)
        monkeypatch.setitem(sys.modules, "charlie.task_journal", fake_journal)

        result = _handle_full_system_status()

        assert str(result) == "Your PC is using 62% CPU and 84% of its memory; 59% of disk space is used."
        assert [gauge["value"] for gauge in result.data["vitals"]["gauges"]] == [62.0, 84.0, 59.0]

    def test_narrow_system_query_does_not_admit_workspace(self):
        assert match_system_workspace("Show me the current CPU utilization.") is None

    def test_current_cpu_temperature_is_local_telemetry(self):
        for query in (
            "Show me the current CPU temperature.",
            "What is my CPU temp right now?",
            "CPU temperature currently",
        ):
            match = match_system_telemetry(query)
            assert match is not None
            assert match.intent == "system_cpu_temperature"
            assert match.arguments == {"check": "cpu_temperature"}

    def test_safe_cpu_temperature_remains_informational(self):
        assert match_system_telemetry("What temperature is considered safe for a CPU?") is None

    def test_cpu_temperature_unavailable_is_explicit(self, monkeypatch):
        fake_psutil = SimpleNamespace(sensors_temperatures=lambda: {})
        monkeypatch.setitem(sys.modules, "psutil", fake_psutil)

        result = _handle_system_diagnostics("cpu_temperature")

        assert result.data["metric_name"] == "CPU Temperature"
        assert result.data["value"] is None
        assert result.data["available"] is False
        assert "unavailable" in result.lower()


    def test_cpu_temperature_reads_cpu_sensor(self, monkeypatch):
        reading = SimpleNamespace(current=67.4)
        fake_psutil = SimpleNamespace(sensors_temperatures=lambda: {"coretemp": [reading]})
        monkeypatch.setitem(sys.modules, "psutil", fake_psutil)

        result = _handle_system_diagnostics("cpu_temperature")

        assert result.data["value"] == 67.4
        assert result.data["available"] is True
        assert "67.4°C" in result

    def test_system_payload_preserves_authoritative_percent_value(self, monkeypatch):
        fake_psutil = SimpleNamespace(
            cpu_percent=lambda interval=None: 74.3,
            cpu_count=lambda logical=True: 12,
            virtual_memory=lambda: SimpleNamespace(percent=74.3, total=1000, available=257),
            disk_usage=lambda path: SimpleNamespace(percent=61.2, free=400, total=1000),
            sensors_battery=lambda: None,
        )
        monkeypatch.setitem(sys.modules, "psutil", fake_psutil)

        result = _handle_system_diagnostics("memory")

        assert isinstance(result, FastPathResult)
        assert result.data["value"] == 74.3
        assert result.data["unit"] == "percent_0_100"
        assert "74.3%" in result

    def test_cpu_sampling_keeps_nonzero_and_authoritative_zero(self, monkeypatch):
        samples = iter([37.0, 0.0])
        fake_psutil = SimpleNamespace(
            cpu_percent=lambda interval=None: next(samples),
            cpu_count=lambda logical=True: 12,
        )
        monkeypatch.setitem(sys.modules, "psutil", fake_psutil)

        nonzero = _handle_system_diagnostics("cpu")
        zero = _handle_system_diagnostics("cpu")

        assert nonzero.data["value"] == 37.0
        assert zero.data["value"] == 0.0

    def test_cpu_queries(self):
        queries = [
            "what's the cpu usage?",
            "CPU usage",
            "cpu load",
            "cpu percent",
            "how much cpu",
        ]
        for q in queries:
            m = match_system_telemetry(q)
            assert m is not None, f"Failed to match CPU query: {q}"
            assert m.intent == "system_cpu"
            assert m.tool_name == "system_diagnostics"
            assert m.arguments == {"check": "cpu"}
            assert m.target_domain == "system"

    def test_ram_queries(self):
        queries = [
            "what's my ram usage?",
            "how much memory is free?",
            "RAM percent",
            "memory usage",
            "Show me the current memory utilization.",
        ]
        for q in queries:
            m = match_system_telemetry(q)
            assert m is not None, f"Failed to match RAM query: {q}"
            assert m.intent == "system_memory"
            assert m.arguments == {"check": "memory"}

    def test_disk_queries(self):
        queries = [
            "what is the disk space?",
            "storage usage",
            "disk free",
            "how much disk space",
        ]
        for q in queries:
            m = match_system_telemetry(q)
            assert m is not None, f"Failed to match Disk query: {q}"
            assert m.intent == "system_disk"
            assert m.arguments == {"check": "disk"}

    def test_battery_queries(self):
        queries = [
            "what's the battery level?",
            "battery percentage",
            "how much battery",
        ]
        for q in queries:
            m = match_system_telemetry(q)
            assert m is not None, f"Failed to match Battery query: {q}"
            assert m.intent == "system_battery"

    def test_processes_queries(self):
        queries = [
            "list running processes",
            "show top processes",
            "what are the running processes",
        ]
        for q in queries:
            m = match_system_telemetry(q)
            assert m is not None, f"Failed to match Processes query: {q}"
            assert m.intent == "system_processes"
            assert m.arguments == {"check": "processes"}


class TestWeatherTimerStopwatchFastpaths:
    def test_location_weather_uses_exact_location_tool(self):
        match = match_fast_path("What's the temperature in Kanpur right now?")
        assert match is not None
        assert match.tool_name == "weather_get"
        assert match.arguments == {"location": "Kanpur"}
        assert match.target_domain == "weather"

    def test_weather_follow_up_reuses_last_explicit_session_location(self):
        from charlie.fastpaths import last_explicit_weather_location

        history = [
            {"role": "user", "content": "What's the weather in Kanpur?"},
            {"role": "assistant", "content": "Current temperature in Kanpur is 26 °C."},
            {"role": "user", "content": "How's the weather today?"},
        ]

        match = match_fast_path(
            "How's the weather today?",
            default_weather_location=last_explicit_weather_location(history),
        )

        assert match is not None
        assert match.tool_name == "weather_get"
        assert match.arguments == {"location": "Kanpur"}

    def test_weather_follow_up_uses_widget_location_when_available(self):
        match = match_fast_path(
            "How's the weather today?",
            default_weather_location="Pune",
        )
        assert match is not None
        assert match.tool_name == "weather_get"
        assert match.arguments == {"location": "Pune"}

    def test_explicit_weather_location_overrides_context(self):
        match = match_fast_path(
            "What's the weather in Delhi today?",
            default_weather_location="Kanpur",
        )
        assert match is not None
        assert match.arguments == {"location": "Delhi"}

    def test_weather_fastpath_does_not_swallow_a_separate_action_as_location(self):
        assert match_fast_path("What is the weather in Paris and show map") is None
        assert match_fast_path("Weather in Toronto by transit") is None
        place_name = match_fast_path("What is the weather in Trinidad and Tobago?")
        assert place_name is not None
        assert place_name.arguments == {"location": "Trinidad and Tobago"}

    def test_ambiguous_non_location_or_research_requests_are_not_fastpathed(self):
        missing_location = match_fast_path("What's the temperature right now?")
        assert missing_location is not None
        assert missing_location.intent == "weather_location_required"
        assert "Which city" in str(missing_location.direct_handler())
        assert match_fast_path("Research the weather in Kanpur") is None

    def test_reminder_after_minutes_uses_deterministic_duration(self):
        match = match_fast_path("Remind me to check the oven after five minutes.")
        assert match is not None
        assert match.tool_name == "timer_create"
        assert match.arguments == {"delay_seconds": 300, "text": "check the oven"}

    def test_timer_cancel_requires_explicit_schedule_id(self):
        timer_id = "0123456789abcdef0123456789abcdef"
        match = match_fast_path(f"Cancel timer {timer_id}.")
        assert match is not None
        assert match.tool_name == "timer_cancel"
        assert match.arguments == {"schedule_id": timer_id}
        assert match_fast_path("Cancel my timer") is None

    def test_stopwatch_lifecycle_uses_canonical_tool(self):
        for query, action in (
            ("Start stopwatch", "start"),
            ("Pause the stopwatch", "stop"),
            ("Reset stopwatch", "reset"),
            ("How long has the stopwatch been running?", "read"),
        ):
            match = match_fast_path(query)
            assert match is not None
            assert match.tool_name == "stopwatch_control"
            assert match.arguments == {"action": action}


class TestMediaVolumeMatching:
    def test_set_volume_percent(self):
        m = match_media_volume("set volume to 45%")
        assert m is not None
        assert m.intent == "volume_set_percent"
        assert m.arguments == {"action": "set_volume", "percent": 45}
        assert m.verifier_name == "verify_volume"

        m2 = match_media_volume("volume 80")
        assert m2 is not None
        assert m2.arguments == {"action": "set_volume", "percent": 80}

    def test_mute_unmute(self):
        m_mute = match_media_volume("mute audio")
        assert m_mute is not None
        assert m_mute.intent == "volume_mute"
        assert m_mute.arguments == {"action": "mute"}

        m_unmute = match_media_volume("unmute sound")
        assert m_unmute is not None
        assert m_unmute.intent == "volume_unmute"
        assert m_unmute.arguments == {"action": "unmute"}

    def test_volume_up_down(self):
        m_up = match_media_volume("turn the volume up")
        assert m_up is not None
        assert m_up.arguments == {"action": "volume_up"}

        m_down = match_media_volume("turn the volume down")
        assert m_down is not None
        assert m_down.arguments == {"action": "volume_down"}

    def test_playback_controls(self):
        assert match_media_volume("pause music").intent == "media_play_pause"
        assert match_media_volume("next track").intent == "media_next_track"
        assert match_media_volume("previous track").intent == "media_prev_track"


class TestWindowsSettingsMatching:
    def test_settings_uris(self):
        settings_test_cases = [
            ("open bluetooth settings", "ms-settings:bluetooth"),
            ("show display settings", "ms-settings:display"),
            ("open sound settings", "ms-settings:sound"),
            ("show wifi settings", "ms-settings:network-wifi"),
            ("open windows update", "ms-settings:windowsupdate"),
            ("open storage settings", "ms-settings:storagesense"),
            ("open apps settings", "ms-settings:appsfeatures"),
        ]
        for query, expected_uri in settings_test_cases:
            m = match_windows_settings(query)
            assert m is not None, f"Failed to match settings query: {query}"
            assert m.intent == "open_windows_settings"
            assert m.arguments["uri"] == expected_uri


class TestAppFocusAndFilesystem:
    def test_focus_app(self):
        m = match_focus_app("switch to Spotify")
        assert m is not None
        assert m.intent == "focus_app"
        assert m.arguments == {"title": "Spotify"}
        assert m.verifier_name == "verify_app_focus"

    def test_filesystem_folder(self):
        m = match_filesystem_basic("show downloads")
        assert m is not None
        assert m.intent == "list_directory"
        assert m.tool_name == "file_read"
        assert "Downloads" in m.arguments["path"]


class TestDirectBrowserUrl:
    def test_direct_url(self):
        m = match_direct_url("go to https://github.com/astral-sh/uv")
        assert m is not None
        assert m.intent == "browser_read_url"
        assert m.arguments["url"] == "https://github.com/astral-sh/uv"
        assert m.verifier_name == "verify_browser_navigate"

        m2 = match_direct_url("navigate to www.python.org")
        assert m2 is not None
        assert m2.arguments["url"] == "https://www.python.org"


class TestMasterFastPathDispatcher:
    def test_master_match_and_fallback(self):
        assert match_fast_path("what's the cpu load?") is not None
        assert match_fast_path("set volume to 60%") is not None
        assert match_fast_path("open bluetooth settings") is not None
        assert match_fast_path("go to https://example.com") is not None

        # Complex / reasoning queries fall through to LLM (return None)
        assert match_fast_path("why is the sky blue?") is None
        assert match_fast_path("write a python script to parse logs") is None
        assert match_fast_path("refactor my database schema") is None


class TestFastPathPolicyIntegration:
    @pytest.mark.asyncio
    async def test_unavailable_cpu_temperature_does_not_enter_research(self, monkeypatch):
        from charlie.config import Config
        from charlie.core import Brain

        fake_psutil = SimpleNamespace(sensors_temperatures=lambda: {})
        monkeypatch.setitem(sys.modules, "psutil", fake_psutil)
        brain = Brain(Config(llm_url="https://example.com/v1", llm_key="test-key", llm_model="dummy"))

        async def fail_research(*args, **kwargs):
            raise AssertionError("local CPU temperature must not enter research")

        monkeypatch.setattr(brain, "_run_research", fail_research)
        chunks = [
            chunk
            async for chunk in brain.chat_stream(
                "Show me the current CPU temperature.", platform="web", session_id="cpu-temp-test"
            )
        ]

        result = "".join(chunks)
        assert "unavailable" in result.lower()

    @pytest.mark.asyncio
    async def test_fast_path_policy_enforcement_allow(self, monkeypatch):
        from charlie.autonomy import Requirement, RiskClass
        from charlie.config import Config
        from charlie.core import Brain

        cfg = Config(llm_url="https://example.com/v1", llm_key="test-key", llm_model="dummy")
        brain = Brain(cfg)

        evaluated_calls = []

        def mock_evaluate(tool_name, arguments):
            evaluated_calls.append((tool_name, arguments))
            return Requirement.ALLOW, RiskClass.SAFE, ""

        monkeypatch.setattr("charlie.core.autonomy_evaluate", mock_evaluate)
        monkeypatch.setattr("charlie.fastpaths.execute_fast_path", lambda m: "CPU is 12%")

        chunks = []
        async for chunk in brain.chat_stream("what is the cpu usage?", platform="voice"):
            chunks.append(chunk)

        assert len(evaluated_calls) == 1
        assert evaluated_calls[0][0] == "system_diagnostics"
        assert "".join(chunks) == "CPU is 12%"

    @pytest.mark.asyncio
    async def test_fast_path_policy_enforcement_declined(self, monkeypatch):
        from charlie.autonomy import Requirement, RiskClass
        from charlie.config import Config
        from charlie.core import Brain

        cfg = Config(llm_url="https://example.com/v1", llm_key="test-key", llm_model="dummy")
        brain = Brain(cfg)

        def mock_evaluate(tool_name, arguments):
            return Requirement.APPROVE, RiskClass.SECURITY_SENSITIVE, "Approval required for sensitive op"

        monkeypatch.setattr("charlie.core.autonomy_evaluate", mock_evaluate)

        async def mock_approval(*a, **kw):
            return False  # User declines

        monkeypatch.setattr(brain, "request_tool_approval", mock_approval)

        chunks = []
        async for chunk in brain.chat_stream("set volume to 100%", platform="voice"):
            chunks.append(chunk)

        result_text = "".join(chunks)
        assert "declined" in result_text.lower()

    @pytest.mark.asyncio
    async def test_fast_path_policy_enforcement_blocked(self, monkeypatch):
        from charlie.autonomy import Requirement, RiskClass
        from charlie.config import Config
        from charlie.core import Brain

        cfg = Config(llm_url="https://example.com/v1", llm_key="test-key", llm_model="dummy")
        brain = Brain(cfg)

        def mock_evaluate(tool_name, arguments):
            return Requirement.BLOCK, RiskClass.IRREVERSIBLE, "Disallowed destructive action"

        monkeypatch.setattr("charlie.core.autonomy_evaluate", mock_evaluate)

        chunks = []
        async for chunk in brain.chat_stream("set volume to 0%", platform="voice"):
            chunks.append(chunk)

        result_text = "".join(chunks)
        assert "blocked by security policy" in result_text.lower()

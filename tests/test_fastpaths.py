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
    match_windows_settings,
)


class TestSystemTelemetryMatching:
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


class TestMediaVolumeMatching:
    def test_set_volume_percent(self):
        m = match_media_volume("set volume to 45%")
        assert m is not None
        assert m.intent == "volume_set_percent"
        assert m.arguments == {"percent": 45}
        assert m.verifier_name == "verify_volume"

        m2 = match_media_volume("volume 80")
        assert m2 is not None
        assert m2.arguments == {"percent": 80}

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
    async def test_fast_path_emits_resolved_presentation_intent(self, monkeypatch):
        from charlie import recovery
        from charlie.autonomy import Requirement, RiskClass
        from charlie.config import Config
        from charlie.core import Brain

        class EventBus:
            def __init__(self):
                self.events = []

            async def emit(self, event_type, payload, *, meta):
                self.events.append((event_type, payload, meta))

        bus = EventBus()
        monkeypatch.setattr(recovery, "_event_bus", bus)
        monkeypatch.setattr(
            "charlie.core.autonomy_evaluate",
            lambda *args: (Requirement.ALLOW, RiskClass.SAFE, ""),
        )
        monkeypatch.setattr("charlie.fastpaths.execute_fast_path", lambda match: "CPU is 12%")

        brain = Brain(Config(llm_url="https://example.com/v1", llm_key="test-key", llm_model="dummy"))
        chunks = [
            chunk
            async for chunk in brain.chat_stream(
                "what is the cpu usage?", platform="web", session_id="gate5-test"
            )
        ]

        assert "".join(chunks) == "CPU is 12%"
        assert len(bus.events) == 1
        event_type, payload, meta = bus.events[0]
        assert event_type == "presentation_intent"
        assert payload["kind"] == "widget"
        assert meta.session_id == "gate5-test"

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

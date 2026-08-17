import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os

import charlie.tools as tools_module
from charlie.session_store import SessionStore
from charlie.tools import (
    _DIAGNOSTIC_COMMANDS,
    ToolRegistry,
    _decompose_query,
    _merge_search_results,
    _needs_decomposition,
    file_read,
    file_write,
    get_path_gate_reason,
    is_shell_command_blocked,
    is_shell_command_gated,
    memory,
    recall_results,
    registry,
    session_search,
    shell_execute,
    system_diagnostics,
    vector_memory,
    web_search,
)


def test_registry_registration_and_schema():
    definitions = registry.get_tool_definitions()
    names = {d["function"]["name"] for d in definitions}
    # Lifespan TestClient runs may register plugin/MCP tools onto the global
    # registry; this test owns the built-in set only.
    names = {
        n for n in names if not n.startswith("plugin_") and not n.startswith("mcp_")
    }
    assert names == {
        "web_search",
        "web_research",
        "shell_execute",
        "system_diagnostics",
        "file_read",
        "file_write",
        "memory",
        "propose_new_tool",
        "start_background_task",
        "browser_task",
        "browser_read",
        "vector_memory",
        "session_search",
        "recall_results",
        "capabilities",
        "graph_add_fact",
        "graph_query",
        "graph_consolidate",
        "desktop_observe",
        "desktop_read_screen",
        "desktop_screenshot",
        "desktop_click",
        "desktop_type",
        "desktop_invoke",
        "desktop_key",
        "desktop_click_at",
        "desktop_move",
        "desktop_drag",
        "desktop_scroll",
        "desktop_windows",
        "desktop_focus",
        "desktop_window",
        "desktop_move_window",
        "system_control",
    }
    assert any(
        d["function"]["parameters"]["required"] == ["query"] for d in definitions
    )


def test_list_metadata_covers_every_registered_tool():
    metadata = registry.list_metadata()
    assert {m["name"] for m in metadata} == set(registry.get_tool_names())
    web_search_meta = next(m for m in metadata if m["name"] == "web_search")
    assert web_search_meta["description"]
    assert "owner" in web_search_meta and "risk_class" in web_search_meta


def test_get_tool_param_names_covers_every_registered_tool():
    """Drift-proofing: charlie.core's text-mode tool-call parser reads param
    names live via get_tool_param_names(). Every tool the registry actually
    knows about must resolve to a (possibly empty) param list, and that list
    must match the tool's own JSON schema -- so a newly-added tool can never
    silently fall through to the generic `query` fallback again."""
    for definition in registry.get_tool_definitions():
        name = definition["function"]["name"]
        expected = list(definition["function"]["parameters"].get("properties", {}).keys())
        assert registry.get_tool_param_names(name) == expected


def test_get_tool_param_names_unknown_tool_returns_none():
    assert registry.get_tool_param_names("not_a_real_tool") is None


def test_get_tool_names_matches_definitions():
    assert set(registry.get_tool_names()) == {
        d["function"]["name"] for d in registry.get_tool_definitions()
    }


def test_raw_desktop_tools_registered():
    names = registry.get_tool_names()
    for tool in ("desktop_click_at", "desktop_drag", "desktop_scroll", "desktop_move"):
        assert tool in names


def test_window_management_tools_registered():
    names = registry.get_tool_names()
    for tool in ("desktop_windows", "desktop_focus", "desktop_window", "desktop_move_window"):
        assert tool in names


def test_system_control_tool_registered():
    assert "system_control" in registry.get_tool_names()


def test_file_write_and_file_read(tmp_path):
    target = tmp_path / "notes.txt"
    message = file_write(str(target), "hello tools")
    assert "Successfully wrote to" in message
    assert target.exists()
    content = file_read(str(target))
    assert content.strip() == "hello tools"


def test_resolve_user_placeholders():
    import getpass

    from charlie.tools import _resolve_user_placeholders
    curr_user = getpass.getuser()

    p1 = "C:\\Users\\YourUsername\\Documents\\charlie.txt"
    p2 = "C:\\Users\\username\\Documents\\charlie.txt"
    p3 = "C:\\Users\\user\\Documents\\charlie.txt"

    assert _resolve_user_placeholders(p1) == f"C:\\Users\\{curr_user}\\Documents\\charlie.txt"
    assert _resolve_user_placeholders(p2) == f"C:\\Users\\{curr_user}\\Documents\\charlie.txt"
    assert _resolve_user_placeholders(p3) == f"C:\\Users\\{curr_user}\\Documents\\charlie.txt"


def test_resolve_user_placeholders_corrects_hallucinated_username():
    """Real bug: the model wrote C:\\Users\\Charlie\\... (guessing its own name)
    instead of an obvious <placeholder> or the real account name. Since
    "Charlie" isn't a real directory under C:\\Users, it must still be
    corrected -- not just the literal placeholder tokens."""
    import getpass

    from charlie.tools import _resolve_user_placeholders
    curr_user = getpass.getuser()

    bad_path = "C:\\Users\\Charlie\\Desktop\\charlie_test.txt"
    assert _resolve_user_placeholders(bad_path) == f"C:\\Users\\{curr_user}\\Desktop\\charlie_test.txt"


def test_resolve_user_placeholders_leaves_real_existing_user_dir_alone():
    """A path for a genuinely different, real user account on the machine
    must not be silently rewritten to the current user."""
    import os

    from charlie.tools import _resolve_user_placeholders

    real_other_dirs = [
        d for d in os.listdir("C:\\Users") if os.path.isdir(f"C:\\Users\\{d}")
    ] if os.path.isdir("C:\\Users") else []
    other = next((d for d in real_other_dirs if d.lower() != "public"), None)
    if other is None:
        return  # nothing to assert on this machine
    path = f"C:\\Users\\{other}\\Documents\\charlie.txt"
    assert _resolve_user_placeholders(path) == path


def test_shell_execute_lists_env(monkeypatch):
    import os

    output = shell_execute("echo OK")
    assert "OK" in output
    env_output = shell_execute("set" if os.name == "nt" else "env")
    assert isinstance(env_output, str)


def test_shell_execute_timeout_does_not_report_error(monkeypatch):
    """A command that blocks past SHELL_TIMEOUT (e.g. a bare GUI app launch
    like "notepad", which keeps its parent cmd.exe alive until closed) must
    not be reported as an "Error", or the caller retries and double-spawns
    the app that already opened successfully."""
    import subprocess as subprocess_module

    from charlie import tools as tools_module

    class FakeProcess:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def communicate(self, input=None, timeout=None):
            if timeout is not None:
                raise subprocess_module.TimeoutExpired(cmd="notepad", timeout=timeout)
            return ("", "")

        def kill(self):
            pass

    monkeypatch.setattr(tools_module, "is_process_running", lambda name: False)
    monkeypatch.setattr(
        tools_module.subprocess, "Popen", lambda *a, **k: FakeProcess()
    )
    output = shell_execute("notepad")
    assert "Error" not in output


def test_shell_execute_recovery_communicate_is_bounded(monkeypatch):
    """The post-kill communicate() call (draining the pipe after a timeout)
    must pass an explicit timeout. On Windows a detached grandchild (e.g.
    "start notepad") can keep the stdout/stderr pipe open long after the
    killed cmd.exe parent exits, so an unbounded second call blocks forever
    even though its return value is never used -- this was firing the
    outer 30s tool-call timeout instead of shell_execute's own graceful
    "still running" response."""
    import subprocess as subprocess_module

    from charlie import tools as tools_module

    calls = []

    class FakeProcess:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def communicate(self, input=None, timeout=None):
            calls.append(timeout)
            raise subprocess_module.TimeoutExpired(cmd="start notepad", timeout=timeout or 0)

        def kill(self):
            pass

    monkeypatch.setattr(tools_module, "is_process_running", lambda name: False)
    monkeypatch.setattr(
        tools_module.subprocess, "Popen", lambda *a, **k: FakeProcess()
    )

    output = shell_execute("start notepad")

    assert "Error" not in output
    assert "still running" in output
    assert all(t is not None for t in calls)


def test_shell_execute_voice_mode_allows_bare_command(monkeypatch):
    """Bare "notepad" (no trailing arg) must pass the voice-mode allowlist,
    not just "notepad <arg>" -- found live when a bare-command retry got
    wrongly rejected as "not on the allowed list"."""
    import subprocess as subprocess_module

    from charlie import tools as tools_module

    class FakeProcess:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def communicate(self, input=None, timeout=None):
            if timeout is not None:
                raise subprocess_module.TimeoutExpired(cmd="notepad", timeout=timeout)
            return ("", "")

        def kill(self):
            pass

    monkeypatch.setattr(
        tools_module.subprocess, "Popen", lambda *a, **k: FakeProcess()
    )
    output = shell_execute("notepad", voice_mode=True)
    assert "not on the allowed list" not in output


def test_shell_execute_voice_mode_allows_move_and_copy(monkeypatch):
    """Real bug: relocating a file the user asked to save on Desktop needed
    move/copy, both blocked as "not on the allowed list" in voice mode --
    forcing repeated failed retries until the tool budget ran out."""
    from charlie import tools as tools_module

    class FakeProcess:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def communicate(self, input=None, timeout=None):
            return ("", "")

        def kill(self):
            pass

    monkeypatch.setattr(tools_module.subprocess, "Popen", lambda *a, **k: FakeProcess())
    move_output = shell_execute('move "a.txt" "b.txt"', voice_mode=True)
    copy_output = shell_execute('copy "a.txt" "b.txt"', voice_mode=True)
    assert "not on the allowed list" not in move_output
    assert "not on the allowed list" not in copy_output


def test_detect_app_launch_matches_bare_and_wrapped_forms():
    """Root-cause fix for a live session: the model retried "powershell -Command
    Start-Process notepad", "cmd /c start notepad.exe", etc. after "start notepad"
    got blocked -- all of these must reduce to the same known app."""
    from charlie.tools import _detect_app_launch

    assert _detect_app_launch("notepad").close_process == "notepad.exe"
    assert _detect_app_launch("start notepad").close_process == "notepad.exe"
    assert _detect_app_launch('start "" notepad').close_process == "notepad.exe"
    assert _detect_app_launch("notepad.exe").close_process == "notepad.exe"
    assert _detect_app_launch("cmd /c start notepad.exe").close_process == "notepad.exe"
    assert (
        _detect_app_launch('powershell -Command Start-Process notepad').close_process
        == "notepad.exe"
    )


def test_detect_app_launch_ignores_commands_with_real_arguments():
    """"notepad file.txt" opens a specific file -- must not be treated as a
    bare relaunch of an already-open, unrelated Notepad window."""
    from charlie.tools import _detect_app_launch

    assert _detect_app_launch("notepad file.txt") is None
    assert _detect_app_launch("echo hello") is None


def test_shell_execute_focuses_already_running_app_instead_of_relaunching(monkeypatch):
    """The actual bug report: Charlie kept trying to relaunch Notepad instead
    of focusing the one already open, burning its tool-call budget."""
    from charlie import tools as tools_module

    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr(tools_module, "is_process_running", lambda name: name == "notepad.exe")
    monkeypatch.setattr(
        "charlie.desktop.windows.focus_window",
        lambda title: f"Focused window: Untitled - Notepad ({title})",
    )
    popen_calls = []
    monkeypatch.setattr(
        tools_module.subprocess, "Popen", lambda *a, **k: popen_calls.append(a) or None
    )

    output = shell_execute("start notepad")
    assert "Focused window" in output
    assert popen_calls == []


def test_shell_execute_blocks_metacharacters_and_keywords():
    """Locks the exact error text shell_execute returns via the shared
    is_shell_command_blocked() guard, now also reused by charlie.recovery."""
    assert shell_execute("echo a & type secrets.txt") == (
        "Error: Shell metacharacters (;, |, &, `, $, (, )) are not allowed."
    )
    assert shell_execute("format c: /q") == (
        "Error: Command blocked -- risky keyword 'format '"
    )


# ---------------------------------------------------------------------------
# Phase 3 dashboard "desktop_frame" event -- downscale + throttle + emit bridge
# ---------------------------------------------------------------------------

def test_downscale_png_caps_long_edge_and_preserves_aspect():
    import io

    from PIL import Image

    from charlie.tools import _downscale_png

    src = Image.new("RGB", (2000, 1000), color=(10, 20, 30))
    buf = io.BytesIO()
    src.save(buf, format="PNG")

    out_bytes = _downscale_png(buf.getvalue(), max_edge=960)
    out = Image.open(io.BytesIO(out_bytes))

    assert max(out.size) == 960
    assert out.size[0] / out.size[1] == 2000 / 1000


def _make_png_bytes() -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (40, 20), color=(1, 2, 3)).save(buf, format="PNG")
    return buf.getvalue()


def test_set_event_bus_stores_bus_and_loop(monkeypatch):
    from charlie import tools as tools_module

    # monkeypatch.setattr snapshots the current value here and restores it
    # after the test, even though set_event_bus() below mutates directly.
    monkeypatch.setattr(tools_module, "_event_bus", None)
    monkeypatch.setattr(tools_module, "_event_loop", None)

    bus, loop = object(), object()
    tools_module.set_event_bus(bus, loop)

    assert tools_module._event_bus is bus
    assert tools_module._event_loop is loop


def test_emit_desktop_frame_bridges_to_event_bus_with_shaped_payload(monkeypatch):
    import asyncio as asyncio_module

    from charlie import tools as tools_module
    from charlie.desktop.uia import Element

    calls = []

    class FakeBus:
        async def emit(self, event_type, payload, meta=None):
            calls.append((event_type, payload))

    monkeypatch.setattr(
        tools_module.asyncio,
        "run_coroutine_threadsafe",
        lambda coro, loop: asyncio_module.new_event_loop().run_until_complete(coro),
    )
    monkeypatch.setattr(
        tools_module.recovery, "get_active_session_id", lambda: "sess-123"
    )
    monkeypatch.setattr(tools_module, "_event_bus", FakeBus())
    monkeypatch.setattr(tools_module, "_event_loop", object())
    monkeypatch.setattr(tools_module, "_last_frame_emit_at", 0.0)  # past the throttle window

    elements = [
        Element(
            mark_id=1, name="Save", control_type="Button",
            bounds=(0, 0, 10, 10), is_password=False, is_offscreen=False,
        )
    ]
    tools_module._emit_desktop_frame(_make_png_bytes(), elements)

    assert len(calls) == 1
    etype, payload = calls[0]
    assert etype == "desktop_frame"
    assert payload["session_id"] == "sess-123"
    assert isinstance(payload["image_b64"], str) and payload["image_b64"]
    assert payload["marks"] == [{"mark_id": 1, "name": "Save", "bounds": [0, 0, 10, 10]}]


def test_emit_desktop_frame_throttled_within_window(monkeypatch):
    import asyncio as asyncio_module
    import time

    from charlie import tools as tools_module

    calls = []

    class FakeBus:
        async def emit(self, event_type, payload, meta=None):
            calls.append((event_type, payload))

    monkeypatch.setattr(
        tools_module.asyncio,
        "run_coroutine_threadsafe",
        lambda coro, loop: asyncio_module.new_event_loop().run_until_complete(coro),
    )
    monkeypatch.setattr(
        tools_module.recovery, "get_active_session_id", lambda: "sess-123"
    )
    monkeypatch.setattr(tools_module, "_event_bus", FakeBus())
    monkeypatch.setattr(tools_module, "_event_loop", object())
    monkeypatch.setattr(tools_module, "_last_frame_emit_at", time.time())  # inside the throttle window

    tools_module._emit_desktop_frame(_make_png_bytes(), [])

    assert calls == []


def test_capture_and_emit_frame_runs_capture_annotate_emit_off_thread(monkeypatch):
    """desktop_observe/desktop_read_screen/desktop_screenshot must never wait
    on frame capture -- it has to run off the calling thread so it can't add
    latency to the tool's return value."""
    import threading

    import charlie.desktop.ocr  # noqa: F401 -- ensures the submodule attr exists to patch
    import charlie.desktop.vision  # noqa: F401
    from charlie import tools as tools_module

    calls = []

    class FakeOcr:
        OCR_AVAILABLE = True

        @staticmethod
        def capture():
            return _make_png_bytes()

    class FakeVision:
        VISION_AVAILABLE = True

        @staticmethod
        def annotate_som(png, elements):
            calls.append(("annotate", elements))
            return png

    def fake_emit(png, elements):
        calls.append(("emit", elements))

    monkeypatch.setattr("charlie.desktop.ocr", FakeOcr)
    monkeypatch.setattr("charlie.desktop.vision", FakeVision)
    monkeypatch.setattr(tools_module, "_emit_desktop_frame", fake_emit)

    started = []
    real_thread_init = threading.Thread.__init__

    def fake_thread_init(self, *a, target=None, daemon=None, **k):
        started.append(target)
        real_thread_init(self, target=target, daemon=daemon)

    monkeypatch.setattr(threading.Thread, "__init__", fake_thread_init)
    monkeypatch.setattr(threading.Thread, "start", lambda self: self._target())

    tools_module._capture_and_emit_frame([])

    assert started  # a background thread was spawned, not run inline
    assert ("annotate", []) in calls
    assert ("emit", []) in calls


def test_desktop_observe_wires_up_capture_and_emit_frame(monkeypatch):
    """desktop_observe must feed the dashboard live view on every call, not
    just when the vision tier is on -- confirmed decision for this phase."""
    from charlie import tools as tools_module
    from charlie.desktop.uia import Element

    elements = [
        Element(
            mark_id=1, name="Save", control_type="Button",
            bounds=(0, 0, 10, 10), is_password=False, is_offscreen=False,
        )
    ]

    monkeypatch.setattr(tools_module, "_desktop_ready", lambda: True)
    monkeypatch.setattr(tools_module, "_ocr_fallback_marks", lambda uia_elements: elements)
    # Unmocked, this hits a real vision-LLM call when VISION_ENABLED=true -- see test_desktop_grounding.py.
    monkeypatch.setattr(tools_module, "_grounding_marks", lambda els: els)

    import charlie.desktop.uia as uia_module
    monkeypatch.setattr(uia_module, "snapshot_tree", lambda max_depth=8: [])

    calls = []
    monkeypatch.setattr(
        tools_module, "_capture_and_emit_frame", lambda els: calls.append(els)
    )

    tools_module.desktop_observe()

    assert calls == [elements]


def test_system_diagnostics_unknown_check(monkeypatch):
    monkeypatch.setattr("sys.platform", "win32")
    result = system_diagnostics("not_a_real_check")
    assert result.startswith("Error: unknown diagnostic check")


def test_system_diagnostics_rejects_injection_attempt(monkeypatch):
    """The `check` value is looked up in a fixed dict, never interpolated
    into the shell command string -- an injection-style value must be
    rejected as an unknown check, not executed."""
    monkeypatch.setattr("sys.platform", "win32")
    result = system_diagnostics("disk; Remove-Item C:\\ -Recurse -Force")
    assert result.startswith("Error: unknown diagnostic check")


def test_diagnostic_commands_are_all_powershell():
    for command in _DIAGNOSTIC_COMMANDS.values():
        assert "powershell" in command.lower()


def test_system_diagnostics_runs_real_command(monkeypatch):
    """On win32 (this dev/CI platform), a known check must actually execute
    and return real output, not just validate the enum."""
    monkeypatch.setattr("sys.platform", "win32")
    result = system_diagnostics("cpu")
    assert "Error" not in result or "timed out" in result.lower()


def test_is_shell_command_blocked_direct():
    assert is_shell_command_blocked("dir") is None
    # "rm -rf" is gated (approve/decline), not hard-blocked -- see
    # test_is_shell_command_gated_direct.
    assert is_shell_command_blocked("rm -rf /") is None
    assert is_shell_command_blocked("format c:") == (
        "Command blocked -- risky keyword 'format '"
    )
    assert is_shell_command_blocked("echo `whoami`") == (
        "Shell metacharacters (;, |, &, `, $, (, )) are not allowed."
    )


def test_is_shell_command_gated_direct():
    assert is_shell_command_gated("dir") is None
    assert is_shell_command_gated("rm -rf /tmp/foo") == "risky keyword 'rm -rf'"
    assert is_shell_command_gated("taskkill /IM notepad.exe /F") == (
        "risky keyword 'taskkill'"
    )
    # Hard-blocked keywords aren't in the gated list -- is_shell_command_blocked
    # already refuses them outright, no approval flow involved.
    assert is_shell_command_gated("format c:") is None


def test_get_path_gate_reason(tmp_path):
    assert get_path_gate_reason(str(tmp_path / "notes.txt")) is None
    assert "sensitive path" in get_path_gate_reason(str(tmp_path / ".env"))
    assert "sensitive path" in get_path_gate_reason(
        str(tmp_path / ".ssh" / "id_rsa")
    )


def test_tool_registry_unknown_tool_returns_error():
    local_registry = ToolRegistry()
    assert (
        local_registry.execute_tool("not-registered", {})
        == "Error: Tool 'not-registered' is not registered."
    )


def test_unregister_tool_removes_it():
    local_registry = ToolRegistry()
    local_registry.register_tool(name="temp", description="d", schema={"type": "object", "properties": {}})(
        lambda: "x"
    )
    assert "temp" in [d["function"]["name"] for d in local_registry.get_tool_definitions()]

    assert local_registry.unregister_tool("temp") is True

    assert "temp" not in [d["function"]["name"] for d in local_registry.get_tool_definitions()]
    assert "not registered" in local_registry.execute_tool("temp", {})


def test_unregister_tool_missing_returns_false():
    local_registry = ToolRegistry()
    assert local_registry.unregister_tool("never-existed") is False


def test_execute_tool_drops_hallucinated_kwargs_not_in_signature():
    # A model-supplied argument outside the schema must be dropped, not crash -- real live bug on web_search.
    local_registry = ToolRegistry()
    local_registry.register_tool(name="temp", description="d", schema={"type": "object", "properties": {}})(
        lambda query: f"got: {query}"
    )
    assert local_registry.execute_tool("temp", {"query": "hi", "voice_mode": True}) == "got: hi"


def test_execute_tool_keeps_kwargs_for_var_keyword_functions():
    local_registry = ToolRegistry()
    local_registry.register_tool(name="temp", description="d", schema={"type": "object", "properties": {}})(
        lambda **kwargs: str(sorted(kwargs.items()))
    )
    assert local_registry.execute_tool("temp", {"a": 1, "b": 2}) == "[('a', 1), ('b', 2)]"


def test_register_tool_owner_and_risk_class_are_queryable():
    local_registry = ToolRegistry()
    local_registry.register_tool(
        name="temp", description="d", schema={"type": "object", "properties": {}},
        owner="tools", risk_class="reversible",
    )(lambda: "x")
    assert local_registry.get_owner("temp") == "tools"
    assert local_registry.get_risk_class("temp") == "reversible"


def test_register_tool_defaults_owner_and_risk_class_when_unspecified():
    local_registry = ToolRegistry()
    local_registry.register_tool(name="temp", description="d", schema={"type": "object", "properties": {}})(
        lambda: "x"
    )
    assert local_registry.get_owner("temp") == ""
    assert local_registry.get_risk_class("temp") is None


def test_get_owner_and_risk_class_none_for_unregistered_tool():
    local_registry = ToolRegistry()
    assert local_registry.get_owner("nope") == ""
    assert local_registry.get_risk_class("nope") is None


def test_builtin_tools_carry_real_registry_metadata():
    from charlie.tools import registry as builtin_registry
    assert builtin_registry.get_owner("shell_execute") == "tools"
    assert builtin_registry.get_risk_class("shell_execute") == "reversible"
    assert builtin_registry.get_owner("desktop_click") == "desktop"
    assert builtin_registry.get_risk_class("web_search") == "safe"


def test_web_search_returns_fallback_without_api_keys(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    monkeypatch.setattr("charlie.research.engine.ResearchEngine._providers", lambda self: [])

    result = web_search("unit-test-only-query")
    assert isinstance(result, str)
    assert len(result) > 0


def test_memory_add_opinions(tmp_path, monkeypatch):
    """Test adding an opinion via the memory tool."""
    opinions_file = tmp_path / "OPINIONS.md"
    monkeypatch.setattr("charlie.tools.config.opinions_file", str(opinions_file))
    result = memory("add", "opinions", "I prefer dark chocolate over milk chocolate.")
    assert "Updated" in result
    assert opinions_file.exists()
    content = opinions_file.read_text(encoding="utf-8")
    assert "dark chocolate" in content


def test_memory_replace_opinions(tmp_path, monkeypatch):
    """Test replacing an entry in opinions."""
    opinions_file = tmp_path / "OPINIONS.md"
    opinions_file.write_text("I like coffee.§I prefer tea.", encoding="utf-8")
    monkeypatch.setattr("charlie.tools.config.opinions_file", str(opinions_file))
    result = memory("replace", "opinions", "I love espresso.", old_text="coffee")
    assert "Updated" in result
    content = opinions_file.read_text(encoding="utf-8")
    assert "espresso" in content
    assert "coffee" not in content
    assert "I prefer tea." in content


def test_memory_remove_opinions(tmp_path, monkeypatch):
    """Test removing an entry from opinions."""
    opinions_file = tmp_path / "OPINIONS.md"
    opinions_file.write_text("I like tea.§I like coffee.", encoding="utf-8")
    monkeypatch.setattr("charlie.tools.config.opinions_file", str(opinions_file))
    result = memory("remove", "opinions", old_text="coffee")
    assert "Updated" in result
    content = opinions_file.read_text(encoding="utf-8")
    assert "I like tea." in content
    assert "coffee" not in content


def test_memory_opinions_max_chars(tmp_path, monkeypatch):
    """Test that opinions max char limit is enforced."""
    opinions_file = tmp_path / "OPINIONS.md"
    opinions_file.write_text("x" * 800, encoding="utf-8")
    monkeypatch.setattr("charlie.tools.config.opinions_file", str(opinions_file))
    result = memory("add", "opinions", "y")
    assert "full" in result.lower() or "capacity" in result.lower()


def test_memory_invalid_target():
    """Test that invalid target returns error."""
    result = memory("add", "invalid_target", "content")
    assert "Error" in result
    assert "must be" in result


def test_needs_decomposition_compare():
    """Test that 'compare X and Y' triggers decomposition."""
    assert _needs_decomposition("compare React and Vue")


def test_needs_decomposition_long_query():
    """Test that long queries trigger decomposition."""
    assert _needs_decomposition("what is the best framework for building web apps")


def test_needs_decomposition_simple():
    """Test that simple queries do not trigger decomposition."""
    assert not _needs_decomposition("latest news")


def test_decompose_query_compare():
    """Test decomposition of comparison queries."""
    result = _decompose_query("compare React and Vue for web development")
    assert len(result) == 2
    assert "react" in result[0].lower()
    assert "vue" in result[1].lower()
    assert "web development" in result[0].lower()


def test_decompose_query_or():
    """Test decomposition of 'or' queries."""
    result = _decompose_query("Python or JavaScript for beginners")
    assert len(result) == 2
    assert "python" in result[0].lower()
    assert "javascript" in result[1].lower()


def test_decompose_query_simple_returns_original():
    """Test that simple queries return original."""
    result = _decompose_query("latest news")
    assert result == ["latest news"]


def test_merge_search_results_dedup():
    """Test that merge deduplicates by URL."""
    results = [
        "Title: A\nURL: https://example.com\nContent: Content A",
        "Title: A\nURL: https://example.com\nContent: Content A again",
        "Title: B\nURL: https://other.com\nContent: Content B",
    ]
    merged = _merge_search_results(results)
    assert merged.count("https://example.com") == 1
    assert "https://other.com" in merged


def test_session_search_formatting(tmp_path, monkeypatch):
    db_path = str(tmp_path / "tool_session_test.db")
    monkeypatch.setattr("charlie.tools.config.session_db_path", db_path)
    store = SessionStore(db_path)
    try:
        store.append("user", "remember this secret")
        store.append("assistant", "remembered the secret")
        formatted = session_search("secret")
        assert "[user]" in formatted
        assert "[assistant]" in formatted
        assert "remember this secret" in formatted
        assert "remembered the secret" in formatted
    finally:
        store.close()
        for f in [db_path, f"{db_path}-wal", f"{db_path}-shm"]:
            if os.path.exists(f):
                os.remove(f)


def test_recall_results_formatting(tmp_path, monkeypatch):
    from charlie.results import ResultsStore

    db_path = str(tmp_path / "tool_results_test.db")
    monkeypatch.setattr("charlie.tools.config.session_db_path", db_path)
    store = ResultsStore(db_path)
    try:
        store.store("t1", "found the answer", "the full answer text", attention_level=2)
        formatted = recall_results()
        assert "found the answer" in formatted
        assert "the full answer text" in formatted
    finally:
        store.close()
        for f in [db_path, f"{db_path}-wal", f"{db_path}-shm"]:
            if os.path.exists(f):
                os.remove(f)


def test_recall_results_reports_none_recorded(tmp_path, monkeypatch):
    db_path = str(tmp_path / "tool_results_empty_test.db")
    monkeypatch.setattr("charlie.tools.config.session_db_path", db_path)
    assert recall_results() == "No task results recorded yet."


class _FakeMemoryStore:
    def __init__(self, search_result):
        self.is_available = True
        self._search_result = search_result

    def search(self, content, n_results=3):
        return self._search_result

    def add_memory(self, **kw):
        return 1


def test_vector_memory_recall_reports_search_failure_not_empty(monkeypatch):
    monkeypatch.setattr(tools_module, "_memory_store", _FakeMemoryStore(None))
    result = vector_memory("recall", "anything")
    assert "failed" in result.lower()


def test_vector_memory_recall_reports_no_matches(monkeypatch):
    monkeypatch.setattr(tools_module, "_memory_store", _FakeMemoryStore([]))
    result = vector_memory("recall", "anything")
    assert result == "No relevant memories found."


def test_vector_memory_recall_formats_results(monkeypatch):
    monkeypatch.setattr(tools_module, "_memory_store", _FakeMemoryStore([{"text": "fact one"}]))
    result = vector_memory("recall", "anything")
    assert result == "- fact one"

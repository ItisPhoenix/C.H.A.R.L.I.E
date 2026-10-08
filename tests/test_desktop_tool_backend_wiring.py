"""Charlie desktop tools must reach the backend on both dispatch paths.

The bug these pin: the backend import and return were nested inside the
``if not _desktop_ready():`` block. With desktop control *enabled* the guard was
skipped, the function fell off the end, and Python returned ``None``. Charlie read
that as a failed action and retried, which is what made the apps reopen repeatedly.

So both paths are asserted: the direct ``tools.desktop_click(...)`` call and
``registry.execute_tool("desktop_click", ...)``. They must agree, both must be
strings, and both must actually reach the backend.

Evidence class: TEST/MOCK.
"""

from __future__ import annotations

import pytest

from charlie import tools as T


class RecordingBackend:
    """Stands in for the Cua backend and records which method was reached."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    def _record(self, name: str, *args) -> str:
        self.calls.append((name, args))
        return f"{name}-ok"

    def observe(self) -> str:
        return self._record("observe")

    def click_mark(self, mark_id) -> str:
        return self._record("click_mark", mark_id)

    def type_text(self, mark_id, text) -> str:
        return self._record("type_text", mark_id, text)

    def key_press(self, keys) -> str:
        return self._record("key_press", keys)

    def scroll(self, notches) -> str:
        return self._record("scroll", notches)

    def drag(self, x1, y1, x2, y2) -> str:
        return self._record("drag", x1, y1, x2, y2)

    def open_apps(self, apps) -> str:
        return self._record("open_apps", tuple(apps))

    def open_app(self, apps, commands=None) -> str:
        return self._record("open_app", apps)


@pytest.fixture
def backend(monkeypatch):
    """Install a recording backend and make the desktop guard pass."""
    from charlie.computer import backend as backend_module

    fake = RecordingBackend()
    monkeypatch.setattr(backend_module, "get_backend", lambda: fake)
    monkeypatch.setattr(T, "_desktop_ready", lambda: True)
    return fake


DIRECT_CASES = [
    ("desktop_observe", (), "observe"),
    ("desktop_click", (7,), "click_mark"),
    ("desktop_type", (7, "hi"), "type_text"),
    ("desktop_key", ("escape",), "key_press"),
    ("desktop_scroll", (3,), "scroll"),
    ("desktop_drag", (1, 2, 3, 4), "drag"),
]

REGISTRY_CASES = [
    ("desktop_observe", {}, "observe"),
    ("desktop_click", {"mark_id": 7}, "click_mark"),
    ("desktop_type", {"mark_id": 7, "text": "hi"}, "type_text"),
    ("desktop_key", {"keys": "escape"}, "key_press"),
    ("desktop_scroll", {"notches": 3}, "scroll"),
    ("desktop_drag", {"x1": 1, "y1": 2, "x2": 3, "y2": 4}, "drag"),
]


class TestDirectPathReturnsAString:
    @pytest.mark.parametrize("tool,args,method", DIRECT_CASES)
    def test_direct_call_reaches_the_backend(self, backend, tool, args, method):
        result = getattr(T, tool)(*args)
        assert isinstance(result, str), f"{tool} returned {type(result).__name__}, not str"
        assert result != "None"
        assert method in [name for name, _ in backend.calls], (
            f"{tool} did not reach the backend; calls={backend.calls}"
        )

    @pytest.mark.parametrize("tool,args,method", DIRECT_CASES)
    def test_no_implicit_none_return(self, backend, tool, args, method):
        """Guards the exact regression: falling off the end returns None."""
        assert getattr(T, tool)(*args) is not None


class TestRegistryPathReturnsAString:
    @pytest.mark.parametrize("tool,args,method", REGISTRY_CASES)
    def test_registry_dispatch_reaches_the_backend(self, backend, tool, args, method):
        result = T.registry.execute_tool(tool, dict(args))
        assert isinstance(result, str), f"{tool} returned {type(result).__name__}, not str"
        assert result != "None"
        assert method in [name for name, _ in backend.calls], (
            f"registry {tool} did not reach the backend; calls={backend.calls}"
        )

    def test_both_paths_agree(self, backend):
        direct = T.desktop_click(11)
        via_registry = T.registry.execute_tool("desktop_click", {"mark_id": 11})
        assert direct == via_registry

    def test_observe_agrees_across_paths(self, backend):
        assert T.desktop_observe() == T.registry.execute_tool("desktop_observe", {})


class TestRegisteredBindingIsTheModuleFunction:
    """The registry must hold the same object the module exposes."""

    @pytest.mark.parametrize(
        "tool",
        ["desktop_observe", "desktop_click", "desktop_type", "desktop_key",
         "desktop_scroll", "desktop_drag", "desktop_open_app"],
    )
    def test_registered_func_is_the_module_attribute(self, tool):
        entry = T.registry._tools.get(tool) or {}
        assert entry.get("func") is not None, f"{tool} is not registered"
        assert entry["func"] is getattr(T, tool), (
            f"{tool} is registered as a different object than tools.{tool}"
        )

    @pytest.mark.parametrize(
        "tool", ["desktop_observe", "desktop_click", "desktop_type", "desktop_key",
                 "desktop_scroll", "desktop_drag"]
    )
    def test_registered_func_has_no_implicit_none_path(self, tool):
        """A str-returning tool must not have a bare ``return None`` of its own."""
        import dis

        code = list(dis.Bytecode(T.registry._tools[tool]["func"]))
        bare_none = [
            i for i in code if i.opname == "LOAD_CONST" and i.argval is None
        ]
        assert not bare_none, (
            f"{tool} still contains an implicit `return None` path; the backend "
            "call is probably nested inside the desktop_ready guard"
        )


class TestGuardStillShortCircuits:
    def test_disabled_desktop_control_does_not_touch_the_backend(self, monkeypatch):
        from charlie.computer import backend as backend_module

        fake = RecordingBackend()
        monkeypatch.setattr(backend_module, "get_backend", lambda: fake)
        monkeypatch.setattr(T, "_desktop_ready", lambda: False)
        for tool, args in (("desktop_click", (1,)), ("desktop_observe", ()),
                           ("desktop_key", ("escape",))):
            result = getattr(T, tool)(*args)
            assert isinstance(result, str)
            assert result == T._DESKTOP_DISABLED_MSG
        assert fake.calls == [], "the backend must not be called when disabled"


def test_type_text_uses_set_value_when_the_fresh_element_exposes_it(monkeypatch):
    from charlie.computer import backend as backend_module
    from charlie.computer.backend import DesktopBackend, _Snapshot
    from charlie.computer.cua_adapter import CuaActionResult, CuaOutcome

    calls = []

    class Adapter:
        def set_value(self, pid, text, **kwargs):
            calls.append(("set_value", pid, text, kwargs))
            return CuaActionResult(outcome=CuaOutcome.EXECUTED_UNVERIFIED)

        def type_text(self, *args, **kwargs):
            calls.append(("type_text", args, kwargs))
            return CuaActionResult(outcome=CuaOutcome.EXECUTED_UNVERIFIED)

    backend = DesktopBackend()
    backend._target = {"pid": 10, "window_id": 20, "create_time": 30.0}
    backend._snapshot = _Snapshot(
        10,
        20,
        {0: "token"},
        {"elements": [{"element_index": 0, "actions": ["set_value"]}]},
    )
    monkeypatch.setattr(backend_module, "_window_target_is_current", lambda _target: True)
    monkeypatch.setattr(backend, "_ensure", lambda target=None: Adapter())

    backend.type_text(0, "fixture")

    assert calls[0][0] == "set_value"
    assert calls[0][1:3] == (10, "fixture")


def test_type_text_reads_back_the_set_value_from_a_fresh_snapshot(monkeypatch):
    from charlie.computer import backend as backend_module
    from charlie.computer.backend import DesktopBackend, _Snapshot
    from charlie.computer.cua_adapter import CuaActionResult, CuaOutcome

    class Adapter:
        def set_value(self, *_args, **_kwargs):
            return CuaActionResult(outcome=CuaOutcome.EXECUTED_UNVERIFIED)

        def window_state(self, *_args, **_kwargs):
            return {"elements": [{"role": "Document", "label": "Text editor", "value": "fixture"}]}

    backend = DesktopBackend()
    backend._target = {"pid": 10, "window_id": 20, "create_time": 30.0}
    backend._snapshot = _Snapshot(
        10, 20, {0: "token"},
        {"elements": [{"element_index": 0, "role": "Document", "label": "Text editor", "actions": ["set_value"]}]},
    )
    monkeypatch.setattr(backend_module, "_window_target_is_current", lambda _target: True)
    monkeypatch.setattr(backend, "_ensure", lambda target=None: Adapter())

    result = backend.type_text(0, "fixture")

    assert "fresh UIA readback matches" in result
    assert backend._snapshot is None


def test_type_text_falls_back_to_exact_window_typing_only_for_no_value_pattern(monkeypatch):
    from charlie.computer import backend as backend_module
    from charlie.computer.backend import DesktopBackend, _Snapshot
    from charlie.computer.cua_adapter import CuaActionResult, CuaOutcome

    calls = []

    class Adapter:
        def set_value(self, *_args, **_kwargs):
            return CuaActionResult(
                outcome=CuaOutcome.FAILED,
                detail={"text": "element does not implement ValuePattern or RangeValuePattern"},
            )

        def click(self, **kwargs):
            calls.append(("focus", kwargs))
            return CuaActionResult(outcome=CuaOutcome.EXECUTED_UNVERIFIED)

        def type_text(self, text, **kwargs):
            calls.append(("type", text, kwargs))
            return CuaActionResult(outcome=CuaOutcome.EXECUTED_UNVERIFIED)

        def window_state(self, *_args, **_kwargs):
            return {"elements": [{"role": "Edit", "label": "File name:", "value": "fixture"}]}

    backend = DesktopBackend()
    backend._target = {"pid": 10, "window_id": 20, "create_time": 30.0}
    backend._snapshot = _Snapshot(
        10, 20, {129: "filename-token"},
        {"elements": [{"element_index": 129, "role": "Edit", "label": "File name:", "actions": ["set_value"]}]},
    )
    monkeypatch.setattr(backend_module, "_window_target_is_current", lambda _target: True)
    monkeypatch.setattr(backend, "_ensure", lambda target=None: Adapter())

    result = backend.type_text(129, "fixture")

    assert [call[0] for call in calls] == ["focus", "type"]
    assert "element_token" not in calls[1][2]
    assert "fresh UIA readback matches" in result


def test_key_chord_splits_into_cua_key_and_modifier_fields(monkeypatch):
    from charlie.computer import backend as backend_module
    from charlie.computer.backend import DesktopBackend, _Snapshot
    from charlie.computer.cua_adapter import CuaActionResult, CuaOutcome

    calls = []

    class Adapter:
        def press_key(self, key, **kwargs):
            calls.append((key, kwargs))
            return CuaActionResult(outcome=CuaOutcome.EXECUTED_UNVERIFIED)

    backend = DesktopBackend()
    backend._target = {"pid": 10, "window_id": 20, "create_time": 30.0}
    backend._snapshot = _Snapshot(10, 20, {}, {})
    monkeypatch.setattr(backend_module, "_window_target_is_current", lambda _target: True)
    monkeypatch.setattr(backend, "_ensure", lambda target=None: Adapter())

    backend.key_press("Ctrl+S")

    assert calls == [("s", {"modifiers": ["ctrl"], "pid": 10, "window_id": 20})]


def test_exact_target_stale_refusal_rebinds_once_to_same_named_window(monkeypatch):
    from charlie.computer import backend as backend_module
    from charlie.computer.backend import DesktopBackend
    from charlie.computer.cua_adapter import CuaActionResult, CuaOutcome

    original = {"pid": 20, "window_id": 30, "title": "Calculator", "create_time": 1.0}
    refreshed = {"pid": 20, "window_id": 31, "title": "Calculator", "create_time": 1.0}
    targets = []

    class Adapter:
        def __init__(self, result):
            self.result = result

        def focus_window(self, pid, window_id):
            targets.append((pid, window_id))
            return self.result

    results = [
        CuaActionResult(
            outcome=CuaOutcome.REFUSED,
            summary="window resource was refused",
            detail={"refusal": {"code": "bounded_resource_outside_manifest"}},
        ),
        CuaActionResult(outcome=CuaOutcome.CONFIRMED, summary="exact target landed"),
    ]
    backend = DesktopBackend()
    monkeypatch.setattr(backend_module, "_window_target_is_current", lambda _target: True)
    monkeypatch.setattr(backend_module, "_existing_native_window", lambda _name: refreshed)
    monkeypatch.setattr(backend, "_ensure", lambda _target=None: Adapter(results.pop(0)))
    monkeypatch.setattr(backend, "observe", lambda window_target=None: "Observed exact target.")

    result = backend.bind_window_target(original)

    assert result.startswith("Opened the app and confirmed")
    assert targets == [(20, 30), (20, 31)]

    refused = CuaActionResult(
        outcome=CuaOutcome.REFUSED, summary="foreground_activation_blocked"
    )
    before = len(targets)
    backend = DesktopBackend()
    monkeypatch.setattr(backend, "_ensure", lambda _target=None: Adapter(refused))

    result = backend.bind_window_target(original)

    assert result.startswith("Refused: foreground_activation_blocked")
    assert targets[before:] == [(20, 30)]


def test_window_close_uses_cua_and_waits_for_disappearance(monkeypatch):
    from charlie.computer import backend as backend_module
    from charlie.computer.backend import DesktopBackend
    from charlie.computer.cua_adapter import CuaActionResult, CuaOutcome

    calls = []
    target = {"pid": 20, "window_id": 30, "title": "Calculator", "create_time": 1.0}

    class Adapter:
        def invoke_menu(self, pid, window_id, path):
            calls.append((pid, window_id, path))
            return CuaActionResult(outcome=CuaOutcome.EXECUTED_UNVERIFIED)

    current = iter((True, False))
    monkeypatch.setattr(backend_module, "_existing_native_window", lambda _name: target)
    monkeypatch.setattr(backend_module, "_window_target_is_current", lambda _target: next(current))
    monkeypatch.setattr(DesktopBackend, "_ensure", lambda _self, _target=None: Adapter())

    result = DesktopBackend().manage_window("Calculator", "close")

    assert result == "Closed the exact window and verified it is gone."
    assert calls == [(20, 30, ["System", "Close"])]


class TestOpenAppUsesTheCanonicalLauncher:
    """desktop_open_app must keep charlie.desktop.apps.launch_apps as its owner.

    App-name resolution, the optional ``commands`` argument and the world-model
    ``app_open`` event all live behind launch_apps. The Cua backend observes and
    drives already-focused windows; it does not replace the launcher.
    """

    def test_open_app_uses_launch_apps_and_honours_commands(self, monkeypatch):
        import charlie.desktop.apps as apps
        import charlie.desktop.windows as windows
        from charlie.computer import backend as backend_module

        seen: dict[str, object] = {}

        def fake_launch(app_list, command_list):
            seen["apps"] = list(app_list)
            seen["commands"] = list(command_list or [])
            return "Opened: notepad"

        class Backend:
            def open_app(self, app_list, command_list):
                launch = apps.launch_apps(app_list, command_list)
                if "could not open" in launch.lower():
                    return launch
                window = windows.find_window_identity(app_list[-1])
                return self.bind_window_target(window) if window else "Refused: no exact window."

            def bind_window_target(self, target):
                seen["target"] = target
                return "Opened the app and confirmed its exact window through bounded Cua."

        monkeypatch.setattr("charlie.tools._desktop_ready", lambda: True)
        monkeypatch.setattr(apps, "launch_apps", fake_launch)
        monkeypatch.setattr(backend_module, "get_backend", lambda: Backend())
        monkeypatch.setattr(
            "charlie.desktop.windows.find_window_identity",
            lambda _name: {"pid": 42, "window_id": 77, "create_time": 123.0},
        )

        result = T.desktop_open_app(["notepad"], [r"C:\Windows\notepad.exe"])

        assert not isinstance(result, str)
        assert seen["apps"] == ["notepad"]
        assert seen["commands"] == [r"C:\Windows\notepad.exe"], (
            "the commands argument must reach the launcher, not be discarded"
        )
        assert seen["target"]["pid"] == 42
        assert getattr(result, "structured_data", {}).get("apps") == ["notepad"]
        assert getattr(result, "structured_data", {}).get("verified") is True


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))

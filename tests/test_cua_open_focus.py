"""Behavioural tests for the Cua adapter's open/focus sequence.

The bug these pin: an "open/show/focus" request relaunched an app that was already
running. Launching is only correct when no window for the app exists.

Evidence class: TEST/MOCK.
"""

from __future__ import annotations

import json

import pytest

from charlie.computer.cua_adapter import CuaActionResult, CuaAdapter, CuaOutcome


class FakeDriver:
    """Records Cua calls and replays scripted results."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.windows: list[dict] = []
        self.launch_payload: dict = {}

    async def call_tool(self, name: str, arguments_json: str):
        args = json.loads(arguments_json)
        self.calls.append((name, args))
        if name == "list_windows":
            return {"structured_json": json.dumps({"windows": self.windows})}
        if name == "launch_app":
            return {"structured_json": json.dumps(self.launch_payload)}
        if name == "bring_to_front":
            return {
                "structured_json": json.dumps(
                    {
                        "landed_on_target": True,
                        "now_fg_hwnd": "0x1234",
                        "target_hwnd": "0x1234",
                    }
                )
            }
        return {"structured_json": json.dumps({"effect": "confirmed"})}

    async def shutdown(self) -> None:
        return None

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


def _adapter_with(windows: list[dict], launch_payload: dict | None = None):
    from charlie.computer.cua_bridge import CuaRuntimeBridge

    driver = FakeDriver()
    driver.windows = windows
    driver.launch_payload = launch_payload or {}
    bridge = CuaRuntimeBridge(driver_factory=lambda: driver)
    bridge.start()
    return CuaAdapter(bridge), driver


CALC_WINDOW = {
    "window_id": 4660,
    "pid": 11944,
    "app_name": "ApplicationFrameHost.exe",
    "title": "Calculator",
}


class TestOpenAndFocusDoesNotRelaunch:
    def test_an_already_running_app_is_focused_not_relaunched(self):
        adapter, driver = _adapter_with([CALC_WINDOW])
        try:
            result = adapter.open_and_focus(name="calc")
            assert result.outcome is CuaOutcome.CONFIRMED
            assert "launch_app" not in driver.names(), (
                "an app that is already running must not be launched again"
            )
            assert "bring_to_front" in driver.names()
        finally:
            adapter.shutdown()

    def test_launch_happens_only_when_no_window_matches(self):
        adapter, driver = _adapter_with([], launch_payload={"pid": 555, "running": True})
        try:
            adapter.open_and_focus(name="calc")
            assert "launch_app" in driver.names(), (
                "no window existed, so the app must be launched"
            )
        finally:
            adapter.shutdown()

    def test_bounded_global_enumeration_refusal_falls_to_verified_launch(self):
        class _Refusing(FakeDriver):
            async def call_tool(self, name, arguments_json):
                if name == "list_windows":
                    raise RuntimeError("global enumeration is outside the manifest")
                return await super().call_tool(name, arguments_json)

        from charlie.computer.cua_bridge import CuaRuntimeBridge

        driver = _Refusing()
        driver.launch_payload = {"pid": 555, "windows": [dict(CALC_WINDOW, pid=555)]}
        bridge = CuaRuntimeBridge(driver_factory=lambda: driver)
        bridge.start()
        adapter = CuaAdapter(bridge)
        try:
            adapter.open_and_focus(name="calc")
            assert "launch_app" in driver.names()
        finally:
            adapter.shutdown()

    def test_focus_exact_window_does_not_require_global_app_authorization(self):
        class _NoEnumeration(FakeDriver):
            async def call_tool(self, name, arguments_json):
                if name == "list_windows":
                    raise AssertionError("an exact-window runtime must not enumerate globally")
                return await super().call_tool(name, arguments_json)

        from charlie.computer.cua_bridge import CuaRuntimeBridge

        driver = _NoEnumeration()
        bridge = CuaRuntimeBridge(driver_factory=lambda: driver)
        bridge.start()
        adapter = CuaAdapter(bridge)
        try:
            result = adapter.focus_window(CALC_WINDOW["pid"], CALC_WINDOW["window_id"])
            assert result.outcome is CuaOutcome.CONFIRMED
            assert "list_windows" not in driver.names()
            assert "get_window_state" in driver.names()
        finally:
            adapter.shutdown()

    def test_exact_focus_refuses_when_fresh_window_observation_is_denied(self):
        class _ObservationRefused(FakeDriver):
            async def call_tool(self, name, arguments_json):
                if name == "get_window_state":
                    return {
                        "structured_json": json.dumps({
                            "refusal": {"code": "bounded_resource_outside_manifest"},
                            "status": "refused",
                        }),
                        "is_error": True,
                    }
                return await super().call_tool(name, arguments_json)

        from charlie.computer.cua_bridge import CuaRuntimeBridge

        driver = _ObservationRefused()
        bridge = CuaRuntimeBridge(driver_factory=lambda: driver)
        bridge.start()
        adapter = CuaAdapter(bridge)
        try:
            result = adapter.focus_window(CALC_WINDOW["pid"], CALC_WINDOW["window_id"])
            assert result.outcome is CuaOutcome.REFUSED
            assert "window state" in result.summary.lower()
        finally:
            adapter.shutdown()

    def test_a_window_is_reused_when_its_process_name_is_requested(self):
        """Calculator lives in ApplicationFrameHost.exe; requesting that host reuses it."""
        adapter, driver = _adapter_with([CALC_WINDOW])
        try:
            adapter.open_and_focus(name="ApplicationFrameHost.exe")
            assert "launch_app" not in driver.names(), (
                "the requested process already has a window, so it must be reused"
            )
        finally:
            adapter.shutdown()

    def test_an_unrelated_process_is_launched(self):
        adapter, driver = _adapter_with([CALC_WINDOW])
        try:
            adapter.open_and_focus(name="notepad")
            assert "launch_app" in driver.names(), (
                "a different app must still be launched"
            )
        finally:
            adapter.shutdown()

    def test_notepad_is_not_relaunched_either(self):
        window = {
            "window_id": 99,
            "pid": 20720,
            "app_name": "Notepad.exe",
            "title": "Untitled - Notepad",
        }
        adapter, driver = _adapter_with([window])
        try:
            adapter.open_and_focus(name="notepad")
            assert "launch_app" not in driver.names()
        finally:
            adapter.shutdown()

    def test_matching_is_case_insensitive(self):
        adapter, driver = _adapter_with([CALC_WINDOW])
        try:
            adapter.open_and_focus(name="CALCULATOR")
            assert "launch_app" not in driver.names()
        finally:
            adapter.shutdown()

    def test_a_minimised_window_is_still_reused(self):
        """Reuse must not depend on the window being on screen."""
        window = dict(CALC_WINDOW, minimized=True, is_on_screen=False)
        adapter, driver = _adapter_with([window])
        try:
            adapter.open_and_focus(name="calc")
            assert "launch_app" not in driver.names()
        finally:
            adapter.shutdown()


class TestForegroundStillVerifiedAfterReuse:
    def test_reuse_still_requires_confirmed_foreground(self):
        """Reusing a window must not weaken the foreground requirement."""

        class _NoLand(FakeDriver):
            async def call_tool(self, name, arguments_json):
                if name == "bring_to_front":
                    return {"structured_json": json.dumps({"landed_on_target": False})}
                return await FakeDriver.call_tool(self, name, arguments_json)

        from charlie.computer.cua_bridge import CuaRuntimeBridge

        driver = _NoLand()
        driver.windows = [CALC_WINDOW]
        bridge = CuaRuntimeBridge(driver_factory=lambda: driver)
        bridge.start()
        adapter = CuaAdapter(bridge)
        try:
            result = adapter.open_and_focus(name="calc")
            assert result.outcome is CuaOutcome.REFUSED
            assert result.summary
        finally:
            adapter.shutdown()

    def test_result_is_never_a_bare_success(self):
        adapter, _ = _adapter_with([CALC_WINDOW])
        try:
            result = adapter.open_and_focus(name="calc")
            assert isinstance(result, CuaActionResult)
            assert result.summary
        finally:
            adapter.shutdown()

def test_typed_window_lists_are_accepted_as_lists():
    windows = CuaAdapter._windows([CALC_WINDOW])
    assert windows == [CALC_WINDOW]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))

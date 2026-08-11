import sys
from unittest.mock import MagicMock

import pytest

from charlie.desktop import actions, uia


@pytest.fixture(autouse=True)
def _reset_capture_bounds():
    uia.set_last_capture_bounds(None)
    yield
    uia.set_last_capture_bounds(None)


@pytest.fixture
def fake_pyautogui(monkeypatch):
    fake = MagicMock()
    monkeypatch.setattr(actions, "pyautogui", fake, raising=False)
    monkeypatch.setattr(actions, "_HAS_PYAUTOGUI", True)
    actions.clear_halt()
    return fake


@pytest.mark.skipif(sys.platform != "win32", reason="real GetTickCount call, Windows only")
def test_check_halt_records_action_tick():
    actions._last_action_tick_ms = 0
    actions._check_halt()
    assert actions.last_action_tick_ms() > 0


def test_check_halt_still_raises_when_halted():
    actions.halt()
    try:
        with pytest.raises(actions.DesktopHalted):
            actions._check_halt()
    finally:
        actions.clear_halt()


def test_click_at_image_coords_translated(fake_pyautogui):
    uia.set_last_capture_bounds((100, 200, 900, 800))
    result = actions.click_at(50, 60)
    fake_pyautogui.click.assert_called_once_with(150, 260, button="left", clicks=1)
    assert "Clicked" in result


def test_click_at_without_capture_errors(fake_pyautogui):
    uia.set_last_capture_bounds(None)
    assert "Error" in actions.click_at(50, 60)


def test_click_at_right_button_and_double(fake_pyautogui):
    uia.set_last_capture_bounds((0, 0, 800, 600))
    actions.click_at(1, 2, button="right", double=True)
    fake_pyautogui.click.assert_called_once_with(1, 2, button="right", clicks=2)


def test_move_to_translates_coords(fake_pyautogui):
    uia.set_last_capture_bounds((100, 200, 900, 800))
    result = actions.move_to(50, 60)
    fake_pyautogui.moveTo.assert_called_once_with(150, 260)
    assert "Moved" in result


def test_drag_between_image_coords(fake_pyautogui):
    uia.set_last_capture_bounds((0, 0, 800, 600))
    actions.drag(10, 10, 200, 300)
    fake_pyautogui.moveTo.assert_called_once_with(10, 10)
    fake_pyautogui.dragTo.assert_called_once()


def test_drag_without_capture_errors(fake_pyautogui):
    uia.set_last_capture_bounds(None)
    assert "Error" in actions.drag(10, 10, 200, 300)


def test_scroll_negative_scrolls_down(fake_pyautogui):
    actions.scroll(-5)
    fake_pyautogui.scroll.assert_called_once_with(-5 * actions._SCROLL_UNIT)


def test_halted_effectors_raise(fake_pyautogui):
    actions.halt()
    with pytest.raises(actions.DesktopHalted):
        actions.click_at(1, 1)
    actions.clear_halt()


def test_halted_move_to_raises(fake_pyautogui):
    actions.halt()
    with pytest.raises(actions.DesktopHalted):
        actions.move_to(1, 1)
    actions.clear_halt()


def test_halted_drag_raises(fake_pyautogui):
    actions.halt()
    with pytest.raises(actions.DesktopHalted):
        actions.drag(1, 1, 2, 2)
    actions.clear_halt()


def test_halted_scroll_raises(fake_pyautogui):
    actions.halt()
    with pytest.raises(actions.DesktopHalted):
        actions.scroll(1)
    actions.clear_halt()


def test_system_control_volume_up(fake_pyautogui):
    result = actions.system_control("volume_up")
    fake_pyautogui.press.assert_called_once_with("volumeup")
    assert "Done" in result


def test_system_control_unknown_action_errors(fake_pyautogui):
    result = actions.system_control("teleport")
    assert "Error" in result
    assert "teleport" in result


def test_system_control_halted_raises(fake_pyautogui):
    actions.halt()
    with pytest.raises(actions.DesktopHalted):
        actions.system_control("mute")
    actions.clear_halt()


def test_invoke_mark_falls_back_to_click_for_ocr_sourced(fake_pyautogui):
    element = uia.Element(
        mark_id=0, name="Total", control_type="TextControl",
        bounds=(10, 10, 50, 50), is_password=False, is_offscreen=False,
    )
    marks = uia.merge_ocr_elements([], [element])
    result = actions.invoke_mark(marks[0].mark_id)
    fake_pyautogui.click.assert_called_once_with(30, 30)
    assert "Clicked" in result


class _FakeUiaControl:
    """Minimal stand-in for a live uiautomation control handle -- not a
    uia.Element, so click_mark/type_text/invoke_mark route it through the
    UIA-native branch instead of the OCR fallback."""

    def __init__(self, invoke_pattern=None, value_pattern=None, bounds=(10, 10, 50, 50)):
        self._invoke_pattern = invoke_pattern
        self._value_pattern = value_pattern
        self.Name = "Fake"
        self.AutomationId = ""
        self.IsPassword = False
        left, top, right, bottom = bounds

        class _Rect:
            pass

        rect = _Rect()
        rect.left, rect.top, rect.right, rect.bottom = left, top, right, bottom
        self.BoundingRectangle = rect

    def GetInvokePattern(self):
        return self._invoke_pattern

    def GetValuePattern(self):
        return self._value_pattern


def _register_live_control(mark_id: int, control: "_FakeUiaControl") -> None:
    with uia._lock:
        uia._controls[mark_id] = control


def test_click_mark_uses_uia_invoke_when_supported(fake_pyautogui):
    invoke_pattern = MagicMock()
    control = _FakeUiaControl(invoke_pattern=invoke_pattern)
    _register_live_control(1, control)
    result = actions.click_mark(1)
    invoke_pattern.Invoke.assert_called_once()
    fake_pyautogui.click.assert_not_called()
    assert "Invoked" in result


def test_click_mark_falls_back_to_pyautogui_when_invoke_unsupported(fake_pyautogui):
    control = _FakeUiaControl(invoke_pattern=None)
    _register_live_control(2, control)
    result = actions.click_mark(2)
    fake_pyautogui.click.assert_called_once_with(30, 30)
    assert "Clicked" in result


def test_type_text_uses_uia_set_value_when_supported(fake_pyautogui):
    value_pattern = MagicMock()
    control = _FakeUiaControl(value_pattern=value_pattern)
    _register_live_control(3, control)
    result = actions.type_text(3, "hello")
    value_pattern.SetValue.assert_called_once_with("hello")
    fake_pyautogui.click.assert_not_called()
    fake_pyautogui.typewrite.assert_not_called()
    assert "Typed" in result


def test_type_text_reports_verified_when_readback_matches(fake_pyautogui):
    value_pattern = MagicMock()
    value_pattern.Value = "hello"
    control = _FakeUiaControl(value_pattern=value_pattern)
    _register_live_control(6, control)
    result = actions.type_text(6, "hello")
    assert "verified" in result
    assert "did not match" not in result


def test_type_text_reports_mismatch_when_readback_differs(fake_pyautogui):
    value_pattern = MagicMock()
    value_pattern.Value = "wrong"
    control = _FakeUiaControl(value_pattern=value_pattern)
    _register_live_control(7, control)
    result = actions.type_text(7, "hello")
    assert "did not match" in result


def test_type_text_falls_back_to_pyautogui_when_set_value_unsupported(fake_pyautogui):
    control = _FakeUiaControl(value_pattern=None)
    _register_live_control(4, control)
    result = actions.type_text(4, "hello")
    fake_pyautogui.click.assert_called_once_with(30, 30)
    fake_pyautogui.typewrite.assert_called_once_with("hello", interval=0.02)
    assert "Typed" in result


def test_type_text_refuses_secure_field_before_uia_attempt(fake_pyautogui):
    value_pattern = MagicMock()
    control = _FakeUiaControl(value_pattern=value_pattern)
    control.IsPassword = True
    _register_live_control(5, control)
    result = actions.type_text(5, "secret")
    value_pattern.SetValue.assert_not_called()
    fake_pyautogui.click.assert_not_called()
    assert "secure field" in result.lower()


def test_invoke_mark_falls_back_through_click_when_invoke_unsupported(fake_pyautogui):
    control = _FakeUiaControl(invoke_pattern=None)
    _register_live_control(6, control)
    result = actions.invoke_mark(6)
    fake_pyautogui.click.assert_called_once_with(30, 30)
    assert "Clicked" in result

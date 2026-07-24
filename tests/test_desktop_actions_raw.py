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
    monkeypatch.setattr(actions, "pyautogui", fake)
    monkeypatch.setattr(actions, "_HAS_PYAUTOGUI", True)
    actions.clear_halt()
    return fake


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

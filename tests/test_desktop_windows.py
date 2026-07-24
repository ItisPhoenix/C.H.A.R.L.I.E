from unittest.mock import MagicMock, patch

from charlie.desktop import windows


def test_list_windows_returns_visible_titled_only(monkeypatch):
    fake = [(1, "Notepad", True), (2, "", True), (3, "Hidden", False)]
    monkeypatch.setattr(windows, "_enum_raw", lambda: fake)
    assert windows.list_windows() == [{"hwnd": 1, "title": "Notepad"}]


def test_find_window_matches_substring_case_insensitive(monkeypatch):
    monkeypatch.setattr(windows, "list_windows",
                        lambda: [{"hwnd": 7, "title": "Untitled - Notepad"}])
    assert windows.find_window("notepad")["hwnd"] == 7
    assert windows.find_window("chrome") is None


def test_manage_window_minimize_calls_showwindow(monkeypatch):
    monkeypatch.setattr(windows, "find_window", lambda _: {"hwnd": 5, "title": "Test"})
    mock_user32 = MagicMock()
    monkeypatch.setattr(windows, "_user32", mock_user32)
    result = windows.manage_window("test", "minimize")
    mock_user32.ShowWindow.assert_called_once_with(5, windows._SW_MINIMIZE)
    assert "Minimized" in result
    assert "Test" in result


def test_manage_window_close_posts_wm_close(monkeypatch):
    monkeypatch.setattr(windows, "find_window", lambda _: {"hwnd": 5, "title": "Test"})
    mock_user32 = MagicMock()
    monkeypatch.setattr(windows, "_user32", mock_user32)
    result = windows.manage_window("test", "close")
    mock_user32.PostMessageW.assert_called_once_with(5, windows._WM_CLOSE, 0, 0)
    assert "close" in result.lower()
    assert "Test" in result


def test_manage_window_unknown_action_errors(monkeypatch):
    monkeypatch.setattr(windows, "find_window", lambda _: {"hwnd": 5, "title": "Test"})
    result = windows.manage_window("test", "levitate")
    assert "Error" in result
    assert "minimize" in result
    assert "maximize" in result
    assert "restore" in result
    assert "close" in result


def test_manage_window_no_match_errors(monkeypatch):
    monkeypatch.setattr(windows, "find_window", lambda _: None)
    result = windows.manage_window("nonexistent", "minimize")
    assert "Error" in result
    assert "nonexistent" in result


def test_move_resize_window_calls_movewindow(monkeypatch):
    monkeypatch.setattr(windows, "find_window", lambda _: {"hwnd": 5, "title": "Test"})
    mock_user32 = MagicMock()
    monkeypatch.setattr(windows, "_user32", mock_user32)
    windows.move_resize_window("test", 10, 20, 300, 400)
    mock_user32.MoveWindow.assert_called_once_with(5, 10, 20, 300, 400, True)


def test_focus_window_no_match_errors(monkeypatch):
    monkeypatch.setattr(windows, "find_window", lambda _: None)
    result = windows.focus_window("nonexistent")
    assert "Error" in result
    assert "nonexistent" in result


def test_focus_window_success_does_not_use_alt_fallback(monkeypatch):
    monkeypatch.setattr(windows, "find_window", lambda _: {"hwnd": 5, "title": "Test"})
    mock_user32 = MagicMock()
    mock_user32.SetForegroundWindow.return_value = 1
    monkeypatch.setattr(windows, "_user32", mock_user32)
    with patch("pyautogui.press") as mock_press:
        result = windows.focus_window("test")
    mock_user32.ShowWindow.assert_called_once_with(5, windows._SW_RESTORE)
    mock_press.assert_not_called()
    assert "Test" in result

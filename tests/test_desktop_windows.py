from unittest.mock import MagicMock

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
    mock_user32.IsIconic.return_value = True
    monkeypatch.setattr(windows, "_user32", mock_user32)
    result = windows.manage_window("test", "minimize")
    mock_user32.ShowWindow.assert_called_once_with(5, windows._SW_MINIMIZE)
    mock_user32.IsIconic.assert_called_once_with(5)
    assert "Minimized" in result
    assert "Test" in result
    assert "(verified)" in result


def test_manage_window_minimize_reports_unconfirmed_when_state_check_fails(monkeypatch):
    monkeypatch.setattr(windows, "find_window", lambda _: {"hwnd": 5, "title": "Test"})
    mock_user32 = MagicMock()
    mock_user32.IsIconic.return_value = False
    monkeypatch.setattr(windows, "_user32", mock_user32)
    result = windows.manage_window("test", "minimize")
    assert "state unconfirmed" in result


def test_manage_window_close_posts_wm_close(monkeypatch):
    monkeypatch.setattr(windows, "find_window", lambda _: {"hwnd": 5, "title": "Test"})
    mock_user32 = MagicMock()
    monkeypatch.setattr(windows, "_user32", mock_user32)
    result = windows.manage_window("test", "close")
    mock_user32.PostMessageW.assert_called_once_with(5, windows._WM_CLOSE, 0, 0)
    assert "close" in result.lower()
    assert "Test" in result
    assert "asynchronous" in result


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


def test_focus_window_verified_when_foreground_hwnd_matches(monkeypatch):
    monkeypatch.setattr(windows, "find_window", lambda _: {"hwnd": 5, "title": "Test"})
    mock_user32 = MagicMock()
    mock_user32.SetForegroundWindow.return_value = 1
    mock_user32.GetForegroundWindow.return_value = 5
    monkeypatch.setattr(windows, "_user32", mock_user32)
    mock_pyautogui = MagicMock()
    monkeypatch.setattr(windows, "pyautogui", mock_pyautogui, raising=False)
    result = windows.focus_window("test")
    mock_user32.ShowWindow.assert_called_once_with(5, windows._SW_RESTORE)
    mock_pyautogui.press.assert_not_called()
    assert "(verified)" in result


def test_focus_window_success_does_not_use_alt_fallback(monkeypatch):
    # Succeeds on the first SetForegroundWindow call, never touches the alt-fallback.
    monkeypatch.setattr(windows, "find_window", lambda _: {"hwnd": 5, "title": "Test"})
    mock_user32 = MagicMock()
    mock_user32.SetForegroundWindow.return_value = 1
    monkeypatch.setattr(windows, "_user32", mock_user32)
    mock_pyautogui = MagicMock()
    monkeypatch.setattr(windows, "pyautogui", mock_pyautogui, raising=False)
    result = windows.focus_window("test")
    mock_user32.ShowWindow.assert_called_once_with(5, windows._SW_RESTORE)
    mock_pyautogui.press.assert_not_called()
    assert "Test" in result
    assert "requested; foreground not confirmed" in result


def test_focus_window_fallback_fires_when_setforeground_fails(monkeypatch):
    # Mocks pyautogui at module level; 3 SetForegroundWindow calls, only the last one succeeds.
    monkeypatch.setattr(windows, "find_window", lambda _: {"hwnd": 5, "title": "Test"})
    mock_user32 = MagicMock()
    mock_user32.SetForegroundWindow.side_effect = [0, 0, 1]
    mock_user32.GetWindowThreadProcessId.return_value = 111
    mock_user32.AttachThreadInput.return_value = 1
    monkeypatch.setattr(windows, "_user32", mock_user32)
    mock_kernel32 = MagicMock()
    mock_kernel32.GetCurrentThreadId.return_value = 222
    monkeypatch.setattr(windows, "_kernel32", mock_kernel32)
    monkeypatch.setattr(windows, "_HAS_PYAUTOGUI", True)
    mock_pyautogui = MagicMock()
    monkeypatch.setattr(windows, "pyautogui", mock_pyautogui, raising=False)
    result = windows.focus_window("test")
    mock_pyautogui.press.assert_called_once_with("alt")
    assert mock_user32.SetForegroundWindow.call_count == 3
    assert "Focused window: Test" in result


def test_focus_window_fallback_degrades_gracefully_without_pyautogui(monkeypatch):
    # Both SetForegroundWindow attempts fail, no pyautogui -- must report failure honestly, not claim success.
    monkeypatch.setattr(windows, "find_window", lambda _: {"hwnd": 5, "title": "Test"})
    mock_user32 = MagicMock()
    mock_user32.SetForegroundWindow.return_value = 0
    mock_user32.GetWindowThreadProcessId.return_value = 111
    mock_user32.AttachThreadInput.return_value = 1
    monkeypatch.setattr(windows, "_user32", mock_user32)
    mock_kernel32 = MagicMock()
    mock_kernel32.GetCurrentThreadId.return_value = 222
    monkeypatch.setattr(windows, "_kernel32", mock_kernel32)
    monkeypatch.setattr(windows, "_HAS_PYAUTOGUI", False)
    mock_pyautogui = MagicMock()
    monkeypatch.setattr(windows, "pyautogui", mock_pyautogui, raising=False)
    result = windows.focus_window("test")
    mock_pyautogui.press.assert_not_called()
    assert mock_user32.SetForegroundWindow.call_count == 2
    assert "could not bring" in result
    assert "Test" in result


def test_get_foreground_window_returns_app_identity(monkeypatch):
    mock_user32 = MagicMock()
    mock_user32.GetForegroundWindow.return_value = 42
    mock_user32.GetWindowTextLengthW.return_value = 5

    def fake_gettext(hwnd, buf, n):
        buf.value = "Notep"

    mock_user32.GetWindowTextW.side_effect = fake_gettext

    def fake_gwtpid(hwnd, pid_ptr):
        pid_ptr._obj.value = 1234
        return 999

    mock_user32.GetWindowThreadProcessId.side_effect = fake_gwtpid
    monkeypatch.setattr(windows, "_user32", mock_user32)

    mock_process = MagicMock()
    mock_process.name.return_value = "notepad.exe"
    monkeypatch.setattr(
        windows, "psutil", MagicMock(Process=MagicMock(return_value=mock_process)), raising=False
    )

    result = windows.get_foreground_window()
    assert result == {"hwnd": 42, "title": "Notep", "pid": 1234, "process_name": "notepad.exe"}


def test_get_foreground_window_returns_none_off_windows(monkeypatch):
    monkeypatch.setattr(windows, "_user32", None)
    assert windows.get_foreground_window() is None


def test_get_foreground_window_returns_none_hwnd(monkeypatch):
    mock_user32 = MagicMock()
    mock_user32.GetForegroundWindow.return_value = 0
    monkeypatch.setattr(windows, "_user32", mock_user32)
    assert windows.get_foreground_window() is None


def test_get_foreground_window_survives_psutil_failure(monkeypatch):
    mock_user32 = MagicMock()
    mock_user32.GetForegroundWindow.return_value = 42
    mock_user32.GetWindowTextLengthW.return_value = 5

    def fake_gettext(hwnd, buf, n):
        buf.value = "Notep"

    mock_user32.GetWindowTextW.side_effect = fake_gettext

    def fake_gwtpid(hwnd, pid_ptr):
        pid_ptr._obj.value = 1234
        return 999

    mock_user32.GetWindowThreadProcessId.side_effect = fake_gwtpid
    monkeypatch.setattr(windows, "_user32", mock_user32)

    mock_psutil = MagicMock()
    mock_psutil.Process.side_effect = Exception("process exited")
    monkeypatch.setattr(windows, "psutil", mock_psutil, raising=False)

    result = windows.get_foreground_window()
    assert result == {"hwnd": 42, "title": "Notep", "pid": 1234, "process_name": None}

"""Tests for capture-bounds coordinate mapping (charlie.desktop.uia / ocr)."""

from unittest.mock import MagicMock, patch

from charlie.desktop import uia


def test_set_and_get_last_capture_bounds():
    uia.set_last_capture_bounds((100, 200, 900, 800))
    assert uia.get_last_capture_bounds() == (100, 200, 900, 800)


def test_image_to_screen_translates_offset():
    uia.set_last_capture_bounds((100, 200, 900, 800))
    assert uia.image_to_screen(50, 60) == (150, 260)


def test_image_to_screen_without_capture_returns_none():
    uia.set_last_capture_bounds(None)
    assert uia.image_to_screen(50, 60) is None


def _make_mock_sct(monitor_dict):
    shot = MagicMock()
    shot.size = (monitor_dict["width"], monitor_dict["height"])
    shot.bgra = b"\x00" * (monitor_dict["width"] * monitor_dict["height"] * 4)
    sct = MagicMock()
    sct.monitors = [monitor_dict]
    sct.grab.return_value = shot
    sct.__enter__.return_value = sct
    sct.__exit__.return_value = False
    return sct


def test_capture_full_screen_records_bounds():
    from charlie.desktop import ocr

    monitor = {"left": 10, "top": 20, "width": 1920, "height": 1080}
    sct = _make_mock_sct(monitor)
    with patch.object(ocr, "OCR_AVAILABLE", True), patch("mss.mss", return_value=sct):
        ocr.capture()
    assert uia.get_last_capture_bounds() == (10, 20, 1930, 1100)


def test_capture_region_records_bounds_exactly():
    from charlie.desktop import ocr

    region = (10, 20, 500, 600)
    monitor = {"left": region[0], "top": region[1], "width": region[2] - region[0], "height": region[3] - region[1]}
    sct = _make_mock_sct(monitor)
    with patch.object(ocr, "OCR_AVAILABLE", True), patch("mss.mss", return_value=sct):
        ocr.capture(region=region)
    assert uia.get_last_capture_bounds() == (10, 20, 500, 600)


def _make_mock_sct_multi(monitors):
    """monitors: list where index N is mss.monitors[N] (index 0 = virtual screen)."""
    sct = MagicMock()
    sct.monitors = monitors

    def _grab(target):
        shot = MagicMock()
        shot.size = (target["width"], target["height"])
        shot.bgra = b"\x00" * (target["width"] * target["height"] * 4)
        return shot

    sct.grab.side_effect = _grab
    sct.__enter__.return_value = sct
    sct.__exit__.return_value = False
    return sct


def test_capture_specific_monitor_records_its_bounds():
    from charlie.desktop import ocr

    monitors = [
        {"left": 0, "top": 0, "width": 3840, "height": 1080},
        {"left": 0, "top": 0, "width": 1920, "height": 1080},
        {"left": 1920, "top": 0, "width": 1920, "height": 1080},
    ]
    sct = _make_mock_sct_multi(monitors)
    with patch.object(ocr, "OCR_AVAILABLE", True), patch("mss.mss", return_value=sct):
        ocr.capture(monitor=2)
    assert uia.get_last_capture_bounds() == (1920, 0, 3840, 1080)


def test_capture_region_ignores_monitor_arg():
    from charlie.desktop import ocr

    monitors = [
        {"left": 0, "top": 0, "width": 3840, "height": 1080},
        {"left": 0, "top": 0, "width": 1920, "height": 1080},
        {"left": 1920, "top": 0, "width": 1920, "height": 1080},
    ]
    sct = _make_mock_sct_multi(monitors)
    region = (10, 20, 500, 600)
    with patch.object(ocr, "OCR_AVAILABLE", True), patch("mss.mss", return_value=sct):
        ocr.capture(region=region, monitor=2)
    assert uia.get_last_capture_bounds() == (10, 20, 500, 600)


def test_control_from_hwnd_returns_none_without_uia(monkeypatch):
    monkeypatch.setattr(uia, "_HAS_UIA", False)
    assert uia.control_from_hwnd(12345) is None


def test_control_from_hwnd_calls_uia_controlfromhandle(monkeypatch):
    mock_uia = MagicMock()
    mock_uia.ControlFromHandle.return_value = "fake-control"
    monkeypatch.setattr(uia, "_HAS_UIA", True)
    monkeypatch.setattr(uia, "_uia", mock_uia)
    result = uia.control_from_hwnd(12345)
    mock_uia.ControlFromHandle.assert_called_once_with(12345)
    assert result == "fake-control"


def test_control_from_hwnd_swallows_exception(monkeypatch):
    mock_uia = MagicMock()
    mock_uia.ControlFromHandle.side_effect = RuntimeError("boom")
    monkeypatch.setattr(uia, "_HAS_UIA", True)
    monkeypatch.setattr(uia, "_uia", mock_uia)
    assert uia.control_from_hwnd(12345) is None

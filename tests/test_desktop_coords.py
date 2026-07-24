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

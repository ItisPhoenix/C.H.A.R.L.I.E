"""Tests for charlie.known_apps -- the single-source-of-truth app registry
that core.py's _OPEN_APP_MAP/_CLOSE_APP_MAP and text_utils.KNOWN_APPS are
derived from."""

from charlie.known_apps import APP_REGISTRY, KNOWN_APP_NAMES


def test_known_app_names_is_superset_of_registry():
    assert set(APP_REGISTRY.keys()) <= KNOWN_APP_NAMES


def test_website_entries_have_no_close_process():
    assert APP_REGISTRY["youtube"].close_process is None
    assert APP_REGISTRY["youtube"].is_website is True


def test_local_app_entries_have_close_process():
    assert APP_REGISTRY["chrome"].close_process == "chrome.exe"
    assert APP_REGISTRY["notepad"].close_process == "notepad.exe"
    assert APP_REGISTRY["chrome"].is_website is False


def test_core_derives_open_and_close_maps_from_registry():
    from charlie.core import _CLOSE_APP_MAP, _OPEN_APP_MAP

    assert _OPEN_APP_MAP["chrome"] == "chrome"
    assert _CLOSE_APP_MAP["chrome"] == "chrome.exe"
    # Websites open but have nothing to close.
    assert _OPEN_APP_MAP["youtube"] == "https://youtube.com"
    assert "youtube" not in _CLOSE_APP_MAP

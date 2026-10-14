from __future__ import annotations

from charlie.computer import backend as backend_module
from charlie.computer.backend import DesktopBackend


def test_focus_overlay_rect_uses_only_the_live_bound_target(monkeypatch):
    backend = DesktopBackend()
    backend._target = {"pid": 7, "window_id": 41, "title": "Editor"}
    monkeypatch.setattr(backend_module, "_window_target_is_current", lambda target: target["window_id"] == 41)
    monkeypatch.setattr(backend_module, "_is_browser_window", lambda target: False)
    monkeypatch.setattr("charlie.desktop.windows.window_rect", lambda hwnd: {"x": 12, "y": 34, "width": 800, "height": 600})

    assert backend.focus_overlay_rect() == {"x": 12, "y": 34, "width": 800, "height": 600}


def test_unmatched_window_hint_does_not_highlight_a_different_bound_target(monkeypatch):
    backend = DesktopBackend()
    backend._target = {"pid": 7, "window_id": 41, "title": "Editor"}
    monkeypatch.setattr(backend_module, "_existing_native_window", lambda _title: None)
    monkeypatch.setattr(backend_module, "_window_target_is_current", lambda _target: True)

    assert backend.focus_overlay_rect(window_hint="Missing window") is None

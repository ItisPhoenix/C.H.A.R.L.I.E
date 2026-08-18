"""Phase 14 ownership regressions for the single React HUD path."""

from pathlib import Path

from main import _surface_request_event

ROOT = Path(__file__).resolve().parents[1]


def test_voice_surface_request_uses_canonical_presentation_intent() -> None:
    event_type, payload, rationale = _surface_request_event("terminal", "show")

    assert event_type == "presentation_intent"
    assert payload["id"] == "presentation:terminal"
    assert payload["kind"] == "workspace"
    assert payload["workspace_type"] == "terminal"
    assert payload["replayable"] is True
    assert rationale == "opened terminal presentation"


def test_voice_surface_hide_uses_canonical_presentation_dismissal() -> None:
    event_type, payload, rationale = _surface_request_event("media", "hide")

    assert event_type == "presentation_dismiss"
    assert payload == {"id": "presentation:media"}
    assert rationale == "dismissed media presentation"


def test_legacy_dashboard_route_and_qt_hud_launcher_are_not_production_paths() -> None:
    app_source = (ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    main_source = (ROOT / "main.py").read_text(encoding="utf-8")

    assert "Dashboard" not in app_source
    assert 'path="/dashboard"' not in app_source
    assert "hud_entry.py" not in main_source

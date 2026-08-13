from charlie.dashboard_intent import match_dashboard_panel_intent


def test_matches_allowlisted_show_intent() -> None:
    intent = match_dashboard_panel_intent("Charlie, show the terminal")

    assert intent is not None
    assert intent.action == "show"
    assert intent.panel_id == "terminal"


def test_matches_allowlisted_hide_intent() -> None:
    intent = match_dashboard_panel_intent("hide media player")

    assert intent is not None
    assert intent.action == "hide"
    assert intent.panel_id == "media"


def test_rejects_unregistered_panel_intent() -> None:
    assert match_dashboard_panel_intent("show the hacking console") is None

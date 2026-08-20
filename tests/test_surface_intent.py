from charlie.surface_intent import match_surface_request


def test_matches_allowlisted_show_intent() -> None:
    intent = match_surface_request("Charlie, show the terminal")

    assert intent is not None
    assert intent.action == "show"
    assert intent.surface_id == "terminal"


def test_matches_allowlisted_hide_intent() -> None:
    intent = match_surface_request("hide media player")

    assert intent is not None
    assert intent.action == "hide"
    assert intent.surface_id == "media_control"


def test_matches_clear_screen_without_surface() -> None:
    intent = match_surface_request("clear the screen")

    assert intent is not None
    assert intent.action == "clear_screen"
    assert intent.surface_id is None


def test_rejects_unregistered_surface_intent() -> None:
    assert match_surface_request("show the hacking console") is None
    assert match_surface_request("open calendar") is None
    assert match_surface_request("open tools") is None
    assert match_surface_request("open MCP") is None


def test_ambiguous_targets_fall_through_voice_parser() -> None:
    assert match_surface_request("show file") is None
    assert match_surface_request("show composed_surface") is None

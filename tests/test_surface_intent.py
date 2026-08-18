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
    assert intent.surface_id == "media"


def test_rejects_unregistered_surface_intent() -> None:
    assert match_surface_request("show the hacking console") is None

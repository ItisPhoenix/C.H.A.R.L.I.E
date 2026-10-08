"""Behavioural tests for Cua envelope normalisation.

These pin the carriers Cua actually returns. The bug they exist to prevent: reading
only the top level of a result and concluding the host returned nothing, when in fact
it returned a payload nested in ``structured_json``, ``raw_json`` or content blocks.

Evidence class: TEST/MOCK, against the real observed result shapes.
"""

from __future__ import annotations

import json

import pytest

from charlie.computer.cua_adapter import (
    CuaOutcome,
    _as_mapping,
    _window_info,
    normalise,
)


class _TypedWindow:
    """Mirrors the real ``WindowInfo`` field set."""

    def __init__(self, window_id, pid, app_name, title, is_on_screen=True, minimized=False):
        self.window_id = window_id
        self.pid = pid
        self.app_name = app_name
        self.title = title
        self.is_on_screen = is_on_screen
        self.minimized = minimized
        self.bounds = None


class _TypedWindowsOutput:
    def __init__(self, windows):
        self.windows = windows
        self.current_space_id = None


class TestStructuredJsonCarrier:
    def test_structured_json_string_is_unwrapped(self):
        """The shape Cua actually returns for action tools."""
        payload = {
            "text": "Launched Calculator (pid 11944)",
            "images": [],
            "structured_json": json.dumps(
                {"effect": "confirmed", "summary": "ok", "windows": [{"window_id": 1}]}
            ),
        }
        got = _as_mapping(payload)
        assert got["effect"] == "confirmed"
        assert got["summary"] == "ok"
        assert got["windows"] == [{"window_id": 1}]

    def test_envelope_extras_survive_unwrapping(self):
        payload = {
            "text": "t",
            "is_error": False,
            "structured_json": json.dumps({"effect": "refused"}),
        }
        got = _as_mapping(payload)
        assert got["effect"] == "refused"
        assert got["is_error"] is False


class TestRawJsonCarrier:
    def test_raw_json_is_unwrapped_when_structured_json_is_absent(self):
        payload = {"raw_json": json.dumps({"windows": [{"window_id": 9}]}), "text": "t"}
        assert _as_mapping(payload)["windows"] == [{"window_id": 9}]

    def test_structured_json_wins_when_both_present(self):
        payload = {
            "structured_json": json.dumps({"effect": "confirmed"}),
            "raw_json": json.dumps({"effect": "refused"}),
        }
        assert _as_mapping(payload)["effect"] == "confirmed"


class TestContentBlockCarrier:
    def test_mcp_style_content_block_text_json_is_unwrapped(self):
        payload = {"content": [{"type": "text", "text": json.dumps({"effect": "confirmed"})}]}
        assert _as_mapping(payload)["effect"] == "confirmed"

    def test_content_block_with_structured_json_key(self):
        payload = {"content": [{"type": "text", "structured_json": json.dumps({"effect": "partial"})}]}
        assert _as_mapping(payload)["effect"] == "partial"

    def test_a_bare_content_list_is_handled(self):
        assert _as_mapping([{"text": json.dumps({"effect": "refused"})}])["effect"] == "refused"


class TestTypedObjects:
    def test_typed_windows_output_is_readable(self):
        out = _TypedWindowsOutput([_TypedWindow(11, 222, "Calculator", "Calculator")])
        windows = [_window_info(w) for w in out.windows]
        assert windows == [
            {
                "window_id": 11,
                "pid": 222,
                "app_name": "Calculator",
                "name": "Calculator",
                "title": "Calculator",
                "is_on_screen": True,
                "minimized": False,
                "bounds": {},
            }
        ]

    def test_window_info_uses_app_name_not_name(self):
        """The real field is ``app_name``; assuming ``name`` silently loses the app."""
        got = _window_info(_TypedWindow(1, 2, "brave.exe", "Example Domain"))
        assert got["app_name"] == "brave.exe"
        assert got["name"] == "brave.exe"


class TestAbsenceIsNotEmptiness:
    def test_unknown_shape_returns_empty_not_a_false_empty_list(self):
        assert _as_mapping(object()) == {}

    def test_none_returns_empty(self):
        assert _as_mapping(None) == {}

    def test_malformed_json_is_not_silently_empty_data(self):
        payload = {"structured_json": "{not json"}
        got = _as_mapping(payload)
        # Falls back to the envelope itself rather than inventing an empty payload.
        assert got.get("structured_json") == "{not json"


class TestOutcomeMapping:
    @pytest.mark.parametrize(
        "payload,expected",
        [
            ({"effect": "confirmed"}, CuaOutcome.CONFIRMED),
            ({"effect": "partial"}, CuaOutcome.PARTIAL),
            ({"effect": "refused"}, CuaOutcome.REFUSED),
            ({"effect": "suspected_noop"}, CuaOutcome.SUSPECTED_NOOP),
            ({"effect": "unverifiable"}, CuaOutcome.UNVERIFIED),
            # An empty payload is malformed, not a known failure: Charlie does not
            # know what happened, and must not claim it did.
            ({}, CuaOutcome.UNVERIFIED),
            ({"is_error": True}, CuaOutcome.FAILED),
        ],
    )
    def test_effect_maps_to_outcome(self, payload, expected):
        assert normalise(payload).outcome is expected

    def test_unknown_verification_downgrades_a_confirmed_effect(self):
        result = normalise({"effect": "confirmed", "verification_status": "unknown"})
        assert result.outcome is CuaOutcome.UNVERIFIED
        assert result.is_success is False

    def test_satisfied_verification_keeps_confirmed(self):
        assert normalise({"effect": "confirmed", "verification_status": "satisfied"}).is_success

    def test_unsatisfied_verification_fails(self):
        assert normalise({"effect": "confirmed", "verification_status": "unsatisfied"}).outcome is CuaOutcome.FAILED

    def test_error_downgrades_a_confirmed_effect(self):
        assert normalise({"effect": "confirmed", "error": "boom"}).outcome is CuaOutcome.FAILED

    @pytest.mark.parametrize(
        "payload",
        [
            {"effect": "refused"},
            {"effect": "suspected_noop"},
            {"effect": "unverifiable"},
            {"effect": "confirmed", "verification_status": "unknown"},
            {},
        ],
    )
    def test_nothing_unverified_becomes_success(self, payload):
        assert normalise(payload).is_success is False

    def test_effect_is_read_from_a_structured_json_envelope(self):
        """The full real path: envelope in, correct outcome out."""
        payload = {
            "text": "ok",
            "images": [],
            "structured_json": json.dumps({"effect": "suspected_noop", "summary": "s"}),
        }
        result = normalise(payload)
        assert result.outcome is CuaOutcome.SUSPECTED_NOOP
        assert result.is_success is False

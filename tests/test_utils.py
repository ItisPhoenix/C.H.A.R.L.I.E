"""Tests for shared response and serialization helpers."""

from charlie.utils import parse_json_object


def test_parse_json_object_accepts_plain_json():
    assert parse_json_object('{"facts": ["User likes tea."]}') == {
        "facts": ["User likes tea."]
    }


def test_parse_json_object_accepts_fenced_json_with_prose():
    content = 'Here is the result:\n```json\n{"facts": []}\n```'
    assert parse_json_object(content) == {"facts": []}


def test_parse_json_object_rejects_empty_null_and_non_object_content():
    assert parse_json_object(None) is None
    assert parse_json_object("   ") is None
    assert parse_json_object("[1, 2, 3]") is None

"""Tests for the vision-LLM GUI grounding fallback (charlie/desktop/grounding.py).

Reuses the existing VISION_LLM_URL/KEY/MODEL config -- no new dependency,
no torch. Runs on any platform; the network call is always mocked.
"""

from unittest.mock import MagicMock

from charlie.config import Config
from charlie.desktop import grounding
from charlie.desktop.uia import Element
from charlie.tools import _grounding_marks
from charlie.tools import config as tools_config


def _vision_config(**overrides):
    fields = dict(
        llm_url="http://localhost:11434",
        llm_key="no-key",
        llm_model="dummy",
        vision_enabled=True,
        vision_llm_url="http://localhost:1234/v1",
        vision_llm_key="no-key",
        vision_llm_model="qwen/qwen3-vl-4b",
    )
    fields.update(overrides)
    return Config(**fields)


def test_parse_elements_converts_normalized_bbox_to_pixels():
    content = '[{"bbox_2d": [0, 0, 500, 1000], "label": "Save button"}]'
    elements = grounding._parse_elements(content, width=200, height=100)
    assert len(elements) == 1
    e = elements[0]
    assert isinstance(e, Element)
    assert e.bounds == (0, 0, 100, 100)
    assert e.name == "Save button"
    assert e.control_type == "GroundedElement"
    assert e.is_password is False
    assert e.is_offscreen is False


def test_parse_elements_ignores_malformed_boxes():
    content = '[{"bbox_2d": [1, 2, 3]}, {"label": "no bbox"}, "garbage"]'
    assert grounding._parse_elements(content, width=100, height=100) == []


def test_parse_elements_handles_prose_wrapped_json():
    content = 'Sure, here are the elements:\n[{"bbox_2d": [0, 0, 1000, 1000], "label": "x"}]\nDone.'
    elements = grounding._parse_elements(content, width=10, height=10)
    assert len(elements) == 1


def test_parse_elements_returns_empty_on_invalid_json():
    assert grounding._parse_elements("not json at all", width=10, height=10) == []


def test_parse_elements_normalizes_swapped_coords():
    """A model can hand back x1>x2/y1>y2 -- must not silently invert the box."""
    content = '[{"bbox_2d": [500, 500, 0, 0], "label": "swapped"}]'
    elements = grounding._parse_elements(content, width=100, height=100)
    assert elements[0].bounds == (0, 0, 50, 50)


def test_parse_elements_clamps_out_of_range_coords():
    """A hallucinated value outside the documented 0-1000 scale must not
    produce an out-of-frame or negative pixel box."""
    content = '[{"bbox_2d": [-50, 0, 1500, 1000], "label": "overshoot"}]'
    elements = grounding._parse_elements(content, width=200, height=100)
    assert elements[0].bounds == (0, 0, 200, 100)


def test_detect_returns_empty_when_vision_disabled():
    config = _vision_config(vision_enabled=False)
    assert grounding.detect(b"fake-png-bytes", config) == []


def test_detect_returns_empty_when_no_vision_url():
    config = _vision_config(vision_llm_url="")
    assert grounding.detect(b"fake-png-bytes", config) == []


def test_detect_calls_vision_endpoint_and_parses_response(monkeypatch):
    config = _vision_config()

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "choices": [{"message": {"content": '[{"bbox_2d": [0, 0, 1000, 1000], "label": "icon"}]'}}]
    }
    mock_post = MagicMock(return_value=mock_response)
    monkeypatch.setattr(grounding.httpx, "post", mock_post)
    monkeypatch.setattr(grounding, "_image_size", lambda png_bytes: (100, 100))

    elements = grounding.detect(b"fake-png-bytes", config)

    assert len(elements) == 1
    assert elements[0].bounds == (0, 0, 100, 100)
    called_url = mock_post.call_args.args[0]
    assert called_url == "http://localhost:1234/v1/chat/completions"


def test_detect_returns_empty_on_request_failure(monkeypatch):
    config = _vision_config()

    def _raise(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(grounding.httpx, "post", _raise)
    assert grounding.detect(b"fake-png-bytes", config) == []


def test_grounding_marks_skips_call_when_elements_plentiful(monkeypatch):
    """Above the sparsity threshold, this must not touch grounding.detect at all."""
    monkeypatch.setattr(tools_config, "vision_enabled", True)

    def _fail_if_called(*a, **k):
        raise AssertionError("grounding.detect should not be called when elements are plentiful")

    monkeypatch.setattr("charlie.desktop.grounding.detect", _fail_if_called)
    elements = [
        Element(mark_id=i, name="x", control_type="Button", bounds=(0, 0, 1, 1),
                is_password=False, is_offscreen=False)
        for i in range(5)
    ]
    assert _grounding_marks(elements) == elements


def test_grounding_marks_skips_call_when_vision_disabled(monkeypatch):
    monkeypatch.setattr(tools_config, "vision_enabled", False)

    def _fail_if_called(*a, **k):
        raise AssertionError("grounding.detect should not be called when vision is disabled")

    monkeypatch.setattr("charlie.desktop.grounding.detect", _fail_if_called)
    assert _grounding_marks([]) == []


if __name__ == "__main__":
    test_parse_elements_converts_normalized_bbox_to_pixels()
    test_parse_elements_ignores_malformed_boxes()
    test_parse_elements_handles_prose_wrapped_json()
    test_parse_elements_returns_empty_on_invalid_json()
    test_parse_elements_normalizes_swapped_coords()
    test_parse_elements_clamps_out_of_range_coords()
    test_detect_returns_empty_when_vision_disabled()
    test_detect_returns_empty_when_no_vision_url()
    print("ok")

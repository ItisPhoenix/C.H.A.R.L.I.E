"""Tests for the vision-LLM GUI grounding fallback (charlie/desktop/grounding.py).

Reuses the existing VISION_LLM_URL/KEY/MODEL config -- no new dependency,
no torch. Runs on any platform; the network call is always mocked.
"""

import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import httpx
import pytest

from charlie import resource_locks
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


def test_detect_requires_vision_gpu(monkeypatch):
    config = _vision_config()
    acquired = []

    def deny(capability, owner_id):
        acquired.append((capability, owner_id))
        return False

    mock_post = MagicMock()
    mock_release = MagicMock()
    monkeypatch.setattr(grounding.resource_locks, "acquire", deny)
    monkeypatch.setattr(grounding.resource_locks, "release", mock_release)
    monkeypatch.setattr(grounding.httpx, "post", mock_post)

    assert grounding.detect(b"fake-png-bytes", config, owner_id="grounding-owner") == []
    assert acquired == [("vision_gpu", "grounding-owner")]
    mock_post.assert_not_called()
    mock_release.assert_not_called()


@pytest.mark.asyncio
async def test_detect_cannot_bypass_existing_vision_lease(monkeypatch):
    config = _vision_config()
    mock_post = MagicMock()
    owner = "existing-vision-owner"
    lease = await resource_locks.default_lease_manager.acquire("vision_gpu", owner)
    monkeypatch.setattr(grounding.httpx, "post", mock_post)
    try:
        assert grounding.detect(b"fake-png-bytes", config, owner_id="grounding-owner") == []
        assert resource_locks.current_owner("vision_gpu") == owner
    finally:
        await lease.release()
    mock_post.assert_not_called()


def test_different_grounding_owners_cannot_run_concurrently(monkeypatch):
    config = _vision_config()
    entered = threading.Event()
    release_request = threading.Event()
    calls = []
    response = MagicMock()
    response.json.return_value = {"choices": [{"message": {"content": "[]"}}]}
    monkeypatch.setattr(grounding, "_image_size", lambda _png: (100, 100))

    def post(*args, **kwargs):
        calls.append((args, kwargs))
        entered.set()
        assert release_request.wait(timeout=2)
        return response

    monkeypatch.setattr(grounding.httpx, "post", post)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(grounding.detect, b"first", config, owner_id="owner-a")
        assert entered.wait(timeout=1)
        second = executor.submit(grounding.detect, b"second", config, owner_id="owner-b")
        assert second.result(timeout=1) == []
        release_request.set()
        assert first.result(timeout=2) == []

    assert len(calls) == 1


def test_detect_calls_vision_endpoint_and_parses_response(monkeypatch):
    config = _vision_config()

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "choices": [{"message": {"content": '[{"bbox_2d": [0, 0, 1000, 1000], "label": "icon"}]'}}]
    }
    mock_post = MagicMock(return_value=mock_response)
    monkeypatch.setattr(grounding.httpx, "post", mock_post)
    monkeypatch.setattr(grounding, "_image_size", lambda png_bytes: (100, 100))

    elements = grounding.detect(b"fake-png-bytes", config, owner_id="grounding-owner")

    assert len(elements) == 1
    assert elements[0].bounds == (0, 0, 100, 100)
    called_url = mock_post.call_args.args[0]
    assert called_url == "http://localhost:1234/v1/chat/completions"


def test_detect_releases_vision_gpu_after_normal_completion(monkeypatch):
    config = _vision_config()
    response = MagicMock()
    response.json.return_value = {"choices": [{"message": {"content": "[]"}}]}
    monkeypatch.setattr(grounding.httpx, "post", MagicMock(return_value=response))
    monkeypatch.setattr(grounding, "_image_size", lambda _png: (100, 100))

    assert grounding.detect(b"fake-png-bytes", config, owner_id="grounding-owner") == []
    assert resource_locks.current_owner("vision_gpu") is None


@pytest.mark.parametrize("failure", ["timeout", "error"])
def test_detect_releases_vision_gpu_after_timeout_or_error(monkeypatch, failure):
    config = _vision_config()

    def raise_failure(*args, **kwargs):
        if failure == "timeout":
            raise httpx.ReadTimeout(
                "timed out",
                request=httpx.Request("POST", "http://localhost:1234/v1/chat/completions"),
            )
        raise OSError("connection refused")

    monkeypatch.setattr(grounding.httpx, "post", raise_failure)
    assert grounding.detect(b"fake-png-bytes", config, owner_id="grounding-owner") == []
    assert resource_locks.current_owner("vision_gpu") is None


def test_grounding_marks_skips_call_when_elements_plentiful(monkeypatch):
    """Above the sparsity threshold, this must not touch grounding.detect at all."""
    monkeypatch.setattr(tools_config, "vision_enabled", True)

    def _fail_if_called(*a, **k):
        raise AssertionError("grounding.detect should not be called when elements are plentiful")

    monkeypatch.setattr("charlie.desktop.grounding.detect", _fail_if_called)

    def fail_if_acquired(*args, **kwargs):
        raise AssertionError("vision_gpu should not be acquired")

    monkeypatch.setattr(
        grounding.resource_locks,
        "acquire",
        fail_if_acquired,
    )
    elements = [
        Element(mark_id=i, name="x", control_type="Button", bounds=(0, 0, 1, 1),
                is_password=False, is_offscreen=False)
        for i in range(5)
    ]
    assert _grounding_marks(elements) == elements


def test_grounding_marks_preserves_elements_when_grounding_unavailable(monkeypatch):
    elements = [
        Element(mark_id=1, name="OCR label", control_type="ocr_text", bounds=(0, 0, 1, 1),
                is_password=False, is_offscreen=False)
    ]
    calls = []
    monkeypatch.setattr(tools_config, "vision_enabled", True)
    monkeypatch.setattr("charlie.desktop.ocr.OCR_AVAILABLE", True)
    monkeypatch.setattr("charlie.desktop.ocr.capture", lambda: b"fake-png-bytes")

    def return_unavailable(*args, **kwargs):
        calls.append((args, kwargs))
        return []

    monkeypatch.setattr("charlie.desktop.grounding.detect", return_unavailable)

    assert _grounding_marks(elements) == elements
    assert len(calls) == 1


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

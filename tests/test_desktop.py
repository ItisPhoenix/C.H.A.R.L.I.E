"""Tests for the native desktop control tool cluster (UIA + OCR fallback).

Runs on any platform/without uiautomation, pyautogui, or pytesseract
installed -- every path here is either pure logic (gate arming, halt flag,
mark merging) or the guarded disabled-by-default path, matching the
optional-import contract in charlie/desktop/__init__.py.
"""

import pytest

from charlie.config import Config
from charlie.core import (
    _DESKTOP_COM_TOOLS,
    _DESKTOP_CONTROL_TOOLS,
    _SCREEN_QUERY_RE,
    _VISION_MAX_TOKENS,
    Brain,
)
from charlie.desktop import DESKTOP_AVAILABLE, UIA_EXECUTOR
from charlie.desktop import actions as desktop_actions
from charlie.desktop import vision as desktop_vision
from charlie.desktop.uia import (
    Element,
    is_low_confidence_mark,
    merge_ocr_elements,
    resolve_bounds,
    resolve_is_password,
    resolve_name,
)
from charlie.tools import config as _tools_config
from charlie.tools import (
    desktop_click,
    desktop_observe,
    desktop_read_screen,
    desktop_screenshot,
    pop_pending_vision_image,
    set_pending_vision_image,
)


@pytest.fixture
def brain_config():
    return Config(
        llm_url="http://localhost:11434",
        llm_key="no-key",
        llm_model="dummy",
        iteration_budget_max=3,
    )


def test_desktop_control_tools_frozenset():
    assert _DESKTOP_CONTROL_TOOLS == {
        "desktop_click", "desktop_type", "desktop_invoke", "desktop_key",
        "desktop_click_at", "desktop_move", "desktop_drag", "desktop_scroll",
        "desktop_focus", "desktop_window", "desktop_move_window",
        "system_control",
    }


def test_system_control_is_gated():
    """Media-key presses mutate real system volume/playback state, same as
    desktop_key sending a chord -- it must go through the same consent-arm/
    panic-halt/rate-limit/idempotency-exclusion gate as every other effector."""
    assert "system_control" in _DESKTOP_CONTROL_TOOLS


def test_desktop_tools_disabled_by_default():
    """desktop_control_enabled defaults to false -- tools must refuse, not crash.

    Forces the flag rather than trusting ambient state: a developer's local
    .env commonly sets DESKTOP_CONTROL_ENABLED=true, which would otherwise
    make this test exercise a real click instead of the disabled path.
    """
    original = _tools_config.desktop_control_enabled
    _tools_config.desktop_control_enabled = False
    try:
        assert "disabled" in desktop_observe()
        assert "disabled" in desktop_click(1)
    finally:
        _tools_config.desktop_control_enabled = original


def test_actions_halt_toggle():
    desktop_actions.clear_halt()
    assert desktop_actions.is_halted() is False
    desktop_actions.halt()
    assert desktop_actions.is_halted() is True
    desktop_actions.clear_halt()
    assert desktop_actions.is_halted() is False


def test_click_mark_unknown_id_returns_error_not_raise():
    desktop_actions.clear_halt()
    assert "Error" in desktop_actions.click_mark(999999)


def test_type_text_unknown_id_returns_error_not_raise():
    desktop_actions.clear_halt()
    assert "Error" in desktop_actions.type_text(999999, "hello")


def test_desktop_read_screen_disabled_by_default():
    """Same ambient-.env hazard as test_desktop_tools_disabled_by_default above."""
    original = _tools_config.desktop_control_enabled
    _tools_config.desktop_control_enabled = False
    try:
        assert "disabled" in desktop_read_screen()
    finally:
        _tools_config.desktop_control_enabled = original


def test_merge_ocr_elements_continues_mark_id_sequence():
    uia = [Element(mark_id=1, name="Save", control_type="Button", bounds=(0, 0, 10, 10),
                    is_password=False, is_offscreen=False)]
    ocr = [Element(mark_id=1, name="hello", control_type="ocr_text", bounds=(20, 20, 30, 30),
                    is_password=False, is_offscreen=False)]
    merged = merge_ocr_elements(uia, ocr)
    assert [e.mark_id for e in merged] == [1, 2]
    assert merged[1].name == "hello"
    assert resolve_bounds(2) == (20, 20, 30, 30)
    assert resolve_is_password(2) is False
    assert resolve_name(2) == "hello"


def test_is_low_confidence_mark_true_for_ocr_element():
    uia = [Element(mark_id=1, name="Save", control_type="Button", bounds=(0, 0, 10, 10),
                    is_password=False, is_offscreen=False)]
    ocr = [Element(mark_id=1, name="hello", control_type="ocr_text", bounds=(20, 20, 30, 30),
                    is_password=False, is_offscreen=False)]
    merged = merge_ocr_elements(uia, ocr)
    ocr_mark_id = merged[1].mark_id
    assert is_low_confidence_mark(ocr_mark_id) is True


def test_is_low_confidence_mark_true_for_unresolvable_mark():
    assert is_low_confidence_mark(999999) is True


def test_desktop_screenshot_disabled_by_default():
    """Same ambient-.env hazard as test_desktop_tools_disabled_by_default above."""
    original = _tools_config.desktop_control_enabled
    _tools_config.desktop_control_enabled = False
    try:
        assert "disabled" in desktop_screenshot()
    finally:
        _tools_config.desktop_control_enabled = original


def test_pending_vision_image_pops_once():
    set_pending_vision_image(None)
    assert pop_pending_vision_image() is None
    set_pending_vision_image("data:image/png;base64,x")
    assert pop_pending_vision_image() == "data:image/png;base64,x"
    assert pop_pending_vision_image() is None


@pytest.mark.asyncio
async def test_describe_image_sends_no_history_or_tools(brain_config, monkeypatch):
    """Regression: the vision model used to receive the full conversation
    history + tool schema via a 'follow-up route' swap, which could include
    malformed history-sanitizer stub messages and caused both hallucinated
    tool calls and, separately, non-terminating generation. _describe_image
    is a single stateless call -- image in, text out -- with no history and
    no tools, so neither failure mode has anything to latch onto."""
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "a red button"}}]}

    class FakeVisionClient:
        async def post(self, url, json):
            captured["payload"] = json
            return FakeResponse()

    brain = Brain(brain_config)
    brain._vision_client = FakeVisionClient()
    brain._vision_model = "vision-model"

    result = await brain._describe_image("data:image/png;base64,x")

    assert result == "a red button"
    payload = captured["payload"]
    assert "tools" not in payload
    assert "tool_choice" not in payload
    assert payload["stream"] is False
    assert payload["max_tokens"] == _VISION_MAX_TOKENS
    assert payload["messages"] == [
        {"role": "system", "content": "Describe what is visible in this image factually and concisely."},
        {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}}]},
    ]


@pytest.mark.asyncio
async def test_describe_image_times_out_gracefully(brain_config, monkeypatch):
    """Core regression check: a vision call that never returns must not hang
    the turn forever -- it must resolve to a graceful message within the
    configured deadline."""
    import asyncio

    class HangingVisionClient:
        async def post(self, url, json):
            await asyncio.sleep(10)

    brain_config.vision_llm_timeout_s = 0.05
    brain = Brain(brain_config)
    brain._vision_client = HangingVisionClient()
    brain._vision_model = "vision-model"

    async with asyncio.timeout(2):
        result = await brain._describe_image("data:image/png;base64,x")

    assert "timed out" in result


def test_screen_query_phrase_matches():
    assert _SCREEN_QUERY_RE.search("what's on my screen")
    assert _SCREEN_QUERY_RE.search("what do you see right now")
    assert _SCREEN_QUERY_RE.search("can you read my screen")
    assert not _SCREEN_QUERY_RE.search("open notepad and type hello")


def test_desktop_com_tools_covers_perception_and_effectors():
    assert _DESKTOP_CONTROL_TOOLS <= _DESKTOP_COM_TOOLS
    assert _DESKTOP_COM_TOOLS == _DESKTOP_CONTROL_TOOLS | {
        "desktop_observe", "desktop_read_screen", "desktop_screenshot",
    }


def test_uia_executor_matches_desktop_availability():
    # Single dedicated COM-initialized thread when UIA is usable, None otherwise
    # (None means _exec_one falls back to the default pool, but desktop tools
    # are gated behind DESKTOP_AVAILABLE before ever reaching it either way).
    assert (UIA_EXECUTOR is not None) == DESKTOP_AVAILABLE
    if UIA_EXECUTOR is not None:
        assert UIA_EXECUTOR._max_workers == 1


def test_vision_annotate_unavailable_without_pillow():
    if desktop_vision.VISION_AVAILABLE:
        pytest.skip("Pillow installed in this environment")
    with pytest.raises(RuntimeError):
        desktop_vision.annotate_som(b"", [])


def test_vision_annotate_handles_negative_and_swapped_bounds():
    if not desktop_vision.VISION_AVAILABLE:
        pytest.skip("Pillow not installed in this environment")
    import io as _io

    from PIL import Image
    img = Image.new("RGB", (100, 100))
    buf = _io.BytesIO()
    img.save(buf, format="PNG")
    elements = [
        Element(mark_id=1, name="offscreen", control_type="Button",
                bounds=(-10, -20, 5, 5), is_password=False, is_offscreen=True),
        Element(mark_id=2, name="swapped", control_type="Button",
                bounds=(50, 50, 10, 10), is_password=False, is_offscreen=False),
    ]
    annotated = desktop_vision.annotate_som(buf.getvalue(), elements)
    assert annotated


def test_to_data_url_downscales_oversized_screenshot():
    """Regression: an unscaled full-resolution screenshot (~1.2MB base64)
    silently produced empty responses from a local vision model instead of
    a clean error -- almost certainly exceeding its practical image-size
    handling. Downscaling on the long edge is the fix."""
    if not desktop_vision.VISION_AVAILABLE:
        pytest.skip("Pillow not installed in this environment")
    import base64
    import io as _io

    from PIL import Image
    img = Image.new("RGB", (1920, 1080), color="red")
    buf = _io.BytesIO()
    img.save(buf, format="PNG")

    url = desktop_vision.to_data_url(buf.getvalue())
    decoded = base64.b64decode(url.split(",", 1)[1])
    resized = Image.open(_io.BytesIO(decoded))
    assert max(resized.size) <= desktop_vision._MAX_VISION_DIMENSION


def test_to_data_url_leaves_small_image_unscaled():
    if not desktop_vision.VISION_AVAILABLE:
        pytest.skip("Pillow not installed in this environment")
    import io as _io

    from PIL import Image
    img = Image.new("RGB", (100, 100), color="blue")
    buf = _io.BytesIO()
    img.save(buf, format="PNG")

    url = desktop_vision.to_data_url(buf.getvalue())
    assert url.startswith("data:image/png;base64,")


if __name__ == "__main__":
    test_desktop_control_tools_frozenset()
    test_desktop_tools_disabled_by_default()
    test_actions_halt_toggle()
    test_click_mark_unknown_id_returns_error_not_raise()
    test_type_text_unknown_id_returns_error_not_raise()
    test_desktop_read_screen_disabled_by_default()
    test_merge_ocr_elements_continues_mark_id_sequence()
    test_desktop_screenshot_disabled_by_default()
    test_pending_vision_image_pops_once()
    test_desktop_com_tools_covers_perception_and_effectors()
    test_uia_executor_matches_desktop_availability()
    test_vision_annotate_unavailable_without_pillow()
    test_vision_annotate_handles_negative_and_swapped_bounds()
    print("ok")

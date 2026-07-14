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
    _DESKTOP_DISARM_RE,
    _SCREEN_QUERY_RE,
    Brain,
    _payload_is_vision,
    _with_vision_image,
)
from charlie.desktop import DESKTOP_AVAILABLE, UIA_EXECUTOR
from charlie.desktop import actions as desktop_actions
from charlie.desktop import vision as desktop_vision
from charlie.desktop.uia import Element, merge_ocr_elements, resolve_bounds, resolve_is_password, resolve_name
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
        small_llm_url="http://localhost:11434",
        small_llm_key="no-key",
        small_llm_model="dummy",
        iteration_budget_max=3,
    )


def test_desktop_control_tools_frozenset():
    assert _DESKTOP_CONTROL_TOOLS == {"desktop_click", "desktop_type", "desktop_invoke", "desktop_key"}


def test_desktop_tools_disabled_by_default():
    """desktop_control_enabled defaults to false -- tools must refuse, not crash."""
    assert "disabled" in desktop_observe()
    assert "disabled" in desktop_click(1)


def test_desktop_gate_reason_arms_once_per_session(brain_config):
    brain = Brain(brain_config)
    assert brain._desktop_gate_reason() == "take control of your desktop"
    brain._desktop_armed = True
    # Stays armed across turns -- one approval covers the whole session.
    assert brain._desktop_gate_reason() is None
    assert brain._desktop_gate_reason() is None


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
    assert "disabled" in desktop_read_screen()


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


def test_desktop_screenshot_disabled_by_default():
    assert "disabled" in desktop_screenshot()


def test_pending_vision_image_pops_once():
    set_pending_vision_image(None)
    assert pop_pending_vision_image() is None
    set_pending_vision_image("data:image/png;base64,x")
    assert pop_pending_vision_image() == "data:image/png;base64,x"
    assert pop_pending_vision_image() is None


def test_with_vision_image_rewrites_last_user_message_only():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "click OK"},
    ]
    out = _with_vision_image(messages, "data:image/png;base64,x")
    assert out[0] == {"role": "system", "content": "sys"}
    assert out[1]["content"] == [
        {"type": "text", "text": "click OK"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}},
    ]
    # original list/dicts untouched -- history persistence stays string-only
    assert messages[1]["content"] == "click OK"
    assert _payload_is_vision({"messages": out}) is True
    assert _payload_is_vision({"messages": messages}) is False


def test_select_followup_route_prefers_vision_when_payload_carries_image(brain_config):
    brain = Brain(brain_config)
    brain._vision_client = object()
    brain._vision_model = "vision-model"
    payload = {"messages": [{"role": "user", "content": [{"type": "text", "text": "x"}]}]}
    client, model, is_vision = brain._select_followup_route(payload, used_fallback=False)
    assert (client, model, is_vision) == (brain._vision_client, "vision-model", True)


def test_select_followup_route_falls_back_to_small_without_image(brain_config):
    brain = Brain(brain_config)
    payload = {"messages": [{"role": "user", "content": "hi"}]}
    client, model, is_vision = brain._select_followup_route(payload, used_fallback=False)
    assert (client, model, is_vision) == (brain.client, brain_config.small_llm_model, False)


def test_desktop_disarm_phrase_matches():
    assert _DESKTOP_DISARM_RE.search("stop controlling my desktop")
    assert _DESKTOP_DISARM_RE.search("please disarm desktop control")
    assert not _DESKTOP_DISARM_RE.search("open notepad and type hello")


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
    test_with_vision_image_rewrites_last_user_message_only()
    test_select_followup_route_prefers_vision_when_payload_carries_image(brain_config())
    test_select_followup_route_falls_back_to_small_without_image(brain_config())
    test_desktop_com_tools_covers_perception_and_effectors()
    test_uia_executor_matches_desktop_availability()
    test_vision_annotate_unavailable_without_pillow()
    test_vision_annotate_handles_negative_and_swapped_bounds()
    print("ok")

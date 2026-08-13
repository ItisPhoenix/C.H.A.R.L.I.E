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
    _VISION_SYSTEM_PROMPT,
    Brain,
    _payload_is_vision,
    _with_vision_image,
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
from charlie.router import SCREEN_QUERY_RE as _SCREEN_QUERY_RE
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


def test_walk_captures_named_document_control():
    """Regression: modern Notepad's text area is a DocumentControl (RichEdit), not EditControl --
    _walk must include it in the set-of-marks output, not silently skip it."""
    from charlie.desktop import uia as uia_mod

    class FakeRect:
        left, top, right, bottom = 0, 0, 100, 100

    class FakeDocumentControl:
        ControlTypeName = "DocumentControl"
        Name = "Text editor"
        IsOffscreen = False
        BoundingRectangle = FakeRect()

        def GetChildren(self):
            return []

    marks: list = []
    controls: dict = {}
    uia_mod._walk(FakeDocumentControl(), marks, controls, depth=0, max_depth=8)
    assert len(marks) == 1
    assert marks[0].control_type == "Document"
    assert marks[0].name == "Text editor"


def test_walk_stops_at_mark_budget_not_depth():
    """A deep, wide tree must stop once max_marks is hit -- max_depth alone was the old,
    too-shallow limiter that returned near-nothing for Chrome/Electron/WinUI trees."""
    from charlie.desktop import uia as uia_mod

    class FakeRect:
        left, top, right, bottom = 0, 0, 10, 10

    class FakeButton:
        ControlTypeName = "ButtonControl"
        Name = "btn"
        IsOffscreen = False
        BoundingRectangle = FakeRect()

        def GetChildren(self):
            return [FakeButton() for _ in range(5)]

    marks: list = []
    controls: dict = {}
    uia_mod._walk(FakeButton(), marks, controls, depth=0, max_depth=25, max_marks=10)
    assert len(marks) == 10


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


def test_build_payload_uses_instance_pending_image_not_shared_global():
    """Regression: two concurrent Brain instances (e.g. foreground + a
    background task) both calling desktop_screenshot used to race on one
    shared tools.py global -- whichever Brain's _build_payload ran second
    could steal or corrupt the other's image. _exec_one now pops the global
    into self._pending_vision_image_url immediately after its own
    desktop_screenshot call, so _build_payload only ever reads its own
    Brain's value and never touches (or is affected by) the shared global."""
    from charlie.config import Config
    from charlie.core import Brain

    cfg = Config(
        llm_url="https://example.com/v1",
        llm_key="test-key",
        llm_model="dummy",
        native_tool_calling=True,
        vision_enabled=True,
    )
    brain = Brain(cfg)
    brain._pending_vision_image_url = "image-for-this-brain"
    # Simulates a second, concurrent Brain instance's own desktop_screenshot
    # call leaving its image in the shared global.
    set_pending_vision_image("image-from-a-different-brain")

    payload = brain._build_payload([{"role": "user", "content": "hi"}])

    content = payload["messages"][-1]["content"]
    assert any(
        isinstance(block, dict) and block.get("image_url", {}).get("url") == "image-for-this-brain"
        for block in content
    )
    # The shared global is untouched by this Brain's payload build.
    assert pop_pending_vision_image() == "image-from-a-different-brain"


def test_build_payload_strips_tools_when_vision_image_attached():
    """A vision model handed the tool schema can emit a broken tool call instead of text."""
    from charlie.config import Config
    from charlie.core import Brain

    cfg = Config(
        llm_url="https://example.com/v1",
        llm_key="test-key",
        llm_model="dummy",
        native_tool_calling=True,
        vision_enabled=True,
    )
    brain = Brain(cfg)
    brain._pending_vision_image_url = "data:image/png;base64,x"

    payload = brain._build_payload([{"role": "user", "content": "what's on my screen?"}])

    assert "tools" not in payload
    assert "tool_choice" not in payload


def test_with_vision_image_rewrites_last_user_message_only():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "click OK"},
    ]
    out = _with_vision_image(messages, "data:image/png;base64,x")
    assert out[0] == {"role": "system", "content": _VISION_SYSTEM_PROMPT}
    assert out[1]["content"] == [
        {"type": "text", "text": "click OK"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}},
    ]
    # original list/dicts untouched -- history persistence stays string-only
    assert messages[1]["content"] == "click OK"
    assert _payload_is_vision({"messages": out}) is True
    assert _payload_is_vision({"messages": messages}) is False


def test_with_vision_image_drops_tool_call_history_and_full_system_prompt():
    # Vision answers from image + question -- prior tool results and Charlie's full system prompt must not ride along.
    messages = [
        {"role": "system", "content": "sys" * 1000},
        {"role": "user", "content": "earlier question"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "1", "type": "function", "function": {}}]},
        {"role": "tool", "content": "a huge OCR dump" * 1000},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "2", "type": "function", "function": {}}]},
        {"role": "tool", "content": "another huge tool result" * 1000},
        {"role": "user", "content": "what's on my screen?"},
    ]
    out = _with_vision_image(messages, "data:image/png;base64,x")
    assert out == [
        {"role": "system", "content": _VISION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "what's on my screen?"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}},
            ],
        },
    ]


def test_select_followup_route_prefers_vision_when_payload_carries_image(brain_config):
    brain = Brain(brain_config)
    brain._vision_client = object()
    brain._vision_model = "vision-model"
    payload = {"messages": [{"role": "user", "content": [{"type": "text", "text": "x"}]}]}
    client, model, is_vision = brain._select_followup_route(payload)
    assert (client, model, is_vision) == (brain._vision_client, "vision-model", True)


def test_select_followup_route_uses_llm_without_image(brain_config):
    brain = Brain(brain_config)
    payload = {"messages": [{"role": "user", "content": "hi"}]}
    client, model, is_vision = brain._select_followup_route(payload)
    assert (client, model, is_vision) == (brain.client, brain_config.llm_model, False)


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
    test_walk_captures_named_document_control()
    test_merge_ocr_elements_continues_mark_id_sequence()
    test_desktop_screenshot_disabled_by_default()
    test_pending_vision_image_pops_once()
    test_build_payload_strips_tools_when_vision_image_attached()
    test_with_vision_image_rewrites_last_user_message_only()
    test_with_vision_image_drops_tool_call_history_and_full_system_prompt()
    test_select_followup_route_prefers_vision_when_payload_carries_image(brain_config())
    test_select_followup_route_uses_llm_without_image(brain_config())
    test_desktop_com_tools_covers_perception_and_effectors()
    test_uia_executor_matches_desktop_availability()
    test_vision_annotate_unavailable_without_pillow()
    test_vision_annotate_handles_negative_and_swapped_bounds()
    print("ok")

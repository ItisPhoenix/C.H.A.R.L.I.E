from charlie.pet_window import _CAPTION_MAX_CHARS, _map_event_to_caption, _map_event_to_state


def test_map_event_to_state():
    assert _map_event_to_state("vad_start", {}) == "listening"
    assert _map_event_to_state("wake_word", {}) == "listening"
    assert _map_event_to_state("thinking", {}) == "thinking"
    assert _map_event_to_state("speaking_start", {}) == "speaking"
    assert _map_event_to_state("speaking_stop", {}) == "idle"
    assert _map_event_to_state("response_done", {}) == "idle"
    assert _map_event_to_state("audio_level", {}) is None
    assert _map_event_to_state("", {}) is None


def test_map_event_to_state_thinking_substates():
    assert _map_event_to_state("thinking", {"payload": {"text": "web_search"}}) == "searching"
    assert _map_event_to_state("thinking", {"payload": {"text": "file_read"}}) == "reading"
    assert _map_event_to_state("thinking_update", {"payload": {"text": "code_exec"}}) == "reading"


def test_map_event_to_caption():
    assert _map_event_to_caption({"type": "vad_start"}) == ("Listening...", "I'm paying attention")
    assert _map_event_to_caption({"type": "thinking"}) == ("Thinking...", "Processing request...")
    assert _map_event_to_caption(
        {"type": "speaking_start", "payload": {"text": "Hello there"}}
    ) == ("Speaking", "Hello there")
    assert _map_event_to_caption({"type": "speaking_start", "payload": {}}) == ("Speaking", "Responding...")
    assert _map_event_to_caption({"type": "speaking_stop"}) == ("", "")
    assert _map_event_to_caption({"type": "response_done"}) == ("", "")
    assert _map_event_to_caption({"type": "vad_stop"}) == ("", "")
    assert _map_event_to_caption({"type": "audio_level"}) == (None, None)


def test_map_event_to_caption_truncates_long_text():
    long_text = "x" * (_CAPTION_MAX_CHARS + 50)
    _title, desc = _map_event_to_caption({"type": "speaking_start", "payload": {"text": long_text}})
    assert desc.endswith("...")
    assert len(desc) <= _CAPTION_MAX_CHARS + 3

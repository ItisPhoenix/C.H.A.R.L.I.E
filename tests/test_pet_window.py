from charlie.pet_window import _CAPTION_MAX_CHARS, _map_event_to_caption, _map_event_to_state


def test_map_event_to_state():
    assert _map_event_to_state("vad_start") == "listening"
    assert _map_event_to_state("wake_word") == "listening"
    assert _map_event_to_state("thinking") == "thinking"
    assert _map_event_to_state("speaking_start") == "speaking"
    assert _map_event_to_state("speaking_stop") == "idle"
    assert _map_event_to_state("response_done") == "idle"
    assert _map_event_to_state("audio_level") is None
    assert _map_event_to_state("") is None


def test_map_event_to_caption():
    assert _map_event_to_caption({"type": "vad_start"}) == "Listening..."
    assert _map_event_to_caption({"type": "thinking"}) == "Thinking..."
    assert _map_event_to_caption(
        {"type": "speaking_start", "payload": {"text": "Hello there"}}
    ) == "Hello there"
    assert _map_event_to_caption({"type": "speaking_start", "payload": {}}) == "Speaking..."
    assert _map_event_to_caption({"type": "speaking_stop"}) == ""
    assert _map_event_to_caption({"type": "response_done"}) == ""
    assert _map_event_to_caption({"type": "audio_level"}) is None


def test_map_event_to_caption_truncates_long_text():
    long_text = "x" * (_CAPTION_MAX_CHARS + 50)
    caption = _map_event_to_caption({"type": "speaking_start", "payload": {"text": long_text}})
    assert caption.endswith("...")
    assert len(caption) <= _CAPTION_MAX_CHARS + 3

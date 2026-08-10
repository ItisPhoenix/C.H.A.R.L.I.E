"""Regression: numbered/bulleted list items must flush as separate TTS chunks."""
from main import _SENTENCE_BOUNDARY, _flush_complete_sentences


def test_numbered_list_items_split_at_newline():
    buffer = "1. Claude Mythos 5 -- #1 at 82.9/100\n2. Claude Fable 5 -- #2 at 80.1/100\n"
    chunks = []
    leftover, flushed = _flush_complete_sentences(buffer, chunks.append)
    assert flushed
    assert any("Mythos" in c for c in chunks)
    assert not any("Mythos" in c and "Fable" in c for c in chunks)


def test_plain_sentence_boundary_still_works():
    assert _SENTENCE_BOUNDARY.search("Hello there. How are you?")

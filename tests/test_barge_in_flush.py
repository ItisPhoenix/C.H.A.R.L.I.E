"""Regression tests for the streaming TTS flush boundaries (main.py's _process
loop): sentence boundary > clause boundary > force-flush at _MAX_FLUSH_CHARS,
with an earlier, lower first-chunk threshold. These exact boundaries have
shipped broken twice (outran a slow remote LLM's prefill, produced an
audible gap) -- see CLAUDE.md section 7. Written before a core.py / main.py
reorganization so the decomposition has something to fail against.

main.py can't be imported directly: it transitively pulls in whisper/kokoro/
torch at module load, which hangs inside a pytest worker (see
test_event_envelopes.py). We extract the real source of the pure pieces via
AST and exec them in an isolated namespace instead -- same pattern already
established in this test suite.
"""

import ast
import os
import re
from typing import Callable, Tuple

MAIN_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py"
)


def _load_main_source() -> str:
    with open(MAIN_PATH, "r", encoding="utf-8") as fh:
        return fh.read()


_MAIN_SOURCE = _load_main_source()
_MAIN_AST = ast.parse(_MAIN_SOURCE)


def _extract_flush_helpers():
    """Pull _SENTENCE_BOUNDARY / _CLAUSE_BOUNDARY / _MAX_FLUSH_CHARS /
    _flush_complete_sentences out of the real main.py source via AST and exec
    them in a fresh namespace, so tests run against the actual shipped
    regexes and thresholds, not a hand-copied duplicate."""
    wanted_assigns = {"_SENTENCE_BOUNDARY", "_CLAUSE_BOUNDARY", "_MAX_FLUSH_CHARS"}
    snippets = []
    for node in _MAIN_AST.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id in wanted_assigns for t in node.targets
        ):
            snippets.append(ast.unparse(node))
        if isinstance(node, ast.FunctionDef) and node.name == "_flush_complete_sentences":
            snippets.append(ast.unparse(node))
    assert len(snippets) == 4, f"expected 3 constants + 1 function, found {len(snippets)}"
    namespace = {"re": re, "Callable": Callable, "Tuple": Tuple}
    exec(compile("\n".join(snippets), "<main.flush_helpers>", "exec"), namespace)
    return namespace


_NS = _extract_flush_helpers()
_flush_complete_sentences = _NS["_flush_complete_sentences"]
_MAX_FLUSH_CHARS = _NS["_MAX_FLUSH_CHARS"]


class TestFlushCompleteSentences:
    def test_no_boundary_returns_buffer_unflushed(self):
        leftover, flushed = _flush_complete_sentences("no boundary here", lambda p: None)
        assert leftover == "no boundary here"
        assert flushed is False

    def test_single_complete_sentence_flushes_and_clears(self):
        sunk = []
        leftover, flushed = _flush_complete_sentences("Hello world. ", sunk.append)
        assert flushed is True
        assert sunk == ["Hello world."]
        assert leftover == ""

    def test_trailing_incomplete_sentence_kept_as_leftover(self):
        sunk = []
        leftover, flushed = _flush_complete_sentences(
            "First sentence. Second incomplete", sunk.append
        )
        assert flushed is True
        assert sunk == ["First sentence."]
        assert leftover == "Second incomplete"

    def test_multiple_complete_sentences_all_flushed_in_order(self):
        sunk = []
        _flush_complete_sentences("One. Two! Three? ", sunk.append)
        assert sunk == ["One.", "Two!", "Three?"]


def test_max_flush_chars_is_200():
    """Regression guard: the force-flush threshold is 200 chars
    (CLAUDE.md section 7). A silent change here changes TTS latency."""
    assert _MAX_FLUSH_CHARS == 200


def _get_process_source() -> ast.AsyncFunctionDef:
    for node in ast.walk(_MAIN_AST):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_process":
            return node
    raise AssertionError("_process() not found in main.py")


def test_flush_order_is_sentence_then_clause_then_force():
    """The streaming flush loop must try, in this exact order: first-flush
    sentence boundary, then normal sentence boundary, then clause/force at
    _MAX_FLUSH_CHARS. Verified structurally (not by running _process, which
    needs a live voice/brain/store) by checking _flush_complete_sentences is
    called before the _MAX_FLUSH_CHARS force-flush branch in source order."""
    process_fn = _get_process_source()
    src = ast.unparse(process_fn)
    first_flush_sentence_idx = src.index("is_first_flush")
    normal_sentence_idx = src.index("_flush_complete_sentences", first_flush_sentence_idx + 1)
    force_flush_idx = src.index("_MAX_FLUSH_CHARS")
    assert first_flush_sentence_idx < normal_sentence_idx < force_flush_idx, (
        "flush precedence changed: expected first-flush -> sentence -> "
        "clause/force order in _process()"
    )


def test_first_flush_force_threshold_is_150():
    """First-chunk force-flush threshold (used only if no sentence boundary
    appears before it). Regression guard on the literal in _process()."""
    process_fn = _get_process_source()
    src = ast.unparse(process_fn)
    assert "150" in src, (
        "first-flush force threshold (150 chars) not found in _process() -- "
        "if this changed intentionally, update this test's expected value"
    )

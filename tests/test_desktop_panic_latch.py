"""Regression tests for the panic-hotkey latch fix.

Bug: the physical panic latch (desktop_actions._HALT) used to auto-clear the
moment the tool loop first observed it halted -- one panic press only ever
stopped the *next* action, not everything until an explicit resume. Fixed by
removing the auto clear_halt() call from chat_stream's tool loop and adding
an explicit resume fast-path (_detect_desktop_resume) instead.
"""

import ast

from charlie.core import Brain, _detect_desktop_resume


class TestDetectDesktopResume:
    def test_continue_matches(self):
        assert _detect_desktop_resume("continue") is True

    def test_resume_matches(self):
        assert _detect_desktop_resume("resume") is True

    def test_keep_going_matches(self):
        assert _detect_desktop_resume("keep going please") is True

    def test_unrelated_text_does_not_match(self):
        assert _detect_desktop_resume("open chrome") is False

    def test_continue_mid_sentence_does_not_match(self):
        # Anchored at the start -- "let's continue the report" is a normal request, not a resume command.
        assert _detect_desktop_resume("let's continue the report") is False


def test_chat_stream_does_not_auto_clear_desktop_halt():
    """Structural regression guard: the desktop-halted branch of chat_stream's
    tool loop must never call desktop_actions.clear_halt() -- only the
    explicit resume fast-path (dispatched earlier, outside this branch) may."""
    import inspect
    import textwrap

    src = textwrap.dedent(inspect.getsource(Brain.chat_stream))
    tree = ast.parse(src)
    func = tree.body[0]
    assert isinstance(func, ast.AsyncFunctionDef)

    halted_branch_src = None
    for node in ast.walk(func):
        if isinstance(node, ast.If):
            test_src = ast.unparse(node.test)
            if "_is_desktop_halted" in test_src:
                halted_branch_src = ast.unparse(node)
                break
    assert halted_branch_src is not None, "desktop-halted branch not found in chat_stream"
    assert "clear_halt" not in halted_branch_src, (
        "chat_stream's tool loop must not auto-clear the panic latch -- "
        "resume must be an explicit user action (_detect_desktop_resume)"
    )

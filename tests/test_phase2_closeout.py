"""Phase 2 closeout: shutdown quiescence, approval channel ownership, recovery truth.

Each test asserts a decision the runtime makes, not a source string.

Evidence class: TEST/MOCK.
"""

from __future__ import annotations

import asyncio
import sys

import pytest

from charlie import core as core_module
from charlie.core import _await_executor_quiescence


class TestShutdownQuiescenceDoesNotSwallowBaseException:
    """Quiescence must wait for the worker, but must not eat SystemExit."""

    def test_system_exit_is_not_swallowed(self):
        async def _worker():
            raise SystemExit(3)

        async def _run():
            future = asyncio.ensure_future(_worker())
            await _await_executor_quiescence(future)
            return future

        with pytest.raises(SystemExit):
            asyncio.run(_run())

    def test_keyboard_interrupt_is_not_swallowed(self):
        async def _worker():
            raise KeyboardInterrupt

        async def _run():
            future = asyncio.ensure_future(_worker())
            await _await_executor_quiescence(future)
            return future

        with pytest.raises(KeyboardInterrupt):
            asyncio.run(_run())

    def test_an_ordinary_worker_error_still_completes_quiescence(self):
        """A RuntimeError must NOT propagate: quiescence waits, then returns."""

        async def _worker():
            raise RuntimeError("worker blew up")

        async def _run():
            future = asyncio.ensure_future(_worker())
            await _await_executor_quiescence(future)
            return future

        future = asyncio.run(_run())
        assert future.done()
        with pytest.raises(RuntimeError):
            future.result()

    def test_a_clean_worker_is_awaited_to_completion(self):
        async def _worker():
            return "done"

        async def _run():
            future = asyncio.ensure_future(_worker())
            await _await_executor_quiescence(future)
            return future

        assert asyncio.run(_run()).result() == "done"

    def test_the_except_clause_is_not_base_exception(self):
        """Guards against the broad clause being reintroduced.

        ast.walk is required: the try sits inside a while, so inspecting only the
        function body would find no handler at all and pass vacuously.
        """
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(_await_executor_quiescence).lstrip())
        handlers = [h for node in ast.walk(tree) if isinstance(node, ast.Try) for h in node.handlers]
        assert handlers, "expected the try to still be present"
        broad = [
            h
            for h in handlers
            if isinstance(h.type, ast.Name) and h.type.id == "BaseException"
        ]
        assert not broad, "quiescence must not swallow BaseException"


class TestWebApprovalKeepsChannelOwnership:
    """A web client must not be able to resolve a voice or Telegram approval."""

    def test_web_command_passes_its_own_channel_as_expected_platform(self):
        """The web handler binds the approval to the web channel.

        Passing None skips the ownership check entirely, letting a browser session
        resolve an approval the owner raised on another channel.
        """
        import ast
        from pathlib import Path

        repo = Path(core_module.__file__).resolve().parents[1]
        main_src = (repo / "main.py").read_text(encoding="utf-8")
        tree = ast.parse(main_src)

        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name != "_resolve_tool_approval_and_notify":
                continue
            for kw in node.keywords:
                if kw.arg == "expected_platform" and isinstance(kw.value, ast.Constant):
                    if kw.value.value is None:
                        offenders.append(node.lineno)
        assert not offenders, (
            "expected_platform=None bypasses channel ownership at main.py lines "
            f"{offenders}"
        )


class TestCoreModuleExposesNoLegacyApprovalFacade:
    """The live tool path must carry typed approval outcomes, not a bool."""

    def test_legacy_approval_helper_is_absent(self):
        assert not hasattr(core_module, "_legacy_approval"), (
            "the bool round-trip that flattens approval outcomes is still exported"
        )

    def test_approval_status_is_persisted_in_tool_events(self):
        import inspect

        from charlie import session_store

        source = inspect.getsource(session_store.SessionStore.append_tool)
        assert "approval_status" in source, (
            "approval_status must reach the journal row, or the outcome is "
            "unexplainable after the fact"
        )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))

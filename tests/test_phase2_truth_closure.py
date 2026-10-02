"""Phase 2 truth closure: an approved recovery is not an executed command.

Four separate losses of truth are closed here.

1. ``recover_tool`` could turn a failure into a success. It returned the
   approval *explanation* ("Found in PATH: C:\\...\\code.CMD") as a plain
   string, and the operation boundary adopted that string as the tool result.
   ``_normalize_tool_result`` classifies any text that does not start with
   ``"Error"`` as ``completed``, and a ``shell_execute`` string has no
   structured exit code, so ``verification`` was ``None`` and the operator saw
   ``completed`` for a command that was never executed.

2. ``Brain.session_history_error`` was structurally unreachable.
   ``SessionStore.get_session_messages`` degrades to ``[]`` on every
   ``sqlite3.Error`` (``_with_retry(..., reraise=False)`` returns ``None`` and
   ``get_recent`` applies ``or []``), so the ``except`` that owns the attribute
   could not run, and the real failure landed on the success line with
   ``session_history_error = None``.

3. The health-truth attributes the Phase 2 pass introduced had no consumer at
   all, so the runtime's subsystem health surface kept showing the last
   successfully delivered transition.

4. A failed user-correction write to ``OPINIONS.md`` returned ``None``, which
   is the same value a dedupe no-op returns, so a lost durable write was
   invisible to a fire-and-forget executor future nobody awaits.

Evidence class: TEST/MOCK. No real command is ever launched. ``shell_execute``
is replaced by an ``execute_override`` returning the exact adapter text a
failing shell call produces, recovery is pointed at a command that is not
executed, and the only real dependency exercised is a real ``SessionStore``
over a temporary SQLite file.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List

import pytest

import charlie.core as core
import charlie.recovery as recovery_module
from charlie.autonomy import Requirement
from charlie.config import Config
from charlie.core import Brain, _apply_correction_to_memory
from charlie.recovery import (
    BaseRecoveryStrategy,
    FailureClass,
    RecoveryResult,
    recover_tool,
)
from charlie.session_store import SessionStore
from charlie.subsystem_health import HealthStatus
from charlie.turn_contracts import ResultStatus

_FOREIGN_SESSION_MARKER = "confidential note that belongs to a different session"
_NOT_FOUND_TEXT = "Error: [WinError 2] The system cannot find the file specified"
_RESOLVED_COMMAND = "C:/charlie-notepad.exe report.txt"


def _config(**overrides) -> Config:
    base = {
        "llm_url": "http://localhost:11434",
        "llm_key": "no-key",
        "llm_model": "dummy",
    }
    base.update(overrides)
    return Config(**base)


class _SingleChunkStream:
    """Minimal ``httpx`` streaming response that emits one content delta."""

    def __init__(self, text: str):
        self._text = text

    def raise_for_status(self):
        return None

    async def aiter_lines(self):
        yield f'data: {json.dumps({"choices": [{"delta": {"content": self._text}}]})}'
        yield "data: [DONE]"

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


_UNWRITABLE_NAMES = {"IsADirectoryError", "PermissionError", "OSError", "EISDIR"}


class _ConfidentStrategy(BaseRecoveryStrategy):
    """A strategy certain it located the executable but that never runs it."""

    def __init__(self, resolved: str = _RESOLVED_COMMAND):
        self._resolved = resolved
        self.proposed: List[str] = []

    def can_handle(self, failure: Dict[str, Any]) -> bool:
        return failure["failure_class"] == FailureClass.NOT_FOUND

    async def recover(self, command: str, failure: Dict[str, Any]) -> RecoveryResult:
        self.proposed.append(command)
        return RecoveryResult(
            success=True,
            command=self._resolved,
            message=f"Found in PATH: {self._resolved}",
        )


class _TimeoutStrategy(BaseRecoveryStrategy):
    """Proposes a different command after a timeout, so the timeout branch has
    an approved recovery to mishandle. The shipped ``DeclassProcessStrategy``
    deliberately refuses timeouts, which is why the timeout call site could not
    be reached by an approved proposal in the first place."""

    def can_handle(self, failure: Dict[str, Any]) -> bool:
        return failure["failure_class"] == FailureClass.TIMEOUT

    async def recover(self, command: str, failure: Dict[str, Any]) -> RecoveryResult:
        return RecoveryResult(
            success=True,
            command=_RESOLVED_COMMAND,
            message=f"Found in PATH: {_RESOLVED_COMMAND}",
        )


@pytest.fixture
def isolated_recovery_cache(monkeypatch):
    """Keep the on-disk recovery cache out of this file and record its writes."""
    from charlie import recovery_cache

    reads: List[Any] = []
    writes: List[Any] = []
    monkeypatch.setattr(recovery_cache, "get_cached_resolution", lambda *a, **k: reads.append(a) and None)
    monkeypatch.setattr(recovery_cache, "set_cached_resolution", lambda *a, **k: writes.append(a))
    return {"reads": reads, "writes": writes}


@pytest.fixture
def approving_brain(monkeypatch, isolated_recovery_cache):
    """A Brain whose shell policy allows the call and whose recovery is approved.

    ``autonomy_evaluate`` is neutralised so the outer ``shell_execute`` gate
    does not demand an interactive approval, and the canonical approval owner
    is stubbed so the *recovery* proposal really is approved -- which is the
    state that used to produce the false success.
    """
    from charlie.core import ApprovalDecision

    brain = Brain(_config())
    monkeypatch.setattr(core, "autonomy_evaluate", lambda *_a, **_k: (Requirement.ALLOW, "safe", ""))

    async def _approved(*_args, **_kwargs):
        return ApprovalDecision.APPROVED

    brain._request_tool_approval_decision = _approved
    return brain


# ---------------------------------------------------------------------------
# 1. An approved recovery must not become a completed tool result.
# ---------------------------------------------------------------------------


class TestApprovedRecoveryIsNotAnExecutedCommand:
    @pytest.mark.asyncio
    async def test_a_recovery_outcome_states_whether_anything_ran(self, monkeypatch, isolated_recovery_cache):
        from charlie.core import ApprovalDecision

        asked: List[Dict[str, Any]] = []

        async def _approve(_brain, **kwargs):
            asked.append(kwargs)
            return ApprovalDecision.APPROVED

        monkeypatch.setattr(recovery_module, "request_recovery_approval", _approve)
        monkeypatch.setattr(recovery_module, "RECOVERY_REGISTRY", [_ConfidentStrategy()])

        outcome = await recover_tool(
            object(),
            "shell_execute",
            {"command": "charlie-notepad.exe report.txt"},
            FileNotFoundError("[WinError 2] The system cannot find the file specified"),
        )

        assert outcome is not None, "an approved recovery must report its outcome"
        assert outcome.executed is False, (
            "recovery located the executable; it never ran it, so the outcome "
            "must not claim an execution"
        )
        assert _RESOLVED_COMMAND in outcome.instruction, (
            "an approved proposal must be turned into an instruction the model "
            "can act on instead of a result it can mistake for a success"
        )
        assert asked and asked[0]["proposed_command"] == _RESOLVED_COMMAND

    @pytest.mark.asyncio
    async def test_a_raising_tool_is_not_completed_by_an_approved_recovery(
        self, monkeypatch, approving_brain
    ):
        """The reported bug: an operator saw ``completed`` for a command that never ran.

        This is the exception call site in ``_execute_operation_primitive``.
        """
        monkeypatch.setattr(recovery_module, "RECOVERY_REGISTRY", [_ConfidentStrategy()])

        def _raise():
            raise FileNotFoundError("[WinError 2] The system cannot find the file specified")

        envelope = await approving_brain.execute_tool_operation(
            "shell_execute",
            {"command": "charlie-notepad.exe report.txt"},
            request="open the report in notepad",
            task_id=None,
            session_id=None,
            turn_id="turn-truth-closure-exception",
            platform="text",
            execute_override=_raise,
        )

        assert envelope.status != ResultStatus.COMPLETED.value, (
            "the approved recovery only located the executable; nothing ran, so "
            "the operation must not be published as completed"
        )
        assert envelope.status == ResultStatus.FAILED.value
        assert envelope.verification_status is None
        assert envelope.data["recovery"]["executed"] is False
        assert _RESOLVED_COMMAND in envelope.data["recovery"]["instruction"]

    @pytest.mark.asyncio
    async def test_a_failed_shell_call_is_not_completed_by_an_approved_recovery(
        self, monkeypatch, approving_brain
    ):
        """The adapter-text call site: the tool returns an ``Error:`` string."""
        monkeypatch.setattr(recovery_module, "RECOVERY_REGISTRY", [_ConfidentStrategy()])

        envelope = await approving_brain.execute_tool_operation(
            "shell_execute",
            {"command": "charlie-notepad.exe report.txt"},
            request="open the report in notepad",
            task_id=None,
            session_id=None,
            turn_id="turn-truth-closure-adapter",
            platform="text",
            execute_override=lambda: _NOT_FOUND_TEXT,
        )

        assert envelope.status != ResultStatus.COMPLETED.value
        assert envelope.status == ResultStatus.FAILED.value
        assert envelope.data["recovery"]["executed"] is False

    @pytest.mark.asyncio
    async def test_a_timeout_is_not_completed_and_is_not_erased_by_the_recovery_text(
        self, monkeypatch, approving_brain
    ):
        """A timeout must stay a timeout even when recovery proposes a retry.

        The old timeout branch assigned the approval explanation to
        ``raw_result`` and then appended that explanation -- not the timeout --
        to ``result_errors``, so the envelope reported neither a timeout nor
        the command that actually failed.
        """
        monkeypatch.setattr(recovery_module, "RECOVERY_REGISTRY", [_TimeoutStrategy()])
        monkeypatch.setattr(core, "_tool_timeout", lambda *_a, **_k: 0.01)

        async def _hang():
            await asyncio.sleep(0.25)
            return "unreachable"

        envelope = await approving_brain.execute_tool_operation(
            "shell_execute",
            {"command": "charlie-notepad.exe report.txt"},
            request="open the report in notepad",
            task_id=None,
            session_id=None,
            turn_id="turn-truth-closure-timeout",
            platform="text",
            execute_override=_hang,
        )

        assert envelope.status == ResultStatus.FAILED.value
        assert envelope.data.get("failure_kind") == "timeout"
        assert envelope.errors, "a timeout must be recorded as an error"
        assert any("timed out" in str(err) for err in envelope.errors), (
            "result_errors must carry the timeout, not the recovery explanation: "
            f"{envelope.errors!r}"
        )
        assert not any("Found in PATH" in str(err) for err in envelope.errors), (
            "an approval explanation is not an error; it must not be recorded as one"
        )

    @pytest.mark.asyncio
    async def test_the_model_facing_text_keeps_the_real_failure_and_adds_the_retry(
        self, monkeypatch, approving_brain
    ):
        """The instruction has to reach the model, which only reads ``envelope.result``."""
        monkeypatch.setattr(recovery_module, "RECOVERY_REGISTRY", [_ConfidentStrategy()])

        envelope = await approving_brain.execute_tool_operation(
            "shell_execute",
            {"command": "charlie-notepad.exe report.txt"},
            request="open the report in notepad",
            task_id=None,
            session_id=None,
            turn_id="turn-truth-closure-model-text",
            platform="text",
            execute_override=lambda: _NOT_FOUND_TEXT,
        )

        model_text = core._result_envelope_to_model_text(envelope)

        assert model_text.startswith("Error:"), (
            "the operation failed; the model must not be handed a success-shaped text"
        )
        assert _RESOLVED_COMMAND in model_text, (
            "the approved resolution must be actionable for the next tool call"
        )

    @pytest.mark.asyncio
    async def test_an_unapproved_recovery_still_reports_nothing(self, monkeypatch, isolated_recovery_cache):
        from charlie.core import ApprovalDecision

        async def _refuse(_brain, **_kwargs):
            return ApprovalDecision.REJECTED

        monkeypatch.setattr(recovery_module, "request_recovery_approval", _refuse)
        monkeypatch.setattr(recovery_module, "RECOVERY_REGISTRY", [_ConfidentStrategy()])

        assert await recover_tool(
            object(),
            "shell_execute",
            {"command": "charlie-notepad.exe report.txt"},
            FileNotFoundError("[WinError 2] The system cannot find the file specified"),
        ) is None


# ---------------------------------------------------------------------------
# 2. A degraded durable read must be observable, not silently empty.
# ---------------------------------------------------------------------------


def _broken_store(tmp_path) -> SessionStore:
    """A real SessionStore whose durable message read can no longer succeed."""
    store = SessionStore(db_path=str(tmp_path / "sessions.db"))
    store.create_session("truth-closure", title="Truth closure", source="text")
    store.conn.execute("DROP TABLE messages")
    return store


class TestDegradedDurableReadsAreObservable:
    def test_a_silently_degraded_read_reports_itself(self, tmp_path):
        store = _broken_store(tmp_path)

        messages = store.get_session_messages("truth-closure")

        assert messages == [], "the caller still receives a usable empty history"
        assert store.last_read_error, (
            "the durable read failed; the degradation must be reported so a lost "
            "read is distinguishable from a session that genuinely has no history"
        )

    def test_a_successful_read_leaves_no_degradation_behind(self, tmp_path):
        store = SessionStore(db_path=str(tmp_path / "sessions.db"))
        store.create_session("truth-closure", title="Truth closure", source="text")
        store.append("user", "hello", session_id="truth-closure")

        assert store.get_session_messages("truth-closure") == [("user", "hello")]
        assert store.last_read_error is None

    def test_a_later_successful_read_clears_an_earlier_degradation(self, tmp_path):
        store = _broken_store(tmp_path)
        assert store.get_session_messages("truth-closure") == []
        assert store.last_read_error

        store.init_db()

        store.get_session_messages("truth-closure")

        assert store.last_read_error is None, (
            "a stale degradation marker would report the store as broken forever"
        )

    @pytest.mark.asyncio
    async def test_brain_records_the_degradation_and_drops_stale_history(
        self, tmp_path, monkeypatch
    ):
        brain = Brain(_config())
        brain.session_store = _broken_store(tmp_path)
        brain.history = [{"role": "user", "content": _FOREIGN_SESSION_MARKER}]

        payloads: List[Dict[str, Any]] = []

        def capture_stream(_method, _url, *, json=None, **_kwargs):
            payloads.append(json)
            return _SingleChunkStream("Understood.")

        monkeypatch.setattr(brain.client, "stream", capture_stream)

        chunks = [
            chunk
            async for chunk in brain.chat_stream(
                "tell me a short joke about otters",
                platform="text",
                skip_pre_search=True,
                session_id="truth-closure",
                task_id="truth-closure",
                turn_id="turn-truth-closure-history",
            )
        ]

        assert chunks == ["Understood."]
        assert brain.session_history_error, (
            "the durable read degraded; the attribute that exists to record that "
            "must not be None on this path"
        )
        assert _FOREIGN_SESSION_MARKER not in json.dumps(
            [msg["content"] for msg in brain.history]
        ), "a lost durable read must not answer from stale history"
        assert _FOREIGN_SESSION_MARKER not in json.dumps(payloads)

    @pytest.mark.asyncio
    async def test_a_recovered_durable_read_clears_the_recorded_degradation(
        self, tmp_path, monkeypatch
    ):
        brain = Brain(_config())
        brain.session_history_error = "OperationalError"
        store = _broken_store(tmp_path)
        brain.session_store = store
        store.init_db()
        store.conn.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY)")

        monkeypatch.setattr(
            brain.client,
            "stream",
            lambda _method, _url, *, json=None, **_kwargs: _SingleChunkStream("Understood."),
        )

        [
            chunk
            async for chunk in brain.chat_stream(
                "tell me a short joke about otters",
                platform="text",
                skip_pre_search=True,
                session_id="truth-closure",
                task_id="truth-closure",
                turn_id="turn-truth-closure-history-recovered",
            )
        ]

        assert brain.session_history_error is None


# ---------------------------------------------------------------------------
# 3. Recorded truth losses must be readable by the health surface.
# ---------------------------------------------------------------------------


class TestRecordedTruthLossesAreReadable:
    def test_a_fresh_brain_reports_no_degradation(self, tmp_path):
        brain = Brain(_config(memory_file=str(tmp_path / "MEMORY.md")))

        assert brain.health_degradations() == {}
        assert brain.is_health_degraded() is False

    def test_an_undelivered_health_transition_is_reported(self):
        brain = Brain(_config())

        def rejecting_sink(_status, _detail=None):
            raise RuntimeError("health registry has no 'llm' subsystem")

        brain._on_llm_health = rejecting_sink
        brain._notify_primary_llm_health(
            brain._allocate_primary_llm_generation(), HealthStatus.DEGRADED, "Unreachable"
        )

        degradations = brain.health_degradations()
        assert degradations.get("llm_health_delivery") == "RuntimeError", (
            "a transition that never reached the sink leaves the published health "
            "surface showing the last delivered state; that loss must be readable"
        )
        assert brain.is_health_degraded() is True

    def test_an_unreadable_durable_context_file_is_reported(self, tmp_path):
        unreadable = tmp_path / "MEMORY.md"
        unreadable.mkdir()

        brain = Brain(_config(memory_file=str(unreadable)))

        degradations = brain.health_degradations()
        assert str(unreadable) in degradations.get("context_tier", {}), (
            "a context tier that silently lost MEMORY must be distinguishable "
            "from one that genuinely holds no memory"
        )

    def test_a_restored_durable_context_file_clears_the_report(self, tmp_path):
        unreadable = tmp_path / "MEMORY.md"
        unreadable.mkdir()
        readable = tmp_path / "MEMORY-restored.md"
        readable.write_text("Prefers dark mode.", encoding="utf-8")

        brain = Brain(_config(memory_file=str(unreadable)))
        assert brain.is_health_degraded() is True

        brain.config.memory_file = str(readable)
        brain.reload_context()

        assert "context_tier" not in brain.health_degradations()

    def test_the_snapshot_is_a_copy_and_cannot_be_corrupted(self, tmp_path):
        brain = Brain(_config(memory_file=str(tmp_path / "MEMORY.md")))
        brain.context_tier_read_errors[str(tmp_path / "MEMORY.md")] = "PermissionError"

        snapshot = brain.health_degradations()
        snapshot["context_tier"].clear()

        assert brain.context_tier_read_errors, "a consumer must not be able to clear Brain state"


# ---------------------------------------------------------------------------
# 4. A lost correction write must be representable.
# ---------------------------------------------------------------------------


class TestLostCorrectionWritesAreRepresentable:
    def test_a_failed_write_is_distinguishable_from_a_dedupe_no_op(self, tmp_path):
        unwritable = tmp_path / "OPINIONS.md"
        unwritable.mkdir()  # a directory cannot be opened for append

        seen: List[str | None] = []
        result = _apply_correction_to_memory(
            "no, I meant blue",
            "The sky is green",
            opinions_path=str(unwritable),
            on_persistence_error=seen.append,
        )

        assert result is None, "the entry could not be written"
        assert seen and seen[0] in _UNWRITABLE_NAMES, (
            "None alone is the dedupe no-op's value; the lost write must be "
            f"reported separately or it is invisible: {seen}"
        )

    def test_a_successful_write_reports_no_degradation(self, tmp_path):
        target = tmp_path / "OPINIONS.md"
        seen: List[str | None] = []

        result = _apply_correction_to_memory(
            "no, I meant blue",
            "The sky is green",
            opinions_path=str(target),
            on_persistence_error=seen.append,
        )

        assert result is not None
        assert seen == [None]

    def test_a_dedupe_no_op_reports_no_degradation(self, tmp_path):
        target = tmp_path / "OPINIONS.md"
        target.write_text(
            "Correction by user: no, I meant blue. Previous answer: 'The sky is green'.\n",
            encoding="utf-8",
        )
        seen: List[str | None] = []

        result = _apply_correction_to_memory(
            "no, I meant blue",
            "The sky is green",
            opinions_path=str(target),
            on_persistence_error=seen.append,
        )

        assert result is None
        assert seen == [None]

    def test_a_non_correction_reports_no_degradation(self, tmp_path):
        target = tmp_path / "OPINIONS.md"
        seen: List[str | None] = []

        result = _apply_correction_to_memory(
            "what's the weather",
            "I don't know",
            opinions_path=str(target),
            on_persistence_error=seen.append,
        )

        assert result is None
        assert seen == [None]

    @pytest.mark.asyncio
    async def test_the_brain_records_a_lost_correction_write(self, tmp_path, monkeypatch):
        """End to end: the fire-and-forget executor future must leave state behind."""
        brain = Brain(_config())
        blocked = tmp_path / "blocked-opinions.md"
        blocked.mkdir()  # a directory cannot be opened for append
        brain.config.opinions_file = str(blocked)

        class SeededStore:
            def get_session_messages(self, _session_id, limit):
                return [("user", "what is the sky?"), ("assistant", "The sky is green")]

            def append_tool(self, **_kwargs):
                return None

        brain.session_store = SeededStore()
        monkeypatch.setattr(
            brain.client,
            "stream",
            lambda _method, _url, *, json=None, **_kwargs: _SingleChunkStream("Understood."),
        )

        [
            chunk
            async for chunk in brain.chat_stream(
                "no, I meant blue",
                platform="text",
                skip_pre_search=True,
                session_id="truth-closure-correction",
                task_id="truth-closure-correction",
                turn_id="turn-truth-closure-correction",
            )
        ]

        for _ in range(100):
            if brain.correction_persistence_error:
                break
            await asyncio.sleep(0.02)

        assert brain.correction_persistence_error in _UNWRITABLE_NAMES, (
            "the correction write is fire-and-forget with no envelope to mark, so "
            f"Brain state is the only place the loss can be represented: "
            f"{brain.correction_persistence_error}"
        )
        assert brain.health_degradations().get("correction_persistence") in _UNWRITABLE_NAMES


if __name__ == "__main__":  # pragma: no cover
    import sys

    sys.exit(pytest.main([__file__, "-q"]))

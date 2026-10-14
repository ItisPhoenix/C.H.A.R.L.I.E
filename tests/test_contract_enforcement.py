"""TEST/MOCK regressions for declared capability contracts."""

import pytest

from charlie.core import Brain, _normalize_tool_result
from charlie.config import Config
from charlie.capabilities import capability_index
from charlie.research.facts import extract_facts_from_document
from charlie.research.models import SourceDocument
from charlie.turn_contracts import ResultStatus


@pytest.mark.parametrize("name", ["file_write", "automation_create", "desktop_open_app"])
def test_mutation_without_postcondition_is_unverified(name):
    result = _normalize_tool_result(
        name, "Action accepted.", request="act", turn_id=None, task_id=None, session_id=None
    )
    assert result.status == ResultStatus.UNVERIFIED
    assert result.verification_status == "executed_unverified"


@pytest.mark.parametrize("name", ["calendar_delete", "graph_consolidate"])
def test_deletion_risk_requires_approval(name):
    assert capability_index.get_operation(name).risk_class == "destructive"


@pytest.mark.asyncio
async def test_override_holds_declared_lease():
    from charlie.resource_locks import current_owner

    brain = Brain(Config(llm_url="http://localhost:11434", llm_key="no-key", llm_model="dummy"))
    observed = []

    async def action():
        observed.append(current_owner("browser"))
        return "Action accepted."

    try:
        await brain._execute_operation_primitive(
            "browser_task", {"task": "read page"}, request="read page", task_id="test-owner",
            turn_id=None, session_id=None, execute_override=action, apply_execution_controls=False,
        )
        assert observed == ["test-owner"]
        assert current_owner("browser") is None
    finally:
        await brain.close()


def test_product_fields_have_citable_extraction():
    doc = SourceDocument(
        source_id="S1", url="https://vendor.example/item", title="Example Device",
        source_class="official", content="Processor: Eight-core chip\nCamera: 48 MP\nDisplay: 6 inch OLED\nSpecifications: IP68 waterproof",
    )
    facts = extract_facts_from_document(doc)
    assert {fact.aspect for fact in facts} >= {"processor", "camera", "display", "specifications"}
    assert all(fact.value in fact.quote and fact.quote in doc.content for fact in facts)


def test_zero_exit_code_does_not_verify_shell_postcondition():
    from charlie.tools import ToolExecutionResult

    result = _normalize_tool_result(
        "shell_execute", ToolExecutionResult("accepted", {"exit_code": 0}),
        request="write file", turn_id=None, task_id=None, session_id=None,
    )
    assert result.status == ResultStatus.UNVERIFIED


@pytest.mark.parametrize("saved_status,verified", [("active", True), ("cancelled", False)])
def test_automation_write_return_does_not_prove_saved_state(monkeypatch, saved_status, verified):
    from types import SimpleNamespace
    from charlie import calendar_runtime
    from charlie.tools import _automation_tool_result

    saved = {"id": "schedule-test", "kind": "reminder", "text": "Tea", "status": saved_status}
    monkeypatch.setattr(calendar_runtime, "calendar_runtime_required", lambda: SimpleNamespace(
        execute_sync=lambda method, identity: saved,
    ))
    result = _automation_tool_result("create", lambda: {**saved, "status": "active"})
    assert result.structured_data["verified"] is verified
    assert result.structured_data["readback"] == saved

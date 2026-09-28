"""Tests for grounded runtime self-knowledge."""

import tempfile
from pathlib import Path

import pytest

from charlie.capabilities import CapabilityDescriptor, CapabilityIndex, CapabilityOperation
from charlie.code_index import CodeIndex
from charlie.config import Config
from charlie.runtime_introspector import RuntimeIntrospector
from charlie.self_knowledge import SelfKnowledgeEvidence, SelfKnowledgeService


@pytest.fixture
def mock_self_knowledge_env():
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir).resolve()
        py_dir = repo_path / "charlie" / "desktop"
        py_dir.mkdir(parents=True, exist_ok=True)
        (py_dir / "manager.py").write_text(
            '"""Desktop effectors and click automation."""\n\n'
            'class DesktopManager:\n'
            '    """Handles mouse click and keyboard input."""\n'
            '    def click_at(self, x: int, y: int) -> bool:\n'
            '        return True\n',
            encoding="utf-8",
        )
        code_index = CodeIndex(repo_path)
        code_index.refresh()

        cfg = Config()
        cfg.llm_provider = "openai"
        cfg.llm_model = "gpt-4o"
        cfg.llm_api_key = "sk-super-secret-key-12345"

        cap_idx = CapabilityIndex()
        cap_idx.register_capability(
            CapabilityDescriptor(
                id="desktop",
                name="Desktop Control",
                description="Desktop automation",
                owner="charlie.desktop",
                operations={
                    "click_at": CapabilityOperation(
                        id="click_at",
                        name="click_at",
                        description="Click coordinates",
                        parameters_schema={"type": "object"},
                        risk_class="reversible",
                    )
                },
                availability_check=lambda: True,
                provenance="builtin",
            )
        )
        cap_idx.register_capability(
            CapabilityDescriptor(
                id="browser",
                name="Browser Automation",
                description="Controlled browser automation",
                owner="charlie.browser",
                operations={
                    "navigate": CapabilityOperation(
                        id="navigate",
                        name="navigate",
                        description="Navigate URL",
                        parameters_schema={"type": "object"},
                        risk_class="safe",
                    )
                },
                availability_check=lambda: False,
                provenance="builtin",
            )
        )

        introspector = RuntimeIntrospector(config=cfg, capability_index=cap_idx)
        yield SelfKnowledgeService(
            runtime_introspector=introspector,
            code_index=code_index,
            capability_index=cap_idx,
            config=cfg,
        ), cfg


def test_classify_self_question(mock_self_knowledge_env):
    service, _ = mock_self_knowledge_env
    assert service.is_self_question("What model are you using?") is True
    assert service.is_self_question("Can you control my PC?") is True
    assert service.is_self_question("Which file implements desktop clicking?") is True
    assert service.is_self_question("Are you healthy?") is True
    assert service.is_self_question("What is the capital of France?") is False


def test_answer_model_question_is_grounded_and_secret_free(mock_self_knowledge_env):
    service, _ = mock_self_knowledge_env
    answer = service.answer_self_question("What model are you currently configured to use?")
    assert answer["is_self_question"] is True
    assert "gpt-4o" in answer["answer"]
    assert "sk-super-secret" not in answer["answer"]
    assert "runtime.model" in answer["evidence_sources"]


def test_answer_capability_honesty(mock_self_knowledge_env):
    service, _ = mock_self_knowledge_env
    desktop = service.answer_self_question("Can you control my desktop right now?")
    browser = service.answer_self_question("Can you browse the web right now?")
    assert "available" in desktop["answer"].lower() or "can" in desktop["answer"].lower()
    assert "unavailable" in browser["answer"].lower() or "not available" in browser["answer"].lower()


def test_answer_code_location_question(mock_self_knowledge_env):
    service, _ = mock_self_knowledge_env
    answer = service.answer_self_question("Which module implements desktop clicking?")
    assert "manager.py" in answer["answer"]
    assert "DesktopManager" in answer["answer"]
    assert any("code_index" in source for source in answer["evidence_sources"])


def test_build_grounded_evidence_is_compact_and_secret_free(mock_self_knowledge_env):
    service, _ = mock_self_knowledge_env
    evidence = service.get_evidence_for_query("How does desktop control work?")
    assert isinstance(evidence, SelfKnowledgeEvidence)
    assert evidence.relevant_symbols
    assert "sk-super-secret" not in str(evidence.to_dict())


def test_answer_tools_memory_and_mcp_questions(mock_self_knowledge_env):
    service, _ = mock_self_knowledge_env
    tools = service.answer_self_question("What tools or capabilities do you have?")
    mcp = service.answer_self_question("Is MCP running?")
    memory = service.answer_self_question("What memory systems do you use?")
    assert "desktop" in tools["answer"]
    assert "MCP" in mcp["answer"]
    assert "Memory system" in memory["answer"]


def test_live_self_knowledge_code_sanity():
    answer = SelfKnowledgeService().answer_self_question("Where is CapabilityIndex implemented?")
    assert answer["is_self_question"] is True
    assert "capabilities.py" in answer["answer"] or "CapabilityIndex" in answer["answer"]


@pytest.mark.parametrize("query", ["Is browser available?", "Is desktop available?", "Is MCP connected?"])
def test_subsystem_status_questions_are_grounded(query):
    service = SelfKnowledgeService()
    result = service.answer_self_question(query)
    assert service.is_self_question(query) is True
    assert result["is_self_question"] is True
    assert "runtime.subsystems" in result["evidence_sources"] or "runtime.mcp" in result["evidence_sources"]

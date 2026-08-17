"""Tests for SelfKnowledge Service."""

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
    """Create isolated environment with CodeIndex and RuntimeIntrospector."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir).resolve()

        # Create sample repo source files
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
                description="Desktop UI automation",
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
                description="Playwright headless browser",
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

        introspector = RuntimeIntrospector(
            config=cfg,
            capability_index=cap_idx,
        )

        service = SelfKnowledgeService(
            runtime_introspector=introspector,
            code_index=code_index,
            capability_index=cap_idx,
            config=cfg,
        )

        yield service, cfg


def test_classify_self_question(mock_self_knowledge_env):
    """Verify self-question classification accurately detects Charlie-internal queries."""
    service, _ = mock_self_knowledge_env

    assert service.is_self_question("What model are you using?") is True
    assert service.is_self_question("Can you control my PC?") is True
    assert service.is_self_question("Which file implements desktop clicking?") is True
    assert service.is_self_question("Are you healthy?") is True
    assert service.is_self_question("What tools do you have?") is True
    assert service.is_self_question("Is MCP running?") is True
    assert service.is_self_question("What memory systems do you use?") is True

    # General non-self questions
    assert service.is_self_question("What is the capital of France?") is False
    assert service.is_self_question("Write a python script to sort numbers.") is False
    assert service.is_self_question("What is the weather in Tokyo?") is False


def test_answer_model_question_grounded(mock_self_knowledge_env):
    """Verify model queries return actual runtime model without leaking API keys."""
    service, cfg = mock_self_knowledge_env

    ans = service.answer_self_question("What model are you currently configured to use?")
    assert ans["is_self_question"] is True
    assert "gpt-4o" in ans["answer"]
    assert "openai" in ans["answer"].lower()
    assert "sk-super-secret" not in ans["answer"]
    assert "runtime.model" in ans["evidence_sources"]


def test_answer_capability_honesty(mock_self_knowledge_env):
    """Verify capabilities reflect live availability honestly (available vs unavailable)."""
    service, _ = mock_self_knowledge_env

    # Desktop is available
    dt_ans = service.answer_self_question("Can you control my desktop right now?")
    assert "available" in dt_ans["answer"].lower() or "can" in dt_ans["answer"].lower()

    # Browser is registered but unavailable
    br_ans = service.answer_self_question("Can you browse the web right now?")
    assert "unavailable" in br_ans["answer"].lower() or "not available" in br_ans["answer"].lower()


def test_answer_code_location_question(mock_self_knowledge_env):
    """Verify code location questions retrieve accurate file and symbol from CodeIndex."""
    service, _ = mock_self_knowledge_env

    ans = service.answer_self_question("Which module or file implements desktop clicking?")
    assert "manager.py" in ans["answer"]
    assert "DesktopManager" in ans["answer"]
    assert any("code_index" in s for s in ans["evidence_sources"])


def test_build_grounded_evidence(mock_self_knowledge_env):
    """Verify evidence bundle is compact, relevant, and secret-free."""
    service, _ = mock_self_knowledge_env

    evidence = service.get_evidence_for_query("How does desktop control work?")
    assert isinstance(evidence, SelfKnowledgeEvidence)
    assert len(evidence.relevant_symbols) >= 1
    assert "DesktopManager" in [s["name"] for s in evidence.relevant_symbols]
    assert "sk-super-secret" not in str(evidence.to_dict())


def test_answer_tools_and_mcp_questions(mock_self_knowledge_env):
    """Verify answers for tools, MCP, and memory questions."""
    service, _ = mock_self_knowledge_env

    tools_ans = service.answer_self_question("What tools or capabilities do you have?")
    assert "desktop" in tools_ans["answer"]
    assert "browser" in tools_ans["answer"]

    mcp_ans = service.answer_self_question("Is MCP running?")
    assert "MCP" in mcp_ans["answer"]

    mem_ans = service.answer_self_question("What memory systems do you use?")
    assert "Memory system" in mem_ans["answer"]


def test_live_self_knowledge_code_sanity():
    """Verify SelfKnowledge can answer questions against the real Charlie codebase."""
    service = SelfKnowledgeService()
    ans = service.answer_self_question("Where is CapabilityIndex implemented?")
    assert ans["is_self_question"] is True
    assert "capabilities.py" in ans["answer"] or "CapabilityIndex" in ans["answer"]


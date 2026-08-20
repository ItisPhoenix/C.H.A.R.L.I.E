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


# -----------------------------------------------------------------------------
# Presentation & HUD Self-Knowledge Tests
# -----------------------------------------------------------------------------


def test_presentation_classification():
    """Verify self-question classification detects presentation queries."""
    service = SelfKnowledgeService()

    assert service.is_self_question("What widgets do you have?") is True
    assert service.is_self_question("What workspaces do you have?") is True
    assert service.is_self_question("What can your HUD show?") is True
    assert service.is_self_question("Do you have a map workspace?") is True
    assert service.is_self_question("Do you have a spatial workspace?") is True
    assert service.is_self_question("Do you have a camera workspace?") is True
    assert service.is_self_question("Do you have a chat workspace?") is True
    assert service.is_self_question("Is settings a workspace?") is True
    assert service.is_self_question("What visual primitives can you render?") is True
    assert service.is_self_question("Can you render charts?") is True
    assert service.is_self_question("Where does your ring go when a workspace opens?") is True
    assert service.is_self_question("Is your pet part of your HUD workspace system?") is True


def test_answer_widgets_inventory():
    """Verify widget inventory query returns canonical widgets with interaction behavior."""
    service = SelfKnowledgeService()
    ans = service.answer_self_question("What widgets do you have?")

    assert ans["is_self_question"] is True
    assert "runtime.presentation" in ans["evidence_sources"]
    assert "system_metric" in ans["answer"]
    assert "composed_surface" in ans["answer"]
    assert "media_control" in ans["answer"]
    assert "file_viewer" in ans["answer"]
    assert "dragging" in ans["answer"].lower() or "pinning" in ans["answer"].lower()


def test_answer_workspaces_inventory():
    """Verify workspace inventory query returns canonical workspaces."""
    service = SelfKnowledgeService()
    ans = service.answer_self_question("What workspaces do you have?")

    assert ans["is_self_question"] is True
    assert "runtime.presentation" in ans["evidence_sources"]
    assert "research" in ans["answer"]
    assert "briefing" in ans["answer"]
    assert "system" in ans["answer"]
    assert "tasks" in ans["answer"]
    assert "map" in ans["answer"]
    assert "vision" in ans["answer"]
    assert "document" in ans["answer"]
    assert "terminal" in ans["answer"]
    assert "conversation" in ans["answer"]
    assert "composed_surface" in ans["answer"]
    assert "settings" not in ans["answer"]  # settings is an overlay, not a workspace


def test_answer_hud_overview():
    """Verify general HUD overview summarizes workspaces, widgets, overlays, SurfaceComposer, and core."""
    service = SelfKnowledgeService()
    ans = service.answer_self_question("What can your HUD show?")

    assert ans["is_self_question"] is True
    assert "workspaces" in ans["answer"].lower()
    assert "widgets" in ans["answer"].lower()
    assert "surfacecomposer" in ans["answer"].lower()
    assert "center" in ans["answer"].lower()
    assert "bottom-right" in ans["answer"].lower() or "dock" in ans["answer"].lower()


def test_answer_specific_workspace_and_alias_resolution():
    """Verify specific workspace lookups and alias canonicalization."""
    service = SelfKnowledgeService()

    # 1. Direct canonical workspace
    map_ans = service.answer_self_question("Do you have a map workspace?")
    assert "map" in map_ans["answer"]
    assert "implemented" in map_ans["answer"]
    assert "spatial" in map_ans["answer"].lower()

    # 2. Alias: spatial -> map
    spatial_ans = service.answer_self_question("Do you have a spatial workspace?")
    assert "map" in spatial_ans["answer"]
    assert "spatial" in spatial_ans["answer"]
    assert "implemented" in spatial_ans["answer"]

    # 3. Alias: camera -> vision
    camera_ans = service.answer_self_question("Do you have a camera workspace?")
    assert "vision" in camera_ans["answer"]
    assert "camera" in camera_ans["answer"]
    assert "implemented" in camera_ans["answer"]

    # 4. Alias: chat -> conversation
    chat_ans = service.answer_self_question("Do you have a chat workspace?")
    assert "conversation" in chat_ans["answer"]
    assert "chat" in chat_ans["answer"]
    assert "implemented" in chat_ans["answer"]


def test_answer_overlay_settings_distinction():
    """Verify Settings is classified as an overlay modal, not a workspace."""
    service = SelfKnowledgeService()

    ans = service.answer_self_question("Is settings a workspace?")
    assert "overlay" in ans["answer"].lower() or "modal" in ans["answer"].lower()
    assert "not a workspace" in ans["answer"].lower()


def test_answer_surface_primitives_and_charts():
    """Verify queries about SurfaceComposer primitives and specific primitives like charts."""
    service = SelfKnowledgeService()

    # All primitives
    all_prims_ans = service.answer_self_question("What visual primitives can you render?")
    assert "surfacecomposer" in all_prims_ans["answer"].lower()
    assert "heading" in all_prims_ans["answer"]
    assert "chart" in all_prims_ans["answer"]
    assert "timeline" in all_prims_ans["answer"]
    assert "metric" in all_prims_ans["answer"]

    # Specific primitive: charts
    chart_ans = service.answer_self_question("Can you render charts?")
    assert "chart" in chart_ans["answer"].lower()
    assert "surfacecomposer" in chart_ans["answer"].lower()


def test_answer_core_docking_behavior():
    """Verify query about core positioning and docking rules."""
    service = SelfKnowledgeService()
    ans = service.answer_self_question("What happens to your core when a workspace opens?")

    assert "center" in ans["answer"].lower()
    assert "dock_bottom_right" in ans["answer"] or "bottom-right" in ans["answer"]
    assert "core-only" in ans["answer"].lower() or "minimal" in ans["answer"].lower()


def test_answer_pet_separation():
    """Verify Pet is recognized as a separate native companion surface outside the HUD."""
    service = SelfKnowledgeService()
    ans = service.answer_self_question("Is your pet part of your HUD workspace system?")

    assert "pet" in ans["answer"].lower()
    assert "separate companion" in ans["answer"].lower() or "outside" in ans["answer"].lower()


def test_truthful_language_no_fabricated_activity():
    """Verify implemented status is not reported as active running state."""
    service = SelfKnowledgeService()
    ans = service.answer_self_question("Do you have a briefing workspace?")

    assert "implemented" in ans["answer"].lower()
    assert "currently active" not in ans["answer"].lower()
    assert "currently open" not in ans["answer"].lower()


def test_hud_activity_unknown_is_explicitly_unknown():
    """Verify missing live HUD-client evidence is not reported as active."""
    service = SelfKnowledgeService()
    ans = service.answer_self_question("What can your HUD show?")

    assert "can't currently verify whether a hud client is connected" in ans["answer"].lower()
    assert "currently active" not in ans["answer"].lower()


def test_widget_behavior_is_derived_from_registry_supports():
    """Verify widget interaction claims follow registry definitions, not defaults."""
    from copy import deepcopy

    from charlie.presentation_registry import PresentationRegistry, get_presentation_registry

    contract = deepcopy(get_presentation_registry().to_dict())
    contract["widgets"] = {
        "custom_widget": {
            "implemented": True,
            "supports": {"drag": False, "resize": False, "pin": False, "auto_dismiss": True},
            "description": "Custom registry widget",
        }
    }
    registry = PresentationRegistry.from_dict(contract)
    service = SelfKnowledgeService(
        runtime_introspector=RuntimeIntrospector(presentation_registry=registry),
        presentation_registry=registry,
    )

    answer = service.answer_self_question("What widgets do you have?")["answer"].lower()
    assert "custom_widget" in answer
    assert "auto-dismiss" in answer
    assert "dragging" not in answer
    assert "pinning" not in answer


def test_malformed_presentation_error_isolation():
    """Verify malformed presentation registry results in clear error messaging without crashing."""

    class MockBrokenIntrospector:
        def get_presentation_info(self):
            return {
                "status": "error",
                "error_type": "PresentationContractError",
                "message": "Corrupted schema",
            }

        def get_model_info(self):
            return {"provider": "mock", "model": "test-model", "api_key_configured": True}

        def get_capabilities_info(self):
            return {"by_id": {}, "total": 0, "available_count": 0}

    broken_service = SelfKnowledgeService(runtime_introspector=MockBrokenIntrospector())
    ans = broken_service.answer_self_question("What workspaces do you have?")

    assert "couldn't inspect my presentation registry" in ans["answer"].lower() or "error" in ans["answer"].lower()
    assert "i have no workspaces" not in ans["answer"].lower()


def test_dynamic_extensibility_hypothetical_workspace():
    """Verify that adding a hypothetical workspace to the registry is recognized without modifying SelfKnowledge."""
    from charlie.presentation_registry import PresentationRegistry

    contract_with_custom_ws = {
        "contract_version": 1,
        "surface_schema_version": 1,
        "presentation_kinds": ["workspace"],
        "core": {"states": ["idle"], "positions": ["center"], "rules": {}},
        "widgets": {},
        "workspaces": {
            "quantum_telemetry": {
                "aliases": ["quantum_spatial"],
                "implemented": True,
                "renderer": "QuantumTelemetryWorkspace",
                "renderer_module": "frontend/src/workspaces/Quantum.tsx",
                "spatial": True,
                "core_position": "dock_bottom_right",
                "dismiss_policy": "persistent",
                "description": "Quantum state visualization",
            }
        },
        "overlays": {},
        "surface_primitives": ["heading"],
        "layout_types": ["stack"],
        "dismiss_policies": ["persistent"],
        "preferred_zones": ["center"],
        "anchors": ["screen"],
        "actions": ["open_workspace"],
    }

    custom_registry = PresentationRegistry.from_dict(contract_with_custom_ws)
    introspector = RuntimeIntrospector(presentation_registry=custom_registry)
    service = SelfKnowledgeService(runtime_introspector=introspector, presentation_registry=custom_registry)

    # 1. Look up custom canonical workspace
    ans = service.answer_self_question("Do you have a quantum_telemetry workspace?")
    assert "quantum_telemetry" in ans["answer"]
    assert "Quantum state visualization" in ans["answer"]
    assert "implemented" in ans["answer"]

    # 2. Look up custom alias
    alias_ans = service.answer_self_question("Do you have a quantum_spatial workspace?")
    assert "quantum_telemetry" in alias_ans["answer"]
    assert "quantum_spatial" in alias_ans["answer"]


@pytest.mark.parametrize(
    "query",
    [
        "Is browser available?",
        "Is desktop available?",
        "Is terminal available?",
        "Is vision available?",
        "Is pet running?",
        "Is MCP connected?",
    ],
)
def test_subsystem_status_questions_remain_self_questions(query):
    """Subsystem status categories must not regress into presentation lookup."""
    service = SelfKnowledgeService()

    result = service.answer_self_question(query)

    assert service.is_self_question(query) is True
    assert result["is_self_question"] is True
    assert "runtime.presentation" not in result["evidence_sources"]


def test_subsystem_status_answers_are_truthful_and_grounded():
    """Status answers use runtime/config evidence and never fabricate Pet activity."""
    service = SelfKnowledgeService()

    browser = service.answer_self_question("Is browser available?")
    assert "browser" in browser["answer"].lower()
    assert "available" in browser["answer"].lower() or "unavailable" in browser["answer"].lower()
    assert "runtime.subsystems" in browser["evidence_sources"]

    pet = service.answer_self_question("Is pet running?")
    assert "implemented" in pet["answer"].lower()
    assert "configured" in pet["answer"].lower()
    assert "unknown" in pet["answer"].lower()
    assert "runtime.config" in pet["evidence_sources"]
    assert "currently running" not in pet["answer"].lower()

    mcp = service.answer_self_question("Is MCP connected?")
    assert "mcp subsystem" in mcp["answer"].lower()
    assert "runtime.mcp" in mcp["evidence_sources"]

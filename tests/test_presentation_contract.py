"""Tests for shared/presentation_contract.json, codegen, and charlie/presentation_registry.py.

Verifies:
1. Canonical contract schema and independence of contract_version and surface_schema_version
2. Codegen consistency: python tools/codegen/generate_presentation_contract.py --check
3. Zero drift between contract, generated types, and charlie.presentation
   (PresentationKind, DismissPolicy, PreferredZone, AnchorTarget)
4. Zero drift between contract, generated types, and charlie.surface_spec (PrimitiveType, LayoutType, SCHEMA_VERSION)
5. Workspace, Widget, and Overlay registry resolution, aliases, uniqueness, and collision avoidance
6. Renderer metadata paths exist on disk for all implemented surfaces
7. Settings is categorized as an overlay/modal, not a workspace
8. Robust error handling for malformed contracts
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from charlie.presentation import (
    AnchorTarget,
    DismissPolicy,
    PreferredZone,
    PresentationKind,
)
from charlie.presentation_contract_generated import (
    CONTRACT_VERSION,
    SURFACE_SCHEMA_VERSION,
)
from charlie.presentation_contract_generated import (
    AnchorTarget as GenAnchorTarget,
)
from charlie.presentation_contract_generated import (
    DismissPolicy as GenDismissPolicy,
)
from charlie.presentation_contract_generated import (
    LayoutType as GenLayoutType,
)
from charlie.presentation_contract_generated import (
    PreferredZone as GenPreferredZone,
)
from charlie.presentation_contract_generated import (
    PresentationKind as GenPresentationKind,
)
from charlie.presentation_contract_generated import (
    PrimitiveType as GenPrimitiveType,
)
from charlie.presentation_registry import (
    OverlayDescriptor,
    PresentationContractError,
    PresentationRegistry,
    WidgetDescriptor,
    WorkspaceDescriptor,
    get_presentation_registry,
)
from charlie.surface_spec import SCHEMA_VERSION, LayoutType, PrimitiveType

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_shared_presentation_contract_loads_valid():
    contract_path = REPO_ROOT / "shared" / "presentation_contract.json"
    assert contract_path.exists(), f"Contract not found at {contract_path}"
    with open(contract_path, encoding="utf-8") as f:
        data = json.load(f)

    assert data.get("contract_version") == 1
    assert data.get("surface_schema_version") == 1
    assert isinstance(data.get("presentation_kinds"), list)
    assert isinstance(data.get("core"), dict)
    assert isinstance(data.get("widgets"), dict)
    assert isinstance(data.get("workspaces"), dict)
    assert isinstance(data.get("overlays"), dict)
    assert isinstance(data.get("surface_primitives"), list)
    assert isinstance(data.get("layout_types"), list)
    assert isinstance(data.get("dismiss_policies"), list)
    assert isinstance(data.get("preferred_zones"), list)
    assert isinstance(data.get("anchors"), list)
    assert isinstance(data.get("actions"), list)
    assert isinstance(data.get("semantic_targets"), dict)


def test_every_semantic_target_resolves_to_typed_canonical_surface():
    registry = get_presentation_registry()
    for role in registry.list_semantic_targets():
        resolution = registry.resolve_semantic_target(role)
        assert resolution.resolved
        assert resolution.descriptor.implemented is True


def test_semantic_target_contract_drift_fails_validation():
    contract = json.loads(json.dumps(get_presentation_registry().to_dict()))
    contract["semantic_targets"]["research_result"]["surface"] = "removed_research"
    with pytest.raises(PresentationContractError, match="research_result"):
        PresentationRegistry.from_dict(contract)


def test_codegen_check_clean():
    """Verify that generated code is current and matches shared contract."""
    res = subprocess.run(
        [sys.executable, "tools/codegen/generate_presentation_contract.py", "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"Codegen check failed:\n{res.stdout}\n{res.stderr}"


def test_version_independence():
    registry = get_presentation_registry()
    assert registry.contract_version == CONTRACT_VERSION
    assert registry.surface_schema_version == SURFACE_SCHEMA_VERSION
    assert SCHEMA_VERSION == SURFACE_SCHEMA_VERSION


def test_drift_presentation_kinds():
    registry = get_presentation_registry()
    contract_kinds = set(registry.list_presentation_kinds())
    code_kinds = {k.value for k in PresentationKind}
    gen_kinds = {k.value for k in GenPresentationKind}

    assert code_kinds == contract_kinds
    assert gen_kinds == contract_kinds


def test_drift_dismiss_policies():
    registry = get_presentation_registry()
    contract_policies = set(registry.list_dismiss_policies())
    code_policies = {p.value for p in DismissPolicy}
    gen_policies = {p.value for p in GenDismissPolicy}

    assert code_policies == contract_policies
    assert gen_policies == contract_policies


def test_drift_preferred_zones():
    registry = get_presentation_registry()
    contract_zones = set(registry.list_preferred_zones())
    code_zones = {z.value for z in PreferredZone}
    gen_zones = {z.value for z in GenPreferredZone}

    assert code_zones == contract_zones
    assert gen_zones == contract_zones


def test_drift_anchors():
    registry = get_presentation_registry()
    contract_anchors = set(registry.list_anchors())
    code_anchors = {a.value for a in AnchorTarget}
    gen_anchors = {a.value for a in GenAnchorTarget}

    assert code_anchors == contract_anchors
    assert gen_anchors == contract_anchors


def test_drift_surface_primitives():
    registry = get_presentation_registry()
    contract_primitives = set(registry.list_surface_primitives())
    code_primitives = {p.value for p in PrimitiveType}
    gen_primitives = {p.value for p in GenPrimitiveType}

    assert code_primitives == contract_primitives
    assert gen_primitives == contract_primitives


def test_drift_layout_types():
    registry = get_presentation_registry()
    contract_layouts = set(registry.list_layout_types())
    code_layouts = {layout.value for layout in LayoutType}
    gen_layouts = {layout.value for layout in GenLayoutType}

    assert code_layouts == contract_layouts
    assert gen_layouts == contract_layouts


def test_registry_workspace_resolution_and_aliases():
    registry = get_presentation_registry()

    canonical_workspaces = [
        "research",
        "briefing",
        "system",
        "tasks",
        "map",
        "vision",
        "document",
        "terminal",
        "conversation",
        "composed_surface",
    ]

    for ws_type in canonical_workspaces:
        assert registry.has_workspace(ws_type)
        assert registry.resolve_workspace_type(ws_type) == ws_type
        desc = registry.get_workspace(ws_type)
        assert isinstance(desc, WorkspaceDescriptor)
        assert desc.name == ws_type
        assert desc.implemented is True
        assert desc.renderer != ""

    # Settings is NOT a workspace
    assert not registry.has_workspace("settings")
    assert registry.resolve_workspace_type("settings") is None

    # Aliases
    assert registry.resolve_workspace_type("telemetry") == "system"
    assert registry.resolve_workspace_type("task") == "tasks"
    assert registry.resolve_workspace_type("plans") == "tasks"
    assert registry.resolve_workspace_type("spatial") == "map"
    assert registry.resolve_workspace_type("camera") == "vision"
    assert registry.resolve_workspace_type("report") == "document"
    assert registry.resolve_workspace_type("file") == "document"
    assert registry.resolve_workspace_type("chat") == "conversation"


def test_registry_widget_resolution_and_aliases():
    registry = get_presentation_registry()

    canonical_widgets = ["system_metric", "composed_surface", "media_control", "file_viewer"]

    for w_type in canonical_widgets:
        assert registry.has_widget(w_type)
        assert registry.resolve_widget_type(w_type) == w_type
        desc = registry.get_widget(w_type)
        assert isinstance(desc, WidgetDescriptor)
        assert desc.name == w_type
        assert desc.implemented is True
        assert desc.default_zone != ""
        assert desc.supports.get("drag") is True

    # Aliases
    assert registry.resolve_widget_type("system") == "system_metric"
    assert registry.resolve_widget_type("media") == "media_control"
    assert registry.resolve_widget_type("file") == "file_viewer"


def test_registry_overlays():
    registry = get_presentation_registry()
    assert "settings" in registry.list_overlays()
    assert registry.has_overlay("settings")
    assert registry.resolve_overlay_type("settings") == "settings"

    desc = registry.get_overlay("settings")
    assert isinstance(desc, OverlayDescriptor)
    assert desc.name == "settings"
    assert desc.implemented is True
    assert desc.renderer == "SettingsModal"
    assert desc.dismiss_policy == "manual"


def test_implemented_renderer_modules_exist_on_disk():
    """Verify that every surface marked implemented=True references a real existing file."""
    registry = get_presentation_registry()

    # Workspaces
    for ws_name in registry.list_workspaces():
        desc = registry.get_workspace(ws_name)
        assert desc is not None
        if desc.implemented and desc.renderer_module:
            target = REPO_ROOT / desc.renderer_module
            assert target.exists(), f"Workspace {ws_name} renderer module not found: {target}"

    # Widgets
    for w_name in registry.list_widgets():
        desc = registry.get_widget(w_name)
        assert desc is not None
        if desc.implemented and desc.renderer_module:
            target = REPO_ROOT / desc.renderer_module
            assert target.exists(), f"Widget {w_name} renderer module not found: {target}"

    # Overlays
    for o_name in registry.list_overlays():
        desc = registry.get_overlay(o_name)
        assert desc is not None
        if desc.implemented and desc.renderer_module:
            target = REPO_ROOT / desc.renderer_module
            assert target.exists(), f"Overlay {o_name} renderer module not found: {target}"


def test_registry_core_rules_and_states():
    registry = get_presentation_registry()

    states = registry.get_core_states()
    assert "idle" in states
    assert "listening" in states
    assert "speaking" in states

    positions = registry.get_core_positions()
    assert "center" in positions
    assert "dock_bottom_right" in positions

    rules = registry.get_core_rules()
    assert "no_workspace" in rules
    assert "active_workspace" in rules
    assert rules["no_workspace"]["position"] == "center"
    assert rules["active_workspace"]["position"] == "dock_bottom_right"


def test_registry_validation_rejects_malformed_contract():
    with pytest.raises(PresentationContractError):
        PresentationRegistry.from_dict({"contract_version": 1})

    with pytest.raises(PresentationContractError):
        PresentationRegistry.from_dict({"contract_version": 999})

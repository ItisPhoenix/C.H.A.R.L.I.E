"""Authoritative Presentation Registry for Charlie V1.

Loads and validates the canonical cross-language presentation contract from
shared/presentation_contract.json, providing an immutable, queryable registry
for all presentation kinds, core invariants, workspaces, widgets, overlays, and
surface primitives.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("charlie.presentation_registry")

_DEFAULT_CONTRACT_PATH = (
    Path(__file__).resolve().parents[1] / "shared" / "presentation_contract.json"
)


class PresentationContractError(ValueError):
    """Raised when the presentation contract is invalid or missing required keys."""


@dataclass(frozen=True)
class WorkspaceDescriptor:
    """Canonical metadata for a HUD workspace."""

    name: str
    aliases: List[str] = field(default_factory=list)
    implemented: bool = True
    renderer: str = ""
    renderer_module: str = ""
    renderer_export: str = ""
    spatial: bool = False
    core_position: str = "dock_bottom_right"
    dismiss_policy: str = "persistent"
    description: str = ""


@dataclass(frozen=True)
class WidgetDescriptor:
    """Canonical metadata for a HUD contextual widget."""

    name: str
    aliases: List[str] = field(default_factory=list)
    implemented: bool = True
    renderer: str = ""
    renderer_module: str = ""
    renderer_export: str = ""
    default_dismiss_policy: str = "timed"
    default_auto_dismiss_ms: int = 5000
    default_zone: str = "top_right"
    supports: Dict[str, bool] = field(default_factory=dict)
    description: str = ""


@dataclass(frozen=True)
class OverlayDescriptor:
    """Canonical metadata for a HUD overlay or modal surface."""

    name: str
    aliases: List[str] = field(default_factory=list)
    implemented: bool = True
    renderer: str = ""
    renderer_module: str = ""
    renderer_export: str = ""
    dismiss_policy: str = "manual"
    anchor: str = "screen"
    description: str = ""


@dataclass(frozen=True)
class SurfaceResolution:
    """Structured registry result distinguishing resolved, unknown, and ambiguous targets."""

    status: str
    matches: tuple[tuple[str, str], ...] = ()
    descriptor: Any = None

    @property
    def resolved(self) -> bool:
        return self.status == "resolved"

    @property
    def taxonomy(self) -> Optional[str]:
        return self.matches[0][0] if self.resolved else None

    @property
    def canonical(self) -> Optional[str]:
        return self.matches[0][1] if self.resolved else None


class PresentationRegistry:
    """Authoritative reader and validation service for Charlie presentation contracts."""

    REQUIRED_ROOT_KEYS = (
        "contract_version",
        "surface_schema_version",
        "presentation_kinds",
        "core",
        "widgets",
        "workspaces",
        "overlays",
        "semantic_targets",
        "surface_primitives",
        "layout_types",
        "dismiss_policies",
        "preferred_zones",
        "anchors",
        "actions",
    )

    def __init__(self, contract_data: Dict[str, Any]) -> None:
        self._raw_contract = contract_data
        self._validate(contract_data)

        self._contract_version: int = int(contract_data["contract_version"])
        self._surface_schema_version: int = int(contract_data["surface_schema_version"])
        self._presentation_kinds: List[str] = list(contract_data["presentation_kinds"])
        self._core: Dict[str, Any] = dict(contract_data["core"])
        self._surface_primitives: List[str] = list(contract_data["surface_primitives"])
        self._layout_types: List[str] = list(contract_data["layout_types"])
        self._dismiss_policies: List[str] = list(contract_data["dismiss_policies"])
        self._preferred_zones: List[str] = list(contract_data["preferred_zones"])
        self._anchors: List[str] = list(contract_data["anchors"])
        self._actions: List[str] = list(contract_data["actions"])

        # Build workspace registry and alias map
        self._workspaces: Dict[str, WorkspaceDescriptor] = {}
        self._workspace_alias_map: Dict[str, str] = {}
        for ws_name, ws_data in contract_data["workspaces"].items():
            aliases = list(ws_data.get("aliases", []))
            descriptor = WorkspaceDescriptor(
                name=ws_name,
                aliases=aliases,
                implemented=bool(ws_data.get("implemented", True)),
                renderer=str(ws_data.get("renderer", "")),
                renderer_module=str(ws_data.get("renderer_module", "")),
                renderer_export=str(ws_data.get("renderer_export", "")),
                spatial=bool(ws_data.get("spatial", False)),
                core_position=str(ws_data.get("core_position", "dock_bottom_right")),
                dismiss_policy=str(ws_data.get("dismiss_policy", "persistent")),
                description=str(ws_data.get("description", "")),
            )
            self._workspaces[ws_name] = descriptor
            self._workspace_alias_map[ws_name.lower()] = ws_name
            for alias in aliases:
                self._workspace_alias_map[alias.lower()] = ws_name

        # Build widget registry and alias map
        self._widgets: Dict[str, WidgetDescriptor] = {}
        self._widget_alias_map: Dict[str, str] = {}
        for w_name, w_data in contract_data["widgets"].items():
            aliases = list(w_data.get("aliases", []))
            descriptor = WidgetDescriptor(
                name=w_name,
                aliases=aliases,
                implemented=bool(w_data.get("implemented", True)),
                renderer=str(w_data.get("renderer", "")),
                renderer_module=str(w_data.get("renderer_module", "")),
                renderer_export=str(w_data.get("renderer_export", "")),
                default_dismiss_policy=str(w_data.get("default_dismiss_policy", "timed")),
                default_auto_dismiss_ms=int(w_data.get("default_auto_dismiss_ms", 5000)),
                default_zone=str(w_data.get("default_zone", "top_right")),
                supports=dict(w_data.get("supports", {})),
                description=str(w_data.get("description", "")),
            )
            self._widgets[w_name] = descriptor
            self._widget_alias_map[w_name.lower()] = w_name
            for alias in aliases:
                self._widget_alias_map[alias.lower()] = w_name

        # Build overlays registry and alias map
        self._overlays: Dict[str, OverlayDescriptor] = {}
        self._overlay_alias_map: Dict[str, str] = {}
        for o_name, o_data in contract_data.get("overlays", {}).items():
            aliases = list(o_data.get("aliases", []))
            descriptor = OverlayDescriptor(
                name=o_name,
                aliases=aliases,
                implemented=bool(o_data.get("implemented", True)),
                renderer=str(o_data.get("renderer", "")),
                renderer_module=str(o_data.get("renderer_module", "")),
                renderer_export=str(o_data.get("renderer_export", "")),
                dismiss_policy=str(o_data.get("dismiss_policy", "manual")),
                anchor=str(o_data.get("anchor", "screen")),
                description=str(o_data.get("description", "")),
            )
            self._overlays[o_name] = descriptor
            self._overlay_alias_map[o_name.lower()] = o_name
            for alias in aliases:
                self._overlay_alias_map[alias.lower()] = o_name

        self._semantic_targets: Dict[str, tuple[str, str]] = {
            role: (str(target["taxonomy"]), str(target["surface"]))
            for role, target in contract_data.get("semantic_targets", {}).items()
        }

    @classmethod
    def from_file(cls, path: Optional[Path | str] = None) -> PresentationRegistry:
        """Load and instantiate a registry from the JSON contract file."""
        target_path = Path(path) if path else _DEFAULT_CONTRACT_PATH
        if not target_path.exists():
            raise PresentationContractError(f"Presentation contract file not found: {target_path}")
        try:
            with open(target_path, encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as exc:
            raise PresentationContractError(f"Invalid JSON in presentation contract: {exc}") from exc
        return cls(data)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PresentationRegistry:
        """Instantiate a registry directly from a validated dictionary."""
        return cls(data)

    def _validate(self, data: Dict[str, Any]) -> None:
        """Validate structure and consistency of contract dictionary."""
        if not isinstance(data, dict):
            raise PresentationContractError("Presentation contract must be a dictionary.")

        missing_keys = [k for k in self.REQUIRED_ROOT_KEYS if k not in data]
        if missing_keys:
            raise PresentationContractError(f"Contract missing required root keys: {missing_keys}")

        version = data.get("contract_version")
        if version != 1:
            raise PresentationContractError(f"Unsupported contract_version: {version}. Expected 1.")

        schema_version = data.get("surface_schema_version")
        if schema_version != 1:
            raise PresentationContractError(f"Unsupported surface_schema_version: {schema_version}. Expected 1.")

        if not isinstance(data.get("presentation_kinds"), list):
            raise PresentationContractError("presentation_kinds must be a list.")

        if not isinstance(data.get("core"), dict):
            raise PresentationContractError("core must be a dictionary.")

        if not isinstance(data.get("widgets"), dict):
            raise PresentationContractError("widgets must be a dictionary.")

        if not isinstance(data.get("workspaces"), dict):
            raise PresentationContractError("workspaces must be a dictionary.")

        if not isinstance(data.get("overlays"), dict):
            raise PresentationContractError("overlays must be a dictionary.")

        semantic_targets = data.get("semantic_targets", {})
        if not isinstance(semantic_targets, dict):
            raise PresentationContractError("semantic_targets must be a dictionary.")
        surfaces = {
            "widget": data["widgets"],
            "workspace": data["workspaces"],
            "overlay": data["overlays"],
        }
        for role, target in semantic_targets.items():
            if not isinstance(role, str) or not role.strip():
                raise PresentationContractError("semantic target roles must be non-empty strings.")
            if not isinstance(target, dict):
                raise PresentationContractError(f"Semantic target '{role}' must be an object.")
            taxonomy = target.get("taxonomy")
            surface = target.get("surface")
            if taxonomy not in surfaces:
                raise PresentationContractError(
                    f"Semantic target '{role}' has unsupported taxonomy: {taxonomy}"
                )
            if not isinstance(surface, str) or surface not in surfaces[taxonomy]:
                raise PresentationContractError(
                    f"Semantic target '{role}' must reference canonical {taxonomy}: {surface}"
                )
            # Semantic targets may intentionally resolve to an unavailable
            # surface. PresentationResolver converts those to an explicit
            # unavailable result; startup must not pretend the target is absent.

    # -------------------------------------------------------------------------
    # Properties
    # -------------------------------------------------------------------------

    @property
    def contract_version(self) -> int:
        return self._contract_version

    @property
    def surface_schema_version(self) -> int:
        return self._surface_schema_version

    # -------------------------------------------------------------------------
    # Introspection & Enumeration
    # -------------------------------------------------------------------------

    def list_presentation_kinds(self) -> List[str]:
        """Return all supported presentation kinds (modalities)."""
        return list(self._presentation_kinds)

    def list_workspaces(self) -> List[str]:
        """Return all canonical workspace types."""
        return list(self._workspaces.keys())

    def list_widgets(self) -> List[str]:
        """Return all canonical widget types."""
        return list(self._widgets.keys())

    def list_overlays(self) -> List[str]:
        """Return all canonical overlay/modal types."""
        return list(self._overlays.keys())

    def list_surface_primitives(self) -> List[str]:
        """Return all supported SurfaceComposer primitive types."""
        return list(self._surface_primitives)

    def list_layout_types(self) -> List[str]:
        """Return all supported SurfaceComposer layout container types."""
        return list(self._layout_types)

    def list_dismiss_policies(self) -> List[str]:
        """Return all supported dismiss policies."""
        return list(self._dismiss_policies)

    def list_preferred_zones(self) -> List[str]:
        """Return all abstract layout placement zones."""
        return list(self._preferred_zones)

    def list_anchors(self) -> List[str]:
        """Return all visual anchor targets."""
        return list(self._anchors)

    def list_actions(self) -> List[str]:
        """Return all supported presentation lifecycle actions."""
        return list(self._actions)

    # -------------------------------------------------------------------------
    # Resolution & Lookups
    # -------------------------------------------------------------------------

    def resolve_workspace_type(self, name: Optional[str]) -> Optional[str]:
        """Canonicalize a workspace type or alias to its canonical name."""
        if not name:
            return None
        return self._workspace_alias_map.get(name.strip().lower())

    def resolve_widget_type(self, name: Optional[str]) -> Optional[str]:
        """Canonicalize a widget type or alias to its canonical name."""
        if not name:
            return None
        return self._widget_alias_map.get(name.strip().lower())

    def resolve_overlay_type(self, name: Optional[str]) -> Optional[str]:
        """Canonicalize an overlay type or alias to its canonical name."""
        if not name:
            return None
        return self._overlay_alias_map.get(name.strip().lower())

    def has_workspace(self, name: str) -> bool:
        """Check if a workspace type (canonical or alias) exists in the registry."""
        return self.resolve_workspace_type(name) is not None

    def has_widget(self, name: str) -> bool:
        """Check if a widget type (canonical or alias) exists in the registry."""
        return self.resolve_widget_type(name) is not None

    def has_overlay(self, name: str) -> bool:
        """Check if an overlay type (canonical or alias) exists in the registry."""
        return self.resolve_overlay_type(name) is not None

    def has_primitive(self, name: str) -> bool:
        """Check if a surface primitive type exists in the registry."""
        return name in self._surface_primitives

    def get_workspace(self, name: str) -> Optional[WorkspaceDescriptor]:
        """Get canonical WorkspaceDescriptor for a canonical name or alias."""
        canonical = self.resolve_workspace_type(name)
        if not canonical:
            return None
        return self._workspaces.get(canonical)

    def get_widget(self, name: str) -> Optional[WidgetDescriptor]:
        """Get canonical WidgetDescriptor for a canonical name or alias."""
        canonical = self.resolve_widget_type(name)
        if not canonical:
            return None
        return self._widgets.get(canonical)

    def get_overlay(self, name: str) -> Optional[OverlayDescriptor]:
        """Get canonical OverlayDescriptor for a canonical name or alias."""
        canonical = self.resolve_overlay_type(name)
        if not canonical:
            return None
        return self._overlays.get(canonical)

    def resolve_surface(self, name: Optional[str]) -> SurfaceResolution:
        """Resolve a target using canonical-name precedence, then aliases.

        Category order never breaks ties: multiple canonical names or aliases
        produce ``ambiguous`` instead of silently choosing a surface.
        """
        if not name:
            return SurfaceResolution("unknown")

        normalized = name.strip().lower()
        canonical_matches = []
        for taxonomy, descriptors in (
            ("overlay", self._overlays),
            ("workspace", self._workspaces),
            ("widget", self._widgets),
        ):
            for canonical, descriptor in descriptors.items():
                if canonical.lower() == normalized:
                    canonical_matches.append((taxonomy, canonical, descriptor))
        if len(canonical_matches) == 1:
            taxonomy, canonical, descriptor = canonical_matches[0]
            return SurfaceResolution("resolved", ((taxonomy, canonical),), descriptor)
        if len(canonical_matches) > 1:
            return SurfaceResolution(
                "ambiguous", tuple((taxonomy, canonical) for taxonomy, canonical, _ in canonical_matches)
            )

        alias_matches = []
        for taxonomy, descriptors, aliases_for in (
            ("overlay", self._overlays, self._overlay_alias_map),
            ("workspace", self._workspaces, self._workspace_alias_map),
            ("widget", self._widgets, self._widget_alias_map),
        ):
            canonical = aliases_for.get(normalized)
            if canonical is not None:
                alias_matches.append((taxonomy, canonical, descriptors[canonical]))
        if len(alias_matches) == 1:
            taxonomy, canonical, descriptor = alias_matches[0]
            return SurfaceResolution("resolved", ((taxonomy, canonical),), descriptor)
        if len(alias_matches) > 1:
            return SurfaceResolution(
                "ambiguous", tuple((taxonomy, canonical) for taxonomy, canonical, _ in alias_matches)
            )
        return SurfaceResolution("unknown")

    def list_semantic_targets(self) -> List[str]:
        """Return declarative semantic presentation roles."""
        return list(self._semantic_targets.keys())

    def resolve_semantic_target(self, role: str) -> SurfaceResolution:
        """Resolve semantic role to its typed canonical registry surface."""
        target = self._semantic_targets.get(role)
        if target is None:
            return SurfaceResolution("unknown")
        taxonomy, canonical = target
        descriptor = {
            "workspace": self._workspaces,
            "widget": self._widgets,
            "overlay": self._overlays,
        }[taxonomy].get(canonical)
        if descriptor is None:
            return SurfaceResolution("unknown")
        return SurfaceResolution("resolved", ((taxonomy, canonical),), descriptor)

    def resolve_typed_surface(self, taxonomy: str, canonical: str) -> SurfaceResolution:
        """Resolve a canonical surface when taxonomy is already known."""
        descriptors = {
            "workspace": self._workspaces,
            "widget": self._widgets,
            "overlay": self._overlays,
        }.get(taxonomy)
        if descriptors is None or canonical not in descriptors:
            return SurfaceResolution("unknown")
        return SurfaceResolution("resolved", ((taxonomy, canonical),), descriptors[canonical])

    # -------------------------------------------------------------------------
    # Core Invariants
    # -------------------------------------------------------------------------

    def get_core_states(self) -> List[str]:
        """Return all valid Charlie core states."""
        return list(self._core.get("states", []))

    def get_core_positions(self) -> List[str]:
        """Return all valid Charlie core spatial positions."""
        return list(self._core.get("positions", []))

    def get_core_rules(self) -> Dict[str, Any]:
        """Return authoritative core positioning and visibility rules."""
        return dict(self._core.get("rules", {}))

    def build_model_awareness_block(self) -> str:
        """Build compact, deterministic semantic presentation knowledge for Brain prompts.

        This intentionally excludes renderer metadata, aliases, runtime state, and
        implementation details. PresentationResolver remains authoritative for the
        final modality and layout.
        """
        rules = self.get_core_rules()
        no_workspace = rules.get("no_workspace", {})
        active_workspace = rules.get("active_workspace", {})
        no_workspace_position = no_workspace.get("position", "unknown")
        active_workspace_position = active_workspace.get("position", "unknown")

        return (
            "[PRESENTATION CAPABILITIES]\n"
            "Semantic HUD presentation available. PresentationResolver selects modality/layout; "
            "describe intent semantically, never UI code or pixels.\n"
            f"Workspaces: {', '.join(self.list_workspaces())}\n"
            f"Widgets: {', '.join(self.list_widgets())}\n"
            f"Overlays: {', '.join(self.list_overlays())}\n"
            f"SurfaceComposer primitives: {', '.join(self.list_surface_primitives())}\n"
            f"Core: no workspace -> {no_workspace_position}; active workspace -> {active_workspace_position}\n"
            "Presentation lifecycle and layout are system-managed.\n"
            "Approved primitives only; never emit React/JSX/HTML/CSS/JavaScript "
           "or pixels. Show may be visual; tell remains conversational. "
            "Never claim open without runtime evidence."
        )

    def to_dict(self) -> Dict[str, Any]:
        """Return the full validated contract dictionary."""
        return dict(self._raw_contract)


_GLOBAL_REGISTRY: Optional[PresentationRegistry] = None


def get_presentation_registry() -> PresentationRegistry:
    """Authoritative singleton accessor for the PresentationRegistry."""
    global _GLOBAL_REGISTRY
    if _GLOBAL_REGISTRY is None:
        _GLOBAL_REGISTRY = PresentationRegistry.from_file()
    return _GLOBAL_REGISTRY

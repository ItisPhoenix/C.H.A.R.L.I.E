"""C.H.A.R.L.I.E. V1 — Canonical SurfaceComposer Specification and Validation.

Hard Security Invariants:
1. No arbitrary JavaScript, React JSX, CSS, HTML, eval(), or script tags.
2. Every primitive is strictly schema-typed and rendered by an approved React component.
3. Explicit schema versioning (version 1).
4. Strictly enforced complexity limits against resource exhaustion.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from charlie.presentation_contract_generated import (
    LayoutType,
    PrimitiveType,
    SURFACE_SCHEMA_VERSION,
)

SCHEMA_VERSION: int = SURFACE_SCHEMA_VERSION

# Complexity limits
MAX_DEPTH: int = 5
MAX_PRIMITIVES: int = 100
MAX_TABLE_ROWS: int = 50
MAX_CHART_POINTS: int = 60
MAX_TEXT_LEN: int = 4000
MAX_ACTIONS: int = 10


class TargetSurface(str, Enum):
    WIDGET = "widget"
    WORKSPACE = "workspace"


@dataclass
class ActionSpec:
    id: str
    label: str
    action_id: str
    payload: Dict[str, Any] = field(default_factory=dict)
    variant: str = "default"  # default, primary, danger, subtle
    disabled: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PrimitiveSpec:
    type: str
    id: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)
    children: List[PrimitiveSpec] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        res: Dict[str, Any] = {
            "type": self.type,
            "data": self.data,
        }
        if self.id:
            res["id"] = self.id
        if self.children:
            res["children"] = [c.to_dict() for c in self.children]
        return res


@dataclass
class SurfaceSpec:
    surface_id: str
    title: str
    target: str = "widget"  # "widget" or "workspace"
    schema_version: int = SCHEMA_VERSION
    revision: int = 1
    surface_type: str = "custom"
    summary: str = ""
    layout: Dict[str, Any] = field(default_factory=lambda: {"type": "stack", "gap": 12})
    primitives: List[PrimitiveSpec] = field(default_factory=list)
    actions: List[ActionSpec] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "surface_id": self.surface_id,
            "title": self.title,
            "target": self.target,
            "revision": self.revision,
            "surface_type": self.surface_type,
            "summary": self.summary,
            "layout": self.layout,
            "primitives": [p.to_dict() for p in self.primitives],
            "actions": [a.to_dict() for a in self.actions],
            "metadata": self.metadata,
        }


# Security sanitization helpers
DANGEROUS_PATTERNS = [
    re.compile(r"<\s*script", re.IGNORECASE),
    re.compile(r"javascript\s*:", re.IGNORECASE),
    re.compile(r"<\s*iframe", re.IGNORECASE),
    re.compile(r"<\s*style", re.IGNORECASE),
    re.compile(r"onload\s*=", re.IGNORECASE),
    re.compile(r"onerror\s*=", re.IGNORECASE),
    re.compile(r"onclick\s*=", re.IGNORECASE),
]


def _check_string_safety(val: str, path: str, errors: List[str]) -> None:
    if len(val) > MAX_TEXT_LEN:
        errors.append(f"Text length at {path} ({len(val)}) exceeds maximum limit of {MAX_TEXT_LEN}")
    for pattern in DANGEROUS_PATTERNS:
        if pattern.search(val):
            errors.append(f"Dangerous script/HTML pattern detected at {path}")


def _validate_primitive_node(
    node_dict: Dict[str, Any],
    depth: int,
    counter: List[int],
    errors: List[str],
    path: str,
) -> None:
    counter[0] += 1
    if counter[0] > MAX_PRIMITIVES:
        errors.append(f"Surface primitive count exceeds maximum allowed limit ({MAX_PRIMITIVES})")
        return

    if depth > MAX_DEPTH:
        errors.append(f"Nesting depth at {path} ({depth}) exceeds maximum limit ({MAX_DEPTH})")
        return

    ptype = node_dict.get("type")
    if not ptype or not isinstance(ptype, str):
        errors.append(f"Missing or invalid primitive type at {path}")
        return

    valid_types = {t.value for t in PrimitiveType}
    if ptype not in valid_types:
        errors.append(f"Unsupported primitive type '{ptype}' at {path}")

    data = node_dict.get("data", {})
    if not isinstance(data, dict):
        errors.append(f"Primitive data must be an object at {path}")
        return

    # Check string safety in data
    for k, v in data.items():
        if isinstance(v, str):
            _check_string_safety(v, f"{path}.data.{k}", errors)

    # Specific primitive checks
    if ptype == PrimitiveType.TABLE.value:
        rows = data.get("rows", [])
        if isinstance(rows, list) and len(rows) > MAX_TABLE_ROWS:
            errors.append(f"Table rows at {path} ({len(rows)}) exceeds limit ({MAX_TABLE_ROWS})")

    elif ptype == PrimitiveType.CHART.value:
        points = data.get("data", [])
        if isinstance(points, list) and len(points) > MAX_CHART_POINTS:
            errors.append(f"Chart data points at {path} ({len(points)}) exceeds limit ({MAX_CHART_POINTS})")

    elif ptype == PrimitiveType.IMAGE.value:
        src = data.get("src", "")
        if isinstance(src, str) and src.lower().startswith("javascript:"):
            errors.append(f"Unsafe image URL scheme detected at {path}.data.src")

    children = node_dict.get("children", [])
    if isinstance(children, list):
        for idx, child in enumerate(children):
            if isinstance(child, dict):
                _validate_primitive_node(child, depth + 1, counter, errors, f"{path}.children[{idx}]")


def validate_surface_spec(spec_dict: Dict[str, Any]) -> Tuple[bool, List[str], Optional[SurfaceSpec]]:
    """
    Authoritative backend validation of a SurfaceSpec dictionary.
    Returns (is_valid, error_list, parsed_spec).
    """
    errors: List[str] = []

    if not isinstance(spec_dict, dict):
        return False, ["Surface specification must be a dictionary"], None

    # 1. Version Check
    version = spec_dict.get("schema_version")
    if version != SCHEMA_VERSION:
        return False, [f"Unsupported schema_version: {version}. Expected {SCHEMA_VERSION}."], None

    # 2. Required Root Fields
    surface_id = spec_dict.get("surface_id")
    if not surface_id or not isinstance(surface_id, str):
        errors.append("surface_id is required and must be a non-empty string")

    title = spec_dict.get("title")
    if not title or not isinstance(title, str):
        errors.append("title is required and must be a string")
    elif isinstance(title, str):
        _check_string_safety(title, "title", errors)

    target = spec_dict.get("target", "widget")
    if target not in {"widget", "workspace"}:
        errors.append(f"target must be 'widget' or 'workspace', got '{target}'")

    summary = spec_dict.get("summary", "")
    if isinstance(summary, str):
        _check_string_safety(summary, "summary", errors)

    # 3. Actions validation
    actions_raw = spec_dict.get("actions", [])
    actions_list: List[ActionSpec] = []
    if isinstance(actions_raw, list):
        if len(actions_raw) > MAX_ACTIONS:
            errors.append(f"Actions count ({len(actions_raw)}) exceeds limit of {MAX_ACTIONS}")
        for idx, a in enumerate(actions_raw):
            if not isinstance(a, dict):
                errors.append(f"Action at index {idx} must be a dictionary")
                continue
            act_id = a.get("id") or f"action_{idx}"
            label = a.get("label", "")
            action_id = a.get("action_id", "")
            if not action_id or not isinstance(action_id, str):
                errors.append(f"Action at index {idx} missing required action_id")
            if isinstance(label, str):
                _check_string_safety(label, f"actions[{idx}].label", errors)
            actions_list.append(
                ActionSpec(
                    id=str(act_id),
                    label=str(label),
                    action_id=str(action_id),
                    payload=a.get("payload", {}) if isinstance(a.get("payload"), dict) else {},
                    variant=str(a.get("variant", "default")),
                    disabled=bool(a.get("disabled", False)),
                )
            )
    else:
        errors.append("actions must be a list")

    # 4. Primitives validation
    primitives_raw = spec_dict.get("primitives", [])
    counter = [0]
    primitives_list: List[PrimitiveSpec] = []

    if isinstance(primitives_raw, list):
        for idx, p in enumerate(primitives_raw):
            if isinstance(p, dict):
                _validate_primitive_node(p, depth=1, counter=counter, errors=errors, path=f"primitives[{idx}]")
                if not errors:
                    # Recursive builder
                    def _build_prim(d: Dict[str, Any]) -> PrimitiveSpec:
                        kids = [_build_prim(c) for c in d.get("children", []) if isinstance(c, dict)]
                        return PrimitiveSpec(
                            type=d.get("type", "text"),
                            id=d.get("id"),
                            data=d.get("data", {}),
                            children=kids,
                        )
                    primitives_list.append(_build_prim(p))
    else:
        errors.append("primitives must be a list")

    if errors:
        return False, errors, None

    spec = SurfaceSpec(
        surface_id=str(surface_id),
        title=str(title),
        target=str(target),
        schema_version=SCHEMA_VERSION,
        revision=int(spec_dict.get("revision", 1)),
        surface_type=str(spec_dict.get("surface_type", "custom")),
        summary=str(summary),
        layout=spec_dict.get("layout", {"type": "stack", "gap": 12}),
        primitives=primitives_list,
        actions=actions_list,
        metadata=spec_dict.get("metadata", {}) if isinstance(spec_dict.get("metadata"), dict) else {},
    )

    return True, [], spec

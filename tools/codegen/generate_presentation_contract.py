"""Deterministic Code Generator for C.H.A.R.L.I.E. Presentation Contract.

Single source of truth: shared/presentation_contract.json
Generates:
- charlie/presentation_contract_generated.py

Supports --check flag for CI / pre-commit staleness verification.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = REPO_ROOT / "shared" / "presentation_contract.json"
PY_TARGET_PATH = REPO_ROOT / "charlie" / "presentation_contract_generated.py"


def load_contract() -> Dict[str, Any]:
    if not CONTRACT_PATH.exists():
        raise FileNotFoundError(f"Contract not found: {CONTRACT_PATH}")
    with open(CONTRACT_PATH, encoding="utf-8") as f:
        return json.load(f)


def _to_enum_name(val: str) -> str:
    return val.upper()


def generate_python(contract: Dict[str, Any]) -> str:
    lines = [
        "# AUTO-GENERATED — DO NOT EDIT",
        "# Source: shared/presentation_contract.json",
        '"""Authoritative generated presentation enums, constants, and metadata."""',
        "",
        "from __future__ import annotations",
        "",
        "from enum import StrEnum",
        "from typing import Any, Dict, Tuple",
        "",
        f"CONTRACT_VERSION: int = {contract['contract_version']}",
        f"SURFACE_SCHEMA_VERSION: int = {contract['surface_schema_version']}",
        "",
        "",
        "class PresentationKind(StrEnum):",
    ]

    for item in contract["presentation_kinds"]:
        lines.append(f'    {_to_enum_name(item)} = "{item}"')

    lines.extend([
        "",
        "",
        "class DismissPolicy(StrEnum):",
    ])
    for item in contract["dismiss_policies"]:
        lines.append(f'    {_to_enum_name(item)} = "{item}"')

    lines.extend([
        "",
        "",
        "class PreferredZone(StrEnum):",
    ])
    for item in contract["preferred_zones"]:
        lines.append(f'    {_to_enum_name(item)} = "{item}"')

    lines.extend([
        "",
        "",
        "class AnchorTarget(StrEnum):",
    ])
    for item in contract["anchors"]:
        lines.append(f'    {_to_enum_name(item)} = "{item}"')

    lines.extend([
        "",
        "",
        "class PrimitiveType(StrEnum):",
    ])
    for item in contract["surface_primitives"]:
        lines.append(f'    {_to_enum_name(item)} = "{item}"')

    lines.extend([
        "",
        "",
        "class LayoutType(StrEnum):",
    ])
    for item in contract["layout_types"]:
        lines.append(f'    {_to_enum_name(item)} = "{item}"')

    lines.extend([
        "",
        "",
        f"PRESENTATION_KINDS: Tuple[str, ...] = {tuple(contract['presentation_kinds'])!r}",
        f"SURFACE_PRIMITIVES: Tuple[str, ...] = {tuple(contract['surface_primitives'])!r}",
        f"LAYOUT_TYPES: Tuple[str, ...] = {tuple(contract['layout_types'])!r}",
        f"DISMISS_POLICIES: Tuple[str, ...] = {tuple(contract['dismiss_policies'])!r}",
        f"PREFERRED_ZONES: Tuple[str, ...] = {tuple(contract['preferred_zones'])!r}",
        f"ANCHOR_TARGETS: Tuple[str, ...] = {tuple(contract['anchors'])!r}",
        f"CORE_STATES: Tuple[str, ...] = {tuple(contract['core']['states'])!r}",
        f"CORE_POSITIONS: Tuple[str, ...] = {tuple(contract['core']['positions'])!r}",
        f"PRESENTATION_ACTIONS: Tuple[str, ...] = {tuple(contract['actions'])!r}",
        "",
        f"CORE_RULES: Dict[str, Any] = {contract['core']['rules']!r}",
        f"WORKSPACES_METADATA: Dict[str, Any] = {contract['workspaces']!r}",
        f"WIDGETS_METADATA: Dict[str, Any] = {contract['widgets']!r}",
        f"OVERLAYS_METADATA: Dict[str, Any] = {contract.get('overlays', {})!r}",
        f"SEMANTIC_TARGETS: Dict[str, Any] = {contract.get('semantic_targets', {})!r}",
        "",
    ])

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the Python presentation contract artifact.")
    parser.add_argument("--check", action="store_true", help="Check if generated files are current without writing.")
    args = parser.parse_args()

    contract = load_contract()
    py_content = generate_python(contract)

    if args.check:
        stale = False
        if not PY_TARGET_PATH.exists() or PY_TARGET_PATH.read_text(encoding="utf-8") != py_content:
            print(f"[STALE] Python target is outdated or missing: {PY_TARGET_PATH}", file=sys.stderr)
            stale = True

        if stale:
            print("Run 'python tools/codegen/generate_presentation_contract.py' to update.", file=sys.stderr)
            return 1

        print("Presentation contract generated artifacts are up to date.")
        return 0

    PY_TARGET_PATH.parent.mkdir(parents=True, exist_ok=True)

    PY_TARGET_PATH.write_text(py_content, encoding="utf-8")
    print(f"Generated: {PY_TARGET_PATH}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

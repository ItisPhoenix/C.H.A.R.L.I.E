"""Inert parsing and schema extraction for owner-reviewed generated tools."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass
class GeneratedToolSpec:
    name: str
    description: str
    schema: Dict[str, Any]


def parse_generated_tool(name: str, raw_text: str) -> GeneratedToolSpec:
    """Parse LLM-authored tool source. Contract: raw_text is exactly one
    top-level function named `name`, with a docstring (used as the tool
    description) and plain positional/keyword parameters (used to build a
    string-typed JSON schema -- no type-hint interpreter, keeps this honest).
    """
    tree = ast.parse(raw_text, mode="exec")
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef):
        raise ValueError("Generated code must contain exactly one top-level function definition")
    func_def = tree.body[0]
    if func_def.name != name:
        raise ValueError(f"Generated function must be named '{name}', found '{func_def.name}'")
    docstring = ast.get_docstring(func_def)
    if not docstring:
        raise ValueError("Generated tool function must have a docstring (used as its description)")

    params = [a.arg for a in func_def.args.args]
    schema = {
        "type": "object",
        "properties": {p: {"type": "string"} for p in params},
        "required": params,
    }

    return GeneratedToolSpec(name=name, description=docstring, schema=schema)


def register_generated_tool(registry: Any, spec: GeneratedToolSpec) -> List[str]:
    raise RuntimeError(
        f"Generated tool '{spec.name}' cannot be activated: no OS-isolated worker is available."
    )

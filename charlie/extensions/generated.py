"""Tier-3 self-extension adapter: Charlie authoring and registering a brand
new runtime tool. Reuses the existing SkillCard/propose/confirm gate in
charlie/extensions/__init__.py -- no new approval mechanism. Generated code
runs same-process, same trust model as today's plugin/MCP tools; the
approval gate (a human reads the code before confirming) is the control,
not process isolation.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any, Callable, Dict, List


@dataclass
class GeneratedToolSpec:
    name: str
    description: str
    schema: Dict[str, Any]
    func: Callable[..., Any]


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

    namespace: Dict[str, Any] = {}
    exec(compile(tree, filename=f"<generated:{name}>", mode="exec"), namespace)
    return GeneratedToolSpec(name=name, description=docstring, schema=schema, func=namespace[name])


def register_generated_tool(registry: Any, spec: GeneratedToolSpec) -> List[str]:
    registry.register_tool(spec.name, spec.description, spec.schema)(spec.func)
    return [spec.name]

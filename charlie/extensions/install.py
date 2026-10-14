"""Install supported skill, OpenAPI, and generated extensions into a registry."""

from __future__ import annotations

from typing import Any, Callable, List, Optional


def declared_tools_for(kind: str, name: str, source: str, raw_text: str) -> List[str]:
    """Parse without registering so the approval record lists declared tools."""
    if kind == "skill":
        from charlie.extensions.skills import parse_skill_md

        return parse_skill_md(raw_text).scripts
    if kind == "openapi":
        from charlie.extensions.openapi_import import parse_openapi_spec

        return [op.operation_id for op in parse_openapi_spec(raw_text, base_url=source).operations]
    if kind == "generated":
        from charlie.extensions.generated import parse_generated_tool

        return [parse_generated_tool(name, raw_text).name]
    raise ValueError(f"Unknown extension kind '{kind}'")


def run_skill_script(script_path: str, args: List[str]) -> str:
    """Execute a skill-bundled script, gated by the same keyword safety
    tiers as shell_execute (see charlie.tools.is_command_keyword_blocked /
    is_command_keyword_gated) -- an imported skill script doesn't get to
    skip Charlie's command-safety rules just because it arrived via the
    Extensions UI instead of a direct shell_execute call. Uses the
    keyword-only checks, not the full shell-metacharacter guard: this runs
    via argv directly (no shell=True), so metacharacters are inert literal
    characters rather than injection vectors -- and script paths routinely
    contain parentheses etc. from the filesystem. Gated commands are
    hard-blocked here rather than routed through the interactive
    approve/decline flow: unlike a normal tool call, there's no guarantee a
    human is watching when a skill script runs."""
    from charlie.tools import is_command_keyword_blocked, is_command_keyword_gated

    command = " ".join([script_path, *args])
    block_reason = is_command_keyword_blocked(command)
    if block_reason:
        return f"Error: script execution blocked ({block_reason}): {script_path}"
    gate_reason = is_command_keyword_gated(command)
    if gate_reason:
        return (
            f"Error: script blocked ({gate_reason}) -- skill scripts run "
            "unsupervised and cannot request approval."
        )

    import subprocess

    try:
        result = subprocess.run(
            [script_path, *args], capture_output=True, text=True, timeout=30
        )
        output = (result.stdout + result.stderr).strip()
        return output or f"Script {script_path} exited with code {result.returncode} (no output)."
    except Exception as exc:
        return f"Error running script {script_path}: {exc}"


def install_extension(
    kind: str,
    name: str,
    source: str,
    raw_text: str,
    registry: Any,
    script_runner: Optional[Callable[[str, List[str]], str]] = None,
) -> List[str]:
    """Parse and register a supported extension into `registry`."""
    before_tools = set(getattr(registry, "_tools", {}))
    try:
        if kind == "skill":

            from charlie.extensions.skills import parse_skill_md, register_skill_scripts

            manifest = parse_skill_md(raw_text)
            runner = script_runner or run_skill_script
            return register_skill_scripts(registry, manifest, runner)
        if kind == "openapi":
            from charlie.extensions.openapi_import import parse_openapi_spec, register_openapi_operations

            spec = parse_openapi_spec(raw_text, base_url=source)
            return register_openapi_operations(registry, spec)
        if kind == "generated":
            from charlie.extensions.generated import parse_generated_tool, register_generated_tool

            spec = parse_generated_tool(name, raw_text)
            return register_generated_tool(registry, spec)
        raise ValueError(f"Unknown extension kind '{kind}'")
    except Exception:
        for tool_name in set(getattr(registry, "_tools", {})) - before_tools:
            registry.unregister_tool(tool_name)
        raise

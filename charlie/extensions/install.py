"""Shared extension-install logic (Phase 5 adapters: mcp/skill/openapi/plugin).

Extracted from charlie/web_server.py so the exact same parse-and-register
code can run in both OS processes: the web server (where the dashboard's
propose/confirm/enable/disable/uninstall REST flow lives) and the voice
process (where the actual chat Brain and its tool-calling loop live). Each
process holds its own ToolRegistry/MCPClient/PluginManager instances --
these are plain functions, not methods on shared state, so a caller in
either process can run them against its own local instances.
"""

from __future__ import annotations

from typing import Any, Callable, List, Optional, Tuple

BUILTIN_PLUGIN_NAMES = ("filesystem", "browser", "calendar", "code_exec")


def builtin_plugin(name: str, plugin_allow_dirs: List[str]) -> Any:
    from charlie.plugins import BrowserPlugin, CalendarPlugin, CodeExecPlugin, FilesystemPlugin

    factories = {
        "filesystem": lambda: FilesystemPlugin(allowed_dirs=plugin_allow_dirs),
        "browser": BrowserPlugin,
        "calendar": CalendarPlugin,
        "code_exec": CodeExecPlugin,
    }
    if name not in factories:
        raise ValueError(f"Unknown built-in plugin '{name}'. Valid: {', '.join(BUILTIN_PLUGIN_NAMES)}")
    return factories[name]()


def parsed_mcp_config(name: str, source: str, raw_text: str) -> Any:
    """Parse an MCP server spec and require its name to match the
    extension's declared name, so `name` stays the single source of truth
    across propose/confirm/enable/disable for every extension kind."""
    from charlie.mcp_client import parse_server_spec

    cfg = parse_server_spec(raw_text or source)
    if cfg.name != name:
        raise ValueError(f"MCP spec name '{cfg.name}' does not match extension name '{name}'")
    return cfg


def declared_tools_for(
    kind: str, name: str, source: str, raw_text: str, plugin_allow_dirs: List[str]
) -> List[str]:
    """Parse (without registering) so propose() can show real declared
    tools in the SkillCard before anything activates."""
    if kind == "mcp":
        parsed_mcp_config(name, source, raw_text)
        return []  # MCP tools aren't known until the server is actually started
    if kind == "skill":
        from charlie.extensions.skills import parse_skill_md

        return parse_skill_md(raw_text).scripts
    if kind == "openapi":
        from charlie.extensions.openapi_import import parse_openapi_spec

        return [op.operation_id for op in parse_openapi_spec(raw_text, base_url=source).operations]
    if kind == "plugin":
        return [t["name"] for t in builtin_plugin(name, plugin_allow_dirs).get_tools()]
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
    plugin_manager: Any,
    mcp_client: Any,
    plugin_allow_dirs: List[str],
    script_runner: Optional[Callable[[str, List[str]], str]] = None,
) -> Tuple[List[str], Any]:
    """Parse and register an approved extension into `registry`.

    Returns (registered_tool_names, mcp_client) -- `mcp_client` is handed
    back since the "mcp" branch may lazily construct one; callers must store
    the returned value back into their own module-level slot.
    """
    if kind == "mcp":
        from charlie.mcp_client import MCPClient

        cfg = parsed_mcp_config(name, source, raw_text)
        if mcp_client is None:
            mcp_client = MCPClient()
        mcp_client.add_server(cfg)
        return mcp_client.enable_server(registry, name), mcp_client
    if kind == "skill":
        from charlie.extensions.skills import parse_skill_md, register_skill_scripts

        manifest = parse_skill_md(raw_text)
        runner = script_runner or run_skill_script
        return register_skill_scripts(registry, manifest, runner), mcp_client
    if kind == "openapi":
        from charlie.extensions.openapi_import import parse_openapi_spec, register_openapi_operations

        spec = parse_openapi_spec(raw_text, base_url=source)
        return register_openapi_operations(registry, spec), mcp_client
    if kind == "plugin":
        from charlie.tools import enable_plugin

        return enable_plugin(registry, plugin_manager, builtin_plugin(name, plugin_allow_dirs)), mcp_client
    if kind == "generated":
        from charlie.extensions.generated import parse_generated_tool, register_generated_tool

        spec = parse_generated_tool(name, raw_text)
        return register_generated_tool(registry, spec), mcp_client
    raise ValueError(f"Unknown extension kind '{kind}'")

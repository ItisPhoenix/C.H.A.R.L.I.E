# Phase 0 - Throwaway Windows-MCP Spike (no code)

> Part of the "Charlie -> Agentic Windows OS" initiative. Standalone; read on its own.

## Goal

Validate the *feel* of UIA-driven desktop control before writing any native code, by borrowing an
existing MCP server. Charlie already ships a full MCP client that registers external tools into the
same `ToolRegistry` the LLM already calls - so this needs **zero new code**, only env vars.

## Why this is safe to throw away

Everything here is ungated and runs someone else's process. It exists only to answer one question:
"does the UIA tree -> text set-of-marks -> text action shape feel right for Charlie's loop?" Nothing
from this phase ships. Delete the env vars when done.

## Steps

1. Install / locate the CursorTouch **Windows-MCP** server (MIT). Note its launch command.
2. Set environment (no file edits):
   - `MCP_ENABLED=true`
   - `MCP_SERVERS=windows-mcp|<Windows-MCP launch command>`
     (spec format is `name|command|arg1,arg2` per `parse_server_spec`, `charlie/mcp_client.py:48`).
3. Start Charlie normally. On boot, `start_mcp(config)` (`charlie/mcp_client.py:452`) launches the
   server and `register_tools_into(registry, prefix="mcp_")` (`charlie/mcp_client.py:209`) auto-registers
   its tools into the shared `ToolRegistry`. They appear in `get_tool_definitions()` and are callable
   through the normal `Brain.chat_stream` loop with no other changes.

## Existing machinery reused (do not modify)

- `charlie/mcp_client.py` - `parse_server_spec` (~48), `register_tools_into` (~209), `start_mcp` (~452).
- `charlie/config.py` - `mcp_enabled` (~98), `mcp_servers` (~101).
- `charlie/core.py` - the `Brain.chat_stream` tool loop (~1794) drives the MCP tools unchanged.

## Verification

- Say / type: "open Notepad and type hello". Watch it drive through the existing loop.
- Try a two-step task ("open Calculator and press 5"). Confirm the perceive->act->perceive rhythm works.

## Exit criterion

You have decided the UIA set-of-marks + text-action shape is right for Charlie. Then:
`MCP_ENABLED=false`, clear `MCP_SERVERS`. Proceed to Phase 1 (native, gated).

## Risks / notes

- **Ungated by design.** MCP tools bypass Charlie's gate (the gate is a hardcoded tool-name ladder at
  `core.py:1722` that only covers `shell_execute`/`file_read`/`file_write`). Run this only on a machine
  you don't mind it poking. This gap is exactly what Phase 1 fixes with native, gated tools.
- Do not build anything on top of the MCP tools here - they are the reference, not the product.

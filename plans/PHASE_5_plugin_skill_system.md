# Phase 5 - Universal Plugin/Skill System (web-managed)

> Part of the "Charlie -> Agentic Windows OS" initiative. Standalone; read on its own.
> Independent of Phases 1-4 (desktop control) - can be built in parallel or separately.

## Goal

Let the user install capabilities built for Claude, ChatGPT, and other agents so they work on Charlie
too, managed from the web dashboard - both a simple installer and a browsable curated catalog. Reality
check kept from the original research: there is no single format every agent uses, but there are real
portable standards worth adapting, and Charlie already has the most important one half-built.

## Four adapters, all converging on the existing `ToolRegistry`

No new execution engine - every adapter ends by registering ordinary tools into the same
`charlie.tools.registry` that built-in tools, MCP tools, and plugin tools already share
(`ToolRegistry.register_tool`, `get_tool_definitions()`, `execute_tool()` - `charlie/tools.py:90-174`).

### 1. MCP (already exists - extend to runtime control)
Today `charlie/mcp_client.py` only starts servers at boot from `config.mcp_servers` (env, startup-only).
Add:
- An unregister path (remove a server's tools from `registry` and stop its subprocess).
- A way to add/enable/disable a server spec at runtime and re-run `register_tools_into` /
  the unregister path, without restarting Charlie. Newly (un)registered tools appear/disappear from
  `get_tool_definitions()` on the very next turn automatically - no other change needed.

### 2. Claude Agent Skills (`SKILL.md`)
New file `charlie/extensions/skills.py`:
- Parse a `SKILL.md` file's frontmatter (name/description/metadata) and body per the Claude Agent Skills
  format (the same format used across Claude Code, Cursor, and the wider "Skills Hub" ecosystem - so
  skills authored for those tools load here too).
- Inject the skill's instructions into the prompt as a new block in the existing CONTEXT tier
  (alongside MEMORY/USER/OPINIONS, `_build_stable_tier`/`_build_context_tier`-style functions in `core.py`).
- If the skill bundles scripts/tools, register each as an ordinary tool (thin wrapper subprocess call),
  same registration path as everything else.

### 3. ChatGPT GPT-Actions / OpenAPI
New file `charlie/extensions/openapi_import.py`:
- Parse an OpenAPI spec (this is literally what GPT Actions and the legacy OpenAI plugin format are).
- For each operation, register a `@registry`-shaped tool that makes the corresponding HTTP call via `httpx`.

### 4. Native plugins (already exists)
Surface the existing `charlie/plugins.py` system (`FilesystemPlugin`, `BrowserPlugin`, `CalendarPlugin`,
`CodeExecPlugin`) in the same web management UI as the other three, instead of only via
`plugins_enabled`/`plugin_allow_dirs` env config.

## Web UI: installer + curated catalog (both)

### Backend (`charlie/web_server.py`)
- New REST endpoints under `/api/extensions`: `list`, `install` (accepts an MCP spec string, an uploaded
  `SKILL.md`/zip, or an OpenAPI URL/file), `enable`/`disable`, `uninstall`.
- Persist the enabled set through the existing config-write path (`/api/config` PUT, ~line 529) rather
  than inventing a second config mechanism.

### Frontend
- Extend the existing InsightRail **MCP tab** into a broader **Extensions** tab:
  - An installer form (paste MCP spec / upload SKILL.md or zip / paste OpenAPI spec URL).
  - A catalog view seeded from a **static curated JSON file** checked into the repo (built from
    well-known lists such as awesome-mcp-servers / awesome-skills style indexes) - explicitly not a
    live registry backend or a moderated marketplace; that's a bigger system than this phase needs.

## Safety: gated install + sandbox + arm

Borrowing two concrete patterns found in research on comparable projects (OpenClaw's Skill Card /
SkillSpector, Hermes's Skills Hub):

- **Gated install.** Installing or enabling any extension is itself an action that goes through
  `Brain.request_tool_approval` (`core.py:1323`) - the same HITL channel used everywhere else. No
  extension activates silently.
- **Provenance "Skill Card."** On install, record name, source (URL/file), declared tools, and a content
  hash; surface this in the approval dialog so the user sees what they're approving, not just a name.
- **Static scan before approval.** Before showing the approval dialog, scan the SKILL.md/manifest/OpenAPI
  spec text for hidden-instruction / prompt-injection patterns and suspicious endpoints (e.g. exact
  string matches or simple heuristics - not a full security product); surface warnings in the same dialog.
- **Sandbox + gate reuse.** Any tool an installed extension registers is **not** automatically trusted:
  if it touches shell/file/desktop actions, it still hits the exact same gate ladder
  (`is_shell_command_gated`, `get_path_gate_reason`, and the Phase 1 desktop gate) as built-in tools.
  Code-executing extensions reuse the existing `CodeExecPlugin` AST-filtered subprocess posture
  (`charlie/plugins.py`) rather than a new sandbox.

## Verification

1. `uv run ruff check .` / `uv run pytest -v` pass. Frontend `npx tsc --noEmit` / `npm test` pass.
2. From the web UI, install one example of each: an MCP server, a sample `SKILL.md`, an OpenAPI spec.
   Confirm each triggers a gated approval showing its Skill Card + scan result before activating.
3. Confirm each installed extension's tools appear in the tool list and are callable through the normal
   `chat_stream` loop.
4. Confirm a skill/tool that attempts a desktop or shell action still hits the existing gate (does not
   bypass Phase 1's arm/confirm or the hard-block/gated-keyword lists).
5. Disable an extension; confirm its tools disappear from `get_tool_definitions()` on the next turn
   without a restart.

## Explicitly out of scope for this phase

A live/moderated marketplace backend, automatic skill discovery/crawling, and any desktop-control work
(Phases 1-4) - this phase is purely about tool/skill interoperability and its own install-time safety gate.

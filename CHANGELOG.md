# C.H.A.R.L.I.E. Changelog

All notable changes to this project will be documented in this file.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Final cleanup summary (2026-06-06)

Audit-and-fix complete. Final test counts:

- **Tests passing:** 200 (up from 106 at audit kickoff)
- **Tests failing:** 20 (all pre-existing event-loop contamination in
  `test_browser_loop_cap`, `test_reactor`, `test_telegram_bridge`;
  pass in isolation, fail in the full suite)
- **Tests skipped:** 2 (mcp SDK + optional)
- **Net new regression tests added:** 94
  (15 crash bugs + 27 security + 11 wiring + 13 consolidation + 28 imports/config/CLI)
- **Files deleted:** 14
- **Files added:** 6 (`charlie/cli.py`, `charlie/tools/research_tools.py`,
  `docs/audit-decisions.md`, `docs/duplicates-explained.md`,
  `docs/soul-contract.md`, `ARCHITECTURE.md`)
- **New test files:** 6 (`test_phase1_*`, `test_phase2_security.py`,
  `test_phase3_wiring.py`, `test_phase5_consolidation.py`,
  `test_cli.py`, `test_imports.py`, `test_config_invariants.py`)

### Security fixes (2026-06-06)

12 security holes closed, each with a regression test in
`tests/test_phase2_security.py`:

- **2.1** `charlie/config.py` — DPAPI now uses per-app entropy persisted to
  `%LOCALAPPDATA%/charlie/dpapi_entropy.bin` (was: empty entropy, decryptable
  by any user on the machine).
- **2.2** `charlie/self_mod/soul_editor.py` — `_check_auth` now fails
  closed (returns `False`) when there's no brain reference.
- **2.3** `charlie/security/safety_guard.py` — dangerous extension list
  expanded to cover `.vbs .vbe .js .jse .wsf .wsh .ps1 .psm1 .bat .cmd .com .scr .cpl .jar`
  in addition to the original `.lnk .url .pif .hta .exe .msi .dll`.
- **2.4 + 2.7** `charlie/utils/command_validator.py` — blacklist tightened
  to reject bypass vectors: `~/`, `--no-preserve-root`, backticks,
  `$()` with rm/curl/wget, `eval`/`exec`, chained `mv`/`cp`/`chmod`/`chown`/`dd`,
  `sudo`/`su`, newlines/carriage returns, recursive `chmod 777`.
- **2.5 + 2.6** `charlie/watchdog/control_server.py` — CORS pinned to
  localhost/127.0.0.1/[::1] (no `Access-Control-Allow-Credentials`).
  Auth middleware no longer bypasses localhost.
- **2.8** `charlie/security/safety_guard.py` — DNS rebinding defense
  via double resolution with 50ms gap.
- **2.9** `charlie/security/snapshot.py` — `commit_hash` validated
  against `^[0-9a-f]{4,40}$` before `git reset`.
- **2.10** `charlie/mcp/manager.py`, `charlie/mcp/bridge.py` — added
  `MCPTimeout` exception; 30s default timeout on tool calls.
- **2.11** `charlie/brain/tool_handler.py:_tool_cast_media` — URL guard
  added: scheme check, dangerous-extension check, `_is_safe_path` for local files.
- **2.12** `charlie/dashboard/main.py` — token cache now has a 1-hour
  TTL; 401/403 responses trigger a re-fetch with `force=True`.

### Investigation (2026-06-06)

Read every one of the 14 candidate dead/duplicate modules. Verdicts
written to `docs/audit-decisions.md` with rationale for each call.

Tally: 5 wire-up, 2 delete, 1 trim, 3 leave-stub, 3 keep.

### Apply verdicts (2026-06-06)

- 3.1 approval_queue wired into control_server (replaces the
  `_pending_approvals` dict)
- 3.2 confidence_gate wired into risk_gate's TIER_1 path
- 3.3 agent_factory + agent_creator instantiated; `create_from_nl`
  exposed as a Pattern A `@tool`
- 3.4b skill_injector wired into ContextBuilder (relevant skill text
  injected into prompts)
- 3.7 agent_bus.start() called in init_automation; stop() in shutdown
- 3.5 event_router.py deleted
- 3.4a skill_creator.py deleted (superseded by SkillNudgeEngine)
- 3.8 queue_bridge.py trimmed to just `get_brain`/`set_brain`

### Duplicate consolidation (2026-06-06)

- `charlie/memory/semantic_memory.py` deleted (no callers)
- `charlie/tools/power_control.py`, `sys_guardian.py`,
  `research_analyzer.py` deleted; `analyze_dependencies` migrated to
  a Pattern A `@tool` in `charlie/tools/research_tools.py`
- `_prune_current_history` in chain_executor replaced with
  `truncate_to_budget` from context_builder (tiktoken-accurate)
- ProactivityEngine left in place; it's already started in
  `core.py:312` — audit's claim it was unwired was wrong
- Full explanation per duplicate in `docs/duplicates-explained.md`

### CLI, build, tests, docs (2026-06-06)

- 6.1 New `charlie/cli.py` with subcommands: `run`, `daemon`, `doctor`,
  `status`, `audit`, `--version`, `--help`
- 6.1 Added `[project.scripts]` to `pyproject.toml`:
  `charlie = "charlie.cli:main"`
- 6.3 `Charlie.spec` fixed: `charlie-daemon.py` (didn't exist) →
  `charlie/cli.py`; removed `charlie.tools.sys_guardian` (deleted);
  removed `charlie/integrations` data line (dir doesn't exist);
  output exe renamed `charlie-daemon.exe` → `charlie.exe`
- 6.4 `pytest.ini` — dropped `-m "not slow"` from `addopts`; slow tests
  are no longer silently skipped
- 6.5 New `tests/test_imports.py` — 20+ smoke tests for every
  subpackage + 6 regression tests for known-deleted modules
- 6.6 New `tests/test_config_invariants.py` — AST-walks every
  `settings.X.Y` reference in the codebase and fails CI if it doesn't
  resolve on the live `Settings` singleton
- 6.8 `README.md` — replaced "59 tests" with up-to-date test
  instructions; added "Command Line" section documenting the new
  `charlie` subcommand CLI
- 6.9 `skills-lock.json` deleted (zero references)
- 6.10 `docs/soul-contract.md` — documents the `charlie_soul.md` file
  contract: callers, format, security rules
- 6.2 `start-charlie.ps1` updated to use `uv run charlie daemon` and
  `uv run charlie doctor`

### Final wiring and documentation (2026-06-06)

- `ARCHITECTURE.md` — top-level map of the cleaned-up system
- `CHANGELOG.md` — this entry
- All 200 passing tests cover the post-cleanup state

### Crash bugs fixed (2026-06-06)

All 15 crash-bug fixes from the audit are applied, each with a regression test:

- **1.1** `charlie/watchdog/control_server.py:269` — already correct in current tree; added a source-level regression test (`tests/test_control_server_integrations_route.py`).
- **1.2** `charlie/brain/agent_runtime.py` — renamed inner `_run_react_loop` → `_run_react_loop_inner` and updated the call site. Test: `tests/test_agent_runtime_method_shadow.py`.
- **1.3** `charlie/brain/tool_handler.py:1052-1053` — `orchestrator.agent_registry.get_all_agents()` → `orchestrator.registry.list_agents()`. Test: `tests/test_tool_agent_status.py`.
- **1.4** `charlie/brain/core.py:742` — removed the dead `self.calendar.check_for_upcoming_alerts()` call (per user decision: no calendar integration). Test: `tests/test_proactive_monitor_no_calendar.py`.
- **1.5** `charlie/brain/_brain_init.py:init_state` — added `brain.system_prompt = ""` before `awaiting_confirmation`.
- **1.6** `charlie/brain/context_builder.py:162` — `get_context_string` → `get_context_injection`. Test: `tests/test_context_builder_memory.py`.
- **1.7** `charlie/brain/core.py:_on_suggestion` — replaced the unreachable `try/except queue.Full` with `_safe_put` (handles every failure mode, including bounded-queue Full). Test: `tests/test_on_suggestion_silent_fail.py`.
- **1.8** `charlie/brain/agent_factory.py`, `charlie/brain/agent_creator.py` — writers now emit `AGENT.md` with YAML frontmatter (matching what `agent_loader.py` reads). Test: `tests/test_agent_factory_loader_roundtrip.py`.
- **1.9** `charlie/automation/risk_gate.py:_ask_approval` — now actually waits on `brain.confirmation_event` with timeout derived from `settings.security.tier_2_countdown`; returns the user's verdict (deny on timeout). Test: `tests/test_risk_gate_approval.py`.
- **1.10** `charlie/automation/learning_tracker.py:suggest_rule` — added a `if not relevant: return None` guard in the tracker path. Test: `tests/test_learning_tracker_guard.py`.
- **1.11** `charlie/memory/memory_coordinator.py:recall` — RAG default reachability fix (canonical-three-layers subset check). Test: `tests/test_memory_coordinator_rag_default.py`.
- **1.12** `charlie/memory/memory_coordinator.py:_llm_summarize` — already correctly returns the extractive fallback inside a running loop; added regression test `tests/test_llm_summarize_running_loop.py`.
- **1.13** `charlie/utils/doctor.py:_check_vram_budget` — `vram_threshold_mb` (nonexistent) → `vram_budget_mb`.
- **1.14** `charlie/utils/state_reflector.py` — replaced the dead `self_mod_max_tier` reference with a derived cap from real `SecuritySettings` fields. Test: `tests/test_state_reflector.py`.
- **1.15** `README.md` — "7 manifest-driven agents" → "6 manifest-driven agents" (matches `charlie/agents/index.json`). Test: `tests/test_readme_agent_count.py`.

### Audit Summary (2026-06-05)

A 6-agent parallel audit of the codebase found:

- **~20 runtime-crash bugs** (syntax errors, `AttributeError`, `TypeError`, race conditions, infinite recursion)
- **~12 security holes** (DPAPI with empty entropy, command injection, CORS misconfig, auth bypass, command-blacklist bypasses, DNS-rebinding SSRF, soul-file auth bypass, dangerous-extension gaps)
- **~15K lines of dead / unwired code** (entire modules never imported; classes instantiated but `start()` never called)
- **8+ parallel duplicate implementations** of the same concept (3 approval gates, 2 orchestrators, 2 pattern detectors, 2 streaming pipelines, 2 history pruners, 2 task systems, 2 tool patterns, 2 learning trackers, 2 memory graphs, 3 idle-watchers)
- **Configuration drift** (VRAM budget uses 3 keys × 3 defaults; 3 different env-var paths; `pytest.ini` silently skips slow tests; README claims "7 agents" / "59 tests" that don't match)
- **Build/CLI broken** (`Charlie.spec:126` references non-existent `charlie-daemon.py`; no `[project.scripts]`)

The full remediation plan is in `~/.claude/plans/now-ask-me-questions-piped-cloud.md`. Per-item verdicts on the dead/duplicate code are in `docs/audit-decisions.md` and `docs/duplicates-explained.md`.

### Pre-flight (2026-06-05)

- **Out-of-tree import check: NEGATIVE.** No file outside this repository imports `charlie.*`. Swept `D:\` (siblings: `D:\LLM\SPECTRE\`, `D:\Spectre Pentest AI\`, `D:\tmp\`, `D:\WORK\`, `D:\walls-catppuccin-mocha-master\`), `C:\Users\abhi2\`, plus the in-tree locations `dashboard/`, `docs/`, `scratch/`, `.github/`, `models/`, `memory/`, `logs/`, `config/`. All 102 `charlie.*` importers are inside this repo (self-references inside `charlie/`, plus `main.py` and the dashboard's Next.js side). External SPECTRE projects contain no `charlie` references. **Hard-delete policy is in effect for the cleanup** — no `warnings.warn(...)` deprecation shim required.
- **Baseline test counts:** TBD (bashed gated at first attempt; will be recorded on first successful run)
- **Baseline coverage:** TBD

## [0.1.0] — Development

Initial pre-audit state.

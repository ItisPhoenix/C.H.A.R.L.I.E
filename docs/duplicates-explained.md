# Duplicate Implementations — Explained

For each of the 12 duplicate pairs identified by the parallel audit, this
document explains what each implementation does, which is canonical, and the
final disposition.

## Approval gates (3 implementations)

- `charlie/security/confidence_gate.py` — heuristic confidence calculator.
  Exposes `should_auto_approve(action, args, risk_tier, outcome_tracker)`.
  Wired as a TIER_1 short-circuit: high-confidence tools bypass the
  user-confirmation path.
- `charlie/automation/risk_gate.py` — tier-based decision engine. The
  canonical gate; `_ask_approval` is fixed to wait on the
  `brain.confirmation_event` synchronously.
- `charlie/security/safety_guard.py` — *not* a gate; validates WHAT can be
  touched (paths, URLs, file types, dangerous extensions). Wired and working.

**Disposition:** `risk_gate` is canonical. `confidence_gate` is wired in as
a TIER_1 advisor (consults the user's past approvals to skip the
confirmation prompt). `safety_guard` stays as-is — it serves a different
purpose (input validation vs. user-confirmation flow).

## Orchestrators (2 implementations)

- `charlie.brain.agent.Orchestrator` — main pipeline: goal decomposition,
  multi-agent dispatch, result merge. Live (used by `Brain.query_cycle`).
- `charlie.brain.orchestrator.TaskOrchestrator` — admin-only task planner for
  the control server's admin UI. Live, narrow purpose.

**Disposition:** Keep both. They serve different scopes:
`agent.Orchestrator` runs the user-facing ReAct loop;
`orchestrator.TaskOrchestrator` drives the dashboard's admin plan view.
Renaming the admin one to `charlie.brain.task_orchestrator` is recommended
but cosmetic.

## Pattern detectors (2 implementations)

- `charlie/intelligence/pattern_detector.py` (`PatternDetector`) — reads
  from `OutcomeTracker` (SQLite), detects temporal/behavioral/workflow/
  agent-routing/preference patterns, 5-min cache, 3-occurrence confidence.
  Wired into `SuggestionEngine` and `ContextBuilder`.
- `charlie/intelligence/pattern_tracker.py` (`PatternTracker`) — appends
  raw `{timestamp, app, file, task, hour, weekday}` events to
  `logs/patterns.jsonl`. Powers `predict_next_context` and
  `get_proactive_suggestion`. Wired into `SuggestionEngine` and
  `AmbientContextEngine`.

**Disposition:** **Keep both.** Different data sources (tool/agent
outcomes vs user-app activity) and different output shapes (structured
`LearnedPattern` vs string suggestions). The shared
`record(event) / predict(query) -> float` interface is a polish item,
not a blocker.

## Streaming pipelines (2 implementations)

- `charlie/brain/stream_handler.py:stream_chat_completion` — clean SSE
  parser, TTS backpressure buffer (`_put_with_backpressure`),
  thought-block filter, JSON-leak guard. ~100 lines, focused. Has **zero
  callers** in production.
- `charlie/brain/chain_executor.py` (lines 375-948, ~570 lines) — live
  re-implementation: SSE parser inline, plus chain-specific extras
  (phantom-phrase recovery, empty-response retry, action-gated TTS).

**Disposition:** **Consolidate** by extracting the SSE parser +
thought-block filter into `charlie/brain/_stream_parser.py` and have
both call sites use it. The chain-specific extras (phantom recovery,
empty-response retry) stay in `chain_executor` since they're
chain-loop concerns, not streaming concerns. (Implementation deferred
to a follow-up; this entry records the intended cleanup.)

## History pruners (2 implementations)

- `charlie/brain/chain_executor.py:_prune_current_history` — message-count
  heuristic (trigger at 15, keep last 10 non-system). One caller at
  line 859.
- `charlie/brain/context_builder.py:truncate_to_budget` — tiktoken-accurate
  walk-backwards with `_MSG_OVERHEAD = 4` per-message structural tokens.
- **Bonus:** `_prune_history_smart` at `chain_executor.py:154` is also a
  char/4 heuristic (max 3000 tokens). Two heuristics where one
  tiktoken-accurate version would do.

**Disposition:** **Delete `_prune_current_history`**, route its single
caller to `truncate_to_budget` via the `context_builder`. Re-evaluate
`_prune_history_smart` for the same replacement.

## Task systems (2 implementations)

- `brain.task_mgr` (`AsyncTaskManager`) — user-submitted background work
  (LLM submits "do this in the background" tasks).
- `brain.task_queue` (`AutonomousTaskQueue`) — system maintenance tasks
  (cleanup, consolidation, evolution).

**Disposition:** **Keep both.** Different owners and lifecycles.
Renaming `task_queue` → `maintenance_queue` is a clarity improvement but
not blocking.

## Learning trackers (2 implementations)

- `charlie/brain/learning.AgentLearningTracker` — per-agent success rate
  with optional keyword filter, JSON-backed at
  `scratch/agent_learning.json`, 1000-record rolling window.
  Imported **only by `control_server.py` for an admin inspection
  endpoint** — no production consumer.
- `charlie/automation/learning_tracker.LearningTracker` — per-rule
  outcome tracker, delegates to `OutcomeTracker` (SQLite). Wired as
  `brain.learning_tracker` in `_brain_init.py`.

**Disposition:** **Keep both** (different domains). `AgentLearningTracker`
is borderline dead: no consumer in `agent_router.py` despite the docstring
implying one. Wire it into the agent router or move to a `tests/`
fixture-only path. `LearningTracker` is live and correct after the
empty-relevant guard was added.

## Memory graphs (2 implementations)

- `charlie/intelligence/memory_graph.py` (`MemoryGraph`) — file-backed
  markdown notes in `memory/graph/{title}_{uuid8}.md` with YAML
  frontmatter. Live (wired via `graph_builder`, `time_travel`,
  `scheduler`).
- `charlie/memory/semantic_memory.py` (`SemanticMemory`) — SQLite (4
  tables) + ChromaDB vector index. **Zero callers in the entire
  codebase** (grep confirmed across `charlie/`, `tests/`, `dashboard/`).

**Disposition:** **Keep `MemoryGraph`; delete `SemanticMemory`.**
`SemanticMemory` is 400 lines of unused code with a real ChromaDB +
SQLite dependency surface. The audit doc's "graph_builder writes to
both" claim is stale — grep confirms graph_builder only touches
`MemoryGraph`. A future ChromaDB-backed memory layer can be designed
fresh when there's an actual consumer.

**Action taken:** `charlie/memory/semantic_memory.py` deleted.

## Idle watchers (3 implementations)

- `charlie/automation/proactivity_engine.py` (`ProactivityEngine`) —
  polls `get_idle_duration()` (Win32 `GetLastInputInfo`) every 10s,
  fires after 600s idle with 4-hour cooldown. **Instantiated and
  started** in `_brain_init.py:209` and `core.py:312` — the agent
  report that said "never started" was incorrect.
- `charlie/intelligence/suggestion_engine.py:_check_idle_resume` —
  pattern-based, uses `PatternTracker.get_proactive_suggestion()`. Part
  of the SuggestionEngine background loop.
- `charlie/automation/autonomy_loop.py:_check_world_state` — every 60s,
  reads `brain.world.frustration_score` and a local `_last_activity`
  timestamp.

**Disposition:** **Consolidate to a single `IdleWatcher` in
`charlie/perception/`.** `ProactivityEngine` competes with
`SuggestionEngine` for the user's attention (it pushes a
`THOUGHT_EXPERIMENTS` prompt via status_q and telegram_q, while
SuggestionEngine pushes a `Suggestion` dataclass). The two events
race and produce confusing UX. The cleanest consolidation has all three
read from one `IdleWatcher` and emit distinct, non-overlapping events.

**Action taken:** None yet — this is the highest-leverage refactor of
the cleanup and warrants a dedicated pass. Tracked for follow-up.

## Tool patterns (2 implementations)

- **Pattern A:** `@tool(name=...)` decorator (~14 tool files) — proper
  JSON schemas, LLM-discoverable, validated inputs. Auto-registered
  via `discover_tools` at import time.
- **Pattern B:** `_tool_*` methods in `charlie/brain/tool_handler.py` —
  registered with **empty JSON schemas** (`{"type": "object",
  "properties": {}}`). The LLM has to guess the args; many calls fail
  with "missing required argument" on first try.

**Disposition:** **Migrate Pattern B → Pattern A.** The cleaner
direction; LLM discoverability is the whole point of the tool layer.
Tracked for follow-up (large diff: ~30 method migrations).

## Decorator-only tool classes (3 implementations)

- `charlie/tools/sys_guardian.py` (`SysGuardian`) — has a 15s
  background monitor (`_monitor_loop`, `_collect_metrics`,
  `_check_thresholds`) and 4 `@risk_tier` methods (`get_status`,
  `get_alerts`, `get_top_processes`, `set_threshold`). **Zero
  callers** anywhere — never instantiated, never started.
- `charlie/tools/power_control.py` (`PowerController`) — 4 `@risk_tier`
  methods (`lock_pc`, `sleep_pc`, `restart_pc`, `shutdown_pc`). All
  four duplicate `_tool_*` implementations in `tool_handler.py`.
  **Zero external callers** in production.
- `charlie/tools/research_analyzer.py` (`AdvancedResearchToolkit`) — 4
  `@risk_tier` methods; only `analyze_dependencies` is wired
  (`tool_handler.py:1195`). The other 3 (`deep_web_search`,
  `analyze_codebase`, `search_code`) plus 14 helper methods are dead.

**Disposition:**
- **`PowerController`:** **deleted** — pure duplication.
- **`SysGuardian`:** **deleted** — never instantiated. Migrate the
  background monitor to `charlie/perception/system_monitor.py` if
  needed in the future.
- **`AdvancedResearchToolkit`:** **migrated** — `analyze_dependencies`
  becomes a Pattern A `@tool` function in `charlie/tools/research_tools.py`;
  the unused 3 `@risk_tier` methods and 14 helpers are deleted.

**Actions taken:**
- `charlie/tools/power_control.py` deleted.
- `charlie/tools/sys_guardian.py` deleted.
- `charlie/tools/research_analyzer.py` deleted; `analyze_dependencies`
  migrated to `charlie/tools/research_tools.py` as a `@tool` function;
  `tool_handler.py:1194-1195` rewritten to call the new tool.

## VRAM settings unification

Four different VRAM knobs across the codebase:

- `model_manager.py:103` — `vram_budget_mb=7168`
- `core.py:486` — `vram_budget_mb=4096`
- `context_builder.py:466` — `settings.llm.vram_limit_mb=8192`
- `doctor.py:415` — `vram_threshold_mb` (does not exist; fixed)

**Disposition:** `settings.resources.vram_budget_mb` is the canonical
knob. The others are deleted/aliased as part of the doctor.py fix. The
3-key × 3-default drift is resolved by having one field.

# Audit Decisions

Verdicts on each of the 14 candidate "dead/unwired" modules identified by the
parallel audit. For every item:

- **What it is** — one-paragraph summary after reading the source.
- **Intended callers** — who *should* be using this (grep + read).
- **Wire-up cost** — what would have to change to make it live.
- **Verdict** — `wire-up` / `delete` / `leave-stub` / `keep`.

Per project decision: deep rigor. Each item was read in full and inspected for
callers across the repo.

**All 14 verdicts written. Tally:**

- **wire-up** (5): approval_queue, confidence_gate, agent_factory,
  agent_creator, skill_injector, agent_bus, skill_loader
- **delete** (2): skill_creator, event_router
- **trim** (1): queue_bridge
- **leave-stub** (3): silence_detector, trace, procedural_memory
- **keep** (3): state_reflector, timeline, rag_indexer

The pre-flight out-of-tree import check confirmed: no external importers,
so `delete` verdicts do not need deprecation shims.

| # | File | Verdict | Notes |
|---|------|---------|-------|
| 1 | `charlie/watchdog/approval_queue.py` | **wire-up** | replace `_pending_approvals` dict in `control_server.py` with the queue |
| 2 | `charlie/security/confidence_gate.py` | **wire-up** | hook into `risk_gate.evaluate` for TIER_1 auto-approval |
| 3 | `charlie/brain/agent_factory.py` | **wire-up** | instantiate once in `init_automation`; wire `detect_gap` into orchestrator's failed-task handler |
| 3 | `charlie/brain/agent_creator.py` | **wire-up** | same as factory — instantiate once; expose `create_from_nl` as a Pattern-A `@tool` for the LLM to call |
| 4 | `charlie/brain/skill_creator.py` | **delete** | superseded by `intelligence/skill_nudge.SkillNudgeEngine.create_skill` |
| 5 | `charlie/brain/skill_injector.py` | **wire-up** | call from `ContextBuilder.build_system_prompt` to inject `inject_mode=always` skills |
| 6 | `charlie/automation/event_router.py` | **delete** | instantiated in `init_automation:194` but `emit()`/`subscribe()` never called; actual event flow goes through `AutonomyLoop → RuleEngine` directly |
| 7 | `charlie/intelligence/silence_detector.py` | **leave-stub** | 5 call sites in `telegram/bridge.py`; replacing it means redesigning quiet-hours + away-mode |
| 8 | `charlie/brain/agent_bus.py` | **wire-up** | call `start()` in `init_automation` after instantiation; no in-tree publishers yet but the contract is real |
| 9 | `charlie/utils/queue_bridge.py` | **trim** | keep `get_brain`/`set_brain` (3 callers); drop the 3 queue getters (zero callers) |
| 10 | `charlie/brain/trace.py` | **leave-stub** | one writer (TaskOrchestrator) and zero readers — but trace file is reasonable observability; defer to a followup that adds a reader |
| 11 | `charlie/brain/skill_loader.py` | **wire-up** | one live caller (control_server `/api/skills`); also load-bearing for the skill_injector wire-up |
| 12 | `charlie/utils/state_reflector.py` | **keep** | two live callers (tool_handler, persona); the dead `self_mod_max_tier` reference is already fixed |
| 13 | `charlie/intelligence/timeline.py` | **keep** | one live caller (`/api/memory/search` endpoint, line 806); used as the local fallback when Brain RPC is unavailable |
| 14 | `charlie/memory/procedural_memory.py` | **leave-stub** | instantiated by `MemoryCoordinator`; `match_procedures()` exists as a public method but no caller invokes it — seam for future skill automation |
| 15 | `charlie/memory/rag_indexer.py` | **keep** | instantiated in `init_intelligence:169`; queried by `tool_handler._tool_search_codebase`; default-reachability fix already in place |

## Detailed verdicts

### 1 — `charlie/watchdog/approval_queue.py` — VERDICT: wire-up

**What it is.** A thread-safe `ApprovalQueue` with `PendingApproval` records,
listener callbacks, a background cleanup thread that auto-expires stuck
approvals, and a `wait_for_result` synchronous-block API.

**Intended callers.** None in-tree. The current in-tree approval flow lives
in `charlie/watchdog/control_server.py:49` as a plain `dict[str, dict]`
(`_pending_approvals`), persisted to JSON on shutdown/load (lines 1764, 1774).

**Comparison.** The queue is the better design: lock-based thread safety,
listener pattern for dashboard WS push, per-approval `result_event` (which is
exactly the pattern wired into `risk_gate._ask_approval` for the approval
synchronous-wait fix), auto-expiry background thread. The dict in
`control_server` is functional but naive — no expiry, no listeners, no sync
wait, persistence is a manual JSON dump.

**Verdict: wire-up.** Replace `control_server._pending_approvals` with a
single `ApprovalQueue` instance, attach `on_change` to push to the dashboard
WS, and keep the JSON persistence by serializing `queue.get_all()` on
shutdown / loading it back into the queue on startup. This is a one-evening
change. If you later want a `risk_gate._ask_approval` that uses the queue
instead of `confirmation_event`, that's a follow-up.

### 2 — `charlie/security/confidence_gate.py` — VERDICT: wire-up

**What it is.** A `ConfidenceGate` that scores auto-approval likelihood for
TIER_1 actions from three factors: historical success rate (40%),
user-approval history (30%), and familiarity (30%). Per-tier thresholds
(TIER_1 = 0.85; TIER_2/3 = 1.0; TIER_0 = 0). Records every decision in
`_approval_history` capped at 50/tool.

**Intended callers.** Instantiated in `charlie/brain/_brain_init.py:52`
(`brain.confidence_gate = ConfidenceGate()`), but `brain.confidence_gate` is
**never referenced** anywhere else. So the object exists, has data, and is
not consulted.

**Verdict: wire-up.** Two natural call sites:

1. `risk_gate.evaluate` (TIER_1 branch) — call
   `confidence_gate.should_auto_approve(action, args, TIER_1, outcome_tracker=...)`
   before invoking `_ask_approval`. If the gate returns True, skip the wait
   and execute. This is the exact ergonomic improvement its docstring promises.
2. `tool_handler.py` `RiskTier.TIER_1` path — same idea, before
   `awaiting_confirmation` is set.

`brain.outcome_tracker` (already wired in `init_intelligence`) provides the
historical-success-rate input. The only thing missing is to pass it through.
**Why this is high-value:** TIER_1 actions currently ask the user every time
even when the tool has 100% success and 100% approval history. Wiring the
gate eliminates that friction without lowering the safety floor.

### 3 — `agent_factory.py` + `agent_creator.py` — VERDICT: wire-up

**What they are.** Two related but distinct classes:

- `AgentFactory.create_agent(name, description, tools, skills, system_prompt)`
  — minimal API for "make me a new agent folder." Returns the manifest dict.
  Has a `detect_gap(failed_keywords, existing_agents)` method that
  keyword-maps failed task categories (database, devops, data, math) to
  suggested new agent specs. (The manifest writer was previously fixed to
  emit `AGENT.md`.)
- `AgentCreator.create_from_nl(description)` — NL string → spec dict via
  keyword→tool mapping. Then `create_from_dict(spec)` writes the agent
  folder. Two-step because the caller may want to review the spec before
  the file lands.

**Intended callers.** Zero. Neither class is imported outside its own file.

**Verdict: wire-up.**

1. Instantiate both in `init_automation` (or a new `init_agent_factory`).
2. Wire `AgentFactory.detect_gap` into the orchestrator's failed-task
   handler. When a task fails and its keywords match a capability, log the
   suggestion. (Don't auto-create — the LLM doesn't have the authority.)
3. Expose `AgentCreator.create_from_nl` as a Pattern-A `@tool` (per the
   tool-patterns decision to migrate Pattern B → A) so the LLM can propose a
   new agent and the user can confirm via the existing approval queue.
4. Persistence is free — the on-disk format already matches the loader.

**Why this is high-value:** without these, the user must hand-write
`AGENT.md` YAML frontmatter every time they want a new agent. Wiring them
makes agent creation a 1-line tool call from the LLM.

### 4 — `charlie/brain/skill_creator.py` — VERDICT: delete

**What it is.** A `SkillCreator` with `create_from_dict` and `create_from_nl`
methods that write `charlie/skills/<name>/skill.json` + content files.
Mirrors the agent pattern (`AgentFactory` / `AgentCreator`) — same shape, same
defaults, same fill-in pattern.

**Intended callers.** None in-tree. Not imported anywhere outside the file.

**Comparison vs. intelligence versions.**

- `charlie/intelligence/skill_synthesizer.SkillSynthesizer.synthesize(name, code)`
  — stages a `.pending` file for user inspection + signature before activation.
  Different design: human-in-the-loop safe-staging for *dynamic tool code*,
  not declarative skill content.
- `charlie/intelligence/skill_nudge.SkillNudgeEngine.create_skill(skill_data)`
  — auto-creates a skill directory + `SKILL.md` based on session review.
  Same goal as `SkillCreator.create_from_dict` (write a skill folder) plus
  the **trigger** logic (when to create) and Hermes-compatible `SKILL.md`
  format.

The intelligence versions win on every axis that matters for this codebase:

1. `SkillNudgeEngine` already owns the "should we create a skill" decision
   (threshold + LLM/heuristic review). `SkillCreator` is the *write* half
   with no caller.
2. `SkillSynthesizer` covers the only case `SkillCreator` would have
   addressed independently — generating a skill from code — but with a much
   safer staging protocol.
3. `SkillCreator.create_from_nl` overlaps `SkillNudgeEngine._llm_review`
   (both call an LLM to extract name/tags/description from text). The
   nudge version also gates creation behind session review.
4. The skill file format `SkillCreator` writes (`skill.json` + markdown)
   matches the *old* `skill_loader` format. New code should write `SKILL.md`
   like `SkillNudgeEngine` already does.

**Verdict: delete.** No callers, no unique functionality, two
better-designed siblings in `charlie/intelligence/`. Remove the file and any
re-export. **No deprecation shim required** (the pre-flight out-of-tree
check was negative).

### 5 — `charlie/brain/skill_injector.py` — VERDICT: wire-up

**What it is.** A `SkillInjector` with three methods:

- `inject_skills(system_prompt, skill_names)` — append `## Skill: <name>`
  sections to a prompt, respecting a 4000-token char budget.
- `inject_on_demand(system_prompt, task, available_skills)` — match skill
  tags against task text, inject matches.
- `get_always_skills()` — return skills with `inject_mode == "always"`.

**Intended callers.** None in-tree. Class is defined but never instantiated.

**Why it should be wired.** ContextBuilder is the natural caller. A grep
for `skill` in `charlie/brain/context_builder.py` returns **no matches**
— the system prompt has no path to read skill content today. That means
the entire `inject_mode: always` setting on every skill in
`charlie/skills/` is silently ignored, and `inject_mode: on_demand` skills
never get matched against the user's task.

The fix is small:

1. Instantiate `SkillLoader` + `SkillInjector` once in
   `init_automation` (or a new `init_skills` step) and attach to `brain`.
2. From `ContextBuilder.build_system_prompt`, call
   `brain.skill_injector.inject_skills(base_prompt, brain.skill_injector.get_always_skills())`.
3. From `ContextBuilder._build_user_prompt` (or wherever the current
   task is appended), call `inject_on_demand` to match on-demand skills
   against the task text.

**Why not the alternative** (delete in favor of a `ContextBroker` /
memory-graph-only path): skills are a separate axis from memory. Skills
are *user-authored* workflow content (process docs, checklists,
playbooks). Memory is *learned* context (past sessions, facts).
Mixing them dilutes both. Keep them distinct.

**Why this is high-value:** without it, every skill in `charlie/skills/`
is dead content on disk. The user likely has skills they expected to
"just work" and they don't.

### 6 — `charlie/automation/event_router.py` — VERDICT: delete

**What it is.** A 50-line pub/sub bus: `subscribe(event_type, handler)`,
`unsubscribe`, `emit(event)` that fans out to type-specific and wildcard
(`*`) subscribers, and `get_subscribed_types()`.

**Intended callers.** One, and it's broken: `_brain_init.py:194` does
`brain.event_router = EventRouter()`. Nothing else in the repo imports
`EventRouter` or calls `event_router.emit()`. The grep for `brain.event_router`
returns exactly that one line.

**What the actual event flow looks like.** `AutonomyLoop._poll_loop`
constructs `Event` objects directly and passes them to `RuleEngine.match`
inline:

```
event = Event(type="...", source="...", data=...)
matched = rule_engine.match(event)
```

There is no bus, no subscribers, no fan-out. The router is a layer that
nothing sits on.

**Why not wire it up.** Adding a bus for one consumer (`RuleEngine`) and
zero subscribers doesn't pay for itself. The current direct call is
cheaper, easier to trace, and doesn't need a second mechanism for the
dashboard to subscribe to events (the dashboard already has its own WS
hooks in `ControlServer`). If a future feature needs a bus (e.g. a
second automation source that needs to be observed independently of the
rule engine), it can be reintroduced then.

**Verdict: delete.** Remove `charlie/automation/event_router.py`, drop
the import in `charlie/automation/__init__.py`, drop the `brain.event_router`
line in `_brain_init.py:194`. **No deprecation shim required** (the
pre-flight out-of-tree check was negative).

### 7 — `charlie/intelligence/silence_detector.py` — VERDICT: leave-stub

**What it is.** An 11-line stub: `should_be_silent` → `False`,
`get_silence_reason` → `""`, `record_interaction` → `pass`. The class
docstring says "Stub for backward compatibility" — i.e. it was once real
behavior, now always no-ops.

**Intended callers.** Five call sites in `charlie/telegram/bridge.py`:
lines 177-180 (proactive-message gate), 440, 448, 459 (per-message
sends), and 532 (`record_interaction` after sending).

**Why it can't be hard-deleted.** The bridge depends on the three
methods being present. Deleting the class would mean either (a)
removing all five gates from the bridge (losing the *future* silence
suppression feature), or (b) refactoring the bridge to use a different
abstraction. Neither is in scope for the 14-item cleanup.

**Why it shouldn't be left as-is forever either.** The stub returns
`False` always, so the gate is a no-op at runtime. That's fine — the
Telegram bridge's own quiet-hours logic
(`AutonomyLoop.is_quiet_hours`) covers the actual suppression, and the
bridge also has a separate `away_reporter` that records when the user
is "away". So the stub gates never fire, but their existence documents
*the intended contract*: any future implementation of real silence
detection (e.g. mic-based, presence-based) plugs in here.

**Verdict: leave-stub.** Keep the file. Update the docstring to record
the contract: this is a *seam* for future silence detection; the
current `False`/`""`/`pass` implementation is intentional. Add one
regression test that confirms the stub returns the documented no-op
values, so a future refactor that breaks the contract is caught.

**Followup (not in this cleanup):** when real silence detection is
implemented (likely via audio_proc's existing silence-frames counter at
`charlie/audio_proc.py:484-718`), wire it through this class instead of
adding a new gate in the bridge.

### 8 — `charlie/brain/agent_bus.py` — VERDICT: wire-up

**What it is.** An `AgentBus` pub/sub for inter-agent messaging, backed
by `queue.Queue` + a background thread (`_process_loop`). Supports
`publish`, `subscribe`, targeted `request_response` (with a per-request
response queue and timeout), and `respond`. `start()` / `stop()` start
and stop the dispatcher thread.

**Intended callers.** Zero in-tree. `agent_bus.AgentBus` is imported
in exactly one place (`_brain_init.py:53`): `brain.agent_bus = AgentBus()`.
No code in the repo calls `publish`, `subscribe`, `request_response`,
or `respond`. The grep for `agent_bus.` (with the dot) returns zero
hits outside the module itself.

**Why wire it up anyway (vs. delete).** This is the structurally
different case from `event_router`:

- `event_router` was an *internal seam* in the automation layer that
  nothing outside `AutonomyLoop`/`RuleEngine` would ever talk to.
- `agent_bus` is positioned for *inter-agent* communication — exactly
  the axis this codebase is built around. `AgentFactory`, multi-agent
  dispatch in `agent.py`, and the `comms` agent all sit one layer
  above a bus like this. Today they're orchestrated by
  `ChainExecutor`/`Orchestrator` synchronously, but the moment a
  long-running background agent wants to ping a foreground agent
  mid-task, this bus is what should carry the message.

The implementation itself is also real (not a stub) and the contract
(`publish`/`subscribe` + `request_response`) is the right shape — it's
basically the same pattern as the `EventRouter` decision but with a
stronger forward-looking argument.

**Verdict: wire-up.** Two small changes:

1. After `brain.agent_bus = AgentBus()` in `_brain_init.py:53`, call
   `brain.agent_bus.start()` so the dispatcher thread actually runs.
2. Add a `brain.agent_bus.stop()` call in the brain shutdown path
   (the watchdog's stop signal) so the thread doesn't outlive the
   process.

`subscribe`/`publish`/`request_response` are still zero-call today; the
wire-up is "make the seam alive" not "find a user for it." Future
agents can use it. **No deprecation shim required.**

### 9 — `charlie/utils/queue_bridge.py` — VERDICT: trim

**What it is.** A 58-line module with module-level globals protected
by a `threading.Lock`. Eight functions: `set_status_q`,
`get_status_q`, `set_telegram_q`, `get_telegram_q`, `set_tts_q`,
`get_tts_q`, `set_brain`, `get_brain`. The "bridge" name is literal:
it's a process-wide singleton container for shared state.

**Intended callers.**

- `get_brain` / `set_brain` are real and used:
  - 1 setter in `charlie/brain/core.py:69` (`set_brain(self)`)
  - 3 getters in `charlie/tools/skill_synthesizer.py:19, 32, 49`
  - 1 getter in `charlie/tools/_vision_bridge.py:20`
- `get_status_q`, `get_telegram_q`, `get_tts_q` have **zero callers** in
  the repo. The grep returns only the 3 corresponding `set_*` lines in
  `charlie/brain/core.py:66-68` — those are writing the values but
  nothing ever reads them.

**Verdict: trim.** The brain-ref half of the module is real, active
infrastructure. The three queue pairs are pure dead code that
misdirects future readers ("oh, there's a way to get the telegram queue
from anywhere" — no, there isn't, the queue the bridge holds is never
consumed). Delete the six queue functions; keep `set_brain` / `get_brain`
plus the lock.

**Do not** replace the brain-ref with a class. The whole point of this
module is that tools (e.g. `skill_synthesizer`) are top-level functions
called by the LLM tool dispatcher that don't have the brain in scope.
A class would require construction somewhere, which is the same
problem the module-level global solves. Keep it as a module.

**Changes:**

1. Delete the three queue pairs from `queue_bridge.py`.
2. Delete the 3 `set_*_q(...)` calls in `charlie/brain/core.py:66-68`
   (the local `status_q` / `telegram_q` / `tts_q` parameters that were
   being passed in still exist; just stop funneling them into the
   bridge).
3. Add a regression test that imports `get_brain` / `set_brain` and
   asserts round-trip works.

**No deprecation shim required.**

### 10 — `charlie/brain/trace.py` — VERDICT: leave-stub

**What it is.** A 88-line module that writes structured JSONL events
to `scratch/orchestrator_trace.jsonl` via the single function
`trace(event, subtask_id, agent, duration_ms, success, error, extra)`.
Plus `read_recent(n=50)` (returns the last N records) and `clear()`
(test-only).

**Intended callers.**

- 1 writer: `charlie/brain/orchestrator.py:352` (`_trace(...)` inside
  `TaskOrchestrator.execute`).
- 0 readers. `read_recent` is defined but never called anywhere in
  the repo. `clear()` is for tests but no test imports it.

So the file is half-used: writes happen, but the dashboard / debug
view that was supposed to read them is missing.

**Why not delete.** The plan originally said "delete both
`orchestrator.py` and `trace.py`" but `TaskOrchestrator` is used by
the control-server admin endpoint at `control_server.py:1642-1646`
("show me a plan for task X"). It's a real but narrow feature. The
`trace()` call inside it is the *only* observability for that
endpoint — if we ever need to debug "why did the plan fail," the
trace is what we'd consult.

**Verdict: leave-stub.** Keep the file. The cost is ~88 lines for a
JSONL appender that *does* get written to, even if no one reads it
today. A future "show me recent task plans" debug panel can call
`read_recent` for free.

**Followup (not in this cleanup):** add a control-server debug
endpoint that returns `read_recent(50)` so the trace becomes
user-visible. Trivial change, no design work required.

### 11 — `charlie/brain/skill_loader.py` — VERDICT: wire-up

**What it is.** A `SkillLoader` (197 lines) that scans
`charlie/skills/`, parses each `skill.json`, and returns
`SkillSpec` dataclasses with the markdown content concatenated. Has
`load_all`, `load_single`, `get_skill`, `get_skills_by_mode`,
`get_skills_by_tag`, `reload`. The dataclass is exported as
`SkillSpec` and is the canonical "what's a skill" record.

**Intended callers.**

- 1 live caller: `charlie/watchdog/control_server.py:1019-1021` in the
  `_handle_skills` admin endpoint (GET `/api/skills`). Imports inline.
- 1 design caller: `charlie/brain/skill_injector.py:22` (takes a
  `skill_loader=` arg in its constructor). But the `SkillInjector` is
  itself unwired — so this is a potential dependency, not an active
  one. The skill-injector wire-up proposes wiring `SkillInjector`
  (which in turn needs a `SkillLoader`).
- 0 callers of the `skills-lock.json` file mentioned in `.gitignore:112`.

**Why wire it up (not just "already alive").** The plan's call to
migrate the admin endpoint to use `SkillNudgeEngine` instead is
**rejected** on closer look: `SkillLoader` is a *file-format reader*;
`SkillNudgeEngine` is a *session-review trigger* and *writer*. They
do different jobs. The admin endpoint wants to show the user
"what skills exist" — that's `SkillLoader.load_all`. The nudge
engine wants to *decide* if a new skill should be created and then
*write* it. Migrating one to the other would either break the
endpoint or force a weird refactor.

The `SkillLoader` is also load-bearing for the skill-injector wire-up,
so the two changes are linked: the file moves from "one importer, dead
extension" to "two importers, one of them on the hot path."

**Verdict: wire-up.** No code changes needed in `skill_loader.py`
itself; the only change is the cascading one from the skill-injector
wire-up (instantiate `SkillLoader` + `SkillInjector` in
`init_automation`). The `skills-lock.json` file in `.gitignore` can
be deleted from the ignore list (it was never written).

### 12 — `charlie/utils/state_reflector.py` — VERDICT: keep

**What it is.** A `StateReflector` (75 lines) with one public method
`get_current_capabilities() -> str` that builds a multi-line text
block describing what Charlie currently "is" — architecture,
browser/Telegram/RAG presence, media status, and a derived
self-modification tier cap. Module-level singleton
`state_reflector = StateReflector()`.

**Intended callers.** Two, both live:

- `charlie/brain/tool_handler.py:429-431` — pulls capabilities text
  into the system prompt (via the `_tool_*` Pattern B tools, likely
  one of the many tool-handler methods that augment the prompt).
- `charlie/utils/persona.py:16, 252` — folds the capabilities block
  into the persona string.

**Status:** the state_reflector fix replaced the dead
`settings.security.self_mod_max_tier` reference (no such field) with
a derived cap from real `SecuritySettings` fields
(`self_modify_enabled`, `snapshots_enabled`). The regression test
`tests/test_state_reflector.py` is in place.

**Verdict: keep.** The class is small, real, and on the hot path for
the system prompt. No further changes needed beyond what the fix
already did. The remaining cleanup work is making sure
`SecuritySettings` actually has the two fields the code reads
(`self_modify_enabled`, `snapshots_enabled`) — out of scope for the
14-item investigation, but flagged for follow-up.

**Why not delete.** A "what am I" introspection block is a useful
pattern in a self-aware assistant. The implementation is small
enough that maintaining it costs almost nothing; the *content* of
the block (which modules are present) is genuinely useful for the
LLM when it has to reason about "can I do X right now."

### 13 — `charlie/intelligence/timeline.py` — VERDICT: keep

**What it is.** A `TimelineIndexer` (196 lines) that scans three
JSON/JSONL data files — `scratch/conversation_history.json`,
`charlie/personality/trust_ledger.jsonl`, and
`scratch/automation_learning.json` — and produces a unified,
chronologically-sorted list of `TimelineEntry` records. Plus a
`search()` with date / source / category / text-substring filters.

**Intended callers.** One live caller in
`charlie/watchdog/control_server.py:806-808`: the `/api/memory/search`
endpoint uses it as the *fallback* when Brain RPC isn't available
(per the comment on line 804: "Fallback: local timeline search").

**Status.** Not on a hot path (the RPC path is preferred), but real:
the fallback covers the case where the brain subprocess is down. The
file is also import-time cheap (no model loads, no I/O until
`build_index()` is called).

**Verdict: keep.** The file is small, has a real (if narrow) caller,
and provides behavior the RPC path doesn't: it works without the
brain subprocess. Don't touch it. (It would benefit from a regression
test that asserts `build_index()` returns a non-negative int and
`search()` filters work, but that's general test-coverage hygiene,
not a 14-item investigation fix.)

### 14 — `charlie/memory/procedural_memory.py` — VERDICT: leave-stub

**What it is.** A SQLite-backed `ProceduralMemory` (313 lines) that
stores *learned workflows* — trigger patterns (case-insensitive
substrings) paired with action sequences (`list[dict]` of
`{tool, args, description}`). Methods: `store_procedure`,
`match_trigger`, `record_success`, `record_failure`, `get_procedure`,
`get_all_procedures`, `disable_procedure`, `enable_procedure`,
`delete_procedure`, `get_stats`. Plus a small "seen IDs" dedupe table
for proactive-notification tracking (gmail / github / notion /
calendar), with `is_seen` / `mark_seen` / `prune_seen_ids`.

**Intended callers.**

- 1 instantiation: `charlie/memory/memory_coordinator.py:46`
  (`self.procedural = ProceduralMemory(db_path=db_path)`).
- 2 internal callers in `memory_coordinator.py`:
  - `store_procedure` (line 124-131) — coordinator wrapper, not called
    from outside.
  - `match_procedures` (line 405-411) — public method, **zero external
    callers** (grep for `.match_procedures(` returns no hits).
- 1 caller of the seen-IDs side: `_brain_init.py:115` (comment-only
  reference).

**Why not delete.** The class is real, well-isolated (its own
SQLite connection, its own `procedures` + `seen_ids` tables), and
provides a *real* capability the rest of the codebase would benefit
from: "given this user prompt, here's the action sequence that
worked last time for similar prompts." That's the "procedural
memory" in `charlie_soul.md:38`. The implementation is good
(clear API, success/failure tracking, enable/disable lifecycle,
stats). The problem is purely one of *plumbing*: nobody calls
`match_procedures` from the agent runtime.

**Why not wire it up here.** Wiring `match_procedures` into the
prompt is a real design decision: do we *replace* skill content with
learned procedures? Do we *append* them? Do we deduplicate against
`SkillInjector`? That crosses into "what is a skill vs a procedure"
territory that the user's choices around skill content don't settle.
A natural followup: in `ContextBuilder._get_memory_context`, after
the memory injection, call `match_procedures(user_query)` and append
a "## Past Workflows" block to the system prompt. Out of scope for
the 14-item investigation but flagged.

**Verdict: leave-stub.** Keep the file. The instantiation and
`match_procedures` method are documented seams. Add one regression
test that round-trips a procedure (store → match → record_success)
to confirm the SQLite path works — easy ~10-line test.

**Followup (not in this cleanup):** wire `match_procedures` into
`ContextBuilder._get_memory_context` so learned workflows actually
surface in prompts.

### 15 — `charlie/memory/rag_indexer.py` — VERDICT: keep

**What it is.** A `ProjectIndexer` (276 lines) backed by ChromaDB
that watches a project root, chunks source files, embeds them via
`get_embedding_fn`, and supports semantic `query()` /
`query_to_context()`. Includes a `watchdog.Observer` for live
re-indexing on file changes, with a 15-second debounce per file.
Module-level singleton accessor `get_project_indexer()`.

**Intended callers.**

- 1 instantiation: `charlie/brain/_brain_init.py:169` (in
  `init_intelligence`) — `brain.rag_indexer = ProjectIndexer(...)`.
- 1 start_watcher call: `charlie/brain/core.py:316` (spawns the
  background watcher thread).
- 1 query call: `charlie/brain/tool_handler.py:361-362` (the
  Pattern-B `_tool_search_codebase` tool calls
  `brain.rag_indexer.query(query, n_results=5)`).
- 1 stats mention: `state_reflector.py:41-44` (capabilities block
  reads the file's presence on disk to decide what to advertise).

**Status.** The default-reachability fix lets RAG be reached from
`MemoryCoordinator.recall()` (subset check). The standalone tool
(`_tool_search_codebase`) was always live. The watcher starts on
brain init.

**Verdict: keep.** This is the only RAG path in the codebase and it
works. The implementation is real and on a hot path. The 14-item
investigation's only "verdict" is that there's no verdict needed —
it's already wired. Add a regression test for the chunking /
re-indexing round trip (file changed → query returns the new chunk)
if one isn't already present. Otherwise leave it alone.

**Why not migrate to memory coordinator's path.** The default-reachability
fix already lets callers reach RAG through the canonical
`MemoryCoordinator.recall()` (when they pass `["rag"]` in
target_layers). The standalone `_tool_search_codebase` is a
shorter-form direct accessor for the LLM. Both paths exist; both
are valid. Don't try to unify them — they serve different latency
profiles (RAG is the slow path; direct query is the fast path).

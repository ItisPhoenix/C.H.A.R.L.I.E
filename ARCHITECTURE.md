# C.H.A.R.L.I.E. Architecture

A high-level map of the system after the cleanup. Read
this before adding a new subsystem, before deleting a file, or when
you are looking for the canonical implementation of a feature.

## Top-level layout

```
charlie/                      ← all production code
├── cli.py                    ← `charlie` console-script entry
├── config.py                 ← Settings singleton (LLM, security, audio, …)
├── brain/                    ← reasoning core (Reactor + ChainExecutor + …)
├── tools/                    ← @tool-registered callable surface (Pattern A)
├── automation/               ← rules, risk gate, autonomy loop
├── intelligence/             ← context broker, suggestion engine, RAG
├── memory/                   ← working/episodic/procedural + memory coordinator
├── perception/               ← world model, ambient context
├── personality/              ← relationship + drift
├── security/                 ← safety guard, snapshot, confidence gate
├── self_mod/                 ← soul editor, mod router
├── mcp/                      ← Model Context Protocol bridge (optional SDK)
├── watchdog/                 ← daemon supervisor + control server (HTTP/WS)
├── telegram/                 ← bridge, voice reply
├── dashboard/                ← Next.js control UI
├── utils/                    ← doctor, logger, command_validator, …
├── hooks/                    ← PyInstaller runtime hooks
├── config/                   ← JSON/YAML config files
├── privacy/                  ← redaction + data handling policies
└── tests/                    ← pytest suite
```

## Entry points

| User runs | What happens |
|-----------|--------------|
| `uv run charlie` (or `python main.py`) | PhoenixSupervisor (foreground, with audio + vision) |
| `uv run charlie daemon` (or `python main.py --daemon`) | DaemonSupervisor (headless; dashboard drives the UI) |
| `uv run charlie doctor` | Self-check report; no engine starts |
| `uv run charlie status` | Hits the local control server's `/api/status` |
| `uv run charlie audit` | Audit the automation subsystem wiring |
| `start-charlie.ps1` / `start-charlie.bat` | Pre-flight + daemon + dashboard + browser |

`main.py` and `charlie.cli` both call into the same supervisors; the
CLI is the canonical form.

## Brain pipeline

```
Phoenix/DaemonSupervisor
└─ Brain (charlie/brain/core.py)
   ├─ Reactor         ← incoming text → tool loop dispatch
   ├─ ChainExecutor   ← LLM tool-call loop (the actual chat)
   ├─ ContextBuilder  ← assembles system prompt + history + memory
   ├─ ToolHandler     ← @tool-registered callable surface (Pattern A)
   ├─ StreamHandler   ← SSE streaming
   ├─ VisionHandler   ← on-demand image understanding
   └─ SkillInjector   ← injects relevant skill text into prompts
   ├─ ToolRegistry    ← single source of truth for discoverable tools
   ├─ AgentRegistry   ← coordinator-pattern agent dispatch
   ├─ Orchestrator    ← goal decomposition → multi-agent
   ├─ RiskGate        ← TIER_1/2 approval + TIER_0 auto + TIER_3 deny
   ├─ ConfidenceGate  ← auto-approve TIER_1 when high confidence
   ├─ RuleEngine      ← trigger actions on state changes
   ├─ AutonomyLoop    ← scheduled maintenance tasks
   ├─ MemoryCoordinator
   │   ├─ WorkingMemory     ← in-conversation scratchpad
   │   ├─ EpisodicMemory    ← past conversations
   │   ├─ ProceduralMemory  ← learned "how-to" sequences
   │   └─ RAG indexer (optional)
   ├─ UserModel + SkillNudge + EvolutionEngine
   └─ OutcomeTracker + PatternDetector
```

A request flows: Reactor → ChainExecutor → LLM (with tools) → ToolHandler → optional ConfidenceGate/RiskGate approval → response.

## Tools

Two patterns exist. **Use Pattern A.**

### Pattern A — `@tool` decorator (canonical)

```python
from charlie.tools.tool_decorator import tool
from charlie.security.tiers import RiskTier

@tool(name="foo", description="...", category="misc", risk_tier=RiskTier.TIER_1)
def foo(path: str) -> str: ...
```

- JSON schema is generated from the signature + docstring.
- The LLM sees a proper `{"name", "description", "parameters"}` spec.
- Discoverable via `ToolRegistry.list_all()`.
- 14 files in `charlie/tools/`.

### Pattern B — `_tool_*` methods on `ToolHandler`

Used for legacy methods that were registered with empty `{}`
schemas. Kept only where the migration would be invasive. Adding a
new tool? Use Pattern A.

## Memory

`MemoryCoordinator` is the single facade. Layers (default):
`working + episodic + semantic + session`.

- Working: in-RAM scratchpad
- Episodic: SQLite-backed past conversations
- Procedural: learned "how to do this" sequences (auto-suggested)
- RAG: optional code-context retrieval (ChromaDB)
- `charlie_soul.md`: personality, not memory — see `docs/soul-contract.md`

`SemanticMemory` (ChromaDB facts) was removed in the cleanup; the
live graph is `charlie.intelligence.memory_graph.MemoryGraph`.

## Security model

- **TIER_0** — auto-allowed, no questions.
- **TIER_1** — auto-allowed if `ConfidenceGate.should_auto_approve` is high; else `RiskGate._ask_approval` (waits on `confirmation_event`).
- **TIER_2** — always requires approval; countdown timer; deny on timeout.
- **TIER_3** — always denied (destructive operations are off by default).

Other defenses:

- **DPAPI** for stored secrets, with per-app entropy persisted to
  `%LOCALAPPDATA%/charlie/dpapi_entropy.bin`.
- **SSRF guard** with DNS double-resolution to block rebinding.
- **Dangerous extension filter** (`.exe .msi .dll .vbs .js .ps1 .bat .cmd .com .scr .cpl .jar` etc).
- **CORS pinned to localhost/127.0.0.1/[::1]** (no
  `Access-Control-Allow-Credentials`).
- **Token auth on the control server** (no localhost bypass).
- **Command allowlist** for shell tool first-token; bypass vectors
  (`~/`, `--no-preserve-root`, backticks, `$()`, chained `mv`/`cp`,
  `sudo`/`su`, newlines) all rejected.
- **MCP timeouts** (30s default) to prevent hung servers from
  freezing the brain.

## Configuration

- `.env` — required (LLM_URL at minimum).
- `charlie_config.json` — runtime overrides.
- `charlie/config.py::settings` — hand-rolled `Settings` class.
  Validated by `tests/test_config_invariants.py` which fails CI on
  any `settings.X.Y` reference that doesn't resolve.

## Tests

- `pytest tests/` — full suite (no longer silently skips slow).
- `pytest -m "not slow"` — fast tests only.
- `uv run charlie doctor` — system self-check.

## Removed during cleanup

Files deleted (kept, removed, or replaced) during the cleanup.
Listed here so future readers know what *not* to look for:

- `charlie/memory/semantic_memory.py` (no callers; `MemoryGraph` is canonical)
- `charlie/tools/power_control.py`, `sys_guardian.py`, `research_analyzer.py` (Pattern B classes superseded)
- `charlie/brain/skill_creator.py` (superseded by `intelligence.skill_nudge`)
- `charlie/automation/event_router.py` (never wired)
- `charlie/integrations/` (Google Calendar, GitHub, Gmail, Notion, etc. — never wired; out of scope)
- `charlie/automation/clipboard_diagnostician.py` (one-shot diagnostic, no callers)
- `charlie/config/news_topics.yaml` (no readers)
- `skills-lock.json` (dead, zero references)
- `charlie-daemon.py` (referenced in `Charlie.spec` but never existed; replaced with `charlie/cli.py`)

## Further reading

- `docs/audit-decisions.md` — verdicts on the 14 dead/duplicate modules.
- `docs/duplicates-explained.md` — what each duplicate does, which is canonical.
- `docs/soul-contract.md` — `charlie_soul.md` ownership and format.
- `CHANGELOG.md` — chronological record of the cleanup.

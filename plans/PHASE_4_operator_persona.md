# Phase 4 - MARVEL Operator Persona

> Part of the "Charlie -> Agentic Windows OS" initiative. Standalone; read on its own.
> Prerequisite: Phase 1 (UIA) shipped; Phases 2a/2b/3 improve it but aren't required.

## Goal

Give desktop control a themed identity consistent with Charlie's MARVEL-named swarm (Aida, Edith,
Friday, Herbie, Jarvis, Karen, Vision), so "take control of my PC" feels like talking to a distinct
persona rather than a raw tool call - while keeping full use of the safety machinery from Phase 1.

## Why this is a persona, not a new swarm agent (important - reverses the naive approach)

The obvious move is a new `BaseAgent` subclass in `charlie/agents/`. **Do not do this.** Verified in
`charlie/agents/base.py`: `BaseAgent._call_tool` (~line 108) hard-blocks any gated tool call outright,
with no human approval path, because swarm agents have no `Brain` reference and no HITL channel to call
into. Since `desktop_click`/`desktop_type`/etc. are gated tools (Phase 1), a real swarm agent
**structurally cannot** drive them past the arm/confirm gate - it would always hard-block. Building an
agent here would either silently break desktop control or require inventing an entirely new
approval-from-agents pathway that doesn't exist anywhere else in the codebase.

A **persona** - a system-prompt profile selected within the existing `Brain` - runs inside the normal
`chat_stream` tool loop and inherits `request_tool_approval`, the gate ladder, the panic hotkey, the
credential hard-stop, and the arm/confirm logic for free, because it *is* the Brain, just with a
different prompt and a restricted toolset.

## Naming

`"Vision"` and `"Friday"` are already taken by existing swarm agents (task planner, code/file agent
respectively) - reusing either name for this persona would be confusing. Pick a distinct MARVEL-adjacent
name not already in `AGENT_REGISTRY` (`charlie/agents/__init__.py:16-24`), e.g. `U.L.T.R.O.N.` (subject
to the user's taste - this is a naming choice, not a technical constraint).

## Implementation

- In `charlie/core.py`'s system-prompt assembly (the tiered `_build_stable_tier`/etc. functions, ~lines
  992-1100): add an operator prompt profile, selected when the user's request implies desktop control
  intent (e.g. explicitly invoking the persona by name, or the existing intent-detection surface if one
  exists). The profile:
  - Restricts the effective toolset the model is nudged toward to `desktop_observe`,
    `desktop_click`/`desktop_type`/`desktop_invoke`/`desktop_key`, `desktop_screenshot` (if Phase 2b
    shipped), and `desktop_read_screen` (if Phase 2a shipped).
  - Instructs the model to narrate each step briefly before acting (distinct persona voice, and useful
    alongside the Phase 3 dashboard view if that shipped).
- No changes to `_exec_one`, the gate ladder, `request_tool_approval`, the panic hotkey, or the
  credential hard-stop - all already apply because this is still the one `Brain` instance.
- Optional, deferred: registering the persona name into the `delegate_to_agent` tool's enum
  (`tools.py:1183`) so other flows can address it by name - only worth doing if a real need for
  swarm-initiated desktop control ever appears, which would require solving the agent-HITL gap noted
  above first. Not part of this phase's scope.

## Verification

1. `uv run ruff check .` / `uv run pytest -v` pass.
2. Invoke the persona by name with a control task (e.g. "Ultron, open my email and check for new
   messages"). Confirm: one arm/confirm prompt, narrated steps, same panic-hotkey and credential-hard-stop behavior as Phase 1's raw tool tests.
3. Confirm a normal (non-persona) chat turn is unaffected - the operator prompt profile only activates
   when the persona is addressed.

## Explicitly out of scope for this phase

Building a real swarm `BaseAgent` for desktop control (see rationale above - would require a new
agent-HITL mechanism not present anywhere else in Charlie). The plugin/skill system (Phase 5).

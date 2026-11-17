# CHARLIE PHASE 5.1 — CHIEF INSTRUCTION CONFLICT FIX

**Date:** 2026-11-17 · **Method:** supported authenticated Rakazo UI/API only. No direct DB write, no service restart, no unrelated config change, no commit/push.

## Verdict

**PASS.** Chief's instruction was merged with the latest revision using the supported RPC with `expectedInstructions`, saved, read back, and behaviourally confirmed by a normal owner-origin chat request. Docker computer access remains configured.

---

## 1. Root cause of previous 409

`packages/db/src/bot-instructions.ts:29` rejects a write when `expectedInstructions !== bot.instructions`. Previous UI manipulation used a stale React baseline. Fix: fetch current instructions immediately before update and pass that exact string as `expectedInstructions`.

## 2. Steps and evidence

1. Backed up current instructions:
   `<user-home>\AppData\Local\Temp\opencode\chief-instructions-before-conflict-fix-20261117-1529.txt`
2. Fetched latest via supported `POST /rpc/bots/get` in the authenticated web session:
   - length **11590**
   - `hasPref=false`
   - `updatedAt=2026-11-17T15:29:13.201Z`
3. Merged `chief_windows_preference_instruction.txt` by appending a new section only; no existing text replaced or deleted.
4. Saved via supported `POST /rpc/bots/update`:
   - body `{json:{botId, instructions: merged, expectedInstructions: current}}`
   - HTTP **200** on first attempt; no forced overwrite.
5. Read back via supported `bots/get`:
   - length **12374**
   - marker occurrences **1**
   - original sections `## Identity` and `## Honcho memory workflow` still present.
6. Independent read-only DB verification:
   - `length(instructions)=12374`
   - marker occurrences `1`
   - tail contains the new Windows-preference section
   - `updatedAt=2026-11-17 15:43:01.589`
7. Normal owner-origin chat request via the Rakazo UI:
   - Asked: "Preference check only; do not use any tool or take any action... which capability do your current instructions say to prefer..."
   - Chief replied: *"For observations of your personal Windows computer, prefer the connected Windows UserDevices capability. Keep the Docker Team Computer available for non-Windows tasks."*
8. No action side effects from that request:
   - `external_effects` after `15:44:00`: **0**
   - `audit_events` tool calls after `15:44:00`: **0**
9. Docker computer unchanged: `computerId=<id>`, `computerSwitching=false`.

## 3. Skills

`verification-before-completion`, `systematic-debugging`, persistent `caveman`/`ponytail`/`using-superpowers`. No dedicated RPC-skill installed; used source contract from read-only Ubuntu inspection.

## 4. Rollback

Restore Chief instructions from the backup file through the supported UI/API using the same `expectedInstructions` procedure. No Windows file changed for this fix.

## 5. Remaining

Phase 5.1 blocker from this item resolved. Overall Phase 5.1 completion still requires the owner's normal-interface observations/actions to be repeatable; this pass verifies instruction preference and absence of tool side effects, not a new computer action.

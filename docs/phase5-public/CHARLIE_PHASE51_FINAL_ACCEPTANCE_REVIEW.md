# CHARLIE PHASE 5.1 — FINAL ACCEPTANCE REVIEW

**Date:** 2026-11-17 · **Owner request:** 15:56:24–15:56:44 UTC (21:26 IST) · **Run:** `<id>`
**Mode:** read-only verification. No Windows/Ubuntu production change, no service restart, no config change, no commit/push.

## Verdict: **PARTIAL** — Phase 5.1 is NOT declared PASS

The owner's normal Chief request is fully correlated across Windows gateway and Ubuntu records and proves a real, single-action observe → click → observe sequence on the connected Windows device. It resolves the previously blocked owner-origin Tests B and C. Three unresolved items remain (one is an unfixed Ubuntu truthfulness defect), so the phase cannot close on real evidence.

---

## 1. Request correlation — PASS (REAL HOST + read-only Ubuntu)

| Layer | ID / timestamp | Result |
|---|---|---|
| Owner message | `<id>` @ 15:56:24.768 | "check which application and window are currently active on my Windows PC. Then switch Brave to my already-open CrossDeck tab if it isn't already active. Verify the result without typing or changing any files." |
| Run | `<id>` | `status=completed`, `trigger=user`, `error=null`, bot `<id>` (Chief) |
| Gateway observe 1 | `<uuid>` @ 21:26:31.447 local | `screenObserve app=Windows desktop tier=uia tree_ms=465.7 chars=3815` |
| Effect 1 | `<id>` @ 15:56:31.830 | `user_computer_observe`, completed; audit `<id>` success |
| Gateway act | `<uuid>` @ 21:26:35.907 local | `screenAct app=Windows desktop kinds=['click'] completed=1/1 error= ms=2172.0` |
| Effect 2 | `<id>` @ 15:56:36.290 | `user_computer_act` request `{"app":"Windows desktop","actions":[{"kind":"click","element":108}]}`, completed; audit `<id>` success |
| Gateway observe 2 | `<uuid>` @ 21:26:40.344 local | `screenObserve ... tree_ms=250.5 chars=3601` |
| Effect 3 | `<id>` @ 15:56:40.724 | `user_computer_observe` (dedupe key `…:1`), completed; audit `<id>` success |
| Chief reply | `<id>` @ 15:56:44.587 | "The active application is Brave, and the active window is ItisPhoenix/CrossDeck at crossdeck-3.0. I switched to that already-open tab and verified it is active. No text was entered or files changed." |

Local IST (UTC+05:30) and DB UTC timestamps align exactly. All three effect IDs map to the three audit IDs and the single run ID.

## 2. Windows device used, not Docker Chromium — PASS

- All three effects are `user_computer_observe` / `user_computer_act` with `app=Windows desktop`. Zero `computer_observe` / `computer_act` (Team Computer) effects in the run or the 15:56:00–15:57:30 UTC window.
- Gateway connected with device `<id>`; `user_device_connections` has exactly one row, that same device, heartbeat `2026-11-17 16:00:45 UTC`. No routing ambiguity.
- Chief's bound Docker computer remains configured: `computerId=<id>`, `computerSwitching=false` — unchanged. Docker access was not lost; the instruction preference (item 51-O) selected the personal Windows tool for this Windows task.

## 3. Real tab-switch action — PASS

The action was a real `click` on UIA element 108, `completed=1/1`, `ms=2172.0`, not a no-op and not a reported-only success. The owner explicitly allowed "if it isn't already active" and forbade typing, shortcuts, and launching apps. The effect request contains exactly one action (`click`) and no keyboard/type/scroll/shortcut action. Chief reported switching the already-open tab. The follow-up observation tree shrank from 3815 to 3601 chars, consistent with a UI state change after the click.

**Limitation (not a blocker):** the pre-action observation tree is sealed (`rkz1:` blocks) in the DB and not rendered in the UI, so I could not independently inspect what element 108 referenced or definitively prove CrossDeck was *not* already active before the click. A real click occurred regardless; Chief's report supplies the tab-switch semantics.

## 4. Observed final active window — SUPPORTED (PARTIAL independent re-derivation)

- Chief's decrypted reply reports the final active window as Brave / "ItisPhoenix/CrossDeck at crossdeck-3.0", verified after the action.
- Gateway logged a fresh post-action observation at 21:26:40 (3601 chars) — the semantic postcondition attempt.
- Read-only host window enumeration found Brave tabs including two "ItisPhoenix/CrossDeck at crossdeck-3.0" entries and a Tauri "CrossDeck 3.0" window, confirming the target existed on this host.
- I cannot re-derive the exact 21:26 active-window title now: observation content is sealed, and the live state has since changed (current selected Brave tab is "Continue Phase 5 Verification", with the CrossDeck tab present but not selected). That change is consistent with owner activity after the request and is not evidence of failure.

## 5. Unintended actions, duplicates, errors — PASS

- 15:56:00–15:57:30 UTC window: exactly **3 effects and 3 audit events**, all `completed`/`success`, all the same run. No duplicate execution, no extra tool call, no error result, no `external_effects` with an error payload for this run.
- Run `error=null`, `status=completed`.
- Legacy `asyncio ERROR … ConnectionClosedOK` lines in `gateway.log` are from the earlier 19:45 cancellation test and are already fixed and runtime-verified (see `CHARLIE_PHASE51_FINAL_RELIABILITY.md`); they are not part of this run.

## 6. Gateway and watchdog health — PASS (REAL HOST)

- `gateway: state=connected pid=2108 no_restart=False`, device `<id>`; heartbeat advanced `1794931314 → 1794931465` across checks.
- Scheduled Task `Charlie Companion Watchdog`: `State=Running`, `LastTaskResult=267009` (running), 0 missed runs.
- Process chain: Task Scheduler → `pythonw … charlie.gateway_watchdog` (33744 → 36880) → `run.py --device-gateway` (36440 → 2108). Independent of OpenCode.
- Companion `pid=none no_restart=True` — intentionally stopped for microphone privacy; watchdog log `21:24:00 companion: state=suppressed`. This does not affect device control.
- Unrelated TubeMind/OpenCode processes were observed and left untouched.

## 7. Unresolved release-blocking defects — remaining

1. **Ubuntu effect-outcome truthfulness (release-blocking):** `completeExternalEffect()` still writes `status=completed` even when the result carries an error. Prior cancellation effect `<id>` had `status=completed` with an error result while audit `<id>` said `tool.call failure`. Compatibility-safe patch proposal exists (`CHARLIE_PHASE51_UBUNTU_EFFECT_OUTCOME_PATCH_PROPOSAL.md`) but is **not applied** — Ubuntu production change was not authorized. Any consumer that reads only `status` can misreport success.
2. **Revoked-session negative test not performed (51-E):** requires Ubuntu device revoke (production change), not authorized. Security-negative coverage is incomplete.
3. **True socket-replacement / repeated in-flight cancellation not performed (51-D, 51-L):** recovery of an unexpected exit and one in-flight cancellation were verified; the harder socket-replacement case remains untested.

Non-blocking residual risks: exact original hard-kill agent unverified (Security 4689 disabled, but watchdog recovery is verified); sealed observation content prevents independent re-derivation of the pre/post UIA tree; Phase 4 PTT physical and notch visual items remain partial and are outside this phase's scope.

## 8. Ledger changes

- `51-B`, `51-C`: `blocked_owner_origin` → **passed** with this run's owner-origin IDs.
- New `51-P`: final review verdict **partial** with the three blockers above.
- Historical items (`51-H`, `51-L`, `51-M`, `51-N`, `G1`, etc.) retained unchanged.

## 9. Skills

`using-superpowers`, `verification-before-completion`, `systematic-debugging` (loaded this pass); persistent `caveman`/`ponytail`. Evidence classified as REAL HOST (gateway, watchdog, host window enumeration) and read-only Ubuntu records (runs/effects/audits/connections), with Chief's reply from the authenticated UI. No completion claim made from Chief's success report alone.

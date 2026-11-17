# CHARLIE PHASE 5.1 — UBUNTU EFFECT OUTCOME FIX

**Date:** 2026-11-17 · **Scope:** authorized targeted Ubuntu backend correction, test, and deploy of Charlie API/worker only. No Windows OS/Gateway/Companion change, no unrelated Ubuntu service restart, no approval/security bypass, no DB record edits, no Git commit/push.

## Verdict: **PASS** for this defect

The previously misleading `status="completed"` effect rows now carry an explicit machine-readable outcome. Real production records and a new normal-interface observation confirm correct classification. Overall Phase 5.1 remains **PARTIAL** for the unrelated items recorded in `51-P` (revoked-session negative test, socket-replacement test).

---

## 1. Exact root cause

`packages/adapters/src/approval-effect.ts` `completeExternalEffect()` unconditionally wrote `status="completed"` and stored the raw tool result. `run-effects.ts` `completeEffect()` stripped `agent_tool_result.details` and passed it through unchanged. Consequences:

- The duplicate-effect gate `resolveDuplicateEffectGate()` treats `completed` as "finalized — return stored result, do not replay" (correct for idempotency) but the ledger had no field distinguishing success from failure.
- `charlie-mission-reconciler.ts` used `effect.status === "completed" && readOnlyTool` to set `reportDelivered=true`; a failed read-only effect could be reported as delivered.
- Audit events already told the truth (`run-tools.ts` computes `success|failure`; `tool-completion-audit.ts` computes `succeeded|error`), so the effect ledger and audit log disagreed.

Observed evidence: cancellation effect `<id>` had `status=completed` with `result={"error":"The user's computer did not finish..."}` while audit `<id>` was `tool.call failure`.

The repo already had three-way machinery (`status="uncertain"`, `UncertainEffectResult`, `settleUncertainEffect()`, retry guard). The fix reuses it rather than inventing a parallel scheme.

## 2. Modified source (Ubuntu working tree, branch `charlie/integration-restore-20261109` @ `7437f6419`)

| File | Change |
|---|---|
| `packages/adapters/src/effect-outcome.ts` **(new)** | Single `effectOutcome(result): "success"|"failure"|"uncertain"`; `storeEffectOutcome()` adds a reserved `outcome` key to object results; `replayEffectResult()` strips it so replay keeps the original result shape. Error/uncertain signals take precedence over any stored marker. Legacy rows (error result, no marker) classify as failure. |
| `packages/adapters/src/approval-effect.ts` | `completeExternalEffect()` and `replaceCompletedExternalEffectResult()` store `storeEffectOutcome(result)`; duplicate gate returns `replayEffectResult(...)` for `completed`/`uncertain`. `status` semantics unchanged. |
| `packages/adapters/src/approval-effect.test.ts` | Updated expectations for the stored outcome marker. |
| `packages/adapters/src/effect-outcome.test.ts` **(new)** | 13 tests: success/failure/uncertain classification, legacy rows, MCP `isError`, stale-marker safety, replay shape, idempotency. |
| `packages/adapters/src/charlie-mission-reconciler.ts` | `reportDelivered` now also requires `effectOutcome(effect.result) === "success"`. **Working-tree only — this file does not exist in the deployed commit, so it was not part of the shipped image.** |

Pre-existing unrelated dirty files (`expo-push.ts`, `expo-push.test.ts`, `honcho-memory-provider.ts`) were preserved untouched. No schema migration; `ExternalEffect.status` is a plain `String`.

## 3. Verification

- **TDD RED:** new test file failed with `Cannot find module './effect-outcome.js'` before implementation.
- **GREEN:** 13 new tests pass; 33 `approval-effect` regression tests pass; `pnpm --filter @rakazo/adapters check` (tsc) clean.
- **Broader regression:** 111 tests pass across 7 files (`effect-outcome`, `approval-effect`, `run-effects`, `executor-effect-idempotency`, `executor-computer-checkpoint`, `run-context-computer-checkpoint`, `turn-checkpoint`). Duplicate gates, recovery and idempotency unchanged.
- **Full adapters suite:** attempted, stalled on Postgres integration tests; interrupted cleanly (no lingering processes). Bounded targeted suites above used instead.
- **Real-record classification** using the exact source shipped in the image: cancellation row → `failure`; earlier successful Windows observation row → `success`.
- **In-image artifact check:** shipped source contains `storeEffectOutcome`; compiled `apps/api/dist/index.js` and `apps/worker/dist/index.js` contain the marker. (Dev test tooling is pruned from the production image, so tests were run on the identical source in the working tree.)

## 4. Deployment evidence

- Built clean image from **deployed API commit `<hash>`** (verified the defect is present there and the relevant files are identical to HEAD) with only the applicable patch: `charlie/effect-outcome:20261117-r1` = `sha256:<hash>`. Worker bundle was byte-identical across the two previously deployed images, so one image serves both without unrelated code changes.
- Used the existing 13-file compose override chain from `<deploy-user-home>/rakazo`; rendered config confirmed only `image` changed (command, 306/261 env vars, volumes, ports, health checks, restart policy preserved).
- Recreated only `rakazo-api-1` and `rakazo-worker-1`, each health-checked (`running healthy`) before proceeding. All other containers (web, voice gateway, STT, TTS, mem0, supervisor, postgres, NextCloud) unchanged and still running.
- Container start: API `2026-11-17T16:47:29Z`, worker `2026-11-17T16:47:44Z`. Both `running healthy` on the new image afterward.
- **Real post-deploy outcome:** normal authenticated UI request (read-only Windows observation) produced effect `<id>` = `user_computer_observe`, `status=completed`, `result={"app":"Windows desktop","outcome":"success"}`, audit `<id>` = `tool.call success`, run `<id>` = `completed`, gateway request `<uuid>` on device `<id>`. Only one observe — no click/type/launch. Legacy cancellation row remains byte-for-byte unchanged (backward compatible).

## 5. Rollback

Backup: `<deploy-user-home>/charlie-backups/20261117-effect-outcome/`
- `source/` — all modified/new source files; `effect-outcome.patch` and `production-applicable.patch`
- `images/api-previous.tar`, `images/worker-previous.tar` (442 MB each), plus image ID manifest
- `compose-args.txt`, `effect-outcome-override.yml`, `rollback-api.yml`, `rollback-both.yml`, `git-status-before.txt`, rendered compose JSON

Rollback command (already staged, one call): re-run compose with `rollback-both.yml` then `rollback-api.yml` from `<deploy-user-home>/rakazo`, restoring `charlie/step57-skill-quality:<hash>` (worker) and `charlie/step57-skill-quality-ownerfix:<hash>` (api). Deploy script had an automatic ERR trap doing exactly this; it did not fire.

## 6. Limitations

- No new *failure* effect was generated post-deploy (would require another authorized interruption); failure classification is proven by tests and by the classified real cancellation record using the shipped source.
- `charlie-mission-reconciler.ts` fix is in the working tree but not in the deployed image (file absent from deployed commit); it will take effect whenever that subsystem ships.
- The public `/health` URL on port 443 belongs to a different service (NextCloud, wildcard-cert mismatch); the app's health was verified through container health checks and the authenticated UI on port 8443.

## 7. Skills

`using-superpowers`, `systematic-debugging`, `test-driven-development`, `verification-before-completion`; persistent `caveman`/`ponytail`. No completion claim from a save/build alone — real records were checked after deployment.

## 8. Final status

Ubuntu git: branch `charlie/integration-restore-20261109`, HEAD `7437f6419`, my 5 source files modified/untracked plus the 3 pre-existing unrelated modifications. No commit or push. Windows repo unchanged for this task except this report and the acceptance ledger.

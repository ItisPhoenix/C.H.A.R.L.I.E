# CHARLIE PHASE 5.2 — PRE-COMMIT GIT AUDIT (READ-ONLY)

**Date:** 2026-11-18 · **Mode:** read-only. No reset, clean, checkout, commit, push, deploy or restart. All files preserved.

---

## 1. Current state

### Windows (repo root)
- Branch `integration/final-runtime-acceptance`, HEAD `<sha>`
- Remote: `origin https://github.com/ItisPhoenix/C.H.A.R.L.I.E.git`; remote branch `origin/integration/final-runtime-acceptance` exists
- **30 tracked modified files**: 3,253 insertions / 398 deletions (broad reconstruction, only partly Phase 5)
- **36 untracked files**: 20 Phase 5 reports, 12 Phase 5 source modules, `charlie_v3.onnx` (790 KB), `data/` lock files, and a 0-byte junk file named `=`
- Ignored: `AGENTS.md`, `charlie/AGENTS.md`, `artifacts/` (incl. the Phase 5 ledger), `.env`, `.venv/`

### Ubuntu (`<deploy-user-home>/Charlie`)
- Branch `charlie/integration-restore-20261109`, HEAD `<sha>`, **no upstream configured**
- Remotes: `origin` ItisPhoenix/Charlie, `fork` ItisPhoenix/rakazo, `upstream` elie222/rakazo
- **12 tracked modified files** (348 insertions / 14 deletions) and **3 untracked** (effect-outcome module/test + preference migration)
- Deployed API/worker: `charlie/routing-preferred:20261117-r1` (`sha256:2ae7a030…`), `GIT_SHA=<hash>`

## 2. Deployed image vs working tree (Ubuntu)

**They are different revisions.** Deployed source is `<hash>`; working HEAD is `7437f6419`. Divergence: **1,968 files, 65,511 insertions, 151,046 deletions**. Critically, HEAD **removes** production customizations: `WorkActivityToken`, `WorkActivityStartToken`, `SpaceInvite`, `BotEmailSend` models (168 schema lines) and 5 `space.*` security-event types. The 3 files carrying our Phase 5 fixes (`user-device-gateway.ts`, `user-devices.ts`, `approval-effect.ts`) are byte-identical between the two revisions, so the fixes themselves are portable; `schema.prisma` and `security-events.ts` are not.

**All production customizations are intact in the deployed image** (verified before deployment) and in the working tree's uncommitted state; this audit changed nothing.

## 3. Phase 5 change inventory

### Windows — Phase 5 specific
- Untracked source: `charlie/device_gateway.py`, `charlie/companion_launcher.py`, `charlie/gateway_watchdog.py`, `charlie/native_executor.py`, `charlie/consent_policy.py`, `charlie/notch.py`, `charlie/notch_bridge.py`, `charlie/ptt.py`, `charlie/rakazo.py`, `charlie/secret_store.py`, `charlie/browser/authorization.py`, `charlie/browser/gateway.py`, `charlie/browser/service.py`
- Tracked Phase 5-related: `run.py` (companion/device-gateway launch modes), `charlie/config.py` (voice backend/PTT/wake-word + DPAPI secrets), `charlie/router.py` (voice actions), `charlie/tools.py` (app/folder open), `charlie/verifiers.py` (`verify_window_state`), `charlie/desktop/apps.py` (Start-app discovery), `charlie/desktop/uia.py`, `charlie/desktop/windows.py`, `charlie/voice.py`, `charlie/wake_word.py`, `charlie/asr_worker.py`, `main.py`, `frontend/src/App.tsx`, `frontend/src/styles.css`, `charlie/capabilities.py`, `charlie/computer/backend.py`, `charlie/execution_context.py`, `charlie/fastpaths.py`, `charlie/media_adapter.py`, `charlie/personality.py`, `charlie/turn_contracts.py`, `charlie/voice_diagnostics.py`, `tests/test_runtime_launcher_r2.py`
- Reports: all 20 `CHARLIE_PHASE51_*.md` / `CHARLIE_PHASE5_WINDOWS_REPORT.md` untracked files
- Chief instruction preference lives in the **Ubuntu database**, not in Git; it is documented in `CHARLIE_PHASE51_CHIEF_INSTRUCTION_CONFLICT_FIX.md`

### Windows — unrelated / unfinished (leave out)
`charlie/calendar_runtime.py`, `calendar_scheduler.py`, `calendar_store.py`, `tests/test_calendar_authority_cal1.py` (calendar authority), `charlie/memory_store.py` (embedding timeouts), `=`, `data/*.lock`, `charlie_v3.onnx`, `AGENTS.md` (ignored).

### Ubuntu — our Phase 5 fixes
- Effect-outcome: `packages/adapters/src/approval-effect.ts` (+ test), new `effect-outcome.ts`/`effect-outcome.test.ts`, `charlie-mission-reconciler.ts`
- Device routing: `packages/adapters/src/user-device-gateway.ts` (+ test), `apps/api/src/user-devices.ts` (+ test), `packages/contracts/src/security-events.ts`, `packages/db/prisma/schema.prisma`, new migration `20261117193000_user_device_preferred/migration.sql`

### Ubuntu — unrelated pre-existing (leave out)
`packages/adapters/src/expo-push.ts`, `expo-push.test.ts`, `honcho-memory-provider.ts` (present before this work; preserved untouched).

## 4. Proposed commit plan (NOT executed)

Target branches: create fresh topic branches from each repo's current HEAD; do **not** commit directly to the dirty integration branches.

### Windows — branch `charlie/phase5.1-windows-gateway` from `8dec668`
1. **feat(device-gateway): socket-bound frames, in-flight cancellation, accurate failures**
   `charlie/device_gateway.py`, `charlie/companion_launcher.py`, `charlie/gateway_watchdog.py`, `run.py`, `tests/test_runtime_launcher_r2.py`
2. **feat(companion): native executor, consent, PTT, notch, browser/voice plumbing**
   `charlie/native_executor.py`, `charlie/consent_policy.py`, `charlie/notch.py`, `charlie/notch_bridge.py`, `charlie/ptt.py`, `charlie/rakazo.py`, `charlie/secret_store.py`, `charlie/browser/authorization.py`, `charlie/browser/gateway.py`, `charlie/browser/service.py`, plus the Phase 5-related tracked files listed above that the owner confirms belong to this scope
3. **docs(phase5.1): acceptance and reliability reports**
   the 20 `CHARLIE_PHASE51_*.md` reports + `CHARLIE_PHASE5_WINDOWS_REPORT.md`

### Ubuntu — branch `charlie/phase5.1-effect-outcome` from `7437f6419`
4. **fix(effects): truthful success/failure/uncertain outcome, backward compatible**
   `packages/adapters/src/approval-effect.ts`, `approval-effect.test.ts`, `effect-outcome.ts` (new), `effect-outcome.test.ts` (new), `charlie-mission-reconciler.ts`

### Ubuntu — branch `charlie/phase5.1-device-routing` from `7437f6419`
5. **feat(user-devices): deterministic preferred-device routing with fail-closed behavior**
   `packages/adapters/src/user-device-gateway.ts`, `user-device-gateway.test.ts`, `apps/api/src/user-devices.ts`, `user-devices.test.ts`, `packages/contracts/src/security-events.ts`, `packages/db/prisma/schema.prisma`, `packages/db/prisma/migrations/20261117193000_user_device_preferred/migration.sql` (new)

### Version-control gaps
- `artifacts/phase5-windows-acceptance.json` is **ignored** (`.gitignore:103`). To version it, add a narrow negation (`!artifacts/phase5-windows-acceptance.json`) or force-add deliberately — owner decision.
- `AGENTS.md` files are ignored; contract changes would need an explicit decision.
- Deployment patches/backups under `<deploy-user-home>/charlie-backups/…` are outside Git by design; reference them in commit messages/release notes, do not copy into the repo.

## 5. Verification run

- **Ubuntu working tree:** 113 targeted tests pass (effect-outcome 13, approval-effect 33, user-device-gateway 32, user-devices 20, user-computer 15); `tsc --noEmit` clean for `@rakazo/adapters`. Same 113 passed in the isolated build used for deployment.
- **Windows:** `py_compile` of `device_gateway.py`, `companion_launcher.py`, `gateway_watchdog.py`, `native_executor.py` → exit 0; Phase 5 ledger JSON valid.
- **Missing / blocked:** `pytest tests/test_runtime_launcher_r2.py` cannot load `tests/conftest.py` — `Config` has no attribute `mcp_config_path` (pre-existing repo/config issue, exit 4). No full Windows suite can be claimed. PTT physical and notch visual acceptance remain untested (owner-facing).

## 6. Risks before proceeding

1. **Never deploy from the Ubuntu working-tree branches.** Their trees delete 151k lines of production custom code relative to `<hash>`. Deployment must keep using the isolated build from the deployed commit + the two approved patches.
2. **No upstream on Ubuntu branch.** Topic branches need an explicit remote/target chosen by the owner; do not assume `origin/main`.
3. **Public-repo hygiene.** Windows `origin` is a public GitHub repo. Reports contain internal hostnames, local paths and device IDs. Before committing reports, scrub or obtain explicit acceptance; Ubuntu AGENTS.md forbids real production data/hostnames in public commits.
4. **Broad Windows dirty reconstruction.** 30 modified files include work from earlier phases and unrelated unfinished features. The split above needs owner confirmation before any staging.
5. **No tokens in Git** — the disposable test tokens were shredded and never written to files or reports (verified).
6. Commit 2 is the least certain grouping; consider splitting further once the owner confirms which tracked modules belong to Phase 5 versus earlier phases.

No files were modified by this audit.

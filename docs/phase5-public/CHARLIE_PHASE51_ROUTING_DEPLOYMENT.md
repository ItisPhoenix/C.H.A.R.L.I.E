# CHARLIE PHASE 5.1 — SAFE ROUTING DEPLOYMENT

**Date:** 2026-11-17 · **Verdict:** **PASS for this deployment; Phase 5.1 overall PARTIAL** (security Tests A/B still unexecuted). No Windows restart, no Windows Gateway/Companion change, no unrelated service recreation, no commit/push.

---

## 1. Source versus deployed revision (preflight)

| Item | Value |
|---|---|
| Deployed API/worker image (before) | `charlie/effect-outcome:20261117-r1` = `sha256:7f395def…` |
| Deployed image `GIT_SHA` | `<hash>` (both containers) |
| Working tree | branch `charlie/integration-restore-20261109`, HEAD `7437f6419` |
| Migrations | DEP tree and production both 250/250 applied — no unexpected pending |

**The working tree was NOT production.** Relative to the deployed commit, HEAD removes production custom models (`WorkActivityToken`, `WorkActivityStartToken`, `SpaceInvite`, `BotEmailSend` — 168 schema lines) and 5 `space.*` security-event types, and adds `CharlieOwnerOriginProof`. Building from the working tree would have removed live Charlie features.

## 2. Custom modifications preserved

Built from an isolated `git archive` of the **deployed commit** `<hash>`, then applied only the two approved patches:
- previously deployed effect-outcome fix (`production-applicable.patch` + `effect-outcome.ts`/`.test.ts`)
- new routing patch (gateway, API, security events, schema, new files, migration)

Verified in the build tree and final image: 4/4 custom models present; `space.invite.created` present; `storeEffectOutcome` present; `userDevicePreference` present; `device.preferred.set` present. The working tree itself was never reset/cleaned/overwritten; all pre-existing custom modifications remain.

## 3. Exact routing patch applied

- `schema.prisma` + migration `20261117193000_user_device_preferred` → table `user_device_preferences(userId PK, deviceId, updatedAt)` (no FKs).
- `user-device-gateway.ts` `route()`: explicit `deviceId` wins; otherwise stored owner preference; missing table → old last-connect-wins; preferred device offline/revoked → **fail closed** (no fallback).
- `user-devices.ts`: owner-only `PUT /api/devices/preferred/:deviceId` and `DELETE /api/devices/preferred`, CSRF origin check, device must belong to caller and not be revoked.
- `security-events.ts`: `device.preferred.set`, `device.preferred.cleared`.
- Tests in `user-device-gateway.test.ts` and `user-devices.test.ts`.

## 4. Tests and deployment evidence

- Isolated tree: `pnpm install --frozen-lockfile` OK; `prisma generate` OK; `tsc --noEmit` clean for adapters/api/contracts/db; **113 tests passed** (5 files: 33 approval-effect, 13 effect-outcome, 32 routing, 20 user-devices, 15 user-computer).
- Image `charlie/routing-preferred:20261117-r1` = `sha256:2ae7a030…` built from that tree.
- Compose render diff: only `api`, `worker`, `migrate` images changed. All other services unchanged.
- Migration: `251` applied (was 250); only `20261117193000_user_device_preferred` added; table columns verified.
- Rollout: api and worker recreated sequentially, each `running healthy` before the next. Both now `charlie/routing-preferred:20261117-r1`, healthy. Unrelated containers unchanged.
- Preference set via supported authenticated API: `PUT /api/devices/preferred/<id>` → **204**; DB row points to the production device; `device.preferred.set` audit event recorded.
- End-to-end: normal read-only UI observation produced effect `<id>` = `user_computer_observe`, `completed`, `result={"app":"Windows desktop","outcome":"success"}` (effect-outcome fix preserved); audit `tool.call success`; gateway request `472cb9e9…` on device `cmwjf4i…`; brief WSS disconnect/reconnect at 23:40 auto-recovered.
- Windows: gateway `connected`, device `cmwjf4i…`, heartbeat current; watchdog task `Running`.

## 5. Backup and rollback

`<deploy-user-home>/charlie-backups/20261117-routing-deploy/`: `routing.patch`, `preference-migration.sql`, `git-status-before.txt`, `source-head.txt`, `source-diff-snapshot.patch`, `running-images.txt`, `migrations-before.txt`, `relevant-db-state.sql` (76 MB), `api-previous.tar` + `worker-previous.tar` (442 MB each), `routing-override.yml`, `compose-args.txt`, `rollback-previous.yml`.

Rollback: `docker compose … -f rollback-previous.yml up -d --no-deps --force-recreate api worker` restores the exact previous image `charlie/effect-outcome:20261117-r1`. The preference table is inert to old code. The deploy script had an ERR trap that would have done this; it did not fire.

## 6. Remaining work / Phase 5.1 closure

- **Test A (disposable-device revocation) and Test B (controlled WebSocket replacement) remain UNECECUTED.** The isolation precondition that blocked them is now addressed by deterministic routing plus the set preference, but these tests still require revocation/connection interruption and a fresh explicit go-ahead. Phase 5.1 **cannot close** until they pass.
- Original hard-kill agent remains unverified (Security 4689 disabled); watchdog recovery is verified.
- The owner should note that a stale preference pointing to an offline device fails closed until cleared or the device returns.

## 7. Skills / evidence

`using-superpowers`, `verification-before-completion`, `deployment-procedures`. Evidence: real preflight diffs, isolated-build tests/typechecks, migration and rollout logs, and post-deploy DB/UI/gateway checks. No completion claim for Phase 5.1 as a whole.

# CHARLIE PHASE 5.1 — DETERMINISTIC DEVICE ROUTING (DESIGN + PATCH, NOT DEPLOYED)

**Date:** 2026-11-17 · **Scope:** Ubuntu working tree source, tests, migration and deployment plan. **Production was not modified**: containers remain `charlie/effect-outcome:20261117-r1` healthy, and `user_device_preferences` does not exist in the production DB. No commit/push.

## Verdict: **PASS (design + isolated verification) / deployment BLOCKED on owner authorization**

---

## 1. Root cause

`UserDevices.route(userId, deviceId?)` selects the freshest connected device (`orderBy connectedAt desc`, filtered by `userId`, `revokedAt: null`, fresh heartbeat). `connected(userId)` calls it with **no** deviceId, so any newly connected device under the owner's userId becomes the routing target. `connectedUserComputer()` pins the resolved device only for the current run; every new run re-resolves by last-connect-wins. A disposable test pairing could therefore capture normal Chief `user_computer_*` requests.

Explicit targeting already existed: `request(userId, req, signal, deviceId)` narrows `route()` to that device. The missing piece was a **stable owner preference** that normal runs use, with safe failure when unavailable.

## 2. Design (minimum change, reuses existing gateway)

| Decision | Rationale |
|---|---|
| Reuse `route()`/`request()` `deviceId` parameter | No second gateway or execution system. Explicit deviceId continues to win. |
| New standalone table `user_device_preferences (userId PK, deviceId, updatedAt)` | One row per user; no relations, so one `CREATE TABLE` migration. Adding a table is backward compatible under the repo's deploy rules. |
| `route()` consults the preference only when no explicit `deviceId`; wrapped in `.catch(() => null)` | Old schema during rollout → no preference → previous last-connect-wins behaviour. Backward compatible. |
| **Fail closed**: preferred device disconnected/revoked → `connected()` returns null → tools unavailable, no silent fallback | Prevents a newer device from taking over when the chosen one is unavailable. |
| Owner-only `PUT/DELETE /api/devices/preferred`, CSRF origin check, device must belong to caller and not be revoked | Preserves authorization, revocation and consent. |
| New security events `device.preferred.set` / `.cleared` | Auditable; generic audit records are emitted by the existing security-event pipeline. No token logged. |

## 3. Modified files (Ubuntu working tree)

- `packages/db/prisma/schema.prisma` — new `UserDevicePreference` model.
- `packages/db/prisma/migrations/20261117193000_user_device_preferred/migration.sql` — single `CREATE TABLE`.
- `packages/adapters/src/user-device-gateway.ts` — preference-aware `route()` with explicit-id precedence and missing-table tolerance.
- `packages/contracts/src/security-events.ts` — two event types.
- `apps/api/src/user-devices.ts` — preference set/clear routes (before the `:deviceId` catch-all).
- Tests: `packages/adapters/src/user-device-gateway.test.ts` (6 targeting tests), `apps/api/src/user-devices.test.ts` (2 authorization tests).

Pre-existing unrelated dirty files (`expo-push*`, `honcho-memory-provider`, and the earlier effect-outcome fix) were preserved.

## 4. Verification (isolated test infrastructure only — no live pairings)

TDD RED confirmed: before implementation, the 3 preference tests failed (`connected()` ignored the preference). After implementation:

- `user-device-gateway.test.ts`: **32 passed** — including *explicit deviceId stays on the named device after a newer device connects*, *preferred device wins over a newer connection*, *fails closed when preferred is unavailable*, *last-connect-wins preserved with no preference*, *explicit id overrides preference*, *revoked preferred device is ignored*.
- `user-devices.test.ts`: **20 passed** — including *only the owner may set/clear*, *unauthenticated 401*, *foreign/revoked device 404*, security events emitted, no token in events.
- `run-tools-user-computer.test.ts`: **15 passed** (no regression).
- `tsc --noEmit` clean for `adapters`, `api`, `contracts`, `db`.
- Total: **67 tests passed**, exit 0.

No disposable pairing was created; no live connection was opened or interrupted.

## 5. Deployment plan (NOT EXECUTED — needs authorization)

1. Back up current source, generated Prisma client, and running API/worker image IDs/tars.
2. Run the new migration via the existing migrate container (`pnpm db:migrate` / compose `migrate` service) BEFORE app rollout. `CREATE TABLE` only; no data migration; old code ignores the table.
3. Build one image from the patched commit using `infra/compose/Dockerfile` (as in the prior effect-outcome rollout).
4. Recreate only `rakazo-api-1` and `rakazo-worker-1` using the existing 13-file compose override chain from `<deploy-user-home>/rakazo`, health-checking each before proceeding. Do not touch web, voice, postgres, supervisor or NextCloud.
5. After health: as the owner, call `PUT /api/devices/preferred/<production device id>` through the authenticated API/UI and verify `connected()` selects that device even after a test device connects in isolated tests. No live test pairing needed for the preference write itself.
6. Post-deploy: verify one normal read-only Windows observation still routes to the production device; verify new audit events; confirm gateway/watchdog health.

## 6. Rollback

- Restore the previous API/worker images via the staged rollback override files (same procedure as the effect-outcome rollout).
- The preference table may remain: previous code never queries it, so rollback is just the image retag/recreate. Optionally drop it in a later deferred migration.
- Clear any preference with `DELETE /api/devices/preferred` before rollback if desired; otherwise it is inert to old code.

## 7. Residual risk / open items

- The preference must be set explicitly by the owner; until then behaviour is unchanged (last-connect-wins). Phase 5.1 test isolation is only solved after deployment **and** the owner sets the preference.
- A stale preference pointing to a revoked/offline device intentionally fails closed (tools unavailable) until cleared or the device returns.
- Migration and app build were not run against production; only generated client and isolated tests were exercised.

## 8. Skills / evidence

`using-superpowers`, `test-driven-development`, `verification-before-completion`, `architecture`. Evidence: RED→GREEN test runs, 67 passing tests, clean typechecks, fresh confirmation that production containers and DB are unchanged. No completion claim for deployment.

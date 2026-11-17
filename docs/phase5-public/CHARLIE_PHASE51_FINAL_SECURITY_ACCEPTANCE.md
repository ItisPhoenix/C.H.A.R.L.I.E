# CHARLIE PHASE 5.1 — FINAL SECURITY ACCEPTANCE (Tests A + B)

**Date:** 2026-11-18 · **Verdict:** **Test A PASS, Test B PASS. Phase 5.1 overall remains PARTIAL** — the security/recovery scope is now complete, but other ledger items are still unresolved. No Windows restart, no production pairing interruption, no unrelated service restart, no commit/push.

---

## Preconditions (all verified before any test action)

1. API and worker `charlie/routing-preferred:20261117-r1` (`sha256:2ae7a030…`), both healthy.
2. Preferred device = `<id>` (DB row + supported API).
3. Deployed image contains preference-aware `route()` and `device.preferred.set`; last-connect-wins replaced.
4. Windows gateway `connected`, device `cmwjf4i…`, heartbeat current; watchdog `Running`.
5. A fresh normal read-only Chief observation routed to the production Windows device with `outcome: "success"` (effects `cmwl1ez6t…`, `cmwl1et1e…`; gateway request `4b4f44e3…`), confirming a newer disposable connection cannot intercept normal requests while the preference is set.

## Test A — revoked-device security: **PASS**

- Disposable pairing `PHASE51-TEST-A` (`<id>`) created via `POST /api/devices/pair` (201); token held only in a 0600 temp file, shredded at cleanup, never in logs/reports.
- Authenticated WSS connection via `Authorization: Bearer` (client log `/tmp/p5a-events.log`): `OPEN`, server `PING`s answered.
- Read-only verification: `GET /api/devices` (200) listed the test device `connected: true`; DB `user_device_connections` row with fresh heartbeat.
- Revoked **only** the disposable device via `DELETE /api/devices/cmwl1h49u…` → 204.
- Client received `{"type":"revoked"}` then `CLOSE code=4001 reason=Revoked`.
- Reconnect with the same token → **HTTP 401**.
- Audit: `device.revoked` with `metadata={"by":"owner","deviceId":"cmwl1h49u…"}` at 18:45:16Z.
- Production device still active, not revoked, sole connection; preference unchanged.

## Test B — controlled WebSocket replacement: **PASS**

- Separate disposable pairing `PHASE51-TEST-B` (`<id>`) via supported API (201).
- Socket A open; a bounded read-only `screenObserve` request was explicitly targeted at the test device using the supported preference API (PUT → 204), with normal owner approval via the UI card (`approval.decided` audit).
- Request `dc231350…` (first attempt) and `3777673f…` (clean attempt) were received **only on socket A** while in flight; B auto-connected 2 s later (`B OPEN`) and A closed **`4000 Replaced`** at 18:49:59Z while `3777673f…` was still in flight.
- The in-flight request failed truthfully: effect `cmwl1rhzj…` `status=completed`, `result={"error":"The user's computer did not finish: Your computer disconnected…","outcome":"failure"}`, audit `tool.call` **failure** — the effect-outcome fix working in production.
- Fresh request `2642051f…` went **only to B**, was answered promptly, effect `cmwl1s697…` `outcome:"success"`, audit `tool.call` **success**.
- No duplicate request IDs, no misrouted frames, no unauthorized execution (read-only `screenObserve` only).
- Production Windows device was **never targeted**: gateway log has zero requests in the 00:18–00:22 IST Test B window.
- Preference restored to `cmwjf4i…` (PUT → 204, audit `device.preferred.set`), test device revoked via `DELETE` (204), sockets terminated, token shredded.

## Cleanup and final state

- Both test devices `revokedBy=owner` with audit events; only the production device connected; preference on production device.
- Temp scripts/tokens removed (`shred -u`); only `/tmp/p5a-events.log`, `/tmp/p5b-events.log`, `/tmp/p5b-events2.log` retained as evidence.
- API and worker healthy; Windows gateway connected; watchdog `Running`.

## Phase 5.1 closure assessment: **cannot officially close (PARTIAL)**

Resolved now: owner-origin observe/act (51-B/C), cancellation truth (51-L), effect-outcome (51-Q), deterministic routing/deploy (51-U), security negatives revoked-device + socket-replacement and unauthenticated WSS 401 (51-E/S).

Still unresolved:
- **P3** — dedicated push-to-talk physical key/mic acceptance (never performed; companion intentionally stopped for mic privacy).
- **P4** — notch visual/resource/multi-monitor acceptance pending owner review.
- **P5** — stable Ubuntu approval identity requires an Ubuntu production change not authorized.
- **51-I** — exact original hard-kill agent unverified (Security 4689 process-exit auditing disabled); watchdog recovery is verified, so residual risk only.

Official closure requires the owner to either complete/waive those items. This pass does not claim full Phase 5.1 completion.

## Phase 5.2 proposed scope (NOT started)

Automatic Windows application discovery, launch and control, reusing the existing runtime:
- Extend `charlie/desktop/apps.py` (resolver/launch/verify) with deterministic discovery of installed apps (Start Menu/AppUserModelID/Appx where available) and a stable app index.
- Fix `charlie/native_executor.py:123` passing a raw name instead of the resolved launch target to `launch_and_verify` (re-verify current source first).
- Reuse capability/lease/verifier boundaries in `charlie/capabilities.py` and Cua-bound reuse/ambiguity handling in `charlie/computer/backend.py`.
- Preserve observe→act→re-observe semantics, consent, and no second execution system.
- Acceptance to be defined with the owner before implementation (discovery accuracy, launch verification, ambiguity handling, no hallucinated apps).

## Skills / evidence

`using-superpowers`, `verification-before-completion`, `security-auditor`. Evidence: client socket logs, supported API status codes, DB effects/audits/security events, gateway logs, and live host health checks. Tokens never logged.

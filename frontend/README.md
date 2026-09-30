# Charlie live frontend

React projection shell for Charlie's runtime. Python remains authoritative; this app renders `/api/scene`, lists received `/api/events` SSE envelopes, and posts text to `/api/commands`. It has no production fixtures.

Run `npm install` and `npm run dev` from this directory. Vite proxies `/api` to `http://127.0.0.1:8001`. Production hosting must serve the UI and API on the same origin to retain the runtime session cookie.

Expected payloads:

- `GET /api/scene`: `{ "revision": 1, "title": "...", "summary": "...", "details": [{ "label": "...", "value": "..." }] }`
- `GET /api/events`: SSE `data:` JSON envelopes with `type`, optional `id`, `source`, `timestamp`, and optional `payload.snapshot` using the scene shape above.
- `POST /api/commands`: `{ "type": "user_message", "text": "..." }`; UI reports acceptance only for `{ "accepted": true }` or `{ "status": "accepted" }`.

The gateway class currently defines scene and event routes, but this checkout has no launch wiring or shared scene schema. A missing route, malformed response, or unconfirmed command stays visibly unresolved.

Checks: `npm run typecheck`, `npm test -- --run`, `npm run build`.

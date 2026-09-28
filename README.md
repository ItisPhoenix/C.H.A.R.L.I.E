# C.H.A.R.L.I.E.

C.H.A.R.L.I.E. is a local, backend-first assistant runtime. The canonical
process owns conversation turns, voice input/output, tool execution, browser
and desktop control, research, memory, background tasks, MCP, plugins,
Telegram, watchers, leases, verification, cancellation, and shutdown.

## Run

Install the project and the extras required by the capabilities you use, then
start the canonical runtime:

```powershell
uv sync --extra desktop --extra browser
uv run python run.py
```

`run.py` has one runtime path. It does not select alternate modes or start a
second process.

## Configuration

Copy `.env.example` to `.env` and set the model, voice, memory, browser,
desktop, MCP, plugin, research, and Telegram settings appropriate for the
machine. Keep credentials outside source control.

## Runtime boundaries

- `main.py` is the process lifecycle and aggregate event authority.
- `charlie/core.py` owns Brain routing, tool execution, approvals, results,
  cancellation, and turn attribution.
- `charlie/browser/` owns Playwright sessions, browser identity, leases, and
  browser verification.
- `charlie/desktop/` owns Windows observation, targeting, control, and
  post-action verification.
- `charlie/research/` is the separate public-source acquisition and evidence
  pipeline; it does not replace interactive browser control.
- `charlie/task_journal.py`, `charlie/session_store.py`, and the memory
  services persist authoritative task, turn, session, and memory state.

Dangerous operations require an active owner approval channel. Voice and the
configured Telegram owner channel are supported; if neither is available,
the operation fails closed.

## Verification

Run the focused backend tests with the repository environment:

```powershell
.venv\\Scripts\\python.exe -m pytest -q
.venv\\Scripts\\python.exe -m ruff check .
```

Tests establish runtime contracts. Native Windows process, audio, browser,
and desktop acceptance still requires evidence from the user's real host.

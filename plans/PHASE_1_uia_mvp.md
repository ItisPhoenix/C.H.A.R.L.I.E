# Phase 1 - Native UIA MVP (text serialization, all models)

> Part of the "Charlie -> Agentic Windows OS" initiative. Standalone; read on its own.
> Prerequisite: Phase 0 spike done (or skipped) - this is the real, gated, native implementation.

## Goal

Give Charlie real hands and eyes on the Windows desktop, reliably, for **every** model (local
text-only included) - not just vision models. Perception is the Windows accessibility tree (UI
Automation / UIA), serialized as plain text ("set-of-marks": `[3] Button "Save"`). Actions are
click / type / invoke / key, gated exactly like existing risky tools.

## Design decisions this phase implements

- Desktop control is **OFF by default** (`desktop_control_enabled=false`).
- **Arm + per-task confirm**: one HITL approval per control task, then the rest of that task's actions
  run autonomously without re-asking.
- **Credential hard-stop**: refuse to type into password/payment fields; hand control back to the human.
- **One desktop action = one tool call** (no macro/batch tool). This is the load-bearing simplification:
  it gives free per-round abort, free budget enforcement, and free per-action gating, for nothing.
- **Abort controls**: global panic hotkey, mouse-corner failsafe, voice/dashboard stop (reuses existing
  barge-in), auto-halt on repeated failure.

## New package: `charlie/desktop/`

### `charlie/desktop/__init__.py`
Optional-import guard, same pattern as `tools.py`/`plugins.py` use for optional deps:
```
try:
    import uiautomation  # noqa: F401
    _HAS_UIA = True
except ImportError:
    _HAS_UIA = False

DESKTOP_AVAILABLE = sys.platform == "win32" and _HAS_UIA
```
Importing this module on non-Windows or without `uiautomation` installed must never raise.

### `charlie/desktop/uia.py` - perception
- `Element` - small dataclass/typed dict: `mark_id: int`, `name: str`, `control_type: str`,
  `bounds: Tuple[int, int, int, int]`, `is_password: bool`, `is_offscreen: bool`.
- `snapshot_tree(max_depth: int, root: Optional[Any] = None) -> List[Element]` - walk the
  **foreground window only** (latency: do not walk the whole desktop). Assign sequential `mark_id`s.
- `serialize_marks(elements: List[Element]) -> str` - render as a text table, e.g.
  `[3] Button "Save"` / `[4] Edit "Search"`. This is what the model sees - identical for local and cloud models.
- `resolve_mark(mark_id: int) -> Element` - look up a live control handle from the current turn's mark
  cache (module-level, rebuilt on each `desktop_observe` call).

### `charlie/desktop/actions.py` - effectors
- Guarded import block; at top (inside the guard): `pyautogui.FAILSAFE = True` (mouse-corner abort).
- `_HALT = threading.Event()` (module-level) with `halt()`, `clear_halt()`, `_check_halt()`. Every
  effector calls `_check_halt()` first and raises `DesktopHalted` if set.
- `click_mark(mark_id: int) -> str`
- `type_text(mark_id: int, text: str) -> str` (credential hard-stop lives here - see below)
- `invoke_mark(mark_id: int) -> str` (default UIA action: toggle/expand/select)
- `key_press(keys: str) -> str` (key chord, e.g. "ctrl+s")
- `_SECURE_REFUSAL` sentinel string constant for the credential hard-stop.

### Credential hard-stop (in `type_text`)
After `resolve_mark(mark_id)`:
- If `element.is_password` (UIA `IsPassword` property) **or** the element's name/automation-id matches
  a payment-field pattern (module constant `_PAYMENT_FIELD_RE`, matching card/cvv/ssn/routing-type
  names): do **not** type. Return the sentinel:
  `"Refusing to type into a secure field. I've handed control back to you - fill it in, then say continue."`
- Never read or log the field's actual value. Log only `"secure field detected"` (no contents).
- The Brain treats this like any other tool result string; the model/user naturally pauses there.

## Edits to existing files

### `charlie/config.py` (near the existing `# --- Agentic OS Toggles ---` block, ~line 96)
Add, following the exact `os.getenv` pattern already used there:
```
desktop_control_enabled: bool = os.getenv("DESKTOP_CONTROL_ENABLED", "false").lower() == "true"
desktop_panic_hotkey: str = os.getenv("DESKTOP_PANIC_HOTKEY", "ctrl+alt+q")
desktop_max_actions: int = int(os.getenv("DESKTOP_MAX_ACTIONS", "40"))
```
Add matching placeholders to `.env.example`.

### `charlie/tools.py` (register new tools at the bottom, same convention as existing tools)
Each wrapper: lazy-imports `charlie.desktop` **inside the function body** (so importing `tools.py`
itself never touches `uiautomation`/`pyautogui` on a system that lacks them), and first checks
`config.desktop_control_enabled` and `charlie.desktop.DESKTOP_AVAILABLE` - if either is false, return a
plain "desktop control is disabled" error string instead of running anything.

| Tool | Purpose | Gating tier |
|---|---|---|
| `desktop_observe` | Return set-of-marks TEXT of the foreground window | ungated (read-only) |
| `desktop_click(mark_id)` | Click a marked element | **gated (arm)** |
| `desktop_type(mark_id, text)` | Type into a marked element (credential-checked) | **gated (arm)** |
| `desktop_invoke(mark_id)` | Invoke default action (toggle/expand/select) | **gated (arm)** |
| `desktop_key(keys)` | Send a key chord | **gated (arm)** |

Mark the four effector tools `is_interactive=True` (so they serialize behind the existing interactive
lock at `core.py:1824-1826`, same as `shell_execute`). `desktop_observe` stays non-interactive.

### `charlie/core.py`
1. **Gate wiring** - module-level `_DESKTOP_CONTROL_TOOLS = frozenset({"desktop_click", "desktop_type",
   "desktop_invoke", "desktop_key"})`. In `_exec_one` (~line 1722), extend the existing gate ladder:
   ```python
   elif tool_name in _DESKTOP_CONTROL_TOOLS:
       gate_reason = self._desktop_gate_reason()
   ```
2. **Arm + per-task confirm** - add `self._desktop_armed_turn: Optional[str] = None` to `Brain.__init__`.
   `_desktop_gate_reason()` returns `None` if `self._desktop_armed_turn == <current turn id>` (already
   confirmed this task), else returns `"take control of your desktop"`. On the first successful
   `request_tool_approval` call for a desktop tool, set `self._desktop_armed_turn` to the current turn
   id. Reset it to `None` at the top of each `chat_stream` call (new turn = re-arm required). This reuses
   `request_tool_approval` (`core.py:1323`) completely unchanged - just extend its `describe` line
   (~1337) to also read an optional `description` argument so desktop-action prompts read naturally.
3. **Dedup bypass** - at the `_seen_tool_calls` check (~line 1700), skip the cache lookup/store when
   `tool_name in _DESKTOP_CONTROL_TOOLS` (two identical clicks on the same button are not the same
   action - the cache would otherwise silently no-op the second one).
4. **Timeouts** - add the four effector tools (and `desktop_observe`) to `_TOOL_TIMEOUTS` (~line 38),
   roughly 15s each.
5. **Panic hotkey** - in `Brain.__init__`, if `config.desktop_control_enabled` and
   `charlie.desktop.DESKTOP_AVAILABLE`, start a `pynput.keyboard.GlobalHotKeys` listener thread bound to
   `config.desktop_panic_hotkey`. Handler `_panic()` calls `charlie.desktop.actions.halt()` **and**
   `self.cancel_chat()`. Stop the listener in `Brain.close()`.
6. **Anomaly auto-halt** - reuse `budget.try_spend` (~line 1811) for desktop tools (already flows through
   it); additionally track `desktop_max_actions` per turn. Track the last desktop `(name, args)` +
   result; if the identical call fails twice consecutively, call `halt()` and yield a stop message
   instead of continuing.
7. **Voice/dashboard stop** - no new code needed. The existing barge-in path already calls
   `cancel_chat()`, which the tool loop honors between rounds (`core.py:1798`). Just confirm in testing
   that both `_panic()` and barge-in funnel through the same `cancel_chat()` + `halt()` pair.

## Friction points to watch for while implementing

- **The gate is a hardcoded `if/elif` tool-name ladder, not a generic hook.** "Extend the gate" means
  editing `core.py:1722` directly - there is no registry-level gating attribute to set instead.
- **COM threading.** `uiautomation` requires a COM-initialized apartment thread. The shared
  `run_in_executor` thread pool used elsewhere does not guarantee this. Pin desktop tool execution to a
  single dedicated worker thread (or initialize COM per call) rather than the default executor pool.
- **UIA tree walks can be slow** on complex windows. Cap `max_depth` and only ever walk the foreground
  window, never the whole desktop.

## New dependencies (Windows-only, optional)

`uiautomation` (Apache-2.0), `pyautogui`, `pynput`. Add under an optional/extra dependency group in
`pyproject.toml` so non-Windows installs are unaffected; all imports are lazy/guarded as above.

## Verification

1. `uv run ruff check .` and `uv run pytest -v` pass.
2. `import charlie.tools` succeeds on a non-Windows machine or one without `uiautomation` installed;
   calling any `desktop_*` tool returns the disabled-error string, nothing raises.
3. With `DESKTOP_CONTROL_ENABLED=true`: say "open Notepad and type a haiku." Expect **exactly one**
   approval prompt, then `desktop_observe` -> `desktop_click`/`desktop_type` run autonomously without
   further prompts for the rest of that turn.
4. Mid-sequence, press the panic hotkey. Confirm all desktop motion stops within one action and the turn ends.
5. Point a task at a real password field (e.g. a login form). Confirm Charlie refuses to type, states it
   handed control back, and does not log the field's contents.
6. Trigger the same failing action twice in a row (e.g. click a mark id that no longer exists) and
   confirm auto-halt fires instead of looping.

## Explicitly out of scope for this phase

Screenshots, OCR, vision models, the dashboard live view, and the MARVEL operator persona - see the
later phase files.

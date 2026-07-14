# Phase 2b - Vision Model + Multimodal Plumbing (local vision model)

> Part of the "Charlie -> Agentic Windows OS" initiative. Standalone; read on its own.
> Prerequisite: Phase 1 (UIA) and Phase 2a (OCR) shipped and verified.

## Goal

Cover the last perception gap: truly graphical targets that have no accessible name (UIA) and no
readable text (OCR) - icons, images, drag handles, canvases with non-text content. This is the tier
that makes Charlie behave "exactly like" Claude Computer Use / ChatGPT Operator (screenshot ->
model picks coordinates -> click), but used only when the cheaper tiers already failed, and driven by
a **local** vision model per the user's privacy preference.

## Why this is a bigger step than Phase 2a

Charlie's LLM message format is **text-only everywhere** today - `content` is always a plain string, in
the live payload, in SSE parsing, and in SQLite session-history persistence. This phase adds the *only*
place images are allowed to enter that pipeline, and only conditionally.

## New file: `charlie/desktop/vision.py`

- Reuses `charlie.desktop.ocr.capture()` for the screenshot (do not re-implement capture here).
- `annotate_som(png_bytes: bytes, elements: List[Element]) -> bytes` - draw numbered boxes over the
  combined UIA + OCR mark list (Set-of-Mark style, per `microsoft/SoM`), so the vision model refers to
  targets by **mark id**, never raw pixel coordinates. This keeps click resolution deterministic even
  though perception is now vision-based.
- `to_data_url(png_bytes: bytes) -> str` - base64 `data:image/png;base64,...` URL for the payload.

## Config additions (`charlie/config.py`)

```
vision_enabled: bool = os.getenv("VISION_ENABLED", "false").lower() == "true"
vision_llm_url: str = os.getenv("VISION_LLM_URL", "")
vision_llm_key: str = os.getenv("VISION_LLM_KEY", "no-key")
vision_llm_model: str = os.getenv("VISION_LLM_MODEL", "")
```
This is a **separate** endpoint from `small_llm_*`/`big_llm_*` - the text models stay text-only; vision
is opt-in and independently configured (matches "local vision model" decision). Add placeholders to `.env.example`.

## The multimodal seam - core.py changes (keep this conditional and narrow)

1. Add `self._pending_vision_image: Optional[str] = None` to `Brain.__init__`, plus a setter
   `set_pending_vision_image(url: Optional[str])` (same injection pattern as `set_blackboard`/`set_memory_store`).
2. New tool `desktop_screenshot` (registered in `charlie/tools.py`, ungated/read-only): captures the
   screen, SoM-annotates it against the current mark cache (UIA + OCR marks from Phases 1/2a), and:
   - **Returns the text set-of-marks as its string tool-result** (so even without vision enabled, or on
     a text-only model, the model still gets a usable result - never a dead end).
   - As a side effect, stashes the annotated image's data URL via `brain.set_pending_vision_image(...)`.
3. **`_build_payload`** (`core.py:1428`) is the *only* place the image is folded into the actual LLM
   request, and only when all of these hold: `config.vision_enabled` AND `self._use_native_tools` AND
   `self._pending_vision_image` is set. When true, rewrite **only the last user message's** `content`
   from a plain string to `[{"type":"text",...}, {"type":"image_url","image_url":{"url":...}}]`. Clear
   `self._pending_vision_image` immediately after building that one payload - it must never leak into a
   later turn or a later message.
4. **History persistence stays string-only.** The data URL is never written to `session_store`
   (`core.py:1493-1495` and the SQLite schema are untouched). The image exists only for the single
   payload build that consumes it. This is what keeps the change small and safe: no schema migration, no
   risk of images bloating stored history.
5. Route requests that carry an image to `vision_llm_url`/`vision_llm_model` (either a third httpx
   client alongside `self.client`/`self._big_client`, or swap the target URL/model in
   `_stream_completion` specifically when the payload contains an image block).

## When this tier engages

Only after Phase 1 (UIA) and Phase 2a (OCR) both fail to produce a usable target - i.e. `desktop_observe`
returns marks, but none match what the user described, or the tree/OCR pass was empty and the surface is
genuinely graphical (canvas, game, icon-only toolbar). The model (or a thin auto-escalation check) calls
`desktop_screenshot` at that point. Once it returns SoM mark ids, clicks/types go through the **exact
same Phase 1 effectors** (`desktop_click`, `desktop_type`, etc.) - gate, arm/confirm, panic hotkey, and
credential hard-stop are all reused unchanged.

## Verification

1. `uv run ruff check .` / `uv run pytest -v` pass.
2. With `VISION_ENABLED=false` (default): run a normal Phase 1/2a task end to end. Confirm **zero**
   errors or 400s from the text model - proving the array-content injection truly never fires when disabled.
3. Set `VISION_ENABLED=true` plus a real local vision endpoint. Open a UIA-blind, OCR-unreadable surface
   (e.g. MS Paint canvas) and say "draw a small circle in the middle." Confirm: `desktop_observe` returns
   near-empty -> `desktop_screenshot` fires -> vision model returns a mark/coordinate -> **one** arm/confirm
   gate -> the action executes.
4. Confirm session history in `sessions.db` after a vision-tier turn contains no embedded image data -
   only the text tool-result strings.
5. Confirm the credential hard-stop and panic hotkey still function identically for vision-resolved actions.

## Friction / risks

- Local vision models are typically slower and less precise on raw coordinates than cloud ones - SoM
  (click by small integer id, not pixel guess) is the mitigation baked into the design.
- Some local OpenAI-compatible servers reject `image_url` content blocks outright. Keeping the vision
  endpoint fully separate and feature-flagged means this failure mode never touches the text path.

## Explicitly out of scope for this phase

Dashboard live view of the screenshots (Phase 3), the MARVEL operator persona (Phase 4).

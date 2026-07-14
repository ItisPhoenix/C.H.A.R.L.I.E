# Phase 2a - OCR Fallback Tier (Tesseract, text-only-model compatible)

> Part of the "Charlie -> Agentic Windows OS" initiative. Standalone; read on its own.
> Prerequisite: Phase 1 (native UIA MVP) shipped and verified.

## Goal

Extend perception to UIA-blind surfaces (canvas apps, games, some Electron/custom-drawn UIs) **without**
requiring a vision model, so local text-only models keep driving. Tesseract OCR is already installed on
the machine - this phase just wires it in as a second, cheaper perception tier that produces the exact
same output shape as Phase 1's UIA tree, so nothing downstream needs to change.

## Why this exists as its own phase (before the vision model)

Phase 2b (vision model + multimodal message plumbing) is real added complexity: a new endpoint, new
config, new payload-building logic. OCR needs none of that - it's a screenshot plus a text/box
extraction that slots into the *same* `Element` / set-of-marks / effector pipeline Phase 1 already
built. Ship this first; it likely closes most of the "UIA returned nothing" gap on its own.

## New file: `charlie/desktop/ocr.py`

- Guarded import block (same pattern as `charlie/desktop/__init__.py`): `pytesseract`, `mss`, `PIL`.
- `capture(region: Optional[Tuple[int,int,int,int]] = None) -> bytes` - screen or region capture via
  `mss`, encoded as PNG bytes via Pillow. This function is also reused by Phase 2b - do not duplicate it there.
- `ocr_marks(png_bytes: bytes, min_conf: int = 60) -> List[Element]` - run
  `pytesseract.image_to_data(..., output_type=Output.DICT)`, filter by confidence, group tokens into
  words/lines, and emit results using the **exact same `Element` shape** defined in
  `charlie/desktop/uia.py` (`mark_id`, `name` = recognized text, `control_type` = `"ocr_text"`,
  `bounds` = bounding box, `is_password=False`, `is_offscreen=False`). This is the key design point:
  `serialize_marks` and `resolve_mark` from Phase 1 are reused completely unchanged.

## Config additions (`charlie/config.py`, same block as Phase 1's desktop flags)

```
desktop_ocr_enabled: bool = os.getenv("DESKTOP_OCR_ENABLED", "true").lower() == "true"
tesseract_cmd: str = os.getenv("TESSERACT_CMD", "")
```
`tesseract_cmd` lets the user point at their existing Tesseract install if it's not on PATH; if empty,
`pytesseract` uses its default lookup. Add both to `.env.example`.

## Wiring into the existing tool (`charlie/tools.py`)

- Extend `desktop_observe` (from Phase 1): after `snapshot_tree()`, if `config.desktop_ocr_enabled`,
  always also call `ocr.capture()` + `ocr.ocr_marks()` and **merge** the OCR elements into the same
  per-turn mark cache that UIA elements use, continuing the `mark_id` sequence. The model receives one
  unified set-of-marks list and never needs to know which tier produced which mark.
  **Revised from an element-count threshold** ("skip OCR if UIA found >= 2 elements"): a browser's
  toolbar alone can hand back 2+ real UIA elements while the entire page content underneath is
  invisible to UIA, so a count threshold can't reliably distinguish "UIA-blind window" from "window
  with a toolbar and nothing else exposed." Always running both is simpler and correct at the cost of
  one extra screenshot+OCR pass per `desktop_observe` call.
- Optional new tool `desktop_read_screen` (ungated, read-only) that force-runs an OCR pass regardless of
  UIA results - useful for "read what's on my screen" requests with no interactive elements involved.

## What does NOT change

- The gate ladder in `core.py:_exec_one`, the arm/confirm logic, the panic hotkey, the credential
  hard-stop, the dedup bypass - all unchanged and automatically apply to OCR-resolved clicks too, since
  they're driven through the same `desktop_click`/`desktop_type`/etc. tools.
- No multimodal/image message plumbing - OCR output is plain text, same as UIA.

## Actual implementation note: `actions.py` needed a small generalization

OCR marks have no live UIA control handle (only a bounding box), so `click_mark`/`type_text`/
`invoke_mark` could not stay byte-for-byte unchanged as originally assumed above. `charlie/desktop/uia.py`
gained duck-typing helpers -- `resolve_bounds()`, `resolve_is_password()`, `resolve_name()` -- that
return the right value whether the resolved mark is a live UIA control or an OCR `Element`.
`charlie/desktop/actions.py`'s effectors call these instead of reading `control.BoundingRectangle`/
`control.IsPassword` directly. `invoke_mark` on an OCR-sourced mark returns an error string (no invoke
pattern exists for recognized text) rather than crashing. The gate ladder, arm/confirm, panic hotkey,
credential hard-stop, and dedup bypass are still exactly as designed above -- only the mark-resolution
layer underneath them changed.

## New dependency

`pytesseract` (thin wrapper around the already-installed Tesseract binary). Add to the same
optional/extra dependency group as Phase 1's Windows-only deps; lazy-imported and guarded identically.

## Friction / risks

- OCR gives you text but not semantic role - a recognized word "Delete" could be a button, a label, or
  body text. Keep OCR-sourced marks ranked below UIA-sourced marks when both exist, and let the model's
  own judgment (plus the arm/confirm gate) handle ambiguity rather than trying to auto-classify.
- OCR accuracy depends on font rendering/DPI scaling; a `min_conf` filter (default 60) trims noise but
  won't eliminate misreads - acceptable since destructive actions are still gated.

## Verification

1. `uv run ruff check .` / `uv run pytest -v` pass.
2. Open a UIA-blind window with visible text (e.g. a canvas-drawn app, or a plain screenshot viewer).
   Call `desktop_observe`; confirm it returns OCR-sourced marks when the UIA tree is empty.
3. With a **text-only local model** configured (no vision endpoint set up), say "click the OK button" on
   such a surface. Confirm it resolves via OCR and clicks correctly - proving this tier needs no vision model.
4. Confirm the arm/confirm prompt, panic hotkey, and credential hard-stop all still function identically
   for OCR-resolved actions (they should, since nothing in the effector/gate path changed).

## Explicitly out of scope for this phase

Vision models, screenshots sent to an LLM, image message plumbing, dashboard live view - see Phase 2b.

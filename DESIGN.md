---
name: Charlie Dashboard
description: Real-time mission-control surface for an autonomous local AI agent
colors:
  canvas: "#000000"
  signal-violet: "#a855f7"
  signal-violet-soft: "#c084fc"
  signal-violet-dim: "rgba(168, 85, 247, 0.12)"
  signal-violet-border: "rgba(168, 85, 247, 0.25)"
  channel-teal: "#06b6d4"
  channel-teal-soft: "#22d3ee"
  channel-teal-dim: "rgba(6, 182, 212, 0.12)"
  glass-bg: "rgba(0, 0, 0, 0.6)"
  glass-bg-2: "rgba(0, 0, 0, 0.55)"
  glass-bg-modal: "rgba(0, 0, 0, 0.88)"
  glass-border: "rgba(255, 255, 255, 0.07)"
  glass-border-hover: "rgba(255, 255, 255, 0.14)"
  surface-hover: "rgba(255, 255, 255, 0.03)"
  text-primary: "#f4f6fa"
  text-secondary: "#d1d5db"
  text-muted: "#6b7280"
  status-idle: "#4b5563"
  status-listening: "#06b6d4"
  status-thinking: "#a855f7"
  status-speaking: "#10b981"
  status-error: "#ef4444"
  status-warning: "#f59e0b"
  status-success: "#10b981"
typography:
  display:
    fontFamily: "Space Grotesk, sans-serif"
    fontSize: "clamp(1.25rem, 2vw, 1.75rem)"
    fontWeight: 600
    lineHeight: 1.15
    letterSpacing: "-0.01em"
  body:
    fontFamily: "Inter, Segoe UI, system-ui, sans-serif"
    fontSize: "0.8125rem"
    fontWeight: 400
    lineHeight: 1.45
    letterSpacing: "normal"
  label:
    fontFamily: "Inter, Segoe UI, system-ui, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 500
    lineHeight: 1.3
    letterSpacing: "normal"
  mono:
    fontFamily: "JetBrains Mono, monospace"
    fontSize: "0.75rem"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "normal"
rounded:
  sm: "6px"
  md: "8px"
  lg: "12px"
  xl: "16px"
  pill: "9999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "16px"
  lg: "24px"
components:
  panel-glass:
    backgroundColor: "{colors.glass-bg}"
    textColor: "{colors.text-primary}"
    rounded: "{rounded.lg}"
    padding: "16px"
  status-pill:
    backgroundColor: "{colors.glass-bg-2}"
    textColor: "{colors.text-secondary}"
    rounded: "{rounded.pill}"
    padding: "6px 12px"
  nav-item:
    backgroundColor: "transparent"
    textColor: "{colors.text-secondary}"
    rounded: "{rounded.md}"
    padding: "8px 10px"
  nav-item-active:
    backgroundColor: "{colors.signal-violet-dim}"
    textColor: "{colors.text-primary}"
    rounded: "{rounded.md}"
    padding: "8px 10px"
---

# Design System: Charlie Dashboard

## 1. Overview

**Creative North Star: "The Night Console"**

The Night Console is a dark cockpit glowing faintly against pure black: a mission-control readout for a system that runs mostly on its own, checked in on rather than driven at every moment. The canvas is true black (`#000000`), never near-black or navy-tinted; light only exists where it's earned, as glass-panel translucency, a status dot, or the Signal Violet accent marking whatever Charlie is actively doing. Every surface is a hairline-bordered pane of frosted black glass floating over that void, layered by opacity rather than by shadow.

This system explicitly rejects the generic SaaS admin panel: no cream or off-white card grids, no hero-metric tiles, no tiny uppercase eyebrows over every section. It also rejects decoration for its own sake — the purple isn't a brand flourish, it's the color of "thinking," and it earns its place by meaning something.

**Key Characteristics:**
- Pure black canvas, glass panels stacked by opacity, no shadows
- Signal Violet marks active/AI state; Channel Teal marks listening/live-input state
- Dense, small-type UI (`text-xs`/`text-sm` throughout) — information density over whitespace
- Status color is the primary semantic language, echoed through lucide-react icons
- Motion is quick and directional (rise/fade, ~0.2–0.35s), never decorative flourish

## 2. Colors

A near-monochrome black system with two active-state accents; color is reserved for meaning, not styling.

### Primary
- **Signal Violet** (`#a855f7`, soft `#c084fc`, dim `rgba(168,85,247,.12)`, border `rgba(168,85,247,.25)`): the "AI is thinking / acting" color. User-configurable at runtime (`useCharlieStore.setAccentColor()`), so treat it as a token, never a hardcoded hex in new components. Marks active nav items, the thinking status state, active accent borders.

### Secondary
- **Channel Teal** (`#06b6d4`, soft `#22d3ee`, dim `rgba(6,182,212,.12)`): the "listening / live input" color. Also the fixed keyboard-focus ring color (`:focus-visible`), independent of the user's chosen accent — focus must stay legible even if a user picks a teal-adjacent accent.

### Neutral
- **Void Black** (`#000000`): the canvas. Never substitute a near-black or navy.
- **Glass Black** (`rgba(0,0,0,.6)` / `.55` / `.88` for modal): panel backgrounds, layered by opacity for depth — base panel, secondary panel, modal.
- **Hairline White** (`rgba(255,255,255,.07)` default border, `.14` hover, `.03` surface-hover wash): the only whites in the system, used exclusively as borders and hover washes, never as fill.
- **Text Fog** (`#f4f6fa` primary, `#d1d5db` secondary, `#6b7280` muted): three-step text hierarchy on black.

### Status (semantic, not decorative)
- **idle** `#4b5563` · **listening** `#06b6d4` · **thinking** `#a855f7` · **speaking** `#10b981` · **error** `#ef4444` · **warning** `#f59e0b` · **success** `#10b981` — each has a `-dim` background variant for pill/badge fills. These map 1:1 to the voice pipeline's real states; don't repurpose them for unrelated UI meaning.

### Named Rules
**The Earned Light Rule.** Color exists to mean something (a status, an active state, a focus ring) — it is never applied purely for visual interest. If a new UI element wants color and isn't reporting state, it should stay in the Text Fog / Hairline White neutral range instead.

## 3. Typography

**Display Font:** Space Grotesk (with sans-serif fallback)
**Body Font:** Inter (with Segoe UI, system-ui fallback)
**Label/Mono Font:** JetBrains Mono, for logs, timestamps, and raw data

**Character:** A geometric, slightly technical display face over a neutral humanist body — confident and legible at small sizes, never ornamental. Mono is reserved for anything that is literally data (logs, IDs, timestamps), which reinforces the console framing.

### Hierarchy
- **Display** (600, `clamp(1.25rem, 2vw, 1.75rem)`, 1.15 line-height, -0.01em): panel titles, the Charlie wordmark. Used sparingly — this is a dense dashboard, not an editorial page.
- **Body** (400, 0.8125rem/13px, 1.45 line-height): chat text, descriptions, primary reading content.
- **Label** (500, 0.75rem/12px, 1.3 line-height): nav items, buttons, status pills, form labels — the dominant size across the UI.
- **Mono** (400, 0.75rem/12px, 1.5 line-height): logs, tool-call payloads, timestamps, session IDs.

### Named Rules
**The Twelve-Pixel Default Rule.** Absent a specific reason to go bigger, UI text is Label size (12px) or Body size (13px). Display size is a rare event reserved for real section headers, not a default heading style. Enforced in code: the codebase's former scatter of `text-[10px]`/`text-[11px]` micro-sizes has been consolidated into the single `text-xs` (12px) Label token.

## 4. Elevation

Flat and translucent, no box-shadows anywhere in the system. Depth comes entirely from stacked opacity: the void canvas at 0%, a base glass panel at `rgba(0,0,0,.6)`, a secondary/nested panel at `rgba(0,0,0,.55)`, and a modal layer at `rgba(0,0,0,.88)` — each paired with `backdrop-blur` and a hairline `rgba(255,255,255,.07)` border. Higher "elevation" reads as *more opaque and more blurred*, not as a drop shadow.

### Named Rules
**The No-Shadow Rule.** Never add `box-shadow` for depth. If a component needs to read as "above" another, raise its glass-bg opacity and blur radius instead, and keep the hairline border. Shadows on pure black are invisible anyway — the opacity stack is the only elevation language that works here.

## 5. Components

### Buttons / Interactive rows
- **Shape:** `rounded-lg` (8px) for buttons and rows, `rounded-full` for pills and dots.
- **Default:** transparent background, Text Secondary label, Hairline border only where the element needs a visible boundary (most nav rows have none at rest).
- **Hover:** background steps to Surface Hover (`rgba(255,255,255,.03)`) or Glass Border Hover; no color shift on text.
- **Active/selected:** background steps to the accent-dim tone (`--color-accent-dim` for AI-driven state, teal-dim for input state), text steps to Text Primary.
- **Micro-interaction:** `active:scale-[0.98]` on press — the only scale transform in the system; keep it exclusive to this or it stops reading as feedback.

### Status Pills / Chips
- **Style:** Glass-bg-2 background, `backdrop-blur-sm`, `rounded-full`, Hairline border, Label-size text, a leading status-color dot or icon.
- **State:** the dot/icon color is always one of the seven status tokens — never an arbitrary color.

### Panels / Containers
- **Corner style:** `rounded-lg` to `rounded-2xl` depending on panel size (larger panel, larger radius, up to 16px). Never exceed 16px — this is a console, not a soft consumer app.
- **Background:** Glass Black at the appropriate opacity tier (see Elevation).
- **Shadow strategy:** none (see Elevation).
- **Border:** Hairline White at rest, Hairline Hover on interaction.
- **Internal padding:** 16px is the panel default; compact rows use 8–10px.

### Inputs / Fields
- **Style:** Glass-bg background, Hairline border, `rounded-lg`.
- **Focus:** `:focus-visible` outline in Channel Teal (`2px solid`, `2px offset`) — fixed, independent of the user's accent color.

### Navigation (Sidebar)
- Compact rows (`rounded-lg`, `px-2.5 py-2`, `text-xs`), icon + label with `gap-2.5`. Active route uses the signal-violet-dim background + Text Primary; inactive routes are Text Secondary on transparent. Collapsible width with a CSS transition, not a hard cut.

### Signature Component: Control Center Orb
A canvas-rendered fibonacci-spiral particle sphere (~640 points) that recolors live by voice state (idle/listening/thinking/speaking), the dashboard's centerpiece and the clearest expression of the Night Console idea — status as ambient, living light rather than a static badge. Status pills and captions rise (`anim-rise`, 0.35s) around it on state change.

## 6. Do's and Don'ts

### Do:
- **Do** keep the canvas pure black (`#000000`) — never substitute near-black or a navy tint.
- **Do** treat Signal Violet and Channel Teal as semantic (AI-state / input-state), not decorative brand colors.
- **Do** build depth with opacity + blur layering (glass-bg tiers), never `box-shadow`.
- **Do** default UI text to 12–13px (Label/Body); reserve Display size for real headers.
- **Do** route every status color through the seven-token status palette, including its `-dim` variant for fills.

### Don't:
- **Don't** use a cream/off-white card-grid admin-panel look — this is explicitly the anti-reference (see PRODUCT.md).
- **Don't** add tiny uppercase tracked eyebrows above sections; not part of this system's vocabulary.
- **Don't** add `box-shadow` anywhere for elevation; it's invisible on black and breaks the flat-glass language.
- **Don't** introduce a light theme; this is a single dark HUD system, not light/dark parity.
- **Don't** use `border-left`/`border-right` colored stripes as an accent pattern.
- **Don't** hand-roll a new glass background value; use the existing `glass-bg` / `glass-bg-2` / `glass-bg-modal` tiers so panels stay visually consistent.
- **Don't** reach for arbitrary Tailwind sizes below `text-xs` (`text-[10px]`, `text-[11px]`, etc.); the Label token is the floor.

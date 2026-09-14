# Charlie spatial design system

This is the production presentation contract for the React HUD. The scene is
one persistent dark environment; workspaces project information into it rather
than creating dashboard pages.

## Foundations

- Background: deep navy/near-black with a restrained cyan grid, radial light,
  noise, and edge vignette from `EnvironmentLayer`.
- Typography: `--font-display` for titles, `--font-body` for readable content,
  and `--font-data` only for labels, revisions, timestamps, and measurements.
- Tokens live in `src/theme/tokens.css`: use the `--type-*`, `--space-*`,
  `--hud-line*`, and `--hud-glow-*` variables instead of one-off values.
- Primary signal is cool cyan/teal. Green, amber, and red are reserved for
  canonical success, degraded/warning, and error states.

## Spatial composition

- Prefer typography, whitespace, alignment, partial hairlines, gradients,
  masks, and glow to communicate structure.
- Do not add cards, framed page surfaces, or floating containers for ordinary
  content. Approval, destructive actions, settings, and text entry may retain
  explicit containment.
- Research, briefing, vision, system, and tasks remain dynamic workspace
  projections. They must preserve truthful empty/unavailable states and may
  render only data supplied by the canonical contract.
- The existing `CharlieRing` is the identity anchor. Size and atmospheric glow
  may adapt between centered and docked modes; do not add competing ring
  geometry or redundant docked status text.

## Motion and responsive behavior

- Use the existing spatial easing and short resolve/trace transitions. Reduced
  motion removes animation while preserving state changes.
- Protect the core safe area at 1920x1080, 1366x768, and 800x600. Workspace
  content may scroll vertically when necessary, never horizontally.
- Controls stay quiet until hover/focus. Every interactive control keeps a
  visible keyboard focus treatment.

## Reference calibration

- The canonical wordmark is `C.H.A.R.L.I.E.` in centered and docked modes.
- The complete centered orbit system targets roughly 60% of viewport height;
  the complete docked orbit targets roughly 46% of viewport height. Positioning
  remains normalized to the centered 50%/50% origin and the docked lower-right
  safe area.
- Ring light is layered on one circumference: deep-blue atmosphere, cyan bloom,
  electric band, thin white-hot core, and one moving partial sweep. No extra
  visible concentric geometry is added.
- Media frames feather into the environment. Missing media is a spatial
  unavailable state, never a fabricated black panel.

## Truth boundary

Backend/main remains authoritative for runtime, task, research, briefing,
vision, topology, and approval data. React stores and renders projections only;
it must not invent healthy defaults, IDs, timestamps, geometry, or media.

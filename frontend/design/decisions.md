# Charlie Jarvis stage decisions

- Matte charcoal `#171717`, raised surface `#222222`, hairline `#383838`.
- Primary text `#F2F2F2`, secondary `#B8B8B8`; white presence ring. No cyan/RGB glow, decorative telemetry, mascot, or production fixture content.
- Segoe UI/system font stack and native CSS; avoid extra UI and icon dependencies.
- One live visual stage, Activity drawer on demand, and fixed command input. Keep document scrolling disabled; constrain overflow to the stage or drawer.
- Python runtime owns scene and command truth. Fetch the initial snapshot, then apply only newer SSE snapshots. Record ordinary incoming event envelopes in Activity.
- Command responses are accepted only when the response explicitly reports `accepted: true` or `status: "accepted"`; acceptance is not completion.
- Gateway routing exists in `charlie/web_gateway.py`, but launch wiring and a shared scene response schema are absent in this checkout. The documented scene/event shapes are a frontend integration contract, not verified backend behavior.
- Keyboard labels/focus remain visible; reduced motion removes transitions.

# `charlie_soul.md` — Contract

`charlie_soul.md` is the *personality* file. It is a free-form markdown
document that defines who Charlie is, how it speaks, and what it
prefers. Two subsystems read or write it; everyone else leaves it
alone.

## Callers

| Module | What it does | Read/Write |
|--------|--------------|------------|
| `charlie/utils/persona.py::get_system_prompt` | Concatenates the soul's text into the system prompt sent to the LLM. | **Read** |
| `charlie/self_mod/soul_editor.py::SoulEditor` | Adds preferences and replaces whole sections, gated by trust level. | **Read + Write** |

If a third caller is added, update this table.

## Format

Plain markdown, UTF-8, LF line endings. The file may be empty; both
readers handle that case.

When `SoulEditor.update_preference("X")` is called and the file is
empty, it initialises with:

```markdown
# C.H.A.R.L.I.E. SOUL

## Preferences
```

`update_preference` then appends `- X` to the `## Preferences` section.
`update_section("Name", "Body")` either replaces an existing
`## Name` block or appends a new one.

Sections recognised by convention (none are mandatory):

- `## Identity` / `## Core Directives` / `## Communication Patterns`
- `## Operational Boundaries` / `## Architecture Awareness`
- `## Preferences` (auto-populated by `SoulEditor`)

The shipped `charlie_soul.md` includes all of these. See that file for
the canonical content.

## Security

- Reads: anyone with filesystem access (no auth).
- Writes: `SoulEditor._check_auth` blocks updates when the brain has
  no reference or the user is below the `Cooperative` trust level.
- The contract is intentionally small. Any module that wants to
  rewrite the soul must go through `SoulEditor`; raw `Path.write_text`
  is a bug.

## Distinguishing soul from other config

`charlie_soul.md` is **personality**, not configuration. Runtime
tuning lives in `charlie_config.json` and `.env`. LLM endpoint, model
name, security tier countdown, etc. do **not** belong in the soul.

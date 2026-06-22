# CLAUDE.md

All agent instructions for CHARLIE have been merged into the unified [**AGENTS.md**](AGENTS.md).
Please read that file before starting any work.

---

## Claude Code-Specific Notes

- Activate relevant skills first via the `using-superpowers` workflow before doing any non-trivial work.
- Use `uv run pytest -v` and `uv run ruff check .` for all Python changes.
- The `skill://` and `web_search` tools are your friends. Use them before writing new code.

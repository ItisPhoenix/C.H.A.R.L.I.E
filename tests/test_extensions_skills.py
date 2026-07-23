"""Tests for the SKILL.md adapter (charlie/extensions/skills.py)."""

import pytest

from charlie.extensions.skills import (
    format_skill_block,
    parse_skill_md,
    register_skill_scripts,
)

_SAMPLE = """---
name: demo-skill
description: A demo skill for tests
metadata:
  type: demo
scripts:
  - scripts/run.py
---

# Demo Skill

Do the demo thing when asked.
"""


class TestParseSkillMd:
    def test_parses_frontmatter_fields(self):
        manifest = parse_skill_md(_SAMPLE)
        assert manifest.name == "demo-skill"
        assert manifest.description == "A demo skill for tests"
        assert manifest.metadata == {"type": "demo"}
        assert manifest.scripts == ["scripts/run.py"]

    def test_parses_instructions_body(self):
        manifest = parse_skill_md(_SAMPLE)
        assert "Do the demo thing when asked." in manifest.instructions
        assert "---" not in manifest.instructions

    def test_missing_frontmatter_raises(self):
        with pytest.raises(ValueError, match="frontmatter"):
            parse_skill_md("# Just a heading, no frontmatter")

    def test_missing_name_raises(self):
        with pytest.raises(ValueError, match="name"):
            parse_skill_md("---\ndescription: no name here\n---\nbody")

    def test_defaults_when_optional_fields_absent(self):
        manifest = parse_skill_md("---\nname: bare\n---\nbody text")
        assert manifest.description == ""
        assert manifest.metadata == {}
        assert manifest.scripts == []


class TestFormatSkillBlock:
    def test_includes_name_and_instructions(self):
        manifest = parse_skill_md(_SAMPLE)
        block = format_skill_block(manifest)
        assert "demo-skill" in block
        assert "Do the demo thing when asked." in block


class _FakeRegistry:
    def __init__(self):
        self.registered = {}

    def register_tool(self, name, description, schema):
        def decorator(func):
            self.registered[name] = {"description": description, "schema": schema, "func": func}
            return func

        return decorator


class TestRegisterSkillScripts:
    def test_registers_one_tool_per_script(self):
        manifest = parse_skill_md(_SAMPLE)
        registry = _FakeRegistry()

        registered = register_skill_scripts(registry, manifest, runner=lambda path, args: "ok")

        assert registered == ["skill_demo_skill_run"]
        assert "skill_demo_skill_run" in registry.registered

    def test_invoking_registered_tool_calls_runner_with_path_and_args(self):
        manifest = parse_skill_md(_SAMPLE)
        registry = _FakeRegistry()
        calls = []

        def runner(path, args):
            calls.append((path, args))
            return "done"

        register_skill_scripts(registry, manifest, runner=runner)
        func = registry.registered["skill_demo_skill_run"]["func"]

        result = func(args=["--flag"])

        assert result == "done"
        assert calls == [("scripts/run.py", ["--flag"])]

    def test_no_scripts_registers_nothing(self):
        manifest = parse_skill_md("---\nname: no-scripts\n---\nbody")
        registry = _FakeRegistry()

        registered = register_skill_scripts(registry, manifest, runner=lambda p, a: "x")

        assert registered == []

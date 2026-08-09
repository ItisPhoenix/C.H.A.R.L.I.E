"""Tests for the tier-3 self-extension adapter: Charlie-authored tools parsed and registered at runtime."""

import pytest

from charlie.extensions.generated import parse_generated_tool, register_generated_tool
from charlie.extensions.install import declared_tools_for, install_extension
from charlie.tools import ToolRegistry

_VALID_SOURCE = '''
def double_it(n):
    """Doubles the given number and returns it as a string."""
    return str(int(n) * 2)
'''


class TestParseGeneratedTool:
    def test_parses_name_description_schema(self):
        spec = parse_generated_tool("double_it", _VALID_SOURCE)
        assert spec.name == "double_it"
        assert "doubles" in spec.description.lower()
        assert spec.schema["properties"] == {"n": {"type": "string"}}
        assert spec.schema["required"] == ["n"]

    def test_func_is_callable(self):
        spec = parse_generated_tool("double_it", _VALID_SOURCE)
        assert spec.func(n="21") == "42"

    def test_rejects_name_mismatch(self):
        with pytest.raises(ValueError, match="must be named"):
            parse_generated_tool("triple_it", _VALID_SOURCE)

    def test_rejects_missing_docstring(self):
        source = "def f(n):\n    return n\n"
        with pytest.raises(ValueError, match="docstring"):
            parse_generated_tool("f", source)

    def test_rejects_multiple_top_level_statements(self):
        source = "import os\ndef f(n):\n    \"\"\"desc\"\"\"\n    return n\n"
        with pytest.raises(ValueError, match="exactly one"):
            parse_generated_tool("f", source)

    def test_rejects_syntax_error(self):
        with pytest.raises(SyntaxError):
            parse_generated_tool("f", "def f(:\n    pass")


class TestRegisterGeneratedTool:
    def test_registers_into_registry_and_is_callable(self):
        registry = ToolRegistry()
        spec = parse_generated_tool("double_it", _VALID_SOURCE)
        names = register_generated_tool(registry, spec)
        assert names == ["double_it"]
        assert registry.execute_tool("double_it", {"n": "10"}) == "20"


class TestInstallDotPyGeneratedKind:
    def test_declared_tools_for_generated(self):
        assert declared_tools_for("generated", "double_it", "", _VALID_SOURCE, []) == ["double_it"]

    def test_install_extension_generated(self):
        registry = ToolRegistry()
        tool_names, _mcp = install_extension(
            "generated", "double_it", "", _VALID_SOURCE,
            registry=registry, plugin_manager=None, mcp_client=None, plugin_allow_dirs=[],
        )
        assert tool_names == ["double_it"]
        assert registry.execute_tool("double_it", {"n": "5"}) == "10"

    def test_unknown_kind_still_rejected(self):
        with pytest.raises(ValueError, match="Unknown extension kind"):
            declared_tools_for("not-a-kind", "x", "", "", [])

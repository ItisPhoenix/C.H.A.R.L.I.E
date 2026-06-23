import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from charlie.tools import registry, ToolRegistry, web_search, shell_execute, file_read, file_write


def test_registry_registration_and_schema():
    definitions = registry.get_tool_definitions()
    names = {d["function"]["name"] for d in definitions}
    assert names == {"web_search", "shell_execute", "file_read", "file_write"}
    assert any(d["function"]["parameters"]["required"] == ["query"] for d in definitions)


def test_file_write_and_file_read(tmp_path):
    target = tmp_path / "notes.txt"
    message = file_write(str(target), "hello tools")
    assert "Successfully wrote to" in message
    assert target.exists()
    content = file_read(str(target))
    assert content.strip() == "hello tools"


def test_shell_execute_lists_env(monkeypatch):
    import os

    output = shell_execute("echo OK")
    assert "OK" in output
    env_output = shell_execute("set" if os.name == "nt" else "env")
    assert isinstance(env_output, str)


def test_tool_registry_unknown_tool_returns_error():
    local_registry = ToolRegistry()
    assert local_registry.execute_tool("not-registered", {}) == "Error: Tool 'not-registered' is not registered."


def test_web_search_returns_fallback_without_api_keys(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("EXA_API_KEY", raising=False)

    result = web_search("unit-test-only-query")
    assert isinstance(result, str)
    assert len(result) > 0

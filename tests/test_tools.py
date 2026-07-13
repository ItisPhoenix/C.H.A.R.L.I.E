import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os

from charlie.session_store import SessionStore
from charlie.tools import (
    _DIAGNOSTIC_COMMANDS,
    ToolRegistry,
    _decompose_query,
    _merge_search_results,
    _needs_decomposition,
    file_read,
    file_write,
    is_shell_command_blocked,
    memory,
    registry,
    session_search,
    shell_execute,
    system_diagnostics,
    web_search,
)


def test_registry_registration_and_schema():
    definitions = registry.get_tool_definitions()
    names = {d["function"]["name"] for d in definitions}
    assert names == {
        "delegate_to_agent",
        "web_search",
        "shell_execute",
        "system_diagnostics",
        "file_read",
        "file_write",
        "memory",
        "vector_memory",
        "session_search",
        "graph_add_fact",
        "graph_query",
        "graph_consolidate",
    }
    assert any(
        d["function"]["parameters"]["required"] == ["query"] for d in definitions
    )


def test_file_write_and_file_read(tmp_path):
    target = tmp_path / "notes.txt"
    message = file_write(str(target), "hello tools")
    assert "Successfully wrote to" in message
    assert target.exists()
    content = file_read(str(target))
    assert content.strip() == "hello tools"


def test_resolve_user_placeholders():
    import getpass

    from charlie.tools import _resolve_user_placeholders
    curr_user = getpass.getuser()

    p1 = "C:\\Users\\YourUsername\\Documents\\charlie.txt"
    p2 = "C:\\Users\\username\\Documents\\charlie.txt"
    p3 = "C:\\Users\\user\\Documents\\charlie.txt"

    assert _resolve_user_placeholders(p1) == f"C:\\Users\\{curr_user}\\Documents\\charlie.txt"
    assert _resolve_user_placeholders(p2) == f"C:\\Users\\{curr_user}\\Documents\\charlie.txt"
    assert _resolve_user_placeholders(p3) == f"C:\\Users\\{curr_user}\\Documents\\charlie.txt"

def test_shell_execute_lists_env(monkeypatch):
    import os

    output = shell_execute("echo OK")
    assert "OK" in output
    env_output = shell_execute("set" if os.name == "nt" else "env")
    assert isinstance(env_output, str)


def test_shell_execute_blocks_metacharacters_and_keywords():
    """Locks the exact error text shell_execute returns via the shared
    is_shell_command_blocked() guard, now also reused by charlie.recovery."""
    assert shell_execute("echo a & type secrets.txt") == (
        "Error: Shell metacharacters (;, |, &, `, $, (, )) are not allowed."
    )
    assert shell_execute("format c: /q") == (
        "Error: Command blocked -- risky keyword 'format '"
    )


def test_system_diagnostics_unknown_check():
    result = system_diagnostics("not_a_real_check")
    assert result.startswith("Error: unknown diagnostic check")


def test_system_diagnostics_rejects_injection_attempt():
    """The `check` value is looked up in a fixed dict, never interpolated
    into the shell command string -- an injection-style value must be
    rejected as an unknown check, not executed."""
    result = system_diagnostics("disk; Remove-Item C:\\ -Recurse -Force")
    assert result.startswith("Error: unknown diagnostic check")


def test_diagnostic_commands_are_all_powershell():
    for command in _DIAGNOSTIC_COMMANDS.values():
        assert "powershell" in command.lower()


def test_system_diagnostics_runs_real_command(monkeypatch):
    """On win32 (this dev/CI platform), a known check must actually execute
    and return real output, not just validate the enum."""
    monkeypatch.setattr("sys.platform", "win32")
    result = system_diagnostics("cpu")
    assert "Error" not in result or "timed out" in result.lower()


def test_is_shell_command_blocked_direct():
    assert is_shell_command_blocked("dir") is None
    assert is_shell_command_blocked("rm -rf /") == (
        "Command blocked -- risky keyword 'rm -rf'"
    )
    assert is_shell_command_blocked("echo `whoami`") == (
        "Shell metacharacters (;, |, &, `, $, (, )) are not allowed."
    )


def test_tool_registry_unknown_tool_returns_error():
    local_registry = ToolRegistry()
    assert (
        local_registry.execute_tool("not-registered", {})
        == "Error: Tool 'not-registered' is not registered."
    )


def test_web_search_returns_fallback_without_api_keys(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("EXA_API_KEY", raising=False)

    result = web_search("unit-test-only-query")
    assert isinstance(result, str)
    assert len(result) > 0


def test_memory_add_opinions(tmp_path, monkeypatch):
    """Test adding an opinion via the memory tool."""
    opinions_file = tmp_path / "OPINIONS.md"
    monkeypatch.setattr("charlie.tools.config.opinions_file", str(opinions_file))
    result = memory("add", "opinions", "I prefer dark chocolate over milk chocolate.")
    assert "Updated" in result
    assert opinions_file.exists()
    content = opinions_file.read_text(encoding="utf-8")
    assert "dark chocolate" in content


def test_memory_replace_opinions(tmp_path, monkeypatch):
    """Test replacing an entry in opinions."""
    opinions_file = tmp_path / "OPINIONS.md"
    opinions_file.write_text("I like coffee.§I prefer tea.", encoding="utf-8")
    monkeypatch.setattr("charlie.tools.config.opinions_file", str(opinions_file))
    result = memory("replace", "opinions", "I love espresso.", old_text="coffee")
    assert "Updated" in result
    content = opinions_file.read_text(encoding="utf-8")
    assert "espresso" in content
    assert "coffee" not in content
    assert "I prefer tea." in content


def test_memory_remove_opinions(tmp_path, monkeypatch):
    """Test removing an entry from opinions."""
    opinions_file = tmp_path / "OPINIONS.md"
    opinions_file.write_text("I like tea.§I like coffee.", encoding="utf-8")
    monkeypatch.setattr("charlie.tools.config.opinions_file", str(opinions_file))
    result = memory("remove", "opinions", old_text="coffee")
    assert "Updated" in result
    content = opinions_file.read_text(encoding="utf-8")
    assert "I like tea." in content
    assert "coffee" not in content


def test_memory_opinions_max_chars(tmp_path, monkeypatch):
    """Test that opinions max char limit is enforced."""
    opinions_file = tmp_path / "OPINIONS.md"
    opinions_file.write_text("x" * 800, encoding="utf-8")
    monkeypatch.setattr("charlie.tools.config.opinions_file", str(opinions_file))
    result = memory("add", "opinions", "y")
    assert "full" in result.lower() or "capacity" in result.lower()


def test_memory_invalid_target():
    """Test that invalid target returns error."""
    result = memory("add", "invalid_target", "content")
    assert "Error" in result
    assert "must be" in result


def test_needs_decomposition_compare():
    """Test that 'compare X and Y' triggers decomposition."""
    assert _needs_decomposition("compare React and Vue")


def test_needs_decomposition_long_query():
    """Test that long queries trigger decomposition."""
    assert _needs_decomposition("what is the best framework for building web apps")


def test_needs_decomposition_simple():
    """Test that simple queries do not trigger decomposition."""
    assert not _needs_decomposition("latest news")


def test_decompose_query_compare():
    """Test decomposition of comparison queries."""
    result = _decompose_query("compare React and Vue for web development")
    assert len(result) == 2
    assert "react" in result[0].lower()
    assert "vue" in result[1].lower()
    assert "web development" in result[0].lower()


def test_decompose_query_or():
    """Test decomposition of 'or' queries."""
    result = _decompose_query("Python or JavaScript for beginners")
    assert len(result) == 2
    assert "python" in result[0].lower()
    assert "javascript" in result[1].lower()


def test_decompose_query_simple_returns_original():
    """Test that simple queries return original."""
    result = _decompose_query("latest news")
    assert result == ["latest news"]


def test_merge_search_results_dedup():
    """Test that merge deduplicates by URL."""
    results = [
        "Title: A\nURL: https://example.com\nContent: Content A",
        "Title: A\nURL: https://example.com\nContent: Content A again",
        "Title: B\nURL: https://other.com\nContent: Content B",
    ]
    merged = _merge_search_results(results)
    assert merged.count("https://example.com") == 1
    assert "https://other.com" in merged


def test_session_search_formatting(tmp_path, monkeypatch):
    db_path = str(tmp_path / "tool_session_test.db")
    monkeypatch.setattr("charlie.tools.config.session_db_path", db_path)
    store = SessionStore(db_path)
    try:
        store.append("user", "remember this secret")
        store.append("assistant", "remembered the secret")
        formatted = session_search("secret")
        assert "[user]" in formatted
        assert "[assistant]" in formatted
        assert "remember this secret" in formatted
        assert "remembered the secret" in formatted
    finally:
        store.close()
        for f in [db_path, f"{db_path}-wal", f"{db_path}-shm"]:
            if os.path.exists(f):
                os.remove(f)

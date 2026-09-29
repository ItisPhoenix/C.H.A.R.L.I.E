from charlie.core import _format_text_tool_summary


def test_text_tool_summary_preserves_short_success_and_exact_error_content():
    calls = [
        {"name": "shell_execute", "arguments": {"command": "python --version"}},
        {"name": "shell_execute", "arguments": {"command": "exit 7"}},
    ]
    summary = _format_text_tool_summary(
        calls,
        ["Command executed successfully. STDOUT: Python 3.14.6", "Error: exit code 7"],
    )

    assert "Command executed successfully. STDOUT: Python 3.14.6" in summary
    assert "Error: exit code 7" in summary
    assert "The command is now running" not in summary

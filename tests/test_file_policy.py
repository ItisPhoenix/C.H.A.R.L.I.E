import hashlib
from pathlib import Path

import pytest

from charlie import tools as tools_module
from charlie.plugins import BrowserPlugin, CalendarPlugin, FilesystemPlugin, PluginManager
from charlie.security.policy import check_tool_call
from charlie.tools import ToolRegistry, disable_plugin, enable_plugin


def test_file_path_policy_and_verified_atomic_write(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    workspace.mkdir()
    home.mkdir()
    monkeypatch.setattr(tools_module, "_WORKSPACE_DIR", workspace)
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: home))

    assert not check_tool_call("file_read", {"path": str(workspace / "read.txt")}).needs_approval
    assert tools_module.get_path_gate_reason(
        str(workspace / "new.txt"), tool_name="file_write"
    ) is None

    for folder in ("Documents", "Downloads", "Desktop"):
        user_folder = home / folder
        user_folder.mkdir()
        assert not check_tool_call(
            "file_read", {"path": str(user_folder / "read.txt")}
        ).needs_approval
        assert tools_module.get_path_gate_reason(
            str(user_folder / "new.txt"), tool_name="file_write"
        ) is None

    outside = tmp_path / "outside.txt"
    outside_read = check_tool_call("file_read", {"path": str(outside)})
    assert outside_read.needs_approval and str(outside) in outside_read.reason
    outside_write = check_tool_call("file_write", {"path": str(outside), "content": "x"})
    assert outside_write.needs_approval and str(outside) in outside_write.reason

    existing = workspace / "existing.txt"
    existing.write_text("old", encoding="utf-8")
    overwrite = check_tool_call(
        "file_write", {"path": str(existing), "content": "new"}
    )
    assert overwrite.needs_approval and "overwrite" in overwrite.reason.lower()
    assert str(existing) in overwrite.reason
    download_overwrite = check_tool_call("download_public_pdf", {"path": str(existing)})
    assert download_overwrite.needs_approval
    assert str(existing) in download_overwrite.reason

    credential_reason = tools_module.get_path_gate_reason(
        str(workspace / ".env"), tool_name="file_read"
    )
    assert credential_reason is not None and "sensitive path" in credential_reason

    content = "verified file contents\n"
    result = tools_module.registry.execute_tool_structured(
        "file_write", {"path": str(workspace / "new.txt"), "content": content}
    )
    actual = (workspace / "new.txt").read_bytes()
    digest = hashlib.sha256(actual).hexdigest()
    assert result.structured_data["verified"] is True
    assert actual == content.encode("utf-8")
    assert result.structured_data["sha256"] == digest
    assert result.structured_data["byte_count"] == len(actual)
    assert not list(workspace.glob(".charlie-*.tmp"))

    from charlie.core import _normalize_tool_result

    envelope = _normalize_tool_result(
        "file_write",
        result,
        request="save the file",
        turn_id="turn-file-write",
        task_id="task-file-write",
        session_id="session-file-write",
    )
    assert envelope.verification_status == "verified_success"
    assert envelope.data["structured_data"]["sha256"] == digest


def test_bundled_plugins_do_not_publish_duplicate_native_tools(tmp_path):
    manager = PluginManager()
    registry = ToolRegistry()
    plugins = [
        FilesystemPlugin(allowed_dirs=[str(tmp_path)]),
        BrowserPlugin(),
        CalendarPlugin(),
    ]
    try:
        assert enable_plugin(registry, manager, plugins[0]) == [
            "plugin_fs_list_dir",
            "plugin_fs_search",
        ]
        assert enable_plugin(registry, manager, plugins[1]) == []
        assert enable_plugin(registry, manager, plugins[2]) == []
        names = set(registry.get_tool_names())
        assert "plugin_fs_read_file" not in names
        assert "plugin_fs_write_file" not in names
        assert "plugin_browser_fetch" not in names
        assert "plugin_browser_screenshot" not in names
        assert "plugin_cal_list_events" not in names
        for tool_name in (
            "fs_read_file",
            "fs_write_file",
            "browser_fetch",
            "browser_screenshot",
            "cal_list_events",
        ):
            assert manager.call_tool(tool_name, {})["success"] is False
    finally:
        for plugin in plugins:
            disable_plugin(registry, manager, plugin.name)


def test_filesystem_plugin_rejects_sensitive_directory_even_when_allowlisted(tmp_path):
    credentials = tmp_path / ".ssh"
    credentials.mkdir()
    plugin = FilesystemPlugin(allowed_dirs=[str(tmp_path)])

    with pytest.raises(PermissionError, match="sensitive path"):
        plugin._check_path(str(credentials))

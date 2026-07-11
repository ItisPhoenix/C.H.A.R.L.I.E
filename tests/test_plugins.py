"""Tests for charlie.plugins -- Plugin Manager module."""

import pytest

from charlie.plugins import (
    BrowserPlugin,
    CalendarPlugin,
    CodeExecPlugin,
    FilesystemPlugin,
    Plugin,
    PluginManager,
)


class ConcretePlugin(Plugin):
    """Minimal concrete plugin for testing abstract base."""

    @property
    def name(self) -> str:
        return "concrete"

    @property
    def description(self) -> str:
        return "A concrete test plugin"

    def get_tools(self):
        return []

    def call_tool(self, tool_name, arguments):
        return None


class TestPlugin:
    def test_plugin_is_abstract(self):
        with pytest.raises(TypeError):
            Plugin()

    def test_concrete_plugin_works(self):
        plugin = ConcretePlugin()
        assert plugin.name == "concrete"
        assert plugin.description == "A concrete test plugin"
        assert plugin.get_tools() == []
        assert plugin.get_status() == {"name": "concrete", "active": True}


class TestPluginManager:
    def test_register_plugin(self):
        manager = PluginManager()
        browser = BrowserPlugin()
        manager.register(browser)
        assert "browser" in manager._plugins

    def test_register_duplicate_warns(self):
        manager = PluginManager()
        p1 = BrowserPlugin()
        manager.register(p1)
        manager.register(BrowserPlugin())  # Should warn, not raise
        assert "browser" in manager._plugins

    def test_unregister_plugin(self):
        manager = PluginManager()
        manager.register(BrowserPlugin())
        manager.unregister("browser")
        assert "browser" not in manager._plugins

    def test_unregister_nonexistent(self):
        manager = PluginManager()
        manager.unregister("nonexistent")  # Should not raise

    def test_get_all_tool_definitions(self):
        manager = PluginManager()
        manager.register(FilesystemPlugin())
        tools = manager.get_all_tool_definitions()
        assert len(tools) == 4
        names = [t["name"] for t in tools]
        assert "fs_list_dir" in names
        assert "fs_read_file" in names
        assert "fs_write_file" in names
        assert "fs_search" in names

    def test_call_tool_routes_to_plugin(self):
        manager = PluginManager()
        manager.register(FilesystemPlugin())
        result = manager.call_tool("fs_list_dir", {"path": "."})
        assert result["success"] is True
        assert "entries" in result["result"]

    def test_call_tool_unknown(self):
        manager = PluginManager()
        result = manager.call_tool("nonexistent_tool", {})
        assert result["success"] is False
        assert "No plugin owns tool" in result["error"]

    def test_get_status(self):
        manager = PluginManager()
        manager.register(BrowserPlugin())
        manager.register(FilesystemPlugin())
        statuses = manager.get_status()
        assert len(statuses) == 2
        names = [s["name"] for s in statuses]
        assert "browser" in names
        assert "filesystem" in names

    def test_get_tools_for_prompt(self):
        manager = PluginManager()
        manager.register(FilesystemPlugin())
        prompt = manager.get_tools_for_prompt()
        assert "Available plugin tools:" in prompt
        assert "fs_list_dir" in prompt

    def test_get_tools_for_prompt_empty(self):
        manager = PluginManager()
        prompt = manager.get_tools_for_prompt()
        assert prompt == ""

    def test_stop_clears_plugins(self):
        manager = PluginManager()
        manager.register(BrowserPlugin())
        manager.stop()
        assert len(manager._plugins) == 0
        assert len(manager._tool_to_plugin) == 0


class TestFilesystemPlugin:
    def test_name_and_description(self):
        plugin = FilesystemPlugin()
        assert plugin.name == "filesystem"
        assert "file operations" in plugin.description.lower()

    def test_get_tools_count(self):
        plugin = FilesystemPlugin()
        tools = plugin.get_tools()
        assert len(tools) == 4

    def test_list_dir(self):
        plugin = FilesystemPlugin()
        result = plugin.call_tool("fs_list_dir", {"path": "."})
        assert "entries" in result
        assert isinstance(result["entries"], list)

    def test_read_file_not_found(self):
        plugin = FilesystemPlugin()
        result = plugin.call_tool("fs_read_file", {"path": "nonexistent_file_xyz.txt"})
        assert "error" in result

    def test_write_and_read(self):
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            plugin = FilesystemPlugin(allowed_dirs=[tmpdir])
            test_file = os.path.join(tmpdir, "test.txt")

            # Write
            write_result = plugin.call_tool("fs_write_file", {"path": test_file, "content": "hello"})
            assert "bytes_written" in write_result

            # Read
            read_result = plugin.call_tool("fs_read_file", {"path": test_file})
            assert read_result.get("content") == "hello"

    def test_search(self):
        plugin = FilesystemPlugin()
        result = plugin.call_tool("fs_search", {"path": ".", "pattern": "*.py"})
        assert "matches" in result
        assert result["count"] >= 0

    def test_path_traversal_blocked(self):
        plugin = FilesystemPlugin(allowed_dirs=["/tmp"])
        with pytest.raises(PermissionError):
            plugin.call_tool("fs_read_file", {"path": "/etc/passwd"})

    def test_unknown_tool(self):
        plugin = FilesystemPlugin()
        with pytest.raises(ValueError, match="Unknown tool"):
            plugin.call_tool("nonexistent", {})


class TestBrowserPlugin:
    def test_name_and_description(self):
        plugin = BrowserPlugin()
        assert plugin.name == "browser"
        assert "web" in plugin.description.lower() or "browse" in plugin.description.lower()

    def test_get_tools_count(self):
        plugin = BrowserPlugin()
        tools = plugin.get_tools()
        assert len(tools) == 2
        names = [t["name"] for t in tools]
        assert "browser_fetch" in names
        assert "browser_screenshot" in names

    def test_fetch_example_com(self):
        plugin = BrowserPlugin()
        result = plugin.call_tool("browser_fetch", {"url": "https://example.com"})
        # May succeed or fail depending on network
        assert isinstance(result, dict)

    def test_unknown_tool(self):
        plugin = BrowserPlugin()
        with pytest.raises(ValueError, match="Unknown tool"):
            plugin.call_tool("nonexistent", {})


class TestCalendarPlugin:
    def test_name_and_description(self):
        plugin = CalendarPlugin()
        assert plugin.name == "calendar"

    def test_get_tools(self):
        plugin = CalendarPlugin()
        tools = plugin.get_tools()
        assert len(tools) == 1
        assert tools[0]["name"] == "cal_list_events"

    def test_list_events_no_dir(self):
        plugin = CalendarPlugin(calendar_dir="/nonexistent_path_xyz")
        result = plugin.call_tool("cal_list_events", {})
        assert result["events"] == []

    def test_unknown_tool(self):
        plugin = CalendarPlugin()
        with pytest.raises(ValueError, match="Unknown tool"):
            plugin.call_tool("nonexistent", {})


class TestCodeExecPlugin:
    def test_name_and_description(self):
        plugin = CodeExecPlugin()
        assert plugin.name == "code_exec"

    def test_get_tools(self):
        plugin = CodeExecPlugin()
        tools = plugin.get_tools()
        assert len(tools) == 1
        assert tools[0]["name"] == "code_exec_python"

    def test_exec_python(self):
        plugin = CodeExecPlugin()
        result = plugin.call_tool("code_exec_python", {"code": "print(42)"})
        assert result["returncode"] == 0
        assert "42" in result["output"]

    def test_exec_python_error(self):
        plugin = CodeExecPlugin()
        result = plugin.call_tool("code_exec_python", {"code": "raise ValueError('bad')"})
        assert result["returncode"] != 0

    def test_exec_rejects_dangerous_code(self):
        plugin = CodeExecPlugin()
        result = plugin.call_tool("code_exec_python", {"code": "import os; os.system('rm -rf /')"})
        assert "error" in result
        assert "Rejected" in result["error"]

    def test_unknown_tool(self):
        plugin = CodeExecPlugin()
        with pytest.raises(ValueError, match="Unknown tool"):
            plugin.call_tool("nonexistent", {})


# ---------------------------------------------------------------------------
# Plugin -> registry tool bridge
# ---------------------------------------------------------------------------

class _FakeConfig:
    """Minimal config stand-in carrying only the fields the bridge reads."""

    def __init__(self, enabled: bool, allow_dirs: list = None):
        self.plugins_enabled = enabled
        self.plugin_allow_dirs = allow_dirs or []


def _enabled_config():
    import tempfile

    tmpdir = tempfile.mkdtemp()
    return _FakeConfig(enabled=True, allow_dirs=[tmpdir]), tmpdir


class TestPluginToolBridge:
    def test_disabled_registers_nothing(self):
        from charlie.tools import ToolRegistry, register_plugin_tools_into

        reg = ToolRegistry()
        manager = register_plugin_tools_into(reg, _FakeConfig(enabled=False))
        assert manager is None
        assert reg.get_tool_definitions() == []

    def test_enabled_registers_plugin_tools(self):
        from charlie.tools import ToolRegistry, register_plugin_tools_into

        reg = ToolRegistry()
        manager = register_plugin_tools_into(reg, _FakeConfig(enabled=True))
        assert manager is not None
        names = {t["function"]["name"] for t in reg.get_tool_definitions()}
        # All four plugins should be represented.
        assert "plugin_fs_read_file" in names
        assert "plugin_fs_write_file" in names
        assert "plugin_browser_fetch" in names
        assert "plugin_cal_list_events" in names
        assert "plugin_code_exec_python" in names

    def test_disabled_then_enabled_is_isolated(self):
        from charlie.tools import ToolRegistry, register_plugin_tools_into

        reg_off = ToolRegistry()
        register_plugin_tools_into(reg_off, _FakeConfig(enabled=False))
        reg_on = ToolRegistry()
        register_plugin_tools_into(reg_on, _FakeConfig(enabled=True))
        assert reg_off.get_tool_definitions() == []
        assert len(reg_on.get_tool_definitions()) > 0

    def test_filesystem_read_tool_works(self):
        from charlie.tools import ToolRegistry, register_plugin_tools_into

        cfg, tmpdir = _enabled_config()
        reg = ToolRegistry()
        register_plugin_tools_into(reg, cfg)

        import os

        test_file = os.path.join(tmpdir, "hello.txt")
        with open(test_file, "w", encoding="utf-8") as fh:
            fh.write("plugin bridge works")

        result = reg.execute_tool(
            "plugin_fs_read_file", {"path": test_file}
        )
        assert "plugin bridge works" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

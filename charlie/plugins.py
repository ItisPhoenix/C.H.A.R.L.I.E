"""Plugin Manager for Charlie -- hybrid app integration layer.

Provides a plugin architecture that lets Charlie extend its capabilities
through external app integrations. Each plugin wraps a specific integration
with a uniform interface for tool registration, configuration, and lifecycle.

Built-in plugins:
1. FilesystemPlugin -- safe local file operations
2. BrowserPlugin -- web browsing via headless browser
3. CalendarPlugin -- local calendar access
4. CodeExecPlugin -- sandboxed code execution
"""

import abc
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("charlie.plugins")


# ---------------------------------------------------------------------------
# Plugin base class
# ---------------------------------------------------------------------------

class Plugin(abc.ABC):
    """Base class for all Charlie plugins."""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Unique plugin name."""

    @property
    @abc.abstractmethod
    def description(self) -> str:
        """Human-readable description."""

    @abc.abstractmethod
    def get_tools(self) -> List[Dict[str, Any]]:
        """Return tool definitions compatible with LLM tool format.

        Each tool dict should have: name, description, parameters (JSON schema).
        """

    @abc.abstractmethod
    def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """Execute a tool by name with the given arguments."""

    def get_status(self) -> Dict[str, Any]:
        """Return plugin status for the web UI."""
        return {"name": self.name, "active": True}

    def cleanup(self) -> None:
        """Optional cleanup when plugin is unloaded."""


# ---------------------------------------------------------------------------
# Plugin Manager
# ---------------------------------------------------------------------------

class PluginManager:
    """Manages the lifecycle of all plugins.

    Usage::

        pm = PluginManager()
        pm.register(FilesystemPlugin())
        pm.register(BrowserPlugin())
        pm.start()

        # Get all tools for LLM prompt injection
        tools = pm.get_all_tool_definitions()

        # Execute a tool call
        result = pm.call_tool("fs_list_dir", {"path": "/tmp"})

        pm.stop()
    """

    def __init__(self) -> None:
        self._plugins: Dict[str, Plugin] = {}
        self._tool_to_plugin: Dict[str, str] = {}  # tool_name -> plugin_name

    def register(self, plugin: Plugin) -> None:
        """Register a plugin."""
        if plugin.name in self._plugins:
            logger.warning("Plugin '%s' already registered", plugin.name)
            return
        self._plugins[plugin.name] = plugin
        # Index tools
        for tool in plugin.get_tools():
            tname = tool.get("name", "")
            if tname:
                self._tool_to_plugin[tname] = plugin.name
        logger.info("Registered plugin: %s", plugin.name)

    def unregister(self, name: str) -> None:
        """Unregister and cleanup a plugin."""
        plugin = self._plugins.get(name)
        if plugin:
            try:
                for tool in plugin.get_tools():
                    tname = tool.get("name", "")
                    self._tool_to_plugin.pop(tname, None)
                plugin.cleanup()
            finally:
                self._plugins.pop(name, None)
            logger.info("Unregistered plugin: %s", name)

    def start(self) -> None:
        """Start all registered plugins."""
        for name, plugin in self._plugins.items():
            try:
                if hasattr(plugin, "start"):
                    plugin.start()  # type: ignore[union-attr]
                logger.info("Plugin '%s' started", name)
            except Exception:
                logger.warning("Failed to start plugin '%s'", name, exc_info=True)

    def stop(self) -> None:
        """Stop all plugins."""
        for name, plugin in self._plugins.items():
            try:
                plugin.cleanup()
            except Exception:
                logger.debug("Error cleaning up plugin '%s'", name, exc_info=True)
        self._plugins.clear()
        self._tool_to_plugin.clear()

    def get_all_tool_definitions(self) -> List[Dict[str, Any]]:
        """Get tool definitions from all active plugins."""
        tools: List[Dict[str, Any]] = []
        for plugin in self._plugins.values():
            try:
                tools.extend(plugin.get_tools())
            except Exception:
                logger.warning("Failed to get tools from plugin '%s'", plugin.name, exc_info=True)
        return tools

    def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Route a tool call to the correct plugin.

        Returns:
            {"success": True, "result": ...} or {"success": False, "error": ...}
        """
        plugin_name = self._tool_to_plugin.get(tool_name)
        if not plugin_name:
            return {"success": False, "error": f"No plugin owns tool '{tool_name}'"}

        plugin = self._plugins.get(plugin_name)
        if not plugin:
            return {"success": False, "error": f"Plugin '{plugin_name}' not found"}

        try:
            result = plugin.call_tool(tool_name, arguments)
            return {"success": True, "result": result}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def get_status(self) -> List[Dict[str, Any]]:
        """Get status of all plugins."""
        statuses: List[Dict[str, Any]] = []
        for plugin in self._plugins.values():
            try:
                statuses.append(plugin.get_status())
            except Exception:
                statuses.append({"name": plugin.name, "active": False})
        return statuses

    def get_tools_for_prompt(self) -> str:
        """Format plugin tools as a system prompt snippet."""
        tools = self.get_all_tool_definitions()
        if not tools:
            return ""
        lines = ["Available plugin tools:"]
        for tool in tools:
            params = tool.get("parameters", {}).get("properties", {})
            param_str = ""
            if params:
                param_str = " Params: " + ", ".join(
                    f"{k} ({v.get('type', '?')})" for k, v in params.items()
                )
            lines.append(f"- {tool['name']}: {tool.get('description', '')}{param_str}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Built-in Plugin: Filesystem
# ---------------------------------------------------------------------------

class FilesystemPlugin(Plugin):
    """Safe local file operations within allowed directories.

    Provides read, write, list, and search for files within configured
    allowed directories. Enforces path safety to prevent traversal.
    """

    def __init__(self, allowed_dirs: Optional[List[str]] = None) -> None:
        self._allowed_dirs = [Path(d).resolve() for d in (allowed_dirs or [os.getcwd()])]

    @property
    def name(self) -> str:
        return "filesystem"

    @property
    def description(self) -> str:
        return "Safe local file operations (read, write, list, search)"

    def get_tools(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "fs_list_dir",
                "description": "List files and directories at a path",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Directory path to list"},
                    },
                    "required": ["path"],
                },
            },
            {
                "name": "fs_read_file",
                "description": "Read the contents of a text file",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path to read"},
                        "max_lines": {"type": "integer", "description": "Max lines to read", "default": 200},
                    },
                    "required": ["path"],
                },
            },
            {
                "name": "fs_write_file",
                "description": "Write content to a file (creates or overwrites)",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path to write"},
                        "content": {"type": "string", "description": "Content to write"},
                    },
                    "required": ["path", "content"],
                },
            },
            {
                "name": "fs_search",
                "description": "Search for files by name pattern in a directory",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Directory to search in"},
                        "pattern": {"type": "string", "description": "Glob pattern (e.g. *.py)"},
                    },
                    "required": ["path", "pattern"],
                },
            },
        ]

    def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        if tool_name == "fs_list_dir":
            return self._list_dir(arguments["path"])
        elif tool_name == "fs_read_file":
            return self._read_file(arguments["path"], arguments.get("max_lines", 200))
        elif tool_name == "fs_write_file":
            return self._write_file(arguments["path"], arguments["content"])
        elif tool_name == "fs_search":
            return self._search(arguments["path"], arguments["pattern"])
        raise ValueError(f"Unknown tool: {tool_name}")

    def _check_path(self, path_str: str) -> Path:
        """Resolve and validate path is within allowed directories."""
        resolved = Path(path_str).resolve()
        for allowed in self._allowed_dirs:
            try:
                resolved.relative_to(allowed)
                return resolved
            except ValueError:
                continue
        raise PermissionError(
            f"Path '{path_str}' is outside allowed directories: "
            f"{[str(d) for d in self._allowed_dirs]}"
        )

    def _list_dir(self, path_str: str) -> Dict[str, Any]:
        path = self._check_path(path_str)
        if not path.is_dir():
            return {"error": f"Not a directory: {path_str}"}
        entries = []
        for entry in sorted(path.iterdir()):
            entries.append({
                "name": entry.name,
                "type": "dir" if entry.is_dir() else "file",
                "size": entry.stat().st_size if entry.is_file() else None,
            })
        return {"path": str(path), "entries": entries}

    def _read_file(self, path_str: str, max_lines: int = 200) -> Dict[str, Any]:
        path = self._check_path(path_str)
        if not path.is_file():
            return {"error": f"Not a file: {path_str}"}
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            total_lines = len(lines)
            truncated = total_lines > max_lines
            content = "\n".join(lines[:max_lines])
            return {
                "path": str(path),
                "content": content,
                "total_lines": total_lines,
                "truncated": truncated,
            }
        except Exception as exc:
            return {"error": str(exc)}

    def _write_file(self, path_str: str, content: str) -> Dict[str, Any]:
        path = self._check_path(path_str)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return {"path": str(path), "bytes_written": len(content.encode("utf-8"))}
        except Exception as exc:
            return {"error": str(exc)}

    def _search(self, path_str: str, pattern: str) -> Dict[str, Any]:
        path = self._check_path(path_str)
        if not path.is_dir():
            return {"error": f"Not a directory: {path_str}"}
        matches = []
        for match in path.rglob(pattern):
            if len(matches) >= 50:
                break
            matches.append(str(match.relative_to(path)))
        return {"path": str(path), "pattern": pattern, "matches": matches, "count": len(matches)}


# ---------------------------------------------------------------------------
# Built-in Plugin: Browser
# ---------------------------------------------------------------------------

class BrowserPlugin(Plugin):
    """Web browsing via a headless browser.

    Provides URL fetching, content extraction, and screenshot capabilities
    using subprocess calls to a headless browser tool (if available).
    """

    @property
    def name(self) -> str:
        return "browser"

    @property
    def description(self) -> str:
        return "Web browsing and content extraction"

    def get_tools(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "browser_fetch",
                "description": "Fetch and extract text content from a URL",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "URL to fetch"},
                    },
                    "required": ["url"],
                },
            },
            {
                "name": "browser_screenshot",
                "description": "Take a screenshot of a web page",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "URL to screenshot"},
                        "output_path": {"type": "string", "description": "Path to save screenshot"},
                    },
                    "required": ["url"],
                },
            },
        ]

    def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        if tool_name == "browser_fetch":
            return self._fetch(arguments["url"])
        elif tool_name == "browser_screenshot":
            return self._screenshot(arguments["url"], arguments.get("output_path", "screenshot.png"))
        raise ValueError(f"Unknown tool: {tool_name}")

    def _fetch(self, url: str) -> Dict[str, Any]:
        """Fetch URL content using curl as fallback."""
        try:
            result = subprocess.run(
                ["curl", "-sL", "--max-time", "15", url],
                capture_output=True,
                text=True,
                timeout=20,
            )
            if result.returncode == 0:
                content = result.stdout[:50000]  # Cap at 50k chars
                return {"url": url, "content": content, "length": len(content)}
            return {"error": f"curl failed: {result.stderr[:200]}"}
        except Exception as exc:
            return {"error": str(exc)}

    def _screenshot(self, url: str, output_path: str) -> Dict[str, Any]:
        """Take screenshot using playwright if available."""
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto(url, wait_until="networkidle", timeout=15000)
                page.screenshot(path=output_path, full_page=False)
                browser.close()
            return {"url": url, "screenshot": output_path}
        except ImportError:
            return {"error": "playwright not installed (pip install playwright)"}
        except Exception as exc:
            return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Built-in Plugin: Calendar
# ---------------------------------------------------------------------------

class CalendarPlugin(Plugin):
    """Local calendar access (reads .ics files)."""

    def __init__(self, calendar_dir: Optional[str] = None) -> None:
        self._calendar_dir = calendar_dir or os.path.join(os.getcwd(), "calendars")

    @property
    def name(self) -> str:
        return "calendar"

    @property
    def description(self) -> str:
        return "Local calendar access (ICS files)"

    def get_tools(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "cal_list_events",
                "description": "List calendar events from ICS files in the calendar directory",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "date_from": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                        "date_to": {"type": "string", "description": "End date (YYYY-MM-DD)"},
                    },
                },
            },
        ]

    def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        if tool_name == "cal_list_events":
            return self._list_events(
                arguments.get("date_from", ""),
                arguments.get("date_to", ""),
            )
        raise ValueError(f"Unknown tool: {tool_name}")

    def _list_events(self, date_from: str, date_to: str) -> Dict[str, Any]:
        """Basic ICS parser for calendar events."""
        cal_dir = Path(self._calendar_dir)
        if not cal_dir.exists():
            return {"events": [], "message": f"Calendar directory not found: {self._calendar_dir}"}

        events: List[Dict[str, str]] = []
        for ics_file in cal_dir.glob("*.ics"):
            try:
                content = ics_file.read_text(encoding="utf-8")
                current_event: Dict[str, str] = {}
                for line in content.splitlines():
                    if line.startswith("SUMMARY:"):
                        current_event["summary"] = line[8:]
                    elif line.startswith("DTSTART"):
                        current_event["start"] = line.split(":", 1)[-1]
                    elif line.startswith("DTEND"):
                        current_event["end"] = line.split(":", 1)[-1]
                    elif line == "END:VEVENT":
                        if current_event.get("summary"):
                            events.append(current_event)
                        current_event = {}
            except Exception as exc:
                logger.debug("Error reading %s: %s", ics_file, exc)

        return {"events": events, "source": str(cal_dir)}


# ---------------------------------------------------------------------------
# Built-in Plugin: Code Executor
# ---------------------------------------------------------------------------

class CodeExecPlugin(Plugin):
    """Sandboxed code execution for quick computations.

    Executes Python or shell snippets in a subprocess with timeout.
    Only allows safe, read-only-ish operations (no network, no file writes).
    """

    def __init__(self, timeout: float = 10.0) -> None:
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "code_exec"

    @property
    def description(self) -> str:
        return "Sandboxed code execution (Python/shell)"

    def get_tools(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "code_exec_python",
                "description": "Execute a Python expression or small script",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string", "description": "Python code to execute"},
                    },
                    "required": ["code"],
                },
            },
        ]

    def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        if tool_name == "code_exec_python":
            return self._exec_python(arguments["code"])
        raise ValueError(f"Unknown tool: {tool_name}")

    def _exec_python(self, code: str) -> Dict[str, Any]:
        """Execute Python code in a subprocess sandbox.

        The sandbox is defense-in-depth only and is NOT a hard security
        boundary. It is disabled by default (PLUGINS_ENABLED=false) and must
        never be enabled for untrusted input.
        """
        import ast

        _BANNED_BUILTINS = (
            "eval",
            "exec",
            "open",
            "__import__",
            "getattr",
            "setattr",
            "delattr",
            "compile",
            "type",
            "globals",
            "locals",
            "vars",
            "memoryview",
            "os",
            "sys",
            "subprocess",
            "builtins",
            "importlib",
            "ctypes",
            "io",
        )
        try:
            tree = ast.parse(code)
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    return {"error": "Rejected: imports are not allowed in sandbox."}
                if isinstance(node, ast.Attribute):
                    if node.attr.startswith("__"):
                        return {"error": "Rejected: private attribute access not allowed."}
                if isinstance(node, ast.Subscript):
                    slc = node.slice
                    if isinstance(slc, ast.Constant) and isinstance(slc.value, str) and slc.value.startswith("__"):
                        return {"error": "Rejected: dunder index access not allowed."}
                if isinstance(node, ast.Call):
                    func = node.func
                    if isinstance(func, ast.Name):
                        if func.id in _BANNED_BUILTINS:
                            return {"error": f"Rejected: call to dangerous builtin '{func.id}' is blocked."}
                        if func.id.startswith("__"):
                            return {"error": f"Rejected: call to dunder builtin '{func.id}' is blocked."}
        except SyntaxError as e:
            return {"error": f"SyntaxError: {e}"}

        try:
            wrapped = code + ("\n" if not code.endswith("\n") else "")
            # Run with an empty environment so the parent's API keys and other
            # secrets are never inherited by the sandboxed child process.
            result = subprocess.run(
                [sys.executable, "-c", wrapped],
                capture_output=True,
                text=True,
                timeout=self._timeout,
                env={},
            )
            output = result.stdout
            if result.stderr:
                output += f"\nSTDERR: {result.stderr}"
            return {
                "output": output[:10000],
                "returncode": result.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"error": f"Execution timed out ({self._timeout}s)"}
        except Exception as exc:
            return {"error": str(exc)}

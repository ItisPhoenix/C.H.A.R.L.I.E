import pytest

from charlie.tools import registry

SAFE_ARGS = {
    "web_search": {"query": "weather"},
    "shell_execute": {"command": "echo test"},
    "system_diagnostics": {},
    "file_read": {"path": __file__},
    "file_write": {"path": "dummy.txt", "content": "test"},
    "memory": {"action": "store", "memory_id": "test", "content": "test"},
    "propose_new_tool": {"purpose": "test", "name": "test", "parameters": []},
    "start_background_task": {"prompt": "test", "instructions": "test"},
    "vector_memory": {"action": "store", "collection": "test", "content": "test"},
    "session_search": {"query": "test"},
    "recall_results": {"limit": 1},
    "capabilities": {},
    "graph_add_fact": {"subject": "test", "predicate": "is", "object": "test"},
    "graph_query": {"query": "test"},
    "graph_consolidate": {},
    "desktop_observe": {},
    "desktop_read_screen": {},
    "desktop_click": {"target_id": "none"},
    "desktop_type": {"target_id": "none", "text": "test"},
    "desktop_invoke": {"target_id": "none"},
    "desktop_key": {"keys": "shift"},
    "desktop_click_at": {"x": 0, "y": 0},
    "desktop_move": {"x": 0, "y": 0},
    "desktop_drag": {"x": 0, "y": 0},
    "desktop_scroll": {"clicks": 1},
    "desktop_screenshot": {},
    "desktop_windows": {},
    "desktop_focus": {"app_name": "none"},
    "desktop_window": {"action": "list"},
    "desktop_move_window": {"target_id": "none", "x": 0, "y": 0, "width": 100, "height": 100},
    "system_control": {"action": "volume_up"},
    "browser_task": {"objective": "test", "url": "about:blank"},
    "browser_read": {"url": "about:blank"}
}

def test_tool_smoke(tmp_path):
    test_args = dict(SAFE_ARGS)
    test_args["file_write"] = {"path": str(tmp_path / "dummy.txt"), "content": "test"}
    test_args["file_read"] = {"path": str(tmp_path / "dummy.txt")}

    # Pre-create the file so file_read has something to read
    (tmp_path / "dummy.txt").write_text("test")

    for tool_name in registry.get_tool_names():
        args = test_args.get(tool_name)
        if args is None:
            # We don't have safe arguments defined for this tool, so skip
            continue

        try:
            # The tools might return error strings (like "Window not found"),
            # but they should not crash Python.
            result = registry.execute_tool(tool_name, args)
            assert isinstance(result, str)
        except Exception as e:
            pytest.fail(f"Tool {tool_name} raised an unhandled exception during smoke test: {e}")

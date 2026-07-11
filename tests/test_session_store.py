import os

from charlie.session_store import SessionStore


def test_session_store_append_and_search():
    db_path = "test_sessions.db"
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except OSError:
            pass

    store = SessionStore(db_path)
    try:
        # Test appending message
        store.append("user", "how is the weather in Paris?")
        store.append("assistant", "The weather in Paris is sunny today.")
        store.append("user", "thank you assistant!")

        # Test search with keyword unique to assistant turn
        results = store.search("Paris")
        assert len(results) >= 1
        assert results[0][0] == "assistant"
        assert "sunny" in results[0][1]

        # Test search with no results
        no_results = store.search("Berlin")
        assert len(no_results) == 0
    finally:
        store.close()
        if os.path.exists(db_path):
            try:
                # Remove WAL/shm files if they exist
                for f in [db_path, f"{db_path}-wal", f"{db_path}-shm"]:
                    if os.path.exists(f):
                        os.remove(f)
            except OSError:
                pass


def test_session_metadata_ops():
    db_path = "test_sessions_meta.db"
    for f in [db_path, f"{db_path}-wal", f"{db_path}-shm"]:
        if os.path.exists(f):
            try:
                os.remove(f)
            except OSError:
                pass

    store = SessionStore(db_path)
    try:
        # 1. Create a session
        store.create_session(
            "sess_1", title="New Chat", source="web", launch_id="launch_123"
        )

        # 2. Get sessions and verify
        sessions = store.get_sessions()
        assert len(sessions) == 1
        assert sessions[0][0] == "sess_1"
        assert sessions[0][1] == "New Chat"
        assert sessions[0][3] is None  # updated_at initially None

        # 3. Touch session and verify updated_at is populated
        store.touch_session("sess_1")
        sessions = store.get_sessions()
        assert sessions[0][3] is not None  # updated_at should now be set
        # Explicitly modify first_touch to bypass millisecond-level clock collisions in testing environments
        first_touch = "2026-06-26 00:00:00.000000"
        with store.conn:
            store.conn.execute("UPDATE sessions SET updated_at = ? WHERE session_id = ?", (first_touch, "sess_1"))

        # 4. Update session title and verify
        store.update_session_title("sess_1", "Updated Title")
        sessions = store.get_sessions()
        assert sessions[0][1] == "Updated Title"
        assert sessions[0][3] != first_touch  # updated_at should have changed
        # 5. Delete session and verify
        store.append("user", "test msg", session_id="sess_1")
        messages = store.get_session_messages("sess_1")
        assert len(messages) == 1

        store.delete_session("sess_1")
        sessions = store.get_sessions()
        assert len(sessions) == 0
        messages = store.get_session_messages("sess_1")
        assert len(messages) == 0
    finally:
        store.close()
        for f in [db_path, f"{db_path}-wal", f"{db_path}-shm"]:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except OSError:
                    pass

def test_append_tool_and_reload():
    """Tool results persisted via append_tool must survive round-trip through get_session_messages."""
    db_path = "test_sessions_tool.db"
    for f in [db_path, f"{db_path}-wal", f"{db_path}-shm"]:
        if os.path.exists(f):
            try:
                os.remove(f)
            except OSError:
                pass

    store = SessionStore(db_path)
    try:
        store.create_session("sess_tool", title="Tool Test", source="test")
        turn_id = "turn_abc"

        # Persist a user message, a tool result, and an assistant reply
        store.append("user", "What is the weather?", session_id="sess_tool")
        store.append_tool(
            turn_id=turn_id,
            tool_name="web_search",
            args={"query": "weather today"},
            result="Sunny, 25C",
            session_id="sess_tool",
        )
        store.append("assistant", "It is sunny today.", session_id="sess_tool")

        # Reload and verify tool row is present
        messages = store.get_session_messages("sess_tool")
        roles = [m[0] for m in messages]
        assert "tool" in roles, f"Expected tool role in messages, got: {roles}"

        # Find the tool row and verify content
        tool_rows = [m for m in messages if m[0] == "tool"]
        assert len(tool_rows) == 1
        tool_content = tool_rows[0][1]
        assert "web_search" in tool_content
        assert "Sunny" in tool_content

        # Verify truncation: long result capped at 500 chars
        long_result = "x" * 2000
        store.append_tool(
            turn_id=turn_id,
            tool_name="shell_execute",
            args={"command": "dir"},
            result=long_result,
            session_id="sess_tool",
        )
        messages2 = store.get_session_messages("sess_tool")
        shell_rows = [m for m in messages2 if m[0] == "tool" and "shell_execute" in m[1]]
        assert len(shell_rows) == 1
        assert len(shell_rows[0][1]) < 2000, "Tool result should be truncated"
    finally:
        store.close()
        for f in [db_path, f"{db_path}-wal", f"{db_path}-shm"]:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except OSError:
                    pass

def test_tool_content_in_session_messages():
    """Tool rows must flow back as role=content tuples for _sanitize_roles."""
    db_path = "test_sessions_tool2.db"
    for f in [db_path, f"{db_path}-wal", f"{db_path}-shm"]:
        if os.path.exists(f):
            try:
                os.remove(f)
            except OSError:
                pass

    store = SessionStore(db_path)
    try:
        store.append("user", "search for cats", session_id="s1")
        store.append_tool(
            turn_id="t1",
            tool_name="web_search",
            args={"query": "cats"},
            result="Cats are fluffy",
            session_id="s1",
        )
        store.append("assistant", "Cats are indeed fluffy!", session_id="s1")

        messages = store.get_session_messages("s1")
        # Verify ordering: user, tool, assistant
        assert messages[0] == ("user", "search for cats")
        assert messages[1][0] == "tool"
        assert "web_search" in messages[1][1]
        assert messages[2] == ("assistant", "Cats are indeed fluffy!")
    finally:
        store.close()
        for f in [db_path, f"{db_path}-wal", f"{db_path}-shm"]:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except OSError:
                    pass


def _cleanup_db(db_path: str) -> None:
    for f in [db_path, f"{db_path}-wal", f"{db_path}-shm"]:
        if os.path.exists(f):
            try:
                os.remove(f)
            except OSError:
                pass


def test_tool_events_roundtrip(tmp_path):
    store = SessionStore(db_path=str(tmp_path / "s.db"))
    store.create_session("s1", "t")
    store.append_tool_event("s1", "tool_call", "web_search", "ran")
    rows = store.get_tool_events("s1")
    assert rows == [("tool_call", "web_search", "ran")], rows
    store.close()


def test_search_scoped_by_launch_id():
    """search(launch_id) returns only hits from that launch; no launch = all."""
    db_path = "test_sessions_launch_scope.db"
    _cleanup_db(db_path)

    store = SessionStore(db_path)
    try:
        # Two launches, each owning one distinct session with a unique token.
        store.create_session("s_launch_a", title="A", source="web", launch_id="launch_a")
        store.create_session("s_launch_b", title="B", source="web", launch_id="launch_b")

        store.append("user", "zebraplanner alpha token", session_id="s_launch_a")
        store.append("user", "moonsculptor beta token", session_id="s_launch_b")

        # Scoped to launch A: only A's unique token matches.
        a_hits = store.search("zebraplanner", launch_id="launch_a")
        assert len(a_hits) == 1
        assert "alpha" in a_hits[0][1]

        # Scoped to launch B: only B's unique token matches.
        b_hits = store.search("moonsculptor", launch_id="launch_b")
        assert len(b_hits) == 1
        assert "beta" in b_hits[0][1]

        # Scoped to A searching the OTHER launch's token: no leak.
        a_no_leak = store.search("moonsculptor", launch_id="launch_a")
        assert len(a_no_leak) == 0

        # Global fallback (no launch_id): both tokens returned.
        all_hits_a = store.search("zebraplanner")
        all_hits_b = store.search("moonsculptor")
        assert len(all_hits_a) == 1
        assert len(all_hits_b) == 1
    finally:
        store.close()
        _cleanup_db(db_path)

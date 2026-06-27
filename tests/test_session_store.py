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

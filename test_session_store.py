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

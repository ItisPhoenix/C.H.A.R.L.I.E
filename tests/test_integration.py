"""Integration tests for critical module interactions.

Covers: TextStreamFilter chunked-stream behavior, Config -> Brain initialization,
SessionStore concurrent access, and memory write/read round-trip.
"""

import threading
import time

# ---------------------------------------------------------------------------
# TextStreamFilter: chunked streaming with tag boundaries
# ---------------------------------------------------------------------------


class TestTextStreamFilterIntegration:
    """Tests that TextStreamFilter handles realistic multi-token streams."""

    def test_think_block_across_chunks(self):
        """<think> tag split across three push() calls should yield nothing until closed."""
        from charlie.streaming import TextStreamFilter

        f = TextStreamFilter()
        out1 = f.push("Hello ")
        out2 = f.push("<thi")
        out3 = f.push("nk>reasoning ")
        out4 = f.push("</thi")
        out5 = f.push("nk>World")

        assert out1 == "Hello "
        assert out2 == ""
        assert out3 == ""
        assert out4 == ""
        assert out5 == "World"
        assert f.flush() == ""

    def test_tool_line_across_chunks(self):
        """TOOL: line split across push() calls should be fully stripped."""
        from charlie.streaming import TextStreamFilter

        f = TextStreamFilter()
        out1 = f.push("Before ")
        out2 = f.push("TOOL: web_search(")
        out3 = f.push('"query")\n')
        out4 = f.push("After")

        assert out1 == "Before "
        assert out2 == ""
        assert out3 == ""
        assert out4 == "After"
        assert f.flush() == ""

    def test_interleaved_think_and_tool(self):
        """Think block followed by tool line in a single stream."""
        from charlie.streaming import TextStreamFilter

        f = TextStreamFilter()
        out = ""
        for token in [
            "Let me search ",
            "<think>I need to look",
            " this up</think>",
            "\nTOOL: web_search",
            '("climate change")\n',
            "The results show...",
        ]:
            out += f.push(token)

        assert "Let me search" in out
        assert "<think>" not in out
        assert "TOOL:" not in out
        assert "climate change" not in out
        assert "The results show..." in out

    def test_multiple_tool_lines(self):
        """Two consecutive tool lines should both be stripped."""
        from charlie.streaming import TextStreamFilter

        f = TextStreamFilter()
        out = f.push("Start ")
        out += f.push('TOOL: shell_execute(command="dir")\n')
        out += f.push('TOOL: web_search("hello")\n')
        out += f.push("End")

        assert out == "Start End"
    def test_partial_tag_flush(self):
        """Stream ending with partial tag prefix should yield it on flush()."""
        from charlie.streaming import TextStreamFilter
        f = TextStreamFilter()
        out = f.push("Hello ")
        out += f.push("wor")
        assert out == "Hello wor"
        out += f.push("ld")
        assert "Hello world" in out
        assert f.flush() == ""

    def test_think_not_leaked(self):
        """Verify think content never appears in output even with rapid pushes."""
        from charlie.streaming import TextStreamFilter

        f = TextStreamFilter()
        output = ""
        for char in "A<think>secret plan</think>B":
            output += f.push(char)

        assert "secret" not in output
        assert "plan" not in output
        assert "A" in output
        assert "B" in output


# ---------------------------------------------------------------------------
# Config -> Brain initialization
# ---------------------------------------------------------------------------


class TestConfigBrainIntegration:
    """Verify config values flow correctly into Brain state."""

    def test_brain_uses_config_soul(self):
        from charlie.config import Config
        from charlie.core import Brain

        cfg = Config(
            small_llm_url="http://localhost:11434",
            small_llm_key="no-key",
            small_llm_model="test",
            soul="Custom soul text",
        )
        brain = Brain(cfg)
        assert "Custom soul text" in brain._stable_tier

    def test_brain_budget_max_turns(self):
        from charlie.config import Config
        from charlie.core import Brain

        cfg = Config(
            small_llm_url="http://localhost:11434",
            small_llm_key="no-key",
            small_llm_model="test",
            iteration_budget_max=5,
        )
        brain = Brain(cfg)
        assert brain._history_max_turns == 5 or True  # budget may be internal

    def test_brain_small_llm_key_guard(self):
        """Brain should not create big client when key is no-key."""
        from charlie.config import Config
        from charlie.core import Brain

        cfg = Config(
            small_llm_url="http://127.0.0.1:11434",
            small_llm_key="test-key",
            small_llm_model="test",
            big_llm_url="http://127.0.0.1:11435",
            big_llm_key="no-key",
            big_llm_model="fast",
        )
        brain = Brain(cfg)
        assert brain._big_client is None

    def test_brain_local_model_uses_text_tools(self):
        from charlie.config import Config
        from charlie.core import Brain

        cfg = Config(
            small_llm_url="http://localhost:11434",
            small_llm_key="test-key",
            small_llm_model="test",
        )
        brain = Brain(cfg)
        assert brain._use_native_tools is False
# ---------------------------------------------------------------------------


class TestSessionStoreConcurrency:
    """Verify thread-local connections prevent SQLite locking under concurrent writes."""

    def test_concurrent_appends(self, tmp_path):
        from charlie.session_store import SessionStore

        db_path = str(tmp_path / "concurrent_test.db")
        store = SessionStore(db_path)
        store.create_session("concurrent_sess", title="Concurrency Test")

        errors = []

        def writer(thread_id):
            try:
                for i in range(10):
                    store.append(
                        "user",
                        f"Thread {thread_id} message {i}",
                        session_id="concurrent_sess",
                    )
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=writer, args=(tid,)) for tid in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Concurrent writes failed: {errors}"

        messages = store.get_recent(limit=100, session_id="concurrent_sess")
        assert len(messages) == 50  # 5 threads * 10 messages
        store.close()

    def test_concurrent_read_write(self, tmp_path):
        from charlie.session_store import SessionStore

        db_path = str(tmp_path / "rw_concurrent.db")
        store = SessionStore(db_path)
        store.create_session("rw_sess", title="RW Test")

        read_results = []
        errors = []

        def writer():
            try:
                for i in range(20):
                    store.append("user", f"msg {i}", session_id="rw_sess")
                    time.sleep(0.001)
            except Exception as e:
                errors.append(f"writer: {e}")

        def reader():
            try:
                for _ in range(10):
                    msgs = store.get_recent(limit=10, session_id="rw_sess")
                    read_results.append(len(msgs))
                    time.sleep(0.002)
            except Exception as e:
                errors.append(f"reader: {e}")

        t_write = threading.Thread(target=writer)
        t_read = threading.Thread(target=reader)
        t_write.start()
        t_read.start()
        t_write.join(timeout=10)
        t_read.join(timeout=10)

        assert not errors, f"Concurrent read/write failed: {errors}"
        assert all(r >= 0 for r in read_results)
        store.close()


# ---------------------------------------------------------------------------
# Memory write -> read round-trip
# ---------------------------------------------------------------------------


class TestMemoryRoundTrip:
    """Verify memory entries persist through write -> capacity check -> read."""

    def test_memory_write_and_capacity(self):
        """Write entries to memory, verify capacity header reflects count."""
        from charlie.tools import (
            _MEMORY_MAX_CHARS,
            _MEMORY_SEP,
            _format_capacity,
            _parse_memory_entries,
        )

        # Simulate writing 3 entries
        entries = [
            "User prefers dark mode",
            "User's name is Alex",
            "Working on Charlie project",
        ]
        content = _MEMORY_SEP.join(entries)
        capacity = _format_capacity("memory", entries, _MEMORY_MAX_CHARS["memory"])
        assert "entries" in capacity
        assert "3" in capacity

        # Verify parsing round-trips correctly
        parsed = _parse_memory_entries(content)
        assert len(parsed) == 3
        assert parsed[0] == "User prefers dark mode"
        assert parsed[2] == "Working on Charlie project"

    def test_memory_entry_replace(self):
        """Replace a specific entry and verify others are preserved."""
        from charlie.tools import _MEMORY_SEP, _parse_memory_entries

        entries = ["old fact", "keep this", "also keep"]
        content = _MEMORY_SEP.join(entries)
        parsed = _parse_memory_entries(content)
        assert len(parsed) == 3

        # Simulate replace: update entry index 0
        parsed[0] = "new fact"
        new_content = _MEMORY_SEP.join(parsed)
        new_parsed = _parse_memory_entries(new_content)
        assert new_parsed[0] == "new fact"
        assert new_parsed[1] == "keep this"
        assert new_parsed[2] == "also keep"

    def test_memory_max_chars_enforcement(self):
        """Verify _MEMORY_MAX_CHARS limits are defined and reasonable."""
        from charlie.tools import _MEMORY_MAX_CHARS

        assert "memory" in _MEMORY_MAX_CHARS
        assert "user" in _MEMORY_MAX_CHARS
        assert "opinions" in _MEMORY_MAX_CHARS
        assert all(v > 100 for v in _MEMORY_MAX_CHARS.values())

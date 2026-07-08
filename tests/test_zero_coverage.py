"""Unit tests for zero-coverage modules.

Covers: text_utils, budget, streaming.TextStreamFilter, recovery_cache,
config defaults, runtime.configure.
"""

import sys

# ---------------------------------------------------------------------------
# text_utils.normalize_app_list
# ---------------------------------------------------------------------------

class TestNormalizeAppList:
    """Tests for charlie.text_utils.normalize_app_list."""

    def test_single_known_app_unchanged(self):
        from charlie.text_utils import normalize_app_list
        assert normalize_app_list("open chrome") == "open chrome"

    def test_two_known_apps_get_and(self):
        from charlie.text_utils import normalize_app_list
        result = normalize_app_list("open chrome notepad")
        assert result == "open chrome and notepad"

    def test_three_known_apps(self):
        from charlie.text_utils import normalize_app_list
        result = normalize_app_list("open chrome notepad calculator")
        assert result == "open chrome and notepad and calculator"

    def test_unknown_words_not_joined(self):
        from charlie.text_utils import normalize_app_list
        # "foo" is not in KNOWN_APPS, so only "chrome" qualifies
        result = normalize_app_list("open chrome foo")
        assert result == "open chrome foo"

    def test_no_open_prefix_unchanged(self):
        from charlie.text_utils import normalize_app_list
        assert normalize_app_list("chrome notepad") == "chrome notepad"

    def test_mixed_known_and_unknown(self):
        from charlie.text_utils import normalize_app_list
        result = normalize_app_list("start firefox edge foobar")
        assert result == "start firefox and edge foobar"

    def test_case_insensitive(self):
        from charlie.text_utils import normalize_app_list
        result = normalize_app_list("Open Chrome Notepad")
        assert result == "Open Chrome and Notepad"

    def test_launch_prefix(self):
        from charlie.text_utils import normalize_app_list
        result = normalize_app_list("launch spotify vlc")
        assert result == "launch spotify and vlc"

    def test_run_prefix(self):
        from charlie.text_utils import normalize_app_list
        result = normalize_app_list("run code terminal")
        assert result == "run code and terminal"

    def test_empty_string(self):
        from charlie.text_utils import normalize_app_list
        assert normalize_app_list("") == ""

    def test_no_apps_after_prefix(self):
        from charlie.text_utils import normalize_app_list
        assert normalize_app_list("open the browser") == "open the browser"


# ---------------------------------------------------------------------------
# budget.IterationBudget
# ---------------------------------------------------------------------------

class TestIterationBudget:
    """Tests for charlie.budget.IterationBudget."""

    def test_default_values(self):
        from charlie.budget import IterationBudget
        b = IterationBudget()
        assert b.max_turns == 12
        assert b.turns_used == 0
        assert b.remaining == 12

    def test_try_spend_success(self):
        from charlie.budget import IterationBudget
        b = IterationBudget(max_turns=5)
        assert b.try_spend("web_search") is True
        assert b.turns_used == 1
        assert b.remaining == 4

    def test_try_spend_cost_accumulates(self):
        from charlie.budget import IterationBudget
        b = IterationBudget(max_turns=5)
        assert b.try_spend("delegate_task") is True  # cost=3
        assert b.turns_used == 3
        assert b.try_spend("web_search") is True  # cost=1
        assert b.turns_used == 4

    def test_try_spend_exhausted_returns_false(self):
        from charlie.budget import IterationBudget
        b = IterationBudget(max_turns=3)
        assert b.try_spend("delegate_task") is True  # cost=3
        assert b.is_exhausted() is True
        assert b.try_spend("web_search") is False

    def test_is_exhausted(self):
        from charlie.budget import IterationBudget
        b = IterationBudget(max_turns=2)
        assert b.is_exhausted() is False
        b.try_spend("web_search")
        assert b.is_exhausted() is False
        b.try_spend("web_search")
        assert b.is_exhausted() is True

    def test_remaining_never_negative(self):
        from charlie.budget import IterationBudget
        b = IterationBudget(max_turns=1)
        b.try_spend("web_search")
        assert b.remaining == 0
        b.try_spend("web_search")  # rejected
        assert b.remaining == 0

    def test_unknown_tool_defaults_to_cost_1(self):
        from charlie.budget import IterationBudget
        b = IterationBudget(max_turns=2)
        assert b.try_spend("unknown_tool_xyz") is True
        assert b.turns_used == 1


# ---------------------------------------------------------------------------
# streaming.TextStreamFilter
# ---------------------------------------------------------------------------

class TestTextStreamFilter:
    """Tests for charlie.streaming.TextStreamFilter."""

    def test_clean_passthrough(self):
        from charlie.streaming import TextStreamFilter
        f = TextStreamFilter()
        assert f.push("hello world") == "hello world"

    def test_think_block_stripped(self):
        from charlie.streaming import TextStreamFilter
        f = TextStreamFilter()
        assert f.push("before<think>reasoning</think>after") == "beforeafter"

    def test_tool_line_stripped(self):
        from charlie.streaming import TextStreamFilter
        f = TextStreamFilter()
        result = f.push("hello\nTOOL: web_search(\"test\")\nworld\n")
        assert "TOOL:" not in result
        assert "hello" in result
        assert "world" in result

    def test_partial_think_tag_across_chunks(self):
        from charlie.streaming import TextStreamFilter
        f = TextStreamFilter()
        out1 = f.push("hello <")
        out2 = f.push("think>hidden")
        out3 = f.push("</think>world")
        combined = out1 + out2 + out3
        assert "hidden" not in combined
        assert "hello" in combined
        assert "world" in combined

    def test_partial_tool_tag_across_chunks(self):
        from charlie.streaming import TextStreamFilter
        f = TextStreamFilter()
        out1 = f.push("hi\nTOO")
        out2 = f.push("L: web_search(\"q\")\nbye\n")
        combined = out1 + out2
        assert "web_search" not in combined
        assert "hi" in combined
        assert "bye" in combined

    def test_think_block_multiple(self):
        from charlie.streaming import TextStreamFilter
        f = TextStreamFilter()
        result = f.push("a<think>x</think>b<think>y</think>c")
        assert result == "abc"

    def test_push_yields_safe_content(self):
        from charlie.streaming import TextStreamFilter
        f = TextStreamFilter()
        result = f.push("hello")
        assert result == "hello"
        assert f.flush() == ""

    def test_flush_returns_residual_buffer(self):
        from charlie.streaming import TextStreamFilter
        f = TextStreamFilter()
        f.push("hello<")
        assert f.flush() == "<"

    def test_flush_returns_empty_during_think(self):
        from charlie.streaming import TextStreamFilter
        f = TextStreamFilter()
        f.push("<think>reasoning")
        assert f.flush() == ""

    def test_flush_returns_empty_during_tool(self):
        from charlie.streaming import TextStreamFilter
        f = TextStreamFilter()
        f.push("TOOL: web_search")
        assert f.flush() == ""

    def test_mixed_content(self):
        from charlie.streaming import TextStreamFilter
        f = TextStreamFilter()
        result = f.push("start<think>think</think>middle TOOL: foo\nend")
        assert "think" not in result
        assert "foo" not in result
        assert "start" in result
        assert "middle" in result
        assert "end" in result

    def test_empty_chunk(self):
        from charlie.streaming import TextStreamFilter
        f = TextStreamFilter()
        assert f.push("") == ""


# ---------------------------------------------------------------------------
# recovery_cache
# ---------------------------------------------------------------------------

class TestRecoveryCache:
    """Tests for charlie.recovery_cache."""

    def test_cache_key_deterministic(self):
        from charlie.recovery_cache import _get_cache_key
        k1 = _get_cache_key("dir /s", "TIMEOUT", "timed out")
        k2 = _get_cache_key("dir /s", "TIMEOUT", "timed out")
        assert k1 == k2

    def test_cache_key_different_for_different_inputs(self):
        from charlie.recovery_cache import _get_cache_key
        k1 = _get_cache_key("cmd1", "TIMEOUT", "err")
        k2 = _get_cache_key("cmd2", "TIMEOUT", "err")
        assert k1 != k2

    def test_get_miss_when_no_cache_file(self, tmp_path, monkeypatch):
        from charlie import recovery_cache
        monkeypatch.setattr(recovery_cache, "CACHE_FILE", str(tmp_path / "missing.json"))
        assert recovery_cache.get_cached_resolution("cmd", "TIMEOUT", "err") is None

    def test_save_then_get_hit(self, tmp_path, monkeypatch):
        from charlie import recovery_cache
        cache_path = tmp_path / "cache.json"
        monkeypatch.setattr(recovery_cache, "CACHE_FILE", str(cache_path))
        recovery_cache.set_cached_resolution("dir /s", "TIMEOUT", "timed out", "dir /s /b")
        result = recovery_cache.get_cached_resolution("dir /s", "TIMEOUT", "timed out")
        assert result == "dir /s /b"

    def test_get_miss_after_save_different_key(self, tmp_path, monkeypatch):
        from charlie import recovery_cache
        cache_path = tmp_path / "cache.json"
        monkeypatch.setattr(recovery_cache, "CACHE_FILE", str(cache_path))
        recovery_cache.set_cached_resolution("cmd1", "TIMEOUT", "err", "fixed1")
        assert recovery_cache.get_cached_resolution("cmd2", "TIMEOUT", "err") is None


# ---------------------------------------------------------------------------
# runtime.configure
# ---------------------------------------------------------------------------

class TestRuntime:
    """Tests for charlie.runtime.configure."""

    def test_non_windows_noop(self):
        """On non-Windows, configure() should be a no-op."""
        import asyncio

        from charlie import runtime
        original_policy = asyncio.get_event_loop_policy()
        runtime.configure()
        # Should not crash; on non-Windows it's a no-op
        # On Windows it sets WindowsSelectorEventLoopPolicy
        if sys.platform != "win32":
            assert asyncio.get_event_loop_policy() is original_policy


# ---------------------------------------------------------------------------
# config defaults
# ---------------------------------------------------------------------------

class TestConfig:
    """Tests for charlie.config.Config defaults."""

    def test_config_loads(self):
        from charlie.config import config
        assert hasattr(config, "small_llm_url")
        assert hasattr(config, "charlie_host")
        assert hasattr(config, "charlie_port")

    def test_charlie_port_is_int(self):
        from charlie.config import config
        assert isinstance(config.charlie_port, int)

    def test_soul_is_string(self):
        from charlie.config import config
        assert isinstance(config.soul, str)
        assert len(config.soul) > 0

    def test_config_singleton(self):
        """Config should be a module-level singleton."""
        from charlie.config import config as c1
        from charlie.config import config as c2
        assert c1 is c2

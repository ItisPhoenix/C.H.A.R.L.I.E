"""Tests for Charlie Buddy widget, dashboard, bridge, and supporting modules.

Tests cover state machine, expression mapping, screen context, proactive engine,
and widget bridge signal wiring. All tests run without a display server (PyQt offscreen).
"""

import os
import time

# Force Qt offscreen platform before any Qt imports
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from charlie.widget_bridge import WidgetBridge
from charlie.screen_context import ScreenContextMonitor
from charlie.proactive_remark import ProactiveRemarkEngine


# ── WidgetBridge ────────────────────────────────────────────────────────────

class TestWidgetBridge:
    def test_init(self):
        bridge = WidgetBridge()
        assert bridge._emotional_state == "neutral"
        assert bridge._state == "idle"
        assert bridge._screen_context == "unknown"

    def test_record_interaction(self):
        bridge = WidgetBridge()
        before = time.time() - 10
        bridge.record_interaction()
        assert bridge._last_interaction_time >= before

    def test_on_emotional_state_stores(self):
        bridge = WidgetBridge()
        bridge.on_emotional_state("energetic")
        assert bridge._emotional_state == "energetic"

    def test_on_screen_context_stores(self):
        bridge = WidgetBridge()
        bridge.on_screen_context("VS Code — test.py")
        assert bridge._screen_context == "VS Code — test.py"


# ── ScreenContextMonitor ────────────────────────────────────────────────────

class TestScreenContextMonitor:
    def test_classify_error(self):
        assert ScreenContextMonitor._classify("Python Error - main.py") == "error"

    def test_classify_code(self):
        assert ScreenContextMonitor._classify("VS Code — test.py") == "coding"

    def test_classify_browser(self):
        assert ScreenContextMonitor._classify("Google - Chrome") == "browsing"

    def test_classify_media(self):
        assert ScreenContextMonitor._classify("YouTube - Video Title") == "leisure"

    def test_classify_other(self):
        assert ScreenContextMonitor._classify("My App Window") == "other"

    def test_classify_empty(self):
        assert ScreenContextMonitor._classify("") == "unknown"

    def test_init(self):
        monitor = ScreenContextMonitor(callback=None, poll_interval=1.0)
        assert monitor.category == "unknown"
        assert monitor.current_title == ""


# ── ProactiveRemarkEngine ───────────────────────────────────────────────────

class TestProactiveRemarkEngine:
    def test_cooldown_enforced(self):
        engine = ProactiveRemarkEngine()
        engine._cooldown_seconds = 0  # Disable cooldown for test
        engine._session_start = time.time() + 1000  # Future start to skip morning greeting
        engine._last_remark_time = time.time()
        engine._cooldown_seconds = 900
        result = engine.check()
        assert result is None  # Cooldown active

    def test_error_window_trigger(self):
        engine = ProactiveRemarkEngine()
        engine._last_remark_time = 0  # No cooldown
        engine._cooldown_seconds = 0
        engine._session_start = time.time() + 1000
        engine._last_window_title = "Error in Python script"
        engine._last_interaction_time = time.time()  # Not silent
        engine._window_change_count = 0
        result = engine.check()
        assert result is not None
        assert "wrong" in result.lower() or "error" in result.lower()

    def test_fallback_templates(self):
        engine = ProactiveRemarkEngine()
        assert len(engine._generate_llm_remark("test prompt")) is not None

    def test_update_screen_context(self):
        engine = ProactiveRemarkEngine()
        engine.update_screen_context("VS Code")
        assert engine._last_window_title == "VS Code"
        engine.update_screen_context("VS Code")  # Same title, no count increment
        assert engine._window_change_count == 1
        engine.update_screen_context("Chrome")
        assert engine._window_change_count == 2

    def test_record_interaction_resets_silence(self):
        engine = ProactiveRemarkEngine()
        engine._last_interaction_time = 0
        engine.record_interaction()
        assert engine._last_interaction_time > 0

    def test_set_user_name(self):
        engine = ProactiveRemarkEngine()
        engine.set_user_name("Abhishek")
        assert engine._user_name == "Abhishek"


# ── Buddy state machine (logic tests only, no QWidget) ──────────────────────

class TestBuddyStateLogic:
    def test_state_colors_cover_emotions(self):
        from charlie.buddy import _ORB_COLORS
        emotions = {"neutral", "energetic", "calm", "frustrated", "sad"}
        for emotion in emotions:
            assert emotion in _ORB_COLORS, f"Missing color for {emotion}"

    def test_expression_map_covers_common_states(self):
        from charlie.buddy import _EXPRESSION_MAP
        # Every (emotion, state) combo used in _STATE_SIZE keys should have an expression
        states = ["idle", "speaking", "listening", "thinking", "sleeping", "stance_pose"]
        for state in states:
            assert ("neutral", state) in _EXPRESSION_MAP, f"Missing expression for (neutral, {state})"

    def test_state_size_covers_states(self):
        from charlie.buddy import _STATE_SIZE
        expected = {"idle", "greeting", "listening", "thinking", "speaking", "sleeping", "stance_pose", "proactive"}
        assert expected == set(_STATE_SIZE.keys())

    def test_stance_map_keys(self):
        from charlie.buddy import STANCE_MAP
        expected = {"ai_hype", "privacy", "open_source", "automation", "big_tech"}
        assert expected == set(STANCE_MAP.keys())

    def test_tod_modifiers(self):
        from charlie.buddy import _tod_modifiers
        # Morning
        b, s = _tod_modifiers(8)
        assert b > 1.0 and s > 1.0
        # Afternoon
        b, s = _tod_modifiers(14)
        assert b == 0.95 and s == 1.0
        # Evening
        b, s = _tod_modifiers(20)
        assert b < 1.0
        # Night
        b, s = _tod_modifiers(2)
        assert b < 1.0 and s < 1.0

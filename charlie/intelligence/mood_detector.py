"""Mood detector — implicit analysis + explicit check-ins for adaptive responses."""

import json
import logging
import os
import time
from collections import deque

logger = logging.getLogger("charlie.intelligence.mood")

# Mood states
MOOD_STRESSED = "stressed"    # Short, rushed messages
MOOD_FOCUSED = "focused"      # Long, detailed messages
MOOD_CASUAL = "casual"        # Normal interaction
MOOD_FRUSTRATED = "frustrated" # Repeated errors, negative words


class MoodDetector:
    """Detects user mood from message patterns and adjusts CHARLIE's behavior."""

    def __init__(self):
        self.message_history = deque(maxlen=50)  # Recent messages
        self.checkin_interval = 3600  # 1 hour between explicit check-ins
        self.last_checkin = 0
        self.current_mood = MOOD_CASUAL
        self._load_state()

    def _load_state(self):
        """Load mood state from config."""
        try:
            config_path = os.path.join(os.getcwd(), "charlie_config.json")
            if os.path.exists(config_path):
                with open(config_path, "r") as f:
                    cfg = json.load(f)
                mood_cfg = cfg.get("telegram", {}).get("mood", {})
                self.current_mood = mood_cfg.get("current", MOOD_CASUAL)
        except Exception:
            pass

    def _save_state(self):
        """Save mood state to config."""
        try:
            config_path = os.path.join(os.getcwd(), "charlie_config.json")
            cfg = {}
            if os.path.exists(config_path):
                with open(config_path, "r") as f:
                    cfg = json.load(f)
            cfg.setdefault("telegram", {}).setdefault("mood", {})["current"] = self.current_mood
            with open(config_path, "w") as f:
                json.dump(cfg, f, indent=2)
        except Exception:
            pass

    def analyze_message(self, text: str) -> str:
        """Analyze a message and update mood. Returns detected mood."""
        if not text:
            return self.current_mood

        entry = {
            "text": text,
            "length": len(text),
            "timestamp": time.time(),
        }
        self.message_history.append(entry)

        # Implicit analysis
        mood = self._analyze_patterns()

        if mood != self.current_mood:
            logger.info(f"mood_change | {self.current_mood} → {mood}")
            self.current_mood = mood
            self._save_state()

        return self.current_mood

    def _analyze_patterns(self) -> str:
        """Analyze recent message patterns to detect mood."""
        if len(self.message_history) < 3:
            return MOOD_CASUAL

        recent = list(self.message_history)[-10:]

        # Average message length
        avg_length = sum(m["length"] for m in recent) / len(recent)

        # Response time (if available)
        times = [m["timestamp"] for m in recent if m.get("timestamp")]
        avg_gap = 0
        if len(times) >= 2:
            gaps = [times[i+1] - times[i] for i in range(len(times)-1)]
            avg_gap = sum(gaps) / len(gaps)

        # Negative word detection
        negative_words = {"error", "fail", "broken", "wrong", "bad", "hate", "annoying", "frustrated", "stuck", "why"}
        neg_count = sum(
            1 for m in recent
            if any(w in m["text"].lower() for w in negative_words)
        )

        # Stressed: short messages, fast responses
        if avg_length < 30 and avg_gap < 10:
            return MOOD_STRESSED

        # Focused: long messages
        if avg_length > 200:
            return MOOD_FOCUSED

        # Frustrated: many negative words
        if neg_count >= 3:
            return MOOD_FRUSTRATED

        return MOOD_CASUAL

    def should_checkin(self) -> bool:
        """Should CHARLIE ask how the user is feeling?"""
        now = time.time()
        if now - self.last_checkin < self.checkin_interval:
            return False
        # Check in if mood has been stressed/frustrated for a while
        if self.current_mood in (MOOD_STRESSED, MOOD_FRUSTRATED):
            self.last_checkin = now
            return True
        return False

    def get_checkin_message(self) -> str:
        """Generate a mood check-in message."""
        if self.current_mood == MOOD_STRESSED:
            return "You seem to be in a rush. Need anything specific I can help with quickly?"
        elif self.current_mood == MOOD_FRUSTRATED:
            return "I notice things might be frustrating. Want me to help troubleshoot, or should I give you some space?"
        return "How are you doing?"

    def get_response_style(self) -> dict:
        """Get response style adjustments based on current mood."""
        styles = {
            MOOD_STRESSED: {
                "verbosity": "minimal",
                "emoji": False,
                "questions": False,
                "detail_level": "concise",
            },
            MOOD_FOCUSED: {
                "verbosity": "thorough",
                "emoji": False,
                "questions": True,
                "detail_level": "comprehensive",
            },
            MOOD_CASUAL: {
                "verbosity": "normal",
                "emoji": True,
                "questions": True,
                "detail_level": "balanced",
            },
            MOOD_FRUSTRATED: {
                "verbosity": "empathetic",
                "emoji": False,
                "questions": False,
                "detail_level": "solution-focused",
            },
        }
        return styles.get(self.current_mood, styles[MOOD_CASUAL])

"""Lightweight personality and emotion detection for Charlie.

Provides zero-latency keyword-based emotion classification and explicit
voice command parsing. No LLM calls -- pure regex/keyword matching.
"""

import re
from typing import Optional

# -- Emotion keyword maps --------------------------------------------------

_ENERGETIC_RE = re.compile(
    r"\b(?:urgent|emergency|crash|asap|now|broken|help|happy|amazing|awesome|yay|love|great news|excited)\b",
    re.IGNORECASE,
)

_SAD_CALM_RE = re.compile(
    r"\b(?:sad|sorry|terrible|depressed|lonely|miss|unfortunately|bad day|frustrat\w*|annoyed|hate|stupid|why won't|useless)\b",
    re.IGNORECASE,
)
# -- Voice command patterns ------------------------------------------------

_VOICE_COMMANDS: list[tuple[re.Pattern, str]] = [
    (
        re.compile(r"\b(?:be energetic|speak faster|cheer up)\b", re.IGNORECASE),
        "energetic",
    ),
    (re.compile(r"\b(?:calm down|speak slower|easy)\b", re.IGNORECASE), "calm"),
]


def get_emotion_for_context(user_text: str, history: list) -> str:
    """Classify user intent into an emotion tag via keyword heuristic.

    Returns one of: "neutral", "energetic", or "calm".
    Zero latency -- no LLM call.
    """
    if not user_text or not user_text.strip():
        return "neutral"

    # Frustrated/annoyed -> calm (skip validation, just fix it)
    # Urgent/emergency -> energetic
    # Happy/excited -> energetic
    # Sad/depressed -> calm
    if _SAD_CALM_RE.search(user_text):
        return "calm"
    if _ENERGETIC_RE.search(user_text):
        return "energetic"
    return "neutral"


def parse_voice_command(user_text: str) -> Optional[str]:
    """Detect explicit TTS override commands.

    Returns the emotion string ("energetic" / "calm") if a command was detected,
    or None if no command was found (normal processing continues).
    """
    if not user_text or not user_text.strip():
        return None

    for pattern, emotion in _VOICE_COMMANDS:
        if pattern.search(user_text):
            return emotion
    return None

"""Deterministic research-intent and mode selection."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from charlie.research.models import ResearchMode

_CURRENT_SIGNALS = re.compile(
    r"\b(latest|current|currently|today|now|right now|live|recent|breaking|trending|news|"
    r"price|prices|cost|buy|shopping|recommend|recommendation|travel|release|version|"
    r"availability|schedule|score|weather|sports|on twitter|on x\b)\b",
    re.IGNORECASE,
)
_RESEARCH_SIGNALS = re.compile(
    r"\b(research|investigate|deep research|in[- ]depth|compare thoroughly|thoroughly|"
    r"look into|analyze current)\b",
    re.IGNORECASE,
)
_INTERACTIVE_SIGNALS = re.compile(
    r"\b(play|watch|listen|open|click|fill|submit|post|send|log in|show me on)\b",
    re.IGNORECASE,
)
_STABLE_EXPLANATION = re.compile(
    r"\b(what is|what are|how does|how do|explain|define)\b.*\b(list comprehension|"
    r"recursion|variable|function|python|javascript|math|grammar)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ResearchDecision:
    should_research: bool
    mode: Optional[ResearchMode]
    reason: str
    interactive: bool = False


def _coerce_mode(mode: str | ResearchMode | None) -> Optional[ResearchMode]:
    if mode is None or str(mode).lower() in {"", "auto"}:
        return None
    try:
        return ResearchMode(str(mode).lower())
    except ValueError:
        return None


def choose_mode(query: str, requested: str | ResearchMode | None = None) -> ResearchDecision:
    """Choose research mode without making the LLM responsible for obvious routing."""
    explicit = _coerce_mode(requested)
    text = query.strip()
    if explicit is not None:
        return ResearchDecision(True, explicit, "explicit mode")
    if _INTERACTIVE_SIGNALS.search(text) and re.search(r"\bon\s+(youtube|amazon|x|twitter)\b", text, re.I):
        return ResearchDecision(False, None, "interactive site task", interactive=True)
    if _RESEARCH_SIGNALS.search(text):
        mode = ResearchMode.DEEP if re.search(r"deep|in[- ]depth|thorough", text, re.I) else ResearchMode.STANDARD
        return ResearchDecision(True, mode, "explicit research intent")
    if _STABLE_EXPLANATION.search(text) and not _CURRENT_SIGNALS.search(text):
        return ResearchDecision(False, None, "stable general knowledge")
    if _CURRENT_SIGNALS.search(text):
        mode = ResearchMode.STANDARD if re.search(
            r"\b(price|shopping|products|recommend|compare|travel|research|investigate|trending)\b", text, re.I
        ) else ResearchMode.QUICK
        return ResearchDecision(True, mode, "fresh or materially changing information")
    return ResearchDecision(False, None, "no live-web signal")


def route(query: str, requested: str | ResearchMode | None = None) -> ResearchDecision:
    return choose_mode(query, requested)

"""Deterministic research-intent and mode selection."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from charlie.research.models import ResearchMode

_CURRENT_SIGNALS = re.compile(
    r"\b(latest|current|currently|today|now|right now|live|recent|recently|lately|breaking|trending|news|"
    r"price|prices|cost|buy|shopping|recommend|recommendation|travel|release|version|"
    r"availability|schedule|score|weather|sports|on twitter|on x\b)\b",
    re.IGNORECASE,
)
_SOCIAL_CONVERSATION_SIGNALS = re.compile(
    r"\b(?:how\s+(?:are|have)\s+you\b|are\s+you\s+(?:okay|ok|well|doing)\b|"
    r"what(?:'s|\s+is)\s+up\s+with\s+you\b|how\s+are\s+things\s+going\b)",
    re.IGNORECASE,
)
_LOCAL_VERSION_CONTEXT = re.compile(
    r"\b(?:installed|locally|on this (?:pc|computer|machine|device)|on my (?:pc|computer|machine|device))\b",
    re.IGNORECASE,
)
_RESEARCH_SIGNALS = re.compile(
    r"\b(research|investigate|deep research|in[- ]depth|compare|comparison|thorough|thoroughly|"
    r"look into|analyze current|multi[- ]source|multi[- ]step)\b",
    re.IGNORECASE,
)
_FETCHED_EVIDENCE_SIGNALS = re.compile(
    r"\b(?:official\s+(?:source|sources|publication|page)|sources?|citations?|"
    r"evidence|publication|paper|report)\b",
    re.IGNORECASE,
)
_SUSTAINED_RESEARCH_SIGNALS = re.compile(
    r"\b(?:investigate|deep\s+research|in[- ]depth|thorough(?:ly)?|"
    r"multi[- ]source|multi[- ]step|look\s+into|analyze\s+current|comparison|compare)\b",
    re.IGNORECASE,
)
_BACKGROUND_TASK_REQUEST = re.compile(
    r"\b(?:start|create|run|schedule)\s+(?:a\s+)?background\s+(?:task|job)\b",
    re.IGNORECASE,
)
_LOCAL_STATE_UPDATE = re.compile(
    r"\b(?:update|change|reschedule|cancel|remove|delete|edit|replace|correct)\b"
    r"(?s:.{0,160}?)\b(?:memory|notes?|reminder|schedule|task)\b",
    re.IGNORECASE,
)
_BRIEFING_SIGNALS = re.compile(
    r"\b(?:briefing|news\s+roundup|news\s+digest|daily\s+summary|intelligence\s+briefing)\b",
    re.IGNORECASE,
)
_FACTUAL_RESEARCH = re.compile(r"\bwhat\s+is\b.+\bused\s+for\b", re.IGNORECASE)
_SPECIALIZED_FACTUAL = re.compile(
    r"\bwhat\s+is\b.+\b(status|proposal|protocol|canonicalization|specification|discovery)\b",
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


def is_briefing_query(query: str) -> bool:
    """Identify briefing intent consistently across routing and research."""
    return bool(_BRIEFING_SIGNALS.search(query.strip()))


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
    requires_fetched_sources = (
        is_briefing_query(text)
        or _RESEARCH_SIGNALS.search(text) is not None
        or _FETCHED_EVIDENCE_SIGNALS.search(text) is not None
        or _FACTUAL_RESEARCH.search(text) is not None
        or _SPECIALIZED_FACTUAL.search(text) is not None
    )
    if explicit is not None and (explicit is not ResearchMode.QUICK or not requires_fetched_sources):
        return ResearchDecision(True, explicit, "explicit mode")
    if _BACKGROUND_TASK_REQUEST.search(text) and not _RESEARCH_SIGNALS.search(text):
        return ResearchDecision(False, None, "explicit background task request")
    if _INTERACTIVE_SIGNALS.search(text) and re.search(r"\bon\s+(youtube|amazon|x|twitter)\b", text, re.I):
        return ResearchDecision(False, None, "interactive site task", interactive=True)
    if is_briefing_query(text):
        return ResearchDecision(True, ResearchMode.STANDARD, "briefing requires fresh sources")
    if _RESEARCH_SIGNALS.search(text):
        mode = ResearchMode.DEEP if re.search(r"deep|in[- ]depth|thorough", text, re.I) else ResearchMode.STANDARD
        return ResearchDecision(True, mode, "explicit research intent")
    # Relative reminder times contain freshness words such as "now" and "schedule".
    # Keep clear local state edits on the tool path instead of sending them to web search.
    if _LOCAL_STATE_UPDATE.search(text):
        return ResearchDecision(False, None, "local state update")
    if _FETCHED_EVIDENCE_SIGNALS.search(text):
        return ResearchDecision(True, ResearchMode.STANDARD, "fetched evidence requested")
    if re.search(r"\bversion\b", text, re.IGNORECASE) and _LOCAL_VERSION_CONTEXT.search(text):
        return ResearchDecision(False, None, "local installed-version lookup")
    if _SOCIAL_CONVERSATION_SIGNALS.search(text):
        return ResearchDecision(False, None, "social conversation is not a live-web request")
    if _STABLE_EXPLANATION.search(text) and not _CURRENT_SIGNALS.search(text):
        return ResearchDecision(False, None, "stable general knowledge")
    if _FACTUAL_RESEARCH.search(text) or _SPECIALIZED_FACTUAL.search(text):
        return ResearchDecision(True, ResearchMode.STANDARD, "factual explanation requiring sources")
    if _CURRENT_SIGNALS.search(text):
        mode = ResearchMode.STANDARD
        return ResearchDecision(True, mode, "fresh or materially changing information")
    return ResearchDecision(False, None, "no live-web signal")


def route(query: str, requested: str | ResearchMode | None = None) -> ResearchDecision:
    return choose_mode(query, requested)


def is_sustained_research_query(query: str, decision: ResearchDecision | None = None) -> bool:
    """Return whether explicit wording asks for an independent research task."""
    if decision is not None and not decision.should_research:
        return False
    return bool(_SUSTAINED_RESEARCH_SIGNALS.search(query.strip()))

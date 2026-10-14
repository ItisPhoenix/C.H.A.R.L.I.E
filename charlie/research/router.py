"""Deterministic research-intent and mode selection."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from charlie.research.models import ResearchMode

_CURRENT_SIGNALS = re.compile(
    r"\b(latest|current|currently|today|now|right now|live|recent|recently|lately|breaking|trending|news|"
    r"price|prices|cost|buy|shopping|recommend|recommendation|travel|"
    r"availability|schedule|score|weather|sports|on twitter|on x\b)\b",
    re.IGNORECASE,
)
_SOCIAL_CONVERSATION_SIGNALS = re.compile(
    r"\b(?:how\s+(?:are|have)\s+you\b|are\s+you\s+(?:okay|ok|well|doing)\b|"
    r"what(?:'s|\s+is)\s+up\s+with\s+you\b|how\s+are\s+things\s+going\b)",
    re.IGNORECASE,
)
_RELEASE_LOOKUP_SIGNALS = re.compile(
    r"\b(?:release\s+(?:notes?|date|history)|version\s+(?:number|\d+(?:\.\d+)+)|"
    r"(?:what|which)\s+(?:is\s+)?(?:the\s+)?version|"
    r"(?:latest|current|recent|newest|stable)\b.{0,40}\b(?:release|version)\b|"
    r"when\s+(?:was|did)\b.{0,100}\breleased)\b",
    re.IGNORECASE,
)
_LOCAL_VERSION_CONTEXT = re.compile(
    r"\b(?:installed|locally|on this (?:pc|computer|machine|device)|on my (?:pc|computer|machine|device))\b",
    re.IGNORECASE,
)
_LOCAL_SHELL_FACTS = re.compile(
    r"\btaskkill\b.*\b(?:help|heading|first)\b"
    r"|\b(?:python|python3)\b.*\bversion\b.*\b(?:installed|locally|on (?:this|my)\b)\b",
    re.IGNORECASE | re.DOTALL,
)
_RESEARCH_SIGNALS = re.compile(
    r"\b(research|investigate|deep research|in[- ]depth|compare|comparison|thorough|thoroughly|"
    r"look into|analyze current|multi[- ]source|multi[- ]step)\b",
    re.IGNORECASE,
)
_EXPLICIT_RESEARCH_KEYWORD = re.compile(r"\bresearch\b", re.IGNORECASE)
_FETCHED_EVIDENCE_SIGNALS = re.compile(
    r"\b(?:official\s+(?:source|sources|publication|page)|(?<!open[- ])sources?|citations?|"
    r"evidence|publication|paper)\b",
    re.IGNORECASE,
)
_SUSTAINED_RESEARCH_SIGNALS = re.compile(
    r"\b(?:investigate|deep\s+research|in[- ]depth|thorough(?:ly)?|"
    r"multi[- ]source|multiple\s+sources?|multi[- ]step|look\s+into|analyze\s+current|comparison|compare)\b",
    re.IGNORECASE,
)
_BROAD_RESEARCH_SUBJECTS = re.compile(r"\b(?:of|from|across)\s+([^?.;]+)", re.IGNORECASE)
_BROAD_RESEARCH_DIMENSIONS = re.compile(
    r"\b(?:architecture|architectures|advantages?|benefits?|limitations?|features?|design|performance)\b",
    re.IGNORECASE,
)
_BACKGROUND_TASK_REQUEST = re.compile(
    r"\b(?:start|create|run|schedule)\s+(?:a\s+)?background\s+(?:task|job)\b",
    re.IGNORECASE,
)
_LOCAL_STATE_UPDATE = re.compile(
    r"\b(?:update|change|reschedule|cancel|remove|delete|edit|replace|correct|rename|relabel)\b"
    r"(?s:.{0,160}?)\b(?:memory|notes?|reminder|schedule|task|timer)\b",
    re.IGNORECASE,
)
_LOCAL_CALENDAR_LOOKUP = re.compile(r"\b(?:my|our)\s+(?:calendar|agenda|schedule|events?)\b", re.IGNORECASE)
_LOCAL_DESKTOP_ACTION = re.compile(
    r"\bnotepad\b(?s:.{0,220}?)\b(?:write|type|save|edit)\b|"
    r"\b(?:write|type|save|edit)\b(?s:.{0,220}?)\b(?:in|into|on|to)\s+(?:the\s+)?(?:notepad|text\s+editor|desktop|file|document)\b|"
    r"\b(?:write|create|save)\b(?s:.{0,180}?)\b(?:on|to|in)\s+(?:my\s+)?desktop\b|"
    r"\b(?:use|operate|control|focus|switch\s+to)\b(?s:.{0,160}?)"
    r"\b(?:current(?:ly)?|open|foreground|native|desktop|mouse|keyboard)\b(?s:.{0,120}?)"
    r"\b(?:window|dialog|file\s+picker|field|control|button|input|form|text\s+editor|notepad)\b"
    r"(?s:.{0,180}?)\b(?:type|enter|fill|write|save|click|press|select|set|create|edit|replace)\b",
    re.IGNORECASE,
)
_EXPLICIT_RESEARCH_LEAD = re.compile(
    r"^\s*(?:please\s+)?(?:(?:can|could|would)\s+you\s+)?"
    r"(?:research|investigate|compare|search|look\s+into|analyze|find\s+out)\b",
    re.IGNORECASE,
)
_BRIEFING_SIGNALS = re.compile(
    r"\b(?:briefing|news\s+roundup|news\s+digest|daily\s+summary|intelligence\s+briefing)\b",
    re.IGNORECASE,
)
_INTERACTIVE_SIGNALS = re.compile(
    r"\b(play|watch|listen|open|click|fill|submit|post|send|log in|show me on)\b",
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


def is_release_lookup_query(query: str) -> bool:
    """Match release/version lookups without treating reimplementations as release news."""
    return bool(_RELEASE_LOOKUP_SIGNALS.search(query.strip()))


def is_local_state_update(query: str) -> bool:
    """Return whether the request edits already-owned local state."""
    return bool(_LOCAL_STATE_UPDATE.search(query.strip()))


def _coerce_mode(mode: str | ResearchMode | None) -> Optional[ResearchMode]:
    if mode is None or str(mode).lower() in {"", "auto"}:
        return None
    try:
        return ResearchMode(str(mode).lower())
    except ValueError:
        return None


def choose_mode(query: str, requested: str | ResearchMode | None = None) -> ResearchDecision:
    """Use full research only when asked; route other live lookups to quick search."""
    explicit = _coerce_mode(requested)
    text = query.strip()
    explicit_research = bool(_EXPLICIT_RESEARCH_KEYWORD.search(text))
    if _LOCAL_CALENDAR_LOOKUP.search(text) and not explicit_research:
        return ResearchDecision(False, None, "personal calendar lookup")
    if _BACKGROUND_TASK_REQUEST.search(text) and not explicit_research:
        return ResearchDecision(False, None, "explicit background task request")
    if _LOCAL_DESKTOP_ACTION.search(text) and not _EXPLICIT_RESEARCH_LEAD.match(text):
        return ResearchDecision(False, None, "local desktop or file action")
    if _INTERACTIVE_SIGNALS.search(text) and re.search(r"\bon\s+(youtube|amazon|x|twitter)\b", text, re.I):
        return ResearchDecision(False, None, "interactive site task", interactive=True)
    if explicit_research:
        if explicit in {ResearchMode.STANDARD, ResearchMode.DEEP}:
            return ResearchDecision(True, explicit, "explicit mode")
        mode = ResearchMode.DEEP if re.search(r"deep|in[- ]depth|thorough", text, re.I) else ResearchMode.STANDARD
        return ResearchDecision(True, mode, "explicit research intent")
    if explicit is ResearchMode.QUICK:
        return ResearchDecision(True, explicit, "explicit mode")
    # Relative reminder times contain freshness words such as "now" and "schedule".
    # Keep clear local state edits on the tool path instead of sending them to web search.
    if is_local_state_update(text):
        return ResearchDecision(False, None, "local state update")
    if _FETCHED_EVIDENCE_SIGNALS.search(text):
        return ResearchDecision(True, ResearchMode.QUICK, "quick lookup with sources")
    if re.search(r"\bversion\b", text, re.IGNORECASE) and _LOCAL_VERSION_CONTEXT.search(text):
        return ResearchDecision(False, None, "local installed-version lookup")
    if _LOCAL_SHELL_FACTS.search(text) and not explicit_research:
        return ResearchDecision(False, None, "local shell fact lookup")
    if _SOCIAL_CONVERSATION_SIGNALS.search(text):
        return ResearchDecision(False, None, "social conversation is not a live-web request")
    if is_briefing_query(text):
        return ResearchDecision(True, ResearchMode.QUICK, "quick web summary")
    if _RESEARCH_SIGNALS.search(text):
        return ResearchDecision(True, ResearchMode.QUICK, "quick web search")
    if _CURRENT_SIGNALS.search(text) or is_release_lookup_query(text):
        return ResearchDecision(True, ResearchMode.QUICK, "quick web search for fresh information")
    return ResearchDecision(False, None, "no live-web signal")


def route(query: str, requested: str | ResearchMode | None = None) -> ResearchDecision:
    return choose_mode(query, requested)


def is_sustained_research_query(query: str, decision: ResearchDecision | None = None) -> bool:
    """Return whether wording or scope requires an independent research task."""
    if decision is not None and not decision.should_research:
        return False
    text = query.strip()
    if not _EXPLICIT_RESEARCH_KEYWORD.search(text):
        return False
    if _SUSTAINED_RESEARCH_SIGNALS.search(text):
        return True
    subject_list = _BROAD_RESEARCH_SUBJECTS.search(text)
    if subject_list is None:
        return False
    subjects = [
        part.strip()
        for part in re.split(r"\s*,\s*|\s+and\s+|\s+or\s+", subject_list.group(1), flags=re.I)
        if part.strip()
    ]
    dimensions = set(match.casefold() for match in _BROAD_RESEARCH_DIMENSIONS.findall(text))
    return len(subjects) >= 3 and len(dimensions) >= 2

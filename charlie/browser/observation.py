"""Turn Playwright's AI-mode accessibility snapshot into the same [N] mark convention as
charlie/desktop/uia.py, so the model carries one mental model across desktop and browser.

page.locator("body").aria_snapshot(mode="ai") returns an indented text tree where actionable
elements carry a [ref=eN] token; many of those elements have no inline accessible name and
their label sits in nested child lines instead, so we harvest each mark's subtree for text.
"""

import re
from dataclasses import dataclass
from typing import Any, List, Optional
from urllib.parse import parse_qs, urlparse

_LINE_RE = re.compile(
    r'^(?P<indent>\s*)- (?P<role>[a-zA-Z][\w-]*)(?:\s+"(?P<name>[^"]*)")?'
    r'(?P<attrs>(?:\s+\[[^\]]*\])*)\s*(?::\s*(?P<value>.*))?$'
)
_REF_RE = re.compile(r"\[ref=(e\d+)\]")
_URL_LINE_RE = re.compile(r"^\s*- /url:\s*(\S+)\s*$")

# Roles worth exposing to the model as marks -- mirrors uia.py's _INTERESTING_CONTROL_TYPES filter.
_INTERESTING_ROLES = {
    "button", "link", "textbox", "searchbox", "combobox", "checkbox",
    "radio", "tab", "menuitem", "listitem", "option",
}
_PRIMARY_INPUT_ROLES = {"textbox", "searchbox", "combobox", "checkbox", "radio"}
_MAX_NAME_CHARS = 100
_MAX_MARKS = 40
_TEXT_BUDGET_CHARS = 600
# headroom under core.py's _TOOL_RESULT_MAX_CHARS (2000) so we truncate deliberately, not mid-line
_OBSERVATION_MAX_CHARS = 1900


@dataclass
class Mark:
    mark_id: int
    role: str
    name: str
    ref: str
    href: Optional[str] = None


def _line_indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _harvest_subtree_text(lines: List[str], start: int, parent_indent: int) -> str:
    """Concatenate quoted names / colon values found anywhere under a mark's line, bounded."""
    parts: List[str] = []
    budget = _MAX_NAME_CHARS
    i = start
    while i < len(lines) and budget > 0:
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if _line_indent(line) <= parent_indent:
            break
        match = _LINE_RE.match(line)
        if match:
            text = match.group("name") or match.group("value")
            if text and text.strip():
                text = text.strip()
                parts.append(text)
                budget -= len(text)
        i += 1
    return " ".join(parts)[:_MAX_NAME_CHARS]


def parse_snapshot(raw: str) -> List[Mark]:
    """Parse an aria_snapshot(mode='ai') string into a flat, 1-indexed list of Marks."""
    lines = raw.split("\n")
    marks: List[Mark] = []
    for i, line in enumerate(lines):
        match = _LINE_RE.match(line)
        if not match:
            continue
        attrs = match.group("attrs") or ""
        ref_match = _REF_RE.search(attrs)
        role = match.group("role")
        if not ref_match or role not in _INTERESTING_ROLES:
            continue
        indent = len(match.group("indent"))
        name = (match.group("name") or match.group("value") or "").strip()
        href = None
        j = i + 1
        while j < len(lines) and _line_indent(lines[j]) > indent:
            url_match = _URL_LINE_RE.match(lines[j])
            if url_match:
                href = url_match.group(1)
                break
            j += 1
        if not name:
            name = _harvest_subtree_text(lines, i + 1, indent)
        marks.append(Mark(mark_id=0, role=role, name=name, ref=ref_match.group(1), href=href))
    for idx, mark in enumerate(marks, start=1):
        mark.mark_id = idx
    return marks


def _rank(mark: Mark) -> int:
    if mark.role in _PRIMARY_INPUT_ROLES:
        return 0
    if mark.role == "link":
        return 1
    if mark.role == "button":
        return 2
    return 3


def _dedupe(marks: List[Mark]) -> List[Mark]:
    """Chrome-heavy pages repeat generic controls ('More actions' x10) that would otherwise
    crowd real content links out of the cap; keep only the first mark per (role, name)."""
    seen = set()
    kept = []
    for mark in marks:
        key = (mark.role, mark.name)
        if mark.name and key in seen:
            continue
        seen.add(key)
        kept.append(mark)
    return kept


def rank_and_cap(marks: List[Mark], max_marks: int = _MAX_MARKS) -> List[Mark]:
    """Search inputs first, then links (page content), then buttons (chrome), then the rest."""
    ranked = sorted(_dedupe(marks), key=_rank)[:max_marks]
    for idx, mark in enumerate(ranked, start=1):
        mark.mark_id = idx
    return ranked


def extract_visible_text(page: Any, max_chars: int = _TEXT_BUDGET_CHARS) -> str:
    """Extract readable page text via trafilatura, same extractor the deleted BrowserPlugin used."""
    import trafilatura
    text = trafilatura.extract(page.content()) or ""
    return text[:max_chars]


def observe(page: Any, max_marks: int = _MAX_MARKS, text_budget: int = _TEXT_BUDGET_CHARS) -> tuple:
    """Parse the page once; return (formatted block, marks, visible text) so callers needing the
    marks/text too (the tier-3 blocked check) don't re-parse the accessibility snapshot."""
    # Guards a real race: observing mid-navigation can catch the DOM with no <body> yet.
    try:
        page.wait_for_selector("body", state="attached", timeout=3000)
    except Exception:
        pass
    raw = page.locator("body").aria_snapshot(mode="ai")
    marks = rank_and_cap(parse_snapshot(raw), max_marks)
    mark_lines = [f'[{m.mark_id}] {m.role} "{m.name}"' for m in marks] if marks else ["(no marked elements)"]
    current_url = str(page.url)
    title = str(page.title())
    result = f"URL: {current_url}\nTITLE: {title}\n" + "\n".join(mark_lines)
    text = extract_visible_text(page, text_budget)
    if text:
        result += f"\nTEXT: {text}"
    # ponytail: blunt tail-truncation -- lower _MAX_MARKS/_TEXT_BUDGET_CHARS instead if this trips often
    if len(result) > _OBSERVATION_MAX_CHARS:
        result = result[:_OBSERVATION_MAX_CHARS - 3] + "..."
    from charlie.browser import session

    session.record_observation(
        current_url,
        page_type=classify_page_type(current_url, title, marks, text),
        capabilities=discover_page_capabilities(marks),
        search_query=discover_search_query(current_url),
        signature=_observation_signature(marks, text),
    )
    return result, marks, text


def build_observation(page: Any, max_marks: int = _MAX_MARKS, text_budget: int = _TEXT_BUDGET_CHARS) -> str:
    """Assemble the full compact observation block the agent/recipes see for the current page."""
    result, _marks, _text = observe(page, max_marks, text_budget)
    return result


_BLOCK_PHRASES = (
    "verify you are human",
    "unusual traffic",
    "enable javascript",
    "access denied",
    "cf-challenge",
    "interstitialchallenge",
    "verify?provider=interstitial",
)


def is_blocked(marks: List[Mark], text: str, status: Optional[int] = None) -> bool:
    """Definite bot-detection signal (status code or challenge phrase) -- safe to hard-stop on.

    Deliberately excludes the "few marks, little text" case: plenty of legitimate pages
    (example.com being the textbook case) are just that sparse, so emptiness alone would abort
    the agent loop on real pages. See looks_empty() for that weaker signal.
    """
    if status in (403, 429):
        return True
    lowered = text.lower()
    return any(phrase in lowered for phrase in _BLOCK_PHRASES)


def looks_empty(marks: List[Mark], text: str) -> bool:
    """Weak signal only -- combine with is_blocked() before treating it as a real block."""
    return len(marks) < 3 and len(text) < 200


def discover_search_query(url: str) -> Optional[str]:
    """Read a search term from any common query parameter without naming a site."""
    query = parse_qs(urlparse(url).query)
    for key in ("q", "query", "search_query", "search"):
        values = query.get(key, [])
        if values and values[0].strip():
            return values[0].strip()
    return None


def discover_page_capabilities(marks: List[Mark]) -> List[str]:
    """Infer capabilities from current accessible roles and rendered labels."""
    names = " ".join(mark.name.casefold() for mark in marks)
    capabilities: set[str] = set()
    if any(mark.role in _PRIMARY_INPUT_ROLES for mark in marks):
        capabilities.add("input")
    if any(mark.role in {"textbox", "searchbox"} for mark in marks) or "search" in names:
        capabilities.add("search")
    if any(mark.role in {"checkbox", "radio", "option", "listitem"} for mark in marks):
        capabilities.add("choice")
    if any(mark.role in {"checkbox", "radio", "combobox", "listbox"} for mark in marks):
        capabilities.add("constraints")
    if any(term in names for term in ("sort", "ascending", "descending", "low to high", "high to low")):
        capabilities.add("sort")
    if any(mark.role == "link" for mark in marks):
        capabilities.add("links")
    if any(term in names for term in ("play", "pause", "volume", "seek")):
        capabilities.add("media_controls")
    if any(
        term in names
        for term in ("repository", "branch", "commit", "source code", "file tree", "directory")
    ):
        capabilities.add("repository")
    return sorted(capabilities)


def classify_page_type(url: str, title: str, marks: List[Mark], text: str) -> str:
    """Classify page shape from semantic evidence, with URL shape as a fallback hint."""
    parsed = urlparse(url)
    path = parsed.path.lower()
    semantic_names = " ".join(mark.name.casefold() for mark in marks)
    semantic_text = f"{title} {semantic_names} {text}".casefold()
    capabilities = set(discover_page_capabilities(marks))
    media_evidence = capabilities.intersection({"media_controls"}) or any(
        term in semantic_text for term in ("video player", "audio player", "media player", "duration")
    )
    if media_evidence:
        return "media_surface"
    if "repository" in capabilities:
        return "repository"
    has_result_evidence = any(mark.role in {"link", "listitem"} for mark in marks) and (
        len(marks) >= 3 or len(text) > 80
    )
    if capabilities.intersection({"search"}) and has_result_evidence:
        return "search_results"
    if "constraints" in capabilities and has_result_evidence:
        return "interactive_results"
    if capabilities.intersection({"search"}):
        return "search"
    if "constraints" in capabilities:
        return "interactive_results"
    if len(text) > 80:
        return "content"
    # URL structure is only a low-confidence hint when semantic evidence is
    # sparse; it cannot override a contradictory rendered page shape.
    if any(segment in path for segment in ("/tree/", "/blob/", "/commit/", "/src/")):
        return "repository"
    if any(segment in path for segment in ("/watch", "/player")):
        return "media_surface"
    if discover_search_query(url) or any(term in path for term in ("/search", "/results", "/find")):
        return "search"
    return "page"


def _observation_signature(marks: List[Mark], text: str) -> str:
    return "|".join(f"{mark.role}:{mark.name}" for mark in marks) + "::" + text[:240]

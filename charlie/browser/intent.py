"""Deterministic phrase detection for the browser subsystem -- no LLM, matches core.py's
_detect_open_app style of fast-path (local/remote models both ignore prompted tool instructions
on latency-critical paths more often than a plain keyword check misses).
"""

import re
from dataclasses import dataclass
from typing import Optional

_OPEN_VERBS = ("play", "watch", "listen", "put on")
_OPEN_PHRASES = ("open it", "show me", "in my browser", "pull it up", "show it to me", "open that")
_BARE_FOLLOWUP_PHRASES = {"open it", "open that", "show me", "show me that", "show it", "pull it up", "play it"}
_FRESHNESS_KEYWORDS = ("price", "news", "latest", "today", "current", "now", "live", "score", "weather")

_SEARCH_QUERY_RE = re.compile(
    r"\b(?:search|find|look\s+up|browse|check)\s+(?:for\s+)?(?P<query>.+?)\s+on\s+(?P<site>[a-z0-9][\w.]*)\b",
    re.IGNORECASE,
)
_SITE_FIRST_SEARCH_RE = re.compile(
    r"\b(?:search|find|look\s+up|browse|check)\s+(?P<site>[a-z0-9][\w.]*(?:\s+india)?)\s+for\s+(?P<query>.+)$",
    re.IGNORECASE,
)
_OPEN_AND_SEARCH_RE = re.compile(
    r"\b(?:search|find|look\s+up|browse|check)\s+(?:for\s+)?(?P<query>.+)$",
    re.IGNORECASE,
)
_MEDIA_QUERY_RE = re.compile(
    r"\b(?:play|watch|listen\s+to|put\s+on)\s+(?P<query>.+?)\s+(?:on\s+)?(?P<site>youtube|spotify)\b",
    re.IGNORECASE,
)
_SEARCH_WORDS_RE = re.compile(r"\b(?:search|find|look\s+up|browse|check)\b", re.IGNORECASE)
_VALUE_RE = re.compile(
    r"(?:(?P<prefix>₹|\$|€|£|rs\.?|usd|inr|eur|gbp)\s*)?"
    r"(?P<number>\d[\d,]*(?:\.\d+)?)\s*(?P<unit>gb|tb|mb|percent|%)?",
    re.IGNORECASE,
)
_COUNT_RE = re.compile(r"\b(?:first|top)\s+(?P<count>\d+)\b", re.IGNORECASE)
_ATTRIBUTE_ALIASES = {
    "ram": "ram",
    "memory": "ram",
    "storage": "storage",
    "ssd": "storage",
    "hdd": "storage",
    "price": "price",
    "cost": "price",
    "brand": "brand",
    "rating": "rating",
    "category": "category",
}


@dataclass(frozen=True)
class BrowserIntent:
    """Normalized site task details shared by recipes and the tier cascade."""

    site: str
    query: str
    original: str
    operation: str = "search"
    attribute: Optional[str] = None
    operator: Optional[str] = None
    value: Optional[str] = None
    direction: Optional[str] = None
    result_count: Optional[int] = None


def parse_browser_intent(task: str, current_domain: str = "") -> BrowserIntent:
    """Normalize natural wording into one operation plus semantic slots."""
    original = task.strip()
    lowered = original.lower()
    site_match = re.search(r"\bon\s+(?P<site>https?://[^\s]+|[a-z0-9][\w.-]*)", lowered)
    site = site_match.group("site").strip(".,!? ") if site_match else current_domain.strip().lower()
    operation = "SEARCH" if _SEARCH_WORDS_RE.search(original) else "OPEN"
    if re.search(r"\bgo\s+back\b|\bback\b", lowered):
        operation = "BACK"
    elif "sort" in lowered or "order" in lowered:
        operation = "SORT"
    elif any(term in lowered for term in ("filter", "under", "below", "less than", "at most", "minimum", "maximum")):
        operation = "FILTER"
    elif "compare" in lowered:
        operation = "COMPARE"
    elif any(term in lowered for term in ("cheapest", "lowest priced", "first three", "most relevant result")):
        operation = "PRODUCT_SELECT"
    elif any(term in lowered for term in ("what is on", "what's on", "read this", "read the current", "how much")):
        operation = "CURRENT_PAGE_FACT"
    elif any(term in lowered for term in ("read", "summarize", "inspect")):
        operation = "READ"

    attribute = next(
        (
            canonical
            for alias, canonical in _ATTRIBUTE_ALIASES.items()
            if re.search(rf"\b{re.escape(alias)}\b", lowered)
        ),
        None,
    )
    operator = None
    if re.search(r"\b(?:under|below|less than|at most|maximum|max)\b", lowered):
        operator = "lte"
    elif re.search(r"\b(?:over|above|more than|at least|minimum|min)\b", lowered):
        operator = "gte"
    elif operation == "FILTER":
        operator = "eq"
    value_matches = list(_VALUE_RE.finditer(lowered))
    if attribute is None and operator in {"lte", "gte"}:
        attribute = "price"
    value_match = None
    if attribute in {"ram", "storage"}:
        value_match = next(
            (
                match
                for match in value_matches
                if (match.group("unit") or "").lower() in {"gb", "tb"} and not match.group("prefix")
            ),
            None,
        )
    elif attribute == "price":
        value_match = next(
            (match for match in value_matches if match.group("prefix") or not match.group("unit")),
            None,
        )
    if value_match is None and value_matches:
        value_match = value_matches[0]
    value = None
    if value_match:
        prefix = value_match.group("prefix") or ""
        number = value_match.group("number").replace(",", "")
        unit = (value_match.group("unit") or "").upper()
        value = " ".join(part for part in (prefix, number, unit) if part).strip()
    direction = None
    if re.search(r"\b(?:low|lowest|ascending|cheapest)\b", lowered):
        direction = "ascending"
    elif re.search(r"\b(?:high|highest|descending|expensive)\b", lowered):
        direction = "descending"
    count_match = _COUNT_RE.search(lowered)
    result_count = int(count_match.group("count")) if count_match else None
    query = re.sub(r"\s+", " ", original).strip(" .,!?")
    if operation == "SEARCH":
        query = re.sub(r"^(?:search|find|look\s+up|browse|check)\s+(?:for\s+)?", "", query, flags=re.IGNORECASE)
        if site:
            query = re.sub(rf"\s+on\s+{re.escape(site)}\s*$", "", query, flags=re.IGNORECASE)
            query = re.sub(
                rf"^(?:{re.escape(site)})(?:\s+for)?\s+",
                "",
                query,
                flags=re.IGNORECASE,
            )
    if operation in {"FILTER", "SORT", "BACK", "CURRENT_PAGE_FACT", "READ", "COMPARE", "PRODUCT_SELECT"}:
        query = ""
    return BrowserIntent(
        site=site,
        query=query,
        original=original,
        operation=operation,
        attribute=attribute,
        operator=operator,
        value=value,
        direction=direction,
        result_count=result_count,
    )


def parse_site_intent(task: str, site: str) -> Optional[BrowserIntent]:
    """Extract the user query from a site command without losing the original task."""
    original = task.strip()
    site_lower = site.lower().strip()
    search_match = _SEARCH_QUERY_RE.search(original)
    if search_match and search_match.group("site").lower().strip(".,!? ") == site_lower:
        query = search_match.group("query")
    else:
        site_first_match = _SITE_FIRST_SEARCH_RE.search(original)
        matched_site = site_first_match.group("site").lower().strip(".,!? ") if site_first_match else ""
        site_matches = matched_site == site_lower or (site_lower == "amazon" and matched_site == "amazon india")
        if site_first_match and site_matches:
            query = site_first_match.group("query")
        else:
            media_match = _MEDIA_QUERY_RE.search(original)
            if media_match and media_match.group("site").lower() == site_lower:
                query = media_match.group("query")
            else:
                open_search = _OPEN_AND_SEARCH_RE.search(original)
                query = (
                    open_search.group("query")
                    if site_lower in original.lower().split() and open_search
                    else original
                )
    query = re.sub(r"\s+", " ", query).strip(" .,!?\t\r\n")
    if not query:
        return None
    parsed = parse_browser_intent(original, site_lower)
    return BrowserIntent(
        site=site_lower,
        query=query,
        original=original,
        operation=parsed.operation,
        attribute=parsed.attribute,
        operator=parsed.operator,
        value=parsed.value,
        direction=parsed.direction,
        result_count=parsed.result_count,
    )


def has_open_intent(task: str) -> bool:
    """True when the request implies the result should open in the user's real browser."""
    lowered = f" {task.lower().strip()} "
    if any(f" {verb} " in lowered for verb in _OPEN_VERBS):
        return True
    return any(phrase in lowered for phrase in _OPEN_PHRASES)


def is_bare_followup(task: str) -> bool:
    """True for a standalone 'open it' / 'show me that' with no new query -- reuse last_url."""
    return task.lower().strip().rstrip(".!") in _BARE_FOLLOWUP_PHRASES


def is_freshness_sensitive(task: str) -> bool:
    """True when the task looks time-sensitive enough that a cached result would be wrong."""
    lowered = task.lower()
    return any(keyword in lowered for keyword in _FRESHNESS_KEYWORDS)


def is_search_intent(task: str) -> bool:
    """Return True for search/find requests that must land on a results page."""
    return bool(re.search(r"\b(?:search|find|look\s+up|browse|check)\b", task, re.IGNORECASE))

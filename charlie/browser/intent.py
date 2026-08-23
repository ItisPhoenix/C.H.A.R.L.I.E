"""Deterministic semantic intent parsing for browser tasks.

Parsing stays deliberately small and deterministic. It produces independent
constraints from user language; rendered-page evidence resolves those
constraints to labels and controls exposed by the active page.
"""

import re
from dataclasses import dataclass
from typing import Iterable, Optional

_OPEN_VERBS = ("play", "watch", "listen", "put on")
_OPEN_PHRASES = ("open it", "show me", "in my browser", "pull it up", "show it to me", "open that")
_BARE_FOLLOWUP_PHRASES = {"open it", "open that", "show me", "show me that", "show it", "pull it up", "play it"}
_FRESHNESS_KEYWORDS = ("price", "news", "latest", "today", "current", "now", "live", "score", "weather")
_CONNECTOR_WORDS = {
    "a", "an", "and", "at", "by", "for", "from", "in", "is", "of", "on", "or",
    "results", "the", "these", "this", "to", "with",
}
_OPERATOR_WORDS = {
    "under": "lte", "below": "lte", "less than": "lte", "at most": "lte", "maximum": "lte", "max": "lte",
    "over": "gte", "above": "gte", "more than": "gte", "at least": "gte", "minimum": "gte", "min": "gte",
    "longer than": "gt", "shorter than": "lt",
}
_OPERATOR_VALUE_WORDS = frozenset(_OPERATOR_WORDS)

_SEARCH_QUERY_RE = re.compile(
    r"\b(?:search|find|look\s+up|browse|check)\s+(?:for\s+)?(?P<query>.+?)\s+on\s+"
    r"(?P<site>https?://[^\s]+|[a-z0-9][\w.-]*)\b",
    re.IGNORECASE,
)
_SITE_FIRST_SEARCH_RE = re.compile(
    r"\b(?:search|find|look\s+up|browse|check)\s+(?P<site>[a-z0-9][\w.-]*(?:\s+[a-z0-9][\w.-]*)?)\s+for\s+(?P<query>.+)$",
    re.IGNORECASE,
)
_OPEN_AND_SEARCH_RE = re.compile(
    r"\b(?:search|find|look\s+up|browse|check)\s+(?:for\s+)?(?P<query>.+)$",
    re.IGNORECASE,
)
_MEDIA_QUERY_RE = re.compile(
    r"\b(?:play|watch|listen\s+to|put\s+on)\s+(?P<query>.+?)"
    r"(?:\s+on\s+(?P<site>https?://[^\s]+|[a-z0-9][\w.-]*(?:\s+[a-z0-9][\w.-]*)?))?$",
    re.IGNORECASE,
)
_MEDIA_CONTROL_RE = re.compile(
    r"\b(?:pause|play|resume|continue|skip|seek|forward|rewind|backward|mute|unmute)\b",
    re.IGNORECASE,
)
_SEARCH_WORDS_RE = re.compile(r"\b(?:search|find|look\s+up|browse|check)\b", re.IGNORECASE)
_VALUE_RE = re.compile(
    r"(?:(?P<prefix>₹|\$|€|£|rs\.?|usd|inr|eur|gbp)\s*)?"
    r"(?P<number>\d[\d,]*(?:\.\d+)?)\s*(?P<unit>gb|tb|mb|hours?|minutes?|seconds?|percent|%)?",
    re.IGNORECASE,
)
_OPERATOR_RE = re.compile(
    r"\b(?P<word>under|below|less\s+than|at\s+most|maximum|max|over|above|more\s+than|at\s+least|minimum|min|longer\s+than|shorter\s+than)\b",
    re.IGNORECASE,
)
_COUNT_RE = re.compile(r"\b(?:first|top)\s+(?P<count>\d+)\b", re.IGNORECASE)

# Hints only. Unknown attributes remain valid and are matched against current
# rendered page labels later.
_ATTRIBUTE_ALIASES = {
    "ram": "ram",
    "memory": "ram",
    "system memory": "ram",
    "storage": "storage",
    "ssd": "storage",
    "hdd": "storage",
    "price": "price",
    "cost": "price",
    "amount": "price",
    "brand": "brand",
    "rating": "rating",
    "score": "rating",
    "category": "category",
}


@dataclass(frozen=True)
class Constraint:
    """One independent semantic requirement on current rendered results."""

    attribute: str
    operator: str = "eq"
    value: str = ""
    unit: Optional[str] = None
    currency: Optional[str] = None


@dataclass(frozen=True)
class SortSpec:
    attribute: str = "relevance"
    direction: str = "ascending"


@dataclass(frozen=True)
class BrowserIntent:
    """Normalized browser task with generic independent constraints."""

    site: str
    query: str
    original: str
    operation: str = "SEARCH"
    target: Optional[str] = None
    context: Optional[str] = None
    constraints: tuple[Constraint, ...] = ()
    sort: Optional[SortSpec] = None
    result_count: Optional[int] = None
    # Compatibility projection for older callers. New code uses constraints/sort.
    attribute: Optional[str] = None
    operator: Optional[str] = None
    value: Optional[str] = None
    direction: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "constraints", tuple(self.constraints or ()))


def normalize_attribute(value: str) -> str:
    """Normalize user/page wording without requiring a production vocabulary."""
    text = re.sub(r"\s+", " ", value or "").strip(" .,:;!?-").casefold()
    text = re.sub(r"^(?:the|a|an)\s+", "", text)
    return _ATTRIBUTE_ALIASES.get(text, text)


def _site_key(value: str) -> str:
    text = value.strip(" .,!?/").casefold()
    text = re.sub(r"^https?://", "", text)
    text = text.removeprefix("www.")
    return text.rstrip("/")


def _site_matches(left: str, right: str) -> bool:
    left_key, right_key = _site_key(left), _site_key(right)
    if left_key == right_key:
        return True
    left_parts, right_parts = left_key.split(), right_key.split()
    if len(left_parts) == 1 and len(right_parts) > 1:
        return left_parts[0] == right_parts[0]
    if len(right_parts) == 1 and len(left_parts) > 1:
        return right_parts[0] == left_parts[0]
    return left_key.split(".", 1)[0] == right_key.split(".", 1)[0]


def _operator_from_word(value: str) -> str:
    return _OPERATOR_WORDS.get(re.sub(r"\s+", " ", value.casefold()), "eq")


def _format_value(match: re.Match[str]) -> tuple[str, Optional[str], Optional[str]]:
    prefix = (match.group("prefix") or "").strip()
    number = match.group("number").replace(",", "")
    unit = (match.group("unit") or "").upper() or None
    value = " ".join(part for part in (prefix, number, unit) if part).strip()
    currency = prefix.casefold() if prefix and not unit else None
    return value, unit, currency


def _clean_context(value: str) -> str:
    text = re.sub(
        r"\b(?:under|below|less\s+than|at\s+most|maximum|max|over|above|more\s+than|at\s+least|minimum|min|longer\s+than|shorter\s+than)\b",
        " ", value, flags=re.IGNORECASE,
    )
    text = re.sub(r"\b\d[\d,]*(?:\.\d+)?\s*(?:gb|tb|mb|percent|%)?\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"(?:₹|\$|€|£|rs\.?|usd|inr|eur|gbp)", " ", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip(" ,.;:-")


def _attribute_from_context(value: str, *, from_end: bool = False) -> Optional[str]:
    text = _clean_context(value)
    if not text:
        return None
    lowered = text.casefold()
    candidates: list[tuple[int, int, str]] = []
    for alias, canonical in _ATTRIBUTE_ALIASES.items():
        for match in re.finditer(rf"\b{re.escape(alias)}\b", lowered):
            candidates.append((match.start(), len(alias), canonical))
    if candidates:
        candidates.sort(key=lambda item: (item[0], item[1]))
        return candidates[-1][2] if from_end else candidates[0][2]
    words = [word for word in re.findall(r"[a-z][a-z0-9_-]*", lowered) if word not in _CONNECTOR_WORDS]
    if not words:
        return None
    return normalize_attribute(" ".join(words[-3:]))


def _constraint_from_match(match: re.Match[str], operator: str, before: str, after: str) -> Constraint:
    value, unit, currency = _format_value(match)
    before_attribute = _attribute_from_context(before, from_end=True)
    if currency:
        attribute = "price"
    elif unit and unit.casefold() in {"hours", "hour", "minutes", "minute", "seconds", "second"}:
        attribute = "duration"
    elif unit is None and before_attribute:
        attribute = before_attribute
    else:
        attribute = _attribute_from_context(after) or before_attribute or ("price" if unit is None else "value")
    return Constraint(normalize_attribute(attribute), operator, value, unit, currency)


def _parse_comparison_constraints(task: str) -> list[Constraint]:
    constraints: list[Constraint] = []
    for operator_match in _OPERATOR_RE.finditer(task):
        value_match = _VALUE_RE.search(task, operator_match.end(), operator_match.end() + 36)
        if value_match is None:
            continue
        before = task[max(0, operator_match.start() - 48) : operator_match.start()]
        after = task[value_match.end() : value_match.end() + 42]
        constraints.append(
            _constraint_from_match(value_match, _operator_from_word(operator_match.group("word")), before, after)
        )
    return constraints


def _parse_numeric_constraints(task: str, comparison_constraints: Iterable[Constraint]) -> list[Constraint]:
    constraints: list[Constraint] = []
    comparison_spans: list[tuple[int, int]] = []
    for operator_match in _OPERATOR_RE.finditer(task):
        value_match = _VALUE_RE.search(task, operator_match.end(), operator_match.end() + 36)
        if value_match is not None:
            comparison_spans.append(value_match.span())
    for value_match in _VALUE_RE.finditer(task):
        if any(start <= value_match.start() < end for start, end in comparison_spans):
            continue
        value, unit, currency = _format_value(value_match)
        before = task[max(0, value_match.start() - 42) : value_match.start()]
        after = task[value_match.end() : value_match.end() + 42]
        attribute = (
            "price"
            if currency
            else "duration"
            if unit and unit.casefold() in {"hours", "hour", "minutes", "minute", "seconds", "second"}
            else _attribute_from_context(after) or _attribute_from_context(before, from_end=True)
        )
        if attribute is None and unit is None:
            continue
        constraints.append(Constraint(normalize_attribute(attribute or "value"), "eq", value, unit, currency))
    return constraints


def _parse_textual_constraints(task: str) -> list[Constraint]:
    constraints: list[Constraint] = []
    for match in re.finditer(
        r"\b(?P<attribute>[a-z][a-z0-9 /_-]{1,36})\s*(?:\bis\b|=|:)\s*"
        r"(?P<value>[a-z0-9][a-z0-9 ._%+/-]{1,60})",
        task, re.IGNORECASE,
    ):
        attribute = normalize_attribute(match.group("attribute"))
        value = re.split(r"\s+(?:and|with|then)\s+", match.group("value"), flags=re.IGNORECASE)[0].strip(" .,;:")
        if attribute and value:
            constraints.append(Constraint(attribute, "eq", value))

    aliases = "|".join(re.escape(alias) for alias in sorted(_ATTRIBUTE_ALIASES, key=len, reverse=True))
    for match in re.finditer(
        rf"\b(?P<attribute>{aliases})\s+(?P<value>(?!{'|'.join(_OPERATOR_VALUE_WORDS)}\b|and\b|with\b|for\b)[a-z][a-z0-9._-]*)\b",
        task,
        re.IGNORECASE,
    ):
        constraints.append(Constraint(normalize_attribute(match.group("attribute")), "eq", match.group("value")))

    # Generic title-case label/value form, e.g. "Display Type OLED". This
    # does not enumerate future attributes; it only uses the user's wording.
    for match in re.finditer(
        r"\b(?P<attribute>[A-Z][A-Za-z0-9_-]*(?:\s+[A-Z][A-Za-z0-9_-]*){1,3})\s+"
        r"(?P<value>[A-Z][A-Za-z0-9_-]{1,24})\b",
        task,
    ):
        attribute = normalize_attribute(match.group("attribute"))
        first_word = attribute.casefold().split(maxsplit=1)[0]
        if first_word not in {"search", "find", "look", "browse", "check", "sort", "open"}:
            constraints.append(Constraint(attribute, "eq", match.group("value")))

    # Generic value + label form, e.g. "OLED display". It is limited to
    # filter context or an explicit value/label pair so search prose stays a
    # query rather than becoming a guessed constraint.
    if re.search(r"\b(?:filter|with|for|where)\b", task, re.IGNORECASE):
        for match in re.finditer(
            r"\b(?P<value>[A-Z][A-Za-z0-9_-]{1,24})\s+(?P<attribute>[a-z][a-z0-9_-]*(?:\s+[a-z][a-z0-9_-]*)?)\b",
            task,
        ):
            value = match.group("value")
            attribute = normalize_attribute(match.group("attribute"))
            first_attribute_word = attribute.casefold().split(maxsplit=1)[0]
            if (
                value.casefold() in _CONNECTOR_WORDS
                or first_attribute_word in _CONNECTOR_WORDS
                or attribute in {"these results", "this page"}
            ):
                continue
            if attribute not in {item.attribute for item in constraints}:
                constraints.append(Constraint(attribute, "eq", value))
    return constraints


def _dedupe_constraints(constraints: Iterable[Constraint]) -> tuple[Constraint, ...]:
    seen: set[tuple[str, str, str]] = set()
    result: list[Constraint] = []
    for constraint in constraints:
        key = (constraint.attribute, constraint.operator, constraint.value.casefold())
        if not constraint.attribute or not constraint.value or key in seen:
            continue
        seen.add(key)
        result.append(constraint)
    return tuple(result)


def _parse_sort(lowered: str) -> Optional[SortSpec]:
    if not re.search(r"\b(?:sort|order)\b", lowered):
        return None
    direction = "ascending" if re.search(r"\b(?:low|lowest|ascending|cheapest)\b", lowered) else "descending"
    by_match = re.search(r"\b(?:by|on)\s+(?P<attribute>[a-z][a-z0-9 /_-]{1,36})", lowered)
    attribute = normalize_attribute(by_match.group("attribute")) if by_match else "relevance"
    attribute = re.split(r"\s+(?:low|high|ascending|descending|to)\b", attribute, maxsplit=1)[0].strip()
    return SortSpec(attribute or "relevance", direction)


def parse_browser_intent(task: str, current_domain: str = "") -> BrowserIntent:
    """Normalize natural wording into generic operations and independent slots."""
    original = task.strip()
    lowered = original.casefold()
    site_match = re.search(
        r"\bon\s+(?P<site>https?://[^\s]+|[a-z0-9][\w.-]*(?:\s+[a-z0-9][\w.-]*)?)",
        original, re.IGNORECASE,
    )
    site = site_match.group("site").strip(".,!? ") if site_match else current_domain.strip().lower()
    site_first_match = _SITE_FIRST_SEARCH_RE.search(original)
    if site_first_match:
        site = site_first_match.group("site").strip(".,!? ")
    media_match = _MEDIA_QUERY_RE.search(original)
    if media_match and media_match.group("site"):
        site = media_match.group("site").strip(".,!? ")

    operation = "SEARCH" if _SEARCH_WORDS_RE.search(original) else "OPEN"
    if media_match or _MEDIA_CONTROL_RE.search(original):
        operation = "MEDIA"
    elif re.search(r"\bopen\b", lowered) and re.search(r"\b(?:video|audio|media)\b", lowered):
        operation = "MEDIA"
    if re.search(r"\bgo\s+back\b|\bback\b", lowered):
        operation = "BACK"
    elif _parse_sort(lowered):
        operation = "SORT"
    elif operation != "MEDIA" and re.search(
        r"\b(?:filter|under|below|less than|at most|minimum|maximum|at least|above|over)\b", lowered
    ):
        operation = "FILTER"
    elif "compare" in lowered:
        operation = "COMPARE"
    elif any(
        term in lowered
        for term in ("cheapest", "lowest priced", "first three", "most relevant result", "highest-rated")
    ):
        operation = "PRODUCT_SELECT"
    elif any(term in lowered for term in ("what is on", "what's on", "read this", "read the current", "how much")):
        operation = "CURRENT_PAGE_FACT"
    elif re.search(r"\bwhat\s+page\s+(?:am\s+i|are\s+we)\s+on\b|\bwhich\s+page\b", lowered):
        operation = "CURRENT_PAGE_FACT"
    elif any(term in lowered for term in ("read", "summarize", "inspect")):
        operation = "READ"

    comparisons = _parse_comparison_constraints(original)
    constraints = _dedupe_constraints(
        [*comparisons, *_parse_numeric_constraints(original, comparisons), *_parse_textual_constraints(original)]
    )
    if constraints and operation == "OPEN":
        operation = "FILTER"
    sort = _parse_sort(lowered)
    count_match = _COUNT_RE.search(lowered)
    result_count = int(count_match.group("count")) if count_match else None

    query = re.sub(r"\s+", " ", original).strip(" .,!? ")
    if operation in {"SEARCH", "MEDIA"}:
        query = re.sub(r"^(?:search|find|look\s+up|browse|check)\s+(?:for\s+)?", "", query, flags=re.IGNORECASE)
        if media_match:
            query = media_match.group("query").strip(" .,!? ")
        if site:
            query = re.sub(rf"\s+on\s+{re.escape(site)}\s*$", "", query, flags=re.IGNORECASE)
            query = re.sub(rf"^(?:{re.escape(site)})(?:\s+for)?\s+", "", query, flags=re.IGNORECASE)
    if operation in {"FILTER", "SORT", "BACK", "CURRENT_PAGE_FACT", "READ", "COMPARE", "PRODUCT_SELECT"}:
        query = ""

    primary = next((constraint for constraint in constraints if constraint.attribute != "price"), None)
    primary = primary or (constraints[0] if constraints else None)
    legacy_operator = next(
        (constraint.operator for constraint in constraints if constraint.attribute == "price"),
        primary.operator if primary else None,
    )
    return BrowserIntent(
        site=site,
        query=query,
        original=original,
        operation=operation,
        target=site or None,
        context="current_page" if current_domain else None,
        constraints=constraints,
        sort=sort,
        result_count=result_count,
        attribute=primary.attribute if primary else None,
        operator=legacy_operator,
        value=primary.value if primary else None,
        direction=sort.direction if sort else None,
    )


def parse_site_intent(task: str, site: str) -> Optional[BrowserIntent]:
    """Extract query from a site command using generic site-token matching."""
    original = task.strip()
    site_key = _site_key(site)
    search_match = _SEARCH_QUERY_RE.search(original)
    if search_match and _site_matches(search_match.group("site"), site_key):
        query = search_match.group("query")
    else:
        site_first_match = _SITE_FIRST_SEARCH_RE.search(original)
        if site_first_match and _site_matches(site_first_match.group("site"), site_key):
            query = site_first_match.group("query")
        else:
            media_match = _MEDIA_QUERY_RE.search(original)
            if media_match and media_match.group("site") and _site_matches(media_match.group("site"), site_key):
                query = media_match.group("query")
            else:
                open_search = _OPEN_AND_SEARCH_RE.search(original)
                query = open_search.group("query") if open_search and site_key in original.casefold() else original
    query = re.sub(r"\s+", " ", query).strip(" .,!?")
    if not query:
        return None
    parsed = parse_browser_intent(original, site)
    return BrowserIntent(
        site=site,
        query=query,
        original=original,
        operation=parsed.operation,
        target=parsed.target,
        context=parsed.context,
        constraints=parsed.constraints,
        sort=parsed.sort,
        result_count=parsed.result_count,
        attribute=parsed.attribute,
        operator=parsed.operator,
        value=parsed.value,
        direction=parsed.direction,
    )


def has_open_intent(task: str) -> bool:
    """True when request implies result should open in real browser."""
    lowered = f" {task.casefold().strip()} "
    if any(f" {verb} " in lowered for verb in _OPEN_VERBS):
        return True
    return any(phrase in lowered for phrase in _OPEN_PHRASES)


def is_media_control(task: str) -> bool:
    """True for media mutation wording, distinct from opening media content."""
    return bool(_MEDIA_CONTROL_RE.search(task))


def is_bare_followup(task: str) -> bool:
    return task.casefold().strip().rstrip(".!") in _BARE_FOLLOWUP_PHRASES


def is_freshness_sensitive(task: str) -> bool:
    lowered = task.casefold()
    return any(keyword in lowered for keyword in _FRESHNESS_KEYWORDS)


def is_search_intent(task: str) -> bool:
    return bool(_SEARCH_WORDS_RE.search(task))

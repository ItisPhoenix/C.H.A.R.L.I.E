"""Tier 1 (known-site recipes) and tier 2 (generic searchbox flow) -- no LLM involved.

Deterministic beats prompted tool calls on latency-critical paths; these run
before the agent loop (tier 3) ever gets a turn. Each function drives its own controller.run()
call, so callers invoke them directly without wrapping.
"""

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Iterable, List, Optional
from urllib.parse import quote, unquote, urljoin, urlparse

from charlie.browser import actions, controller, session
from charlie.browser.intent import _ATTRIBUTE_ALIASES, BrowserIntent, Constraint, normalize_attribute
from charlie.browser.observation import Mark, is_blocked, parse_snapshot, rank_and_cap
from charlie.known_apps import resolve_website_url

logger = logging.getLogger("charlie.browser")

_SPELLED_DURATION_PART_RE = re.compile(r"(\d+)\s*(hours?|minutes?|seconds?)", re.IGNORECASE)
# ponytail: denylist, not a real chrome/content classifier -- good enough for the generic fallback
_CHROME_LINK_NAMES = {"home", "ask", "sign up", "log in", "about", "help", "settings", "skip to main content"}
# Outer bound in case a page hangs somewhere actions.py's own goto/selector timeouts don't cover.
_RECIPE_TIMEOUT_S = 20.0


@dataclass
class BrowserResult:
    url: Optional[str] = None
    answer: Optional[str] = None
    success: bool = False
    verification: str = "unverified"
    site: Optional[str] = None
    query: Optional[str] = None


def _normalize_semantic_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip().casefold()


def _control_evidence(control: Any) -> str:
    values: list[str] = []
    for attribute in ("aria-label", "placeholder", "name", "title", "value"):
        try:
            value = control.get_attribute(attribute)
        except Exception:
            value = None
        if value:
            values.append(str(value))
    try:
        values.append(str(control.inner_text(timeout=500)))
    except Exception:
        pass
    return " ".join(_normalize_semantic_text(value) for value in values if value).strip()


def _visible_role_controls(page: Any, roles: Iterable[str], limit: int = 250) -> list[Any]:
    controls: list[Any] = []
    for role in roles:
        try:
            locator = page.get_by_role(role)
            count = min(int(locator.count()), limit)
        except Exception:
            continue
        for index in range(count):
            try:
                control = locator.nth(index)
                if control.is_visible():
                    controls.append(control)
            except Exception:
                continue
    return controls


def semantic_control_match(
    page: Any,
    labels: Iterable[str],
    roles: Iterable[str] = ("button", "link", "checkbox", "radio", "option", "combobox", "textbox", "searchbox"),
) -> Optional[Any]:
    """Select live controls by accessible/visible evidence, never by DOM position."""
    wanted = [_normalize_semantic_text(label) for label in labels if label]
    if not wanted:
        return None
    candidates = _visible_role_controls(page, roles)
    ranked: list[tuple[int, Any]] = []
    for control in candidates:
        evidence = _control_evidence(control)
        if not evidence:
            continue
        score = 0
        for label in wanted:
            if evidence == label:
                score += 100
            elif label in evidence:
                score += 30
            else:
                label_words = set(re.findall(r"[a-z0-9]+", label))
                evidence_words = set(re.findall(r"[a-z0-9]+", evidence))
                score += 5 * len(label_words & evidence_words)
        if score:
            ranked.append((score, control))
    return max(ranked, key=lambda item: item[0])[1] if ranked else None


def discover_search_control(page: Any) -> Optional[Any]:
    """Find current page search input from role and accessible metadata."""
    ranked: list[tuple[int, Any]] = []
    semantic_words = re.compile(r"\b(?:search|find|query)\b", re.IGNORECASE)
    for role in ("searchbox", "textbox", "combobox"):
        for control in _visible_role_controls(page, (role,)):
            score = 120 if role == "searchbox" else 0
            evidence = _control_evidence(control)
            if semantic_words.search(evidence):
                score += 80
            try:
                context = control.evaluate(
                    """element => {
                        const form = element.closest('form, [role="search"]');
                        const labels = element.labels ? [...element.labels].map(label => label.innerText) : [];
                        const submitRoot = form || element.parentElement;
                        const submits = submitRoot
                            ? [...submitRoot.querySelectorAll('button, input[type="submit"]')]
                                .map(item => item.getAttribute('aria-label') || item.value || item.innerText || '')
                            : [];
                        return {
                            type: element.getAttribute('type') || '',
                            labels: labels.join(' '),
                            form: form
                            ? [form.getAttribute('role'), form.getAttribute('aria-label'),
                                form.getAttribute('name'), form.id]
                                    .filter(Boolean).join(' ')
                                : '',
                            submit: submits.join(' '),
                        };
                    }"""
                ) or {}
            except Exception:
                context = {}
            if str(context.get("type", "")).casefold() == "search":
                score += 100
            if semantic_words.search(" ".join(str(context.get(key, "")) for key in ("labels", "form", "submit"))):
                score += 60
            if score > 0:
                ranked.append((score, control))
    return max(ranked, key=lambda item: item[0])[1] if ranked else None


def submit_search(page: Any, query: str) -> bool:
    """Fill and submit a discovered search control, then invalidate stale marks."""
    control = discover_search_control(page)
    if control is None:
        return False
    try:
        control.fill(query, timeout=2500)
        control.press("Enter")
        session.invalidate_observation()
        session.record_action(f"search current page for {query}")
        return True
    except Exception:
        return False


def extract_result_evidence(link: Any, page: Any = None) -> dict[str, Any]:
    """Extract current link evidence without assuming card ancestry or URL shape."""
    values: list[str] = []
    try:
        values.append(str(link.inner_text(timeout=700)))
    except Exception:
        pass
    for attribute in ("aria-label", "title"):
        try:
            value = link.get_attribute(attribute)
        except Exception:
            value = None
        if value:
            values.append(str(value))
    text = " ".join(" ".join(value.split()) for value in values if value).strip()
    href = ""
    try:
        href = str(link.get_attribute("href") or "")
    except Exception:
        pass
    absolute = urljoin(page.url, href) if page is not None and href else href
    return {
        "title": " ".join(text.split())[:240],
        "text": text,
        "href": href,
        "url": absolute,
        "price": parse_price(text),
        "attributes": extract_attributes(text),
        "facts": extract_rendered_facts(text),
        "duration": _duration_text_to_seconds(text),
    }


def parse_price(text: str) -> Optional[int]:
    """Parse displayed price evidence across currencies; currency is not site state."""
    match = re.search(
        r"(?:₹|\$|€|£|\b(?:rs\.?|inr|usd|eur|gbp)\b)\s*"
        r"([\d][\d,]*(?:\.\d{1,2})?)",
        text or "",
        re.IGNORECASE,
    )
    if not match:
        return None
    try:
        return int(float(match.group(1).replace(",", "")))
    except ValueError:
        return None


def extract_attributes(text: str) -> dict[str, str]:
    """Compatibility mapping over generic rendered facts."""
    return {fact["normalized_key"]: fact["value"] for fact in extract_rendered_facts(text)}


def extract_rendered_facts(text: str) -> list[dict[str, str]]:
    """Extract label/value facts from the current rendered text.

    Labels come from the page itself. The parser only gives price and numeric
    values scalar treatment; it does not maintain a product-attribute list.
    """
    facts: list[dict[str, str]] = []
    chunks = [chunk.strip(" \t\r\n|•·;") for chunk in re.split(r"[\r\n|•·;]+", text or "")]
    value_pattern = r"(?:₹|\$|€|£)?\s*\d[\d,.]*(?:\s*(?:GB|TB|MB|Hz|inch(?:es)?|%|percent))?"

    def add_fact(raw_label: str, value: str, evidence: str) -> None:
        label = re.sub(r"\s+", " ", raw_label).strip(" .,:;-")
        clean_value = re.sub(r"\s+", " ", value).strip(" .,:;-")
        if not label or not clean_value:
            return
        for alias in sorted(_ATTRIBUTE_ALIASES, key=len, reverse=True):
            if re.match(rf"{re.escape(alias)}\b", label, re.IGNORECASE):
                label = alias
                break
        normalized_key = normalize_attribute(label)
        if not normalized_key or normalized_key in {"and", "with", "for", "the"}:
            return
        fact = {
            "normalized_key": normalized_key,
            "value": clean_value,
            "raw_label": label,
            "evidence": evidence.strip(),
        }
        if not any(
            existing["normalized_key"] == normalized_key and existing["value"].casefold() == clean_value.casefold()
            for existing in facts
        ):
            facts.append(fact)

    for chunk in chunks:
        if not chunk or len(chunk) > 220:
            continue
        label_value = re.match(
            r"^(?P<label>[A-Za-z][A-Za-z0-9 /_()&+.-]{1,60}?)\s*(?::|=)\s*(?P<value>.+)$",
            chunk,
        )
        if label_value:
            add_fact(label_value.group("label"), label_value.group("value"), chunk)
            continue

        for numeric_value in re.finditer(
            rf"(?<![A-Za-z0-9])(?P<value>{value_pattern})\s+(?P<label>[A-Za-z][A-Za-z0-9 /_()&+.-]{{1,30}}?)"
            rf"(?=(?:\s*(?:₹|\$|€|£|,)|\s+\d)|$)",
            chunk,
            re.IGNORECASE,
        ):
            add_fact(numeric_value.group("label"), numeric_value.group("value"), chunk)

        for title_case in re.finditer(
            r"\b(?P<label>[A-Z][A-Za-z0-9_-]*\s+[A-Z][A-Za-z0-9_-]*)\s+"
            r"(?P<value>[A-Z][A-Za-z0-9_-]*)(?=\s+(?:₹|\$|€|£)\s*\d|$)",
            chunk,
        ):
            if title_case.group("label").casefold().split(maxsplit=1)[0] not in {
                "search",
                "find",
                "sort",
                "open",
            }:
                add_fact(title_case.group("label"), title_case.group("value"), chunk)

        value_label = re.match(
            r"^(?P<value>[A-Z][A-Za-z0-9_-]{1,30})\s+(?P<label>[a-z][A-Za-z0-9 /_()&+.-]{1,50})$",
            chunk,
        )
        if value_label:
            add_fact(value_label.group("label"), value_label.group("value"), chunk)

    return facts


def discover_results(page: Any, limit: int = 40, *, require_price: bool = False) -> list[dict[str, Any]]:
    """Discover repeated result links from live accessible evidence."""
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for link in _visible_role_controls(page, ("link",), limit * 4):
        evidence = extract_result_evidence(link, page)
        if not evidence["url"] or evidence["url"] == page.url or len(evidence["text"]) < 12:
            continue
        if require_price and (evidence["price"] is None or len(evidence["text"]) < 20):
            continue
        if evidence["url"] in seen:
            continue
        seen.add(evidence["url"])
        results.append(evidence)
        if len(results) >= limit:
            break
    return results


def rank_results(results: list[dict[str, Any]], query: str = "") -> list[dict[str, Any]]:
    words = {word for word in re.findall(r"[a-z0-9]+", query.casefold()) if len(word) > 2}
    return sorted(
        results,
        key=lambda item: len(words & set(re.findall(r"[a-z0-9]+", str(item.get("title", "")).casefold()))),
        reverse=True,
    )


def discover_filter_controls(page: Any) -> list[Any]:
    """Discover live choice controls; requested attributes score them later.

    No product vocabulary is used here. A fieldset/group label, accessible
    name, option text, or nearby rendered value is the evidence source.
    """
    return _visible_role_controls(
        page,
        ("checkbox", "radio", "option", "combobox", "button", "link", "menuitem", "listbox"),
    )


def discover_sort_controls(page: Any) -> list[Any]:
    return _visible_role_controls(page, ("option", "combobox", "button", "link", "menuitem", "radio"))


def _constraint_list(constraints: Any) -> list[Constraint]:
    if isinstance(constraints, Mapping):
        normalized: list[Constraint] = []
        for attribute, expected in constraints.items():
            if isinstance(expected, Mapping):
                normalized.append(
                    Constraint(
                        str(attribute),
                        str(expected.get("operator", "eq")),
                        str(expected.get("value", "")),
                    )
                )
            else:
                normalized.append(Constraint(str(attribute), "eq", str(expected)))
        return normalized
    return [item for item in constraints or [] if isinstance(item, Constraint)]


def _same_attribute(requested: str, actual: str) -> bool:
    requested_words = set(re.findall(r"[a-z0-9]+", normalize_attribute(requested)))
    actual_words = set(re.findall(r"[a-z0-9]+", normalize_attribute(actual)))
    return bool(
        requested_words
        and actual_words
        and (requested_words <= actual_words or actual_words <= requested_words)
    )


def _fact_value(item: dict[str, Any], attribute: str) -> Optional[str]:
    for key, value in (item.get("attributes") or {}).items():
        if _same_attribute(attribute, str(key)):
            return str(value)
    for fact in item.get("facts") or []:
        if _same_attribute(attribute, str(fact.get("normalized_key", ""))):
            return str(fact.get("value", ""))
    return None


def _constraint_matches(item: dict[str, Any], constraint: Constraint) -> bool:
    if normalize_attribute(constraint.attribute) == "price":
        actual = item.get("price")
        expected = _numeric_slot(constraint.value)
        if actual is None or expected is None:
            return False
        if constraint.operator == "lte":
            return actual <= expected
        if constraint.operator == "gte":
            return actual >= expected
        if constraint.operator == "lt":
            return actual < expected
        if constraint.operator == "gt":
            return actual > expected
        return actual == expected
    actual = _fact_value(item, constraint.attribute)
    if actual is None:
        return False
    expected_number = _numeric_slot(constraint.value)
    actual_number = _numeric_slot(actual)
    if expected_number is not None and actual_number is not None:
        if constraint.operator == "lte":
            return actual_number <= expected_number
        if constraint.operator == "gte":
            return actual_number >= expected_number
        if constraint.operator == "lt":
            return actual_number < expected_number
        if constraint.operator == "gt":
            return actual_number > expected_number
    return constraint.value.casefold().strip() in actual.casefold()


def verify_constraints(results: list[dict[str, Any]], constraints: Any, sample_size: int = 3) -> tuple[bool, str]:
    sample = results[:sample_size]
    if not sample:
        return False, "No current rendered results were available."
    for constraint in _constraint_list(constraints):
        for item in sample:
            if not _constraint_matches(item, constraint):
                return (
                    False,
                    f"Current result did not verify {constraint.attribute} "
                    f"{constraint.operator} {constraint.value}.",
                )
    return True, ""


def extract_structured_facts(page: Any) -> dict[str, Any]:
    """Read current heading/body facts without assuming a product URL or card shape."""
    body = ""
    try:
        body = page.locator("body").inner_text(timeout=2500)
    except Exception:
        pass
    title = ""
    try:
        headings = page.get_by_role("heading", level=1)
        for index in range(min(int(headings.count()), 5)):
            heading = headings.nth(index)
            if heading.is_visible():
                title = " ".join(heading.inner_text(timeout=700).split())
                if title:
                    break
    except Exception:
        pass
    if not title:
        try:
            title = str(page.title()).strip()
        except Exception:
            pass
    return {
        "title": title,
        "price": parse_price(body),
        "attributes": extract_attributes(body),
        "facts": extract_rendered_facts(body),
        "text": body[:50000],
    }


def refresh_live_url(page: Any) -> str:
    """Read URL from active page and invalidate state when navigation changed."""
    current_url = str(page.url)
    if session.get_session().current_url != current_url:
        session.record_navigation(current_url)
    return current_url


def _numeric_slot(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    match = re.search(r"\d[\d,]*(?:\.\d+)?", value)
    if not match:
        return None
    try:
        return int(float(match.group(0).replace(",", "")))
    except ValueError:
        return None


def _selected_option_text(control: Any) -> str:
    """Read a select's active option without assuming a site-specific selector."""
    try:
        selected = control.locator("option:checked")
        if selected.count():
            return " ".join(selected.first().inner_text(timeout=500).split())
    except Exception:
        pass
    return ""


def _price_control_kind(control: Any) -> Optional[str]:
    """Classify a price control from rendered evidence, not its DOM position."""
    evidence = f"{_control_evidence(control)} {_selected_option_text(control)}".casefold()
    if re.search(r"\b(min|minimum|from|starting)\b", evidence):
        return "min"
    if re.search(r"\b(max|maximum|to|up to)\b", evidence) or "+" in evidence:
        return "max"
    return None


def _click_live_control(control: Any, description: str) -> bool:
    try:
        control.click(timeout=2500)
        session.invalidate_observation()
        session.record_action(description)
        return True
    except Exception:
        return False


def apply_constraint(
    page: Any,
    constraint: Constraint | str,
    operator: Optional[str] = None,
    value: Optional[str] = None,
) -> bool:
    """Apply one currently rendered semantic constraint and invalidate stale evidence."""
    if isinstance(constraint, Constraint):
        requested = constraint
    else:
        requested = Constraint(str(constraint), operator or "eq", value or "")
    attribute = normalize_attribute(requested.attribute)
    operator = requested.operator
    value = requested.value
    if not attribute or not value:
        return False
    numeric_value = _numeric_slot(value)
    if attribute == "price" and numeric_value is not None and operator in {"lte", "gte"}:
        comboboxes = _visible_role_controls(page, ("combobox",))
        preferred_kind = "max" if operator == "lte" else "min"
        classified = [(control, _price_control_kind(control)) for control in comboboxes]
        preferred = [control for control, kind in classified if kind == preferred_kind]
        if len(preferred) == 1:
            candidate_controls = preferred
        else:
            # Unknown or duplicate range roles cannot be selected safely by DOM order.
            candidate_controls = []
        for control in candidate_controls:
            try:
                options = control.get_by_role("option")
                choices: list[tuple[int, str]] = []
                for index in range(min(int(options.count()), 100)):
                    label = " ".join(options.nth(index).inner_text(timeout=400).split())
                    candidate = parse_price(label) or _numeric_slot(label)
                    if candidate is not None and "min" not in label.casefold() and "+" not in label:
                        choices.append((candidate, label))
                eligible = (
                    [choice for choice in choices if choice[0] <= numeric_value]
                    if operator == "lte"
                    else [choice for choice in choices if choice[0] >= numeric_value]
                )
                if not eligible:
                    continue
                target = (
                    max(eligible, key=lambda choice: choice[0])
                    if operator == "lte"
                    else min(eligible, key=lambda choice: choice[0])
                )
                control.select_option(label=target[1])
                session.invalidate_observation()
                session.record_action(f"set semantic {attribute} {operator} {value}")
                return True
            except Exception:
                continue
        return False

    expected = _normalize_semantic_text(value)
    controls = discover_filter_controls(page)
    ranked: list[tuple[int, Any]] = []
    for control in controls:
        evidence = _control_evidence(control)
        score = 0
        if expected and expected in evidence:
            score += 100
        if numeric_value is not None and str(numeric_value) in evidence:
            score += 30
        attribute_words = set(re.findall(r"[a-z0-9]+", attribute))
        evidence_words = set(re.findall(r"[a-z0-9]+", evidence))
        score += 20 * len(attribute_words & evidence_words)
        if operator in {"lte", "gte"}:
            operator_words = {
                "lte": ("under", "below", "maximum", "max", "to"),
                "gte": ("over", "above", "minimum", "min", "from"),
            }[operator]
            if any(word in evidence for word in operator_words):
                score += 10
        if score:
            ranked.append((score, control))
    if not ranked:
        section = semantic_control_match(
            page,
            (attribute, f"filter {attribute}"),
            ("button", "link", "checkbox", "radio", "option", "menuitem", "combobox", "listbox"),
        )
        if section is not None:
            if not _click_live_control(section, f"open semantic {attribute} filter"):
                return False
            controls = discover_filter_controls(page)
            for control in controls:
                evidence = _control_evidence(control)
                control_words = set(re.findall(r"[a-z0-9]+", evidence))
                attribute_words = set(re.findall(r"[a-z0-9]+", attribute))
                if expected in evidence or (numeric_value is not None and str(numeric_value) in evidence):
                    ranked.append((80 + 10 * len(attribute_words & control_words), control))
    if not ranked:
        return False
    return _click_live_control(max(ranked, key=lambda item: item[0])[1], f"set semantic {attribute} {operator} {value}")


def apply_sort(page: Any, attribute: str, direction: str) -> bool:
    """Select a rendered sort control using semantic labels and current page evidence."""
    direction_word = "low to high" if direction == "ascending" else "high to low"
    labels = (f"{attribute} {direction_word}", direction_word, direction, "sort", "order")
    target = semantic_control_match(
        page,
        labels,
        ("button", "link", "option", "menuitem", "combobox", "radio"),
    )
    if target is None:
        return False
    return _click_live_control(target, f"sort {attribute} {direction}")


def _record_result_observation(page: Any, results: list[dict[str, Any]], page_type: str = "search_results") -> None:
    session.record_observation(page.url, page_type=page_type, results=results)


def apply_current_page_intent(task: str, parsed: BrowserIntent) -> Optional[BrowserResult]:
    """Run generic current-page operations before any site adapter or agent fallback."""
    supported = {"BACK", "FILTER", "SORT", "READ", "CURRENT_PAGE_FACT", "COMPARE", "PRODUCT_SELECT"}
    if parsed.operation not in supported:
        return None

    def run(page):
        current_url = refresh_live_url(page)
        domain = session.get_session().current_domain or urlparse(current_url).hostname or None
        if parsed.operation == "BACK":
            before = current_url
            actions.back(page)
            if page.url == before:
                return BrowserResult(
                    url=page.url,
                    answer="The browser did not expose a previous page.",
                    verification="back-unverified",
                    site=domain,
                )
            return BrowserResult(
                url=page.url,
                answer=f"Returned to {page.url}.",
                success=True,
                verification="back",
                site=domain,
            )

        if parsed.operation in {"READ", "CURRENT_PAGE_FACT"}:
            facts = extract_structured_facts(page)
            if not facts.get("title") and len(facts.get("text", "")) < 80:
                return None
            _record_result_observation(page, [], page_type=session.get_session().page_type or "page")
            answer = facts.get("title") or "Current page"
            if facts.get("price") is not None:
                answer += f" — price {facts['price']}"
            return BrowserResult(
                url=page.url,
                answer=f"{answer}: {facts.get('text', '')[:500]}",
                success=True,
                verification="current-page-facts",
                site=domain,
            )

        results = discover_results(page, limit=max(parsed.result_count or 10, 10))
        if parsed.operation == "COMPARE":
            if len(results) < 2:
                return None
            return BrowserResult(
                url=page.url,
                answer="; ".join(f"{item['title']} ({item['url']})" for item in results[:2]),
                success=True,
                verification="result-comparison",
                site=domain,
            )

        if parsed.operation == "FILTER":
            constraints = list(parsed.constraints)
            if not constraints and parsed.attribute and parsed.value:
                constraints = [Constraint(parsed.attribute, parsed.operator or "eq", parsed.value)]
            if not constraints:
                return None

            applied: list[Constraint] = []
            reapplied = False
            fresh: list[dict[str, Any]] = []
            detail = ""
            for constraint in constraints:
                if not apply_constraint(page, constraint):
                    return BrowserResult(
                        url=page.url,
                        answer=f"Could not find a live control for {constraint.attribute}={constraint.value}.",
                        verification="constraint-unverified",
                        site=domain,
                    )
                applied.append(constraint)
                fresh = discover_results(page, limit=max(parsed.result_count or 10, 10))
                verified, detail = verify_constraints(fresh, applied)
                if not verified and not reapplied:
                    # A dynamic page may replace a prior control after the next
                    # filter. Re-observe and semantically reapply once, then stop.
                    reapplied = True
                    for prior in applied:
                        if not apply_constraint(page, prior):
                            break
                    fresh = discover_results(page, limit=max(parsed.result_count or 10, 10))
                    verified, detail = verify_constraints(fresh, applied)
                if not verified:
                    return BrowserResult(
                        url=page.url,
                        answer=detail,
                        verification="constraint-unverified",
                        site=domain,
                    )

            for constraint in applied:
                expected: Any = constraint.value
                if constraint.attribute == "price":
                    expected = {"operator": constraint.operator, "value": _numeric_slot(constraint.value)}
                session.set_constraint(constraint.attribute, expected)
            _record_result_observation(page, fresh)
            summary = ", ".join(f"{item.attribute} {item.operator} {item.value}" for item in applied)
            return BrowserResult(
                url=page.url,
                answer=f"Applied {summary}.",
                success=True,
                verification="constraints",
                site=domain,
            )

        if parsed.operation == "SORT":
            sort_attribute = parsed.sort.attribute if parsed.sort else (parsed.attribute or "price")
            sort_direction = parsed.sort.direction if parsed.sort else (parsed.direction or "ascending")
            if not apply_sort(page, sort_attribute, sort_direction):
                return None
            fresh = discover_results(page, limit=max(parsed.result_count or 10, 10))
            values: list[Any] = []
            for item in fresh:
                if normalize_attribute(sort_attribute) == "price":
                    candidate = item.get("price")
                else:
                    candidate = _fact_value(item, sort_attribute)
                    candidate = _numeric_slot(candidate) if candidate is not None else None
                if candidate is not None:
                    values.append(candidate)
            ordered = values == sorted(values, reverse=sort_direction == "descending") if values else False
            if not ordered:
                return BrowserResult(
                    url=page.url,
                    answer="The current rendered results did not verify the requested sort.",
                    verification="sort-unverified",
                    site=domain,
                )
            session.get_session().sort_state = {
                "attribute": sort_attribute,
                "direction": sort_direction,
            }
            _record_result_observation(page, fresh)
            return BrowserResult(
                url=page.url,
                answer=f"Sorted {sort_attribute} {sort_direction}.",
                success=True,
                verification="sort",
                site=domain,
            )

        if parsed.operation == "PRODUCT_SELECT":
            if not results:
                return None
            selected = (
                min(results, key=lambda item: item["price"])
                if "cheap" in task.casefold() and any(item.get("price") for item in results)
                else rank_results(results, task)[0]
            )
            actions.navigate(page, selected["url"])
            facts = extract_structured_facts(page)
            if not facts.get("title") or page.url == current_url:
                return BrowserResult(
                    url=page.url,
                    answer="The selected rendered result did not verify after opening.",
                    verification="result-open-unverified",
                    site=domain,
                )
            session.record_observation(page.url, page_type="page", selected_result=selected)
            return BrowserResult(
                url=page.url,
                answer=f"Opened {facts['title']}.",
                success=True,
                verification="result-opened",
                site=domain,
            )
        return None

    try:
        return controller.run(run, timeout=_RECIPE_TIMEOUT_S)
    except Exception:
        logger.debug("Generic current-page operation failed for %r", task, exc_info=True)
        return None


def resolve_site(name: str) -> Optional[str]:
    """Resolve an alias hint or arbitrary valid web target."""
    return resolve_website_url(name)


def _observe(page) -> List[Mark]:
    marks = rank_and_cap(parse_snapshot(page.locator("body").aria_snapshot(mode="ai")))
    session.record_marks(marks)
    return marks


def _link_count(page) -> int:
    try:
        return page.locator("a[href]").count()
    except Exception:
        return 0


def _wait_for_search_results(page, before_url: str, before_links: int) -> bool:
    """Wait for navigation or a changed main-content link set instead of sleeping."""
    try:
        page.wait_for_function(
            """state => {
                const root = document.querySelector('main, [role="main"], article') || document.body;
                return location.href !== state.url || root.querySelectorAll('a[href]').length > state.links;
            }""",
            {"url": before_url, "links": before_links},
            timeout=5000,
        )
        return True
    except Exception:
        logger.debug("site_search: result condition did not settle before timeout")
        return False


def _content_root(page, preferred_selector: Optional[str] = None):
    """Prefer semantic content containers so navigation chrome is not reported as a result."""
    selectors = ([preferred_selector] if preferred_selector else []) + [
                "main",
                '[role="main"]',
                "article",
            ]
    for selector in selectors:
        locator = page.locator(selector)
        if locator.count():
            return locator.first
    return None


def _content_text(page, preferred_selector: Optional[str] = None) -> str:
    root = _content_root(page, preferred_selector)
    if root is None:
        return ""
    try:
        return root.inner_text(timeout=1500).strip()
    except Exception:
        return ""


def _content_links(page, preferred_selector: Optional[str] = None) -> List[str]:
    root = _content_root(page, preferred_selector)
    if root is None:
        return []
    links = []
    locator = root.locator("a[href]")
    for index in range(min(locator.count(), 12)):
        try:
            name = locator.nth(index).inner_text(timeout=500).strip()
        except Exception:
            continue
        if name and name.lower() not in _CHROME_LINK_NAMES:
            links.append(name)
    return links


def _spelled_duration_to_seconds(text: str) -> Optional[int]:
    parts = _SPELLED_DURATION_PART_RE.findall(text)
    if not parts:
        return None
    total = 0
    for value, unit in parts:
        multiplier = {"hour": 3600, "hours": 3600, "minute": 60, "minutes": 60, "second": 1, "seconds": 1}[unit.lower()]
        total += int(value) * multiplier
    return total


def _duration_text_to_seconds(text: str) -> Optional[int]:
    """Parse rendered MM:SS/HH:MM:SS or spelled media duration text."""
    compact_match = re.search(r"(?<!\d)(\d{1,2}:\d{2}(?::\d{2})?)(?!\d)", text or "")
    compact = compact_match.group(1) if compact_match else text.strip()
    if re.fullmatch(r"\d{1,2}:\d{2}:\d{2}", compact) or re.fullmatch(r"\d{1,3}:\d{2}", compact):
        parts = [int(part) for part in compact.split(":")]
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return _spelled_duration_to_seconds(text)


def media_player_command(task: str) -> Optional[str]:
    """Recognize the small generic media command vocabulary from user wording."""
    lowered = task.casefold()
    if re.search(r"\bunmute\b", lowered):
        return "unmute"
    if re.search(r"\bmute\b", lowered):
        return "mute"
    if re.search(r"\b(?:pause|stop)\b", lowered):
        return "pause"
    if re.search(r"\b(?:play|resume|continue)\b", lowered):
        return "play"
    if re.search(r"\b(?:skip|seek|forward|rewind|backward)\b", lowered):
        return "seek"
    if re.search(r"\bvolume\b", lowered):
        return "volume"
    return None


def _media_command_value(task: str, command: str) -> Optional[float]:
    match = re.search(r"\b(\d+(?:\.\d+)?)\s*(seconds?|secs?|s|percent|%)?\b", task, re.IGNORECASE)
    if command == "seek":
        amount = float(match.group(1)) if match else 10.0
        return -amount if re.search(r"\b(?:rewind|backward|back)\b", task, re.IGNORECASE) else amount
    if command == "volume" and match:
        return float(match.group(1)) / 100.0
    return None


def media_control_current(task: str) -> Optional[BrowserResult]:
    """Control an active HTML media element and verify its resulting state."""
    command = media_player_command(task)
    if command is None:
        return None

    def run(page):
        result = actions.media_player_action(page, command, _media_command_value(task, command))
        before = result["before"]
        if not before.get("media"):
            return BrowserResult(
                url=page.url,
                answer="The current page does not expose one unambiguous active media element.",
                verification=result["reason"],
            )
        if not result["verified"]:
            return BrowserResult(
                url=page.url,
                answer="The browser-native media action did not satisfy its rendered postcondition.",
                verification="media-action-unverified",
            )
        return BrowserResult(
            url=page.url,
            answer=f"Media {command.replace('_', ' ')} verified.",
            success=True,
            verification=f"media-{command}",
        )

    try:
        return controller.run(run, timeout=_RECIPE_TIMEOUT_S)
    except Exception:
        logger.debug("Generic media control failed for %r", task, exc_info=True)
        return None


def _duration_constraint(constraints: Iterable[Constraint]) -> Optional[Constraint]:
    return next((item for item in constraints if normalize_attribute(item.attribute) == "duration"), None)


def _duration_constraint_seconds(constraint: Constraint) -> Optional[float]:
    spelled = _spelled_duration_to_seconds(constraint.value)
    if spelled is not None:
        return float(spelled)
    value = _numeric_slot(constraint.value)
    if value is None:
        return None
    unit = (constraint.unit or "seconds").casefold()
    multiplier = 3600 if unit.startswith("hour") else 60 if unit.startswith("minute") else 1
    return float(value * multiplier)


def _duration_matches(duration: Optional[float], constraint: Optional[Constraint]) -> bool:
    if constraint is None:
        return True
    expected = _duration_constraint_seconds(constraint)
    if duration is None or expected is None:
        return False
    return {
        "gt": duration > expected,
        "gte": duration >= expected,
        "lt": duration < expected,
        "lte": duration <= expected,
        "eq": abs(duration - expected) <= 1,
    }.get(constraint.operator, False)


def _open_verified_media_result(
    page: Any, query: str, constraints: Iterable[Constraint] = ()
) -> BrowserResult:
    duration_constraint = _duration_constraint(constraints)
    candidates = media_result_candidates(page, query, constraints=constraints)
    if not candidates:
        return BrowserResult(
            url=page.url,
            answer="Current rendered results exposed no media satisfying the requested constraints.",
            verification="media-duration-unverified" if duration_constraint else "media-result-unverified",
            query=query,
        )
    selected = candidates[0]
    before = page.url
    actions.navigate(page, selected["url"])
    state = actions.media_player_state(page)
    if not state.get("media") or not _duration_matches(state.get("duration"), duration_constraint):
        return BrowserResult(
            url=page.url,
            answer="The selected result opened, but its active media constraints were not verified.",
            verification="media-duration-unverified" if duration_constraint else "media-open-unverified",
            query=query,
        )
    try:
        title = str(page.title()).strip()
    except Exception:
        title = ""
    title = title or selected.get("title", "").strip()
    if page.url == before or not title:
        return BrowserResult(
            url=page.url,
            answer="The selected rendered media result did not verify after opening.",
            verification="media-open-unverified",
            query=query,
        )
    session.record_observation(page.url, page_type="media_surface", capabilities=["media_controls"])
    return BrowserResult(
        url=page.url,
        answer=title,
        success=True,
        verification="media-opened",
        query=query,
    )


def media_request(site_url: str, query: str, parsed: Optional[BrowserIntent] = None) -> Optional[BrowserResult]:
    """Search a current web target and open a verified rendered media result.

    The flow is site-agnostic: accessible search, rendered result links,
    visible duration evidence, navigation, then HTMLMediaElement verification.
    """
    active = session.get_session().current_url or session.get_session().last_url
    if active:
        try:
            current = controller.run(lambda page: page.url, timeout=5.0)
            if current and urlparse(current).netloc == urlparse(active).netloc:
                current_result = media_control_current(parsed.original if parsed else query)
                if current_result is not None:
                    return current_result
                if parsed and parsed.operation == "MEDIA":
                    return controller.run(
                        lambda page: _open_verified_media_result(page, query, parsed.constraints),
                        timeout=_RECIPE_TIMEOUT_S,
                    )
        except Exception:
            logger.debug("Could not inspect active media surface", exc_info=True)

    searched = site_search(site_url, query, None)
    if searched is None or not searched.success:
        return searched

    try:
        return controller.run(
            lambda page: _open_verified_media_result(page, query, parsed.constraints if parsed else ()),
            timeout=max(_RECIPE_TIMEOUT_S, 30.0),
        )
    except Exception:
        logger.debug("Generic media result selection failed for %r", query, exc_info=True)
        return BrowserResult(
            url=searched.url,
            answer=searched.answer,
            verification="media-open-unverified",
            query=query,
        )


def media_result_candidates(
    page: Any, query: str = "", constraints: Iterable[Constraint] = ()
) -> list[dict[str, Any]]:
    """Rank rendered media links by query relevance and verified duration."""
    words = {word for word in re.findall(r"[a-z0-9]+", query.casefold()) if len(word) > 2}
    duration_constraint = _duration_constraint(constraints)
    candidates = []
    for item in discover_results(page, limit=80):
        duration = item.get("duration")
        if not _duration_matches(duration, duration_constraint):
            continue
        relevance = len(words & set(re.findall(r"[a-z0-9]+", item.get("title", "").casefold())))
        item = dict(item)
        item["relevance"] = relevance
        candidates.append(item)
    return sorted(candidates, key=lambda item: (item["relevance"], item.get("duration") or 0), reverse=True)


def site_search(site_url: str, query: str, site_name: Optional[str] = None) -> Optional[BrowserResult]:
    """Search the live page semantically, with one stable protocol fallback."""

    resolved_site_url = resolve_website_url(site_url)
    if not resolved_site_url:
        return None

    def wikipedia_fallback(page) -> BrowserResult:
        search_url = f"https://en.wikipedia.org/w/index.php?search={quote(query)}"
        actions.navigate(page, search_url)
        content = _content_text(page)
        try:
            title = page.title().strip()
        except Exception:
            title = "Wikipedia"
        query_words = [word.lower() for word in query.split() if len(word) > 2]
        lowered_content = f"{title}\n{content}".lower()
        if len(content) >= 80 and query_words and all(word in lowered_content for word in query_words):
            return BrowserResult(
                url=page.url,
                answer=f"{title}: {content[:500]}",
                success=True,
                verification="wikipedia-content",
                site=site_name,
                query=query,
            )
        return BrowserResult(
            url=page.url,
            answer=f"I reached Wikipedia, but couldn't verify an article for '{query}'.",
            verification="wikipedia-content-not-found",
            site=site_name,
            query=query,
        )

    def run(page):
        is_wikipedia = bool(site_name and site_name.lower() == "wikipedia") or "wikipedia.org" in resolved_site_url
        actions.navigate(page, resolved_site_url)
        marks = _observe(page)
        before_url = page.url
        before_links = _link_count(page)
        submitted = submit_search(page, query)
        if not submitted:
            # A combobox may be a select and reject fill(). Text-like marks win;
            # combobox is the bounded compatibility fallback.
            candidates = [m for m in marks if m.role in ("textbox", "searchbox")]
            candidates += [m for m in marks if m.role == "combobox"]
            for search_mark in candidates:
                try:
                    actions.type_text(page, search_mark.mark_id, query, submit=True)
                    submitted = True
                    break
                except Exception:
                    logger.debug("site_search: mark %d not fillable, trying next candidate", search_mark.mark_id)
        if not submitted:
            return wikipedia_fallback(page) if is_wikipedia else None
        try:
            page.wait_for_load_state("domcontentloaded", timeout=8000)
        except Exception:
            logger.debug("site_search: no navigation detected after submit on %s", resolved_site_url)
        settled = _wait_for_search_results(page, before_url, before_links)
        if not settled:
            if is_wikipedia:
                return wikipedia_fallback(page)
            rendered = _content_text(page)
            try:
                rendered = f"{rendered}\n{page.content()[:12000]}"
            except Exception:
                pass
            if is_blocked(marks, rendered):
                return BrowserResult(
                    url=page.url,
                    answer=(
                        f"SITE_STATE_BLOCKED: {site_name or resolved_site_url} displayed an external "
                        "verification or access challenge, so search results could not be verified."
                    ),
                    verification="site-state-blocked",
                    site=site_name,
                    query=query,
                )
            return BrowserResult(
                url=page.url,
                answer=f"I reached {site_name or resolved_site_url}, but couldn't verify search results for '{query}'.",
                verification="search-not-settled",
                site=site_name,
                query=query,
            )
        refresh_live_url(page)
        generic_results = discover_results(page, limit=20)
        links = _content_links(page)[:5]
        if not links:
            links = [item["title"] for item in rank_results(generic_results, query)[:5]]
        if not links:
            return BrowserResult(
                url=page.url,
                answer=f"I reached {site_name or resolved_site_url}, but couldn't verify search results for '{query}'.",
                verification="content-not-found",
                site=site_name,
                query=query,
            )
        content = _content_text(page)
        if len(content) < 40:
            if is_wikipedia:
                return wikipedia_fallback(page)
            return BrowserResult(
                url=page.url,
                answer=f"I reached {site_name or resolved_site_url}, but couldn't verify search results for '{query}'.",
                verification="content-too-short",
                site=site_name,
                query=query,
            )
        return BrowserResult(
            url=page.url,
            answer="; ".join(links)[:400],
            success=True,
            verification="content-links",
            site=site_name,
            query=query,
        )

    try:
        return controller.run(run, timeout=_RECIPE_TIMEOUT_S)
    except Exception:
        logger.warning("Tier 2 site_search failed for %s %r", resolved_site_url, query, exc_info=True)
        return None


_GITHUB_REPO_URL_RE = re.compile(r"^https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/#?]+)(?:/|$)", re.IGNORECASE)
_REPOSITORY_SYMBOL_RE = re.compile(r"\b([A-Z][A-Za-z0-9_]{2,}|[a-z][A-Za-z0-9_]*_[A-Za-z0-9_]+)\b")
_REPOSITORY_QUERY_RE = re.compile(
    r"\b(?:search|find|look\s+up)\b(?:\s+where)?(?:\s+is)?\s+(?:this\s+repository\s+for\s+)?"
    r"(?P<target>[A-Za-z_][A-Za-z0-9_.-]*)",
    re.IGNORECASE,
)
_GITHUB_AUTH_WALL_PHRASES = (
    "sign in to search code",
    "you must be signed in",
    "sign in to view",
    "authentication required",
)


def github_repository_context(current_url: Optional[str]) -> Optional[tuple[str, str]]:
    """Extract arbitrary owner/repository from the active GitHub URL."""
    if not current_url:
        return None
    match = _GITHUB_REPO_URL_RE.match(current_url)
    if not match:
        return None
    return match.group("owner"), match.group("repo").removesuffix(".git")


def _github_ref_from_url(current_url: Optional[str]) -> Optional[str]:
    if not current_url:
        return None
    parsed = urlparse(current_url)
    segments = [unquote(segment) for segment in parsed.path.split("/") if segment]
    if len(segments) < 4:
        return None
    if segments[2].lower() not in {"tree", "blob", "find", "commits"}:
        return None
    # An encoded slash is an unambiguous ref. Unencoded extra segments may be
    # branch path or file path; rendered branch controls resolve that case.
    if len(segments) == 4 or "/" in segments[3]:
        return segments[3] or None
    return None


def repository_search_query(task: str) -> Optional[str]:
    """Extract a symbol/search term without sending conversational wording to GitHub."""
    for match in _REPOSITORY_SYMBOL_RE.finditer(task):
        target = match.group(1).strip(".,?!:;()[]{}")
        if target.lower() not in {"find", "search", "open", "read", "this", "repository"}:
            return target
    match = _REPOSITORY_QUERY_RE.search(task)
    return match.group("target").strip(".,?!:;()[]{}") if match else None


def _github_auth_wall(text: str, url: str) -> bool:
    lowered = text.lower()
    return "/login" in url.lower() or any(phrase in lowered for phrase in _GITHUB_AUTH_WALL_PHRASES)


def _github_normalize_path(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _github_links(page, limit: int = 300) -> list[tuple[str, str]]:
    try:
        return [
            (item.get("href", ""), item.get("name", "").strip())
            for item in page.locator("a[href]").evaluate_all(
                "(els, limit) => els.slice(0, limit).map(e => ({href: e.getAttribute('href') || '', "
                "name: e.innerText || ''}))",
                limit,
            )
        ]
    except Exception:
        return []


def github_repository_ref(page, current_url: Optional[str] = None) -> Optional[str]:
    """Discover the active repository ref from the current URL or rendered links."""
    ref = _github_ref_from_url(current_url)
    if ref:
        return ref
    context = github_repository_context(current_url or getattr(page, "url", ""))
    if not context:
        return None
    owner, repo = context
    root_prefix = f"/{owner}/{repo}/tree/".lower()
    control_candidates: list[tuple[int, str]] = []
    link_candidates: list[tuple[int, str]] = []
    for role in ("button", "link", "combobox"):
        try:
            controls = page.get_by_role(role)
            for index in range(min(int(controls.count()), 100)):
                control = controls.nth(index)
                if not control.is_visible():
                    continue
                evidence = _control_evidence(control)
                if re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+", evidence):
                    control_candidates.append((len(evidence.split("/")), evidence))
        except Exception:
            continue
    for href, name in _github_links(page):
        parsed = urlparse(urljoin(getattr(page, "url", ""), href))
        path = unquote(parsed.path)
        marker = path.lower().find(root_prefix)
        if marker < 0:
            continue
        tail = path[marker + len(root_prefix) :].strip("/")
        if not tail:
            continue
        if tail and (not name.strip() or name.strip().casefold() == tail.rsplit("/", 1)[-1].casefold()):
            link_candidates.append((tail.count("/") + 1, tail))
    if control_candidates:
        control_candidates.sort(key=lambda item: item[0], reverse=True)
        return control_candidates[0][1]
    if not link_candidates:
        return None
    link_candidates.sort(key=lambda item: item[0])
    return link_candidates[0][1]


def _github_tree_file(page, owner: str, repo: str, query: str, ref: str) -> Optional[tuple[str, str]]:
    """Find a filename match by browsing repository tree pages, without API/search calls."""
    target = _github_normalize_path(query)
    github_url = resolve_site("github")
    if not github_url:
        return None
    repository_url = f"{github_url.rstrip('/')}/{quote(owner)}/{quote(repo)}"
    pending = [(f"{repository_url}/tree/{quote(ref, safe='')}", 0)]
    seen = set()
    while pending and len(seen) < 10:
        tree_url, depth = pending.pop(0)
        if tree_url in seen:
            continue
        seen.add(tree_url)
        actions.navigate(page, tree_url)
        for href, name in _github_links(page):
            if f"/{owner}/{repo}/blob/" in href and target in _github_normalize_path(f"{name} {href}"):
                return urljoin(page.url, href), name
            if depth < 2 and f"/{owner}/{repo}/tree/" in href and href not in seen:
                relative = href.split(f"/{owner}/{repo}/tree/", 1)[-1]
                if not relative.startswith("."):
                    pending.append((urljoin(page.url, href), depth + 1))
    return None


def current_repository_search(task: str, current_url: Optional[str]) -> Optional[BrowserResult]:
    """Tier 1: search the active GitHub repository through its real browser page."""
    context = github_repository_context(current_url)
    query = repository_search_query(task)
    lowered = task.lower()
    if not context or not query or "github" not in (current_url or "").lower():
        return None
    if not any(cue in lowered for cue in ("repository", "repo", "implemented", "file", "result")):
        return None
    owner, repo = context

    def run(page):
        github_url = resolve_site("github")
        if not github_url:
            return BrowserResult(
                answer="GitHub is not configured as a valid web target.",
                verification="github-site-unavailable",
                site="github",
                query=query,
            )
        ref = github_repository_ref(page, current_url)
        repository_url = f"{github_url.rstrip('/')}/{quote(owner)}/{quote(repo)}"
        if not ref:
            actions.navigate(page, repository_url)
            ref = github_repository_ref(page, page.url)
        if not ref:
            return BrowserResult(
                url=page.url,
                answer=f"I couldn't discover the active branch or tag for {owner}/{repo}; I did not assume one.",
                verification="github-ref-unavailable",
                site="github",
                query=query,
            )
        # GitHub's anonymous repository finder is browser-native and avoids code-search auth APIs.
        search_url = f"{repository_url}/find/{quote(ref, safe='')}?pattern={quote(query)}"
        actions.navigate(page, search_url)
        try:
            page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass
        body = page.locator("body").inner_text(timeout=2000).strip()
        if _github_auth_wall(body, page.url):
            return BrowserResult(
                url=page.url,
                answer=f"GitHub requires sign-in to search {owner}/{repo} for {query}.",
                verification="github-auth-required",
                site="github",
                query=query,
            )

        query_lower = query.lower()
        candidates = []
        for href, name in _github_links(page, 120):
            if not href or f"/{owner}/{repo}/" not in href:
                continue
            if query_lower in f"{name} {href}".lower():
                candidates.append((href, name))
        if not candidates:
            tree_match = _github_tree_file(page, owner, repo, query, ref)
            if tree_match:
                result_url, name = tree_match
                actions.navigate(page, result_url)
                result_body = page.locator("body").inner_text(timeout=2000).strip()
                verified = query_lower in result_body.lower() or query_lower in result_url.lower()
                if verified:
                    return BrowserResult(
                        url=page.url,
                        answer=f"Verified GitHub file for {query}: {name or page.url} ({page.url}).",
                        success=True,
                        verification="github-file-result",
                        site="github",
                        query=query,
                    )
            lowered_body = body.lower()
            if query_lower in lowered_body and ".py" in lowered_body:
                return BrowserResult(
                    url=page.url,
                    answer=f"GitHub shows {query} in {owner}/{repo}, but no verifiable result link was exposed.",
                    verification="github-result-unlinked",
                    site="github",
                    query=query,
                )
            return BrowserResult(
                url=page.url,
                answer=(
                    f"GitHub's anonymous browser UI exposed no verified code result for {query} "
                    f"in {owner}/{repo}; I did not claim success."
                ),
                verification="github-search-unavailable",
                site="github",
                query=query,
            )

        href, name = candidates[0]
        result_url = urljoin(page.url, href)
        actions.navigate(page, result_url)
        result_body = page.locator("body").inner_text(timeout=2000).strip()
        verified = query_lower in result_body.lower() or query_lower in result_url.lower()
        if not verified:
            return BrowserResult(
                url=page.url,
                answer=f"GitHub result link for {query} did not verify after opening.",
                verification="github-result-unverified",
                site="github",
                query=query,
            )
        return BrowserResult(
            url=page.url,
            answer=f"Verified GitHub result for {query}: {name or page.url} ({page.url}).",
            success=True,
            verification="github-result",
            site="github",
            query=query,
        )

    try:
        return controller.run(run, timeout=_RECIPE_TIMEOUT_S)
    except Exception:
        logger.warning("GitHub repository search recipe failed for %s %r", current_url, query, exc_info=True)
        return None

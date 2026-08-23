"""Tier 1 (known-site recipes) and tier 2 (generic searchbox flow) -- no LLM involved.

Deterministic beats prompted tool calls on latency-critical paths; these run
before the agent loop (tier 3) ever gets a turn. Each function drives its own controller.run()
call, so callers invoke them directly without wrapping.
"""

import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Iterable, List, Optional
from urllib.parse import parse_qs, quote, unquote, unquote_plus, urljoin, urlparse

from charlie.browser import actions, controller, session
from charlie.browser.intent import BrowserIntent
from charlie.browser.observation import Mark, parse_snapshot, rank_and_cap
from charlie.known_apps import resolve_website_url

logger = logging.getLogger("charlie.browser")

_MIN_DURATION_S = 60
_SHORTS_MARKER = "/shorts/"
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
    controls = _visible_role_controls(page, ("searchbox", "textbox", "combobox"))
    if not controls:
        return None
    scored = []
    for control in controls:
        evidence = _control_evidence(control)
        scored.append((100 if "search" in evidence or "find" in evidence else 0, control))
    return max(scored, key=lambda item: item[0])[1]


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
    """Extract common user-facing attribute evidence without knowing a site schema."""
    attributes: dict[str, str] = {}
    ram = re.search(
        r"\b(?:ram|memory|system memory)\s*[:\-]?\s*(\d{1,3})\s*gb\b|"
        r"\b(\d{1,3})\s*gb\s*(?:ddr\d*|lpddr\d*x?)?\s*ram\b",
        text or "",
        re.IGNORECASE,
    )
    if ram:
        attributes["ram"] = f"{next(group for group in ram.groups() if group)} GB"
    storage = re.search(r"\b(\d+(?:\.\d+)?)\s*(gb|tb)\s*(?:ssd|hdd|emmc|storage)\b", text or "", re.IGNORECASE)
    if storage:
        attributes["storage"] = f"{storage.group(1)} {storage.group(2).upper()}"
    return attributes


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
    controls = _visible_role_controls(page, ("checkbox", "radio", "option", "combobox", "button", "link"))
    terms = ("filter", "price", "ram", "memory", "storage", "brand", "rating")
    return [control for control in controls if any(term in _control_evidence(control) for term in terms)]


def discover_sort_controls(page: Any) -> list[Any]:
    controls = _visible_role_controls(page, ("option", "combobox", "button", "link", "menuitem"))
    terms = ("sort", "ascending", "descending", "low to high", "high to low")
    return [control for control in controls if any(term in _control_evidence(control) for term in terms)]


def verify_constraints(
    results: list[dict[str, Any]], constraints: dict[str, Any], sample_size: int = 3
) -> tuple[bool, str]:
    sample = results[:sample_size]
    if not sample:
        return False, "No current rendered results were available."
    for attribute, expected in constraints.items():
        for item in sample:
            if attribute == "price":
                actual = item.get("price")
                if actual is None:
                    return False, "Current result price evidence is missing."
                if isinstance(expected, dict) and expected.get("operator") in {"lte", "gte"}:
                    limit = expected.get("value")
                    if not isinstance(limit, (int, float)):
                        return False, "Requested price limit was not parsed."
                    if expected.get("operator") == "lte" and actual > limit:
                        return False, f"Current result exceeds maximum price {limit}."
                    if expected.get("operator") == "gte" and actual < limit:
                        return False, f"Current result is below minimum price {limit}."
            else:
                actual = item.get("attributes", {}).get(attribute)
                if actual is None or str(expected).casefold() not in str(actual).casefold():
                    return False, f"Current result did not verify {attribute}={expected}."
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
    return {"title": title, "price": parse_price(body), "attributes": extract_attributes(body), "text": body[:50000]}


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


def apply_constraint(page: Any, attribute: str, operator: str, value: str) -> bool:
    """Apply one currently rendered semantic constraint and invalidate stale evidence."""
    if not attribute or not value:
        return False
    numeric_value = _numeric_slot(value)
    if attribute == "price" and numeric_value is not None and operator in {"lte", "gte"}:
        comboboxes = _visible_role_controls(page, ("combobox",))
        preferred_kind = "max" if operator == "lte" else "min"
        classified = [(control, _price_control_kind(control)) for control in comboboxes]
        preferred = [control for control, kind in classified if kind == preferred_kind]
        unknown = [control for control, kind in classified if kind is None]
        if preferred:
            candidate_controls = preferred
        elif len(comboboxes) == 1:
            candidate_controls = comboboxes
        else:
            # Multiple unlabelled price controls cannot be selected safely by order.
            candidate_controls = unknown
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
        if attribute in evidence:
            score += 15
        if score:
            ranked.append((score, control))
    if not ranked:
        section = semantic_control_match(
            page,
            (attribute, f"filter {attribute}"),
            ("button", "link", "checkbox", "radio", "option", "menuitem"),
        )
        if section is not None:
            _click_live_control(section, f"open semantic {attribute} filter")
            controls = discover_filter_controls(page)
            for control in controls:
                evidence = _control_evidence(control)
                if expected in evidence or (numeric_value is not None and str(numeric_value) in evidence):
                    ranked.append((80, control))
    if not ranked:
        return False
    return _click_live_control(max(ranked, key=lambda item: item[0])[1], f"set semantic {attribute} {operator} {value}")


def apply_sort(page: Any, attribute: str, direction: str) -> bool:
    """Select a rendered sort control using semantic labels and current page evidence."""
    direction_word = "low to high" if direction == "ascending" else "high to low"
    labels = (f"{attribute} {direction_word}", direction_word, direction, "sort", "order")
    controls = discover_sort_controls(page)
    target = semantic_control_match(
        page,
        labels,
        ("button", "link", "option", "menuitem", "combobox", "radio"),
    )
    if target is None and controls:
        target = controls[0]
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
            if not apply_constraint(page, parsed.attribute or "", parsed.operator or "eq", parsed.value or ""):
                return None
            fresh = discover_results(page, limit=max(parsed.result_count or 10, 10))
            expected: Any = parsed.value or ""
            if parsed.attribute == "price":
                expected = {"operator": parsed.operator, "value": _numeric_slot(parsed.value)}
            verified, detail = verify_constraints(fresh, {parsed.attribute or "": expected})
            if not verified:
                return BrowserResult(url=page.url, answer=detail, verification="constraint-unverified", site=domain)
            session.set_constraint(parsed.attribute or "", expected)
            _record_result_observation(page, fresh)
            return BrowserResult(
                url=page.url,
                answer=f"Applied {parsed.attribute} constraint {parsed.value}.",
                success=True,
                verification="constraint",
                site=domain,
            )

        if parsed.operation == "SORT":
            if not apply_sort(page, parsed.attribute or "price", parsed.direction or "ascending"):
                return None
            fresh = discover_results(page, limit=max(parsed.result_count or 10, 10))
            prices = [item.get("price") for item in fresh if item.get("price") is not None]
            ordered = prices == sorted(prices, reverse=parsed.direction == "descending") if prices else False
            if not ordered:
                return BrowserResult(
                    url=page.url,
                    answer="The current rendered results did not verify the requested sort.",
                    verification="sort-unverified",
                    site=domain,
                )
            session.get_session().sort_state = {
                "attribute": parsed.attribute or "price",
                "direction": parsed.direction or "ascending",
            }
            _record_result_observation(page, fresh)
            return BrowserResult(
                url=page.url,
                answer=f"Sorted {parsed.attribute or 'results'} {parsed.direction or 'ascending'}.",
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


def _youtube_result_links(page) -> List[str]:
    """Read visible normal-video result titles from semantic links first."""
    try:
        generic = discover_results(page, limit=20)
        names = [
            item["title"]
            for item in generic
            if "/watch" in item["url"]
            and _SHORTS_MARKER not in item["url"].lower()
            and item["title"].lower() not in {"watch", "more"}
            and "now playing" not in item["title"].lower()
            and len(item["title"].split()) >= 2
        ]
        if names:
            return names
    except Exception:
        pass
    # Compatibility fallback for older YouTube snapshots with no exposed role links.
    links = page.locator('[role="main"] a[href*="/watch?v="]')
    names = []
    for index in range(min(links.count(), 12)):
        try:
            name = links.nth(index).inner_text(timeout=500).strip()
        except Exception:
            continue
        if (
            name
            and name.lower() not in {"watch", "more"}
            and "now playing" not in name.lower()
            and _SHORTS_MARKER not in name.lower()
            and len(name.split()) >= 2
        ):
            names.append(name)
    return names


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
    """Parse rendered YouTube MM:SS/HH:MM:SS or spelled duration text."""
    compact = text.strip()
    if re.fullmatch(r"\d{1,2}:\d{2}:\d{2}", compact) or re.fullmatch(r"\d{1,3}:\d{2}", compact):
        parts = [int(part) for part in compact.split(":")]
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return _spelled_duration_to_seconds(text)


def _youtube_visible_candidates(page, query: str) -> list[dict]:
    """Group visible result title/duration evidence by video id."""
    query_words = {word.lower() for word in query.split() if len(word) > 2}
    grouped: dict[str, dict] = {}
    links = page.locator('a[href*="/watch?v="]')
    for index in range(min(links.count(), 120)):
        link = links.nth(index)
        try:
            if not link.is_visible():
                continue
            href = link.get_attribute("href") or ""
            parsed = urlparse(href)
            video_id = parse_qs(parsed.query).get("v", [""])[0]
            if not video_id or "/shorts/" in href or "list" in parse_qs(parsed.query):
                continue
            raw_text = link.inner_text(timeout=500).strip()
            values = [raw_text]
            for attribute in ("aria-label", "title"):
                value = link.get_attribute(attribute)
                if value:
                    values.append(value)
            text = " ".join(" ".join(value.split()) for value in values if value)
            try:
                own_visible_text = link.evaluate("node => node.innerText || ''")
            except Exception:
                own_visible_text = ""
            card_text = " ".join(value for value in (text, own_visible_text) if value)
        except Exception:
            continue
        item = grouped.setdefault(video_id, {"href": href, "title": "", "duration": None, "card_text": card_text})
        duration = _duration_text_to_seconds(raw_text.split("\n", 1)[0])
        if duration is not None:
            item["duration"] = max(item["duration"] or 0, duration)
        elif text and "now playing" not in text.lower() and len(text.split()) >= 2:
            item["title"] = item["title"] or text
        if card_text:
            item["card_text"] = card_text
    candidates = []
    for item in grouped.values():
        title = item["title"].strip()
        if not title or re.search(r"\bshorts?\b", title.lower()) or "sponsored" in item["card_text"].lower():
            continue
        words = set(re.findall(r"[a-z0-9]+", title.lower()))
        relevance = len(query_words & words)
        item["relevance"] = relevance
        candidates.append(item)
    return sorted(
        candidates,
        key=lambda item: (item["relevance"], item["duration"] is not None, item["duration"] or 0),
        reverse=True,
    )


def youtube_play(query: str) -> Optional[BrowserResult]:
    """Tier 1: a real page, used when tier 0's HTTP parse (fastpath.youtube_play) found nothing."""

    def run(page):
        youtube_url = resolve_site("youtube")
        if not youtube_url:
            return None
        actions.navigate(page, f"{youtube_url.rstrip('/')}/results?search_query={quote(query)}")
        marks = _observe(page)
        generic_candidates = discover_results(page, limit=80)
        query_words = {w.lower() for w in query.split() if len(w) > 2}
        for item in generic_candidates:
            if "/watch" not in item["url"] or _SHORTS_MARKER in item["url"].lower():
                continue
            if query_words & set(re.findall(r"[a-z0-9]+", item["title"].lower())):
                return BrowserResult(
                    url=item["url"],
                    success=True,
                    verification="youtube-watch-url",
                    site="youtube",
                    query=query,
                )
        candidates = [
            m for m in marks if m.role == "link" and m.href and "/watch?v=" in m.href and _SHORTS_MARKER not in m.href
        ]

        def long_enough(mark: Mark) -> bool:
            seconds = _spelled_duration_to_seconds(mark.name)
            return seconds is None or seconds >= _MIN_DURATION_S

        for mark in candidates:
            if long_enough(mark) and query_words & set(mark.name.lower().split()):
                return BrowserResult(
                    url=urljoin(page.url, mark.href),
                    success=True,
                    verification="youtube-watch-url",
                    site="youtube",
                    query=query,
                )
        for mark in candidates:
            if long_enough(mark):
                return BrowserResult(
                    url=urljoin(page.url, mark.href),
                    success=True,
                    verification="youtube-watch-url",
                    site="youtube",
                    query=query,
                )
        return None

    try:
        return controller.run(run, timeout=_RECIPE_TIMEOUT_S)
    except Exception:
        logger.warning("Tier 1 youtube_play recipe failed for %r", query, exc_info=True)
        return None


def youtube_open_current(task: str) -> Optional[BrowserResult]:
    """Open and verify a normal long video from the visible current results page."""

    def run(page):
        current_url = page.url
        query = parse_qs(urlparse(current_url).query).get("search_query", [""])[0]
        candidates = _youtube_visible_candidates(page, query)
        if not candidates:
            return BrowserResult(
                answer="I couldn't find a visible relevant normal YouTube video.",
                verification="youtube-open-candidate-not-found",
                site="youtube",
                query=query,
            )
        relevant = [item for item in candidates if item["relevance"] > 0] or candidates
        long_videos = [item for item in relevant if (item["duration"] or 0) >= 300]
        selected = (long_videos or relevant)[0]
        actions.navigate(page, urljoin(page.url, selected["href"]))
        session.record_action(f'open YouTube result "{selected["title"]}"')
        try:
            page.wait_for_url("**/watch?v=**", timeout=8000)
        except Exception:
            return BrowserResult(
                url=page.url,
                answer="I selected a YouTube result but couldn't verify the watch page.",
                verification="youtube-watch-page-not-found",
                site="youtube",
                query=query,
            )
        try:
            page.wait_for_selector("h1", timeout=10000)
        except Exception:
            logger.debug("YouTube watch heading did not settle")
        try:
            page.wait_for_function(
                "() => document.title && document.title.toLowerCase() !== 'youtube'",
                timeout=5000,
            )
        except Exception:
            logger.debug("YouTube document title did not settle")
        duration_verified = True
        try:
            page.wait_for_function(
                "() => { const v = document.querySelector('video'); return v && "
                "(Number.isNaN(v.duration) || v.duration >= 300); }",
                timeout=10000,
            )
        except Exception:
            state = actions.youtube_player_state(page)
            if not state.get("adActive"):
                return BrowserResult(
                    url=page.url,
                    answer=(
                        "I reached a YouTube watch page, but the active media is not verified as longer than 5 minutes."
                    ),
                    verification="youtube-watch-duration-not-found",
                    site="youtube",
                    query=query,
                )
            duration_verified = False
        title = ""
        try:
            title = page.locator("h1").first.inner_text(timeout=1500).strip()
        except Exception:
            title = ""
        if not title:
            try:
                title = page.title().removesuffix(" - YouTube").strip()
            except Exception:
                title = ""
        if not title or title.lower() == "youtube" or "youtube" not in page.url.lower():
            return BrowserResult(
                url=page.url,
                answer="I reached a YouTube watch URL but couldn't verify its title.",
                verification="youtube-watch-title-not-found",
                site="youtube",
                query=query,
            )
        session.record_navigation(page.url)
        return BrowserResult(
            url=page.url,
            answer=(
                title
                if duration_verified
                else f"{title} (watch page open; content duration is pending while an advertisement is active)"
            ),
            success=True,
            verification="youtube-watch-page" if duration_verified else "youtube-watch-page-ad-active",
            site="youtube",
            query=query,
        )

    try:
        return controller.run(run, timeout=max(_RECIPE_TIMEOUT_S, 30.0))
    except Exception:
        logger.warning("YouTube current-page open recipe failed", exc_info=True)
        return BrowserResult(
            answer="I couldn't verify opening a normal YouTube video from the current results.",
            verification="youtube-open-recipe-failed",
            site="youtube",
        )


def youtube_player_control(task: str) -> Optional[BrowserResult]:
    """Control and verify YouTube media using the player keyboard shortcuts."""
    command = youtube_player_command(task)
    if command is None:
        return None

    def run(page):
        if "/watch?v=" not in page.url or "/shorts/" in page.url:
            return None
        before = actions.youtube_player_state(page)
        if not before.get("video"):
            return BrowserResult(
                answer="The YouTube watch page has no active video element.",
                verification="youtube-player-missing",
            )
        if before.get("adActive"):
            state = "AD_PAUSED" if before.get("paused") else "AD_PLAYING"
            message = f"Advertisement active ({state}); cannot verify requested content {command.replace('_', ' ')}."
            return BrowserResult(
                answer=message,
                verification="youtube-ad-active",
                site="youtube",
            )
        if command == "pause" and before.get("paused"):
            return BrowserResult(
                answer="The video is already paused.",
                success=True,
                verification="youtube-player-paused",
                site="youtube",
            )
        if command == "play" and not before.get("paused"):
            return BrowserResult(
                answer="The video is already playing.",
                success=True,
                verification="youtube-player-playing",
                site="youtube",
            )
        if command == "seek_forward" and before.get("currentTime") is None:
            return BrowserResult(
                answer="The active video time is unavailable.",
                verification="youtube-player-time-missing",
            )
        actions.youtube_player_key(page, "l" if command == "seek_forward" else "k")
        try:
            expression = {
                "pause": "() => Boolean(document.querySelector('video')?.paused)",
                "play": "() => Boolean(document.querySelector('video') && !document.querySelector('video').paused)",
                "seek_forward": (
                    "() => Boolean(document.querySelector('video') && "
                    f"document.querySelector('video').currentTime >= {(before.get('currentTime') or 0) + 8})"
                ),
            }[command]
            page.wait_for_function(
                expression,
                timeout=5000,
            )
        except Exception:
            after = actions.youtube_player_state(page)
            return BrowserResult(
                answer=f"I sent the YouTube {command} shortcut but couldn't verify the resulting player state.",
                verification="youtube-player-unverified",
                site="youtube",
            )
        after = actions.youtube_player_state(page)
        page.wait_for_timeout(150)
        stable = actions.youtube_player_state(page)
        if stable.get("adActive"):
            return BrowserResult(
                answer="Advertisement became active; content player state is not verified.",
                verification="youtube-ad-active",
                site="youtube",
            )
        if command == "pause" and not stable.get("paused"):
            actions.youtube_player_key(page, "k")
            page.wait_for_function("() => Boolean(document.querySelector('video')?.paused)", timeout=5000)
            stable = actions.youtube_player_state(page)
        elif command == "play" and stable.get("paused"):
            actions.youtube_player_key(page, "k")
            page.wait_for_function(
                "() => Boolean(document.querySelector('video') && !document.querySelector('video').paused)",
                timeout=5000,
            )
            stable = actions.youtube_player_state(page)
        elif command == "seek_forward" and (stable.get("currentTime") or 0) < (before.get("currentTime") or 0) + 8:
            return BrowserResult(
                answer="I sent the YouTube seek shortcut but the active video time did not remain advanced.",
                verification="youtube-player-unverified",
                site="youtube",
            )
        if command == "pause":
            answer, verification = "Video paused.", "youtube-player-paused"
        elif command == "play":
            answer, verification = "Video playing.", "youtube-player-playing"
        else:
            answer, verification = "Video skipped forward 10 seconds.", "youtube-player-seek-forward"
        return BrowserResult(
            answer=answer,
            success=True,
            verification=verification,
            site="youtube",
            query=str(after),
        )

    try:
        return controller.run(run, timeout=_RECIPE_TIMEOUT_S)
    except Exception:
        logger.warning("YouTube player control recipe failed", exc_info=True)
        return None


def youtube_filter_videos() -> Optional[BrowserResult]:
    """Apply current YouTube Filters > Type > Videos menu selection."""

    def run(page):
        if "/results" not in page.url or "youtube.com" not in page.url.lower():
            return None
        semantic_filter = semantic_control_match(
            page,
            ("search filters", "filters"),
            ("button", "link", "menuitem"),
        )
        if semantic_filter is not None and _click_live_control(semantic_filter, "open video filters"):
            try:
                dialog = page.get_by_role("dialog")
                options = _visible_role_controls(dialog, ("option", "link", "button", "menuitem"))
                chosen = next(
                    (option for option in options if is_youtube_video_filter_label(_control_evidence(option))),
                    None,
                )
                if chosen is not None and _click_live_control(chosen, "select normal video filter"):
                    page.wait_for_function("state => location.href !== state.url", {"url": page.url}, timeout=8000)
                    valid_results = [
                        item
                        for item in discover_results(page, limit=20)
                        if "/watch" in item["url"] and "/shorts/" not in item["url"]
                    ]
                    if valid_results:
                        session.record_observation(page.url, page_type="search_results", results=valid_results)
                        return BrowserResult(
                            url=page.url,
                            answer="Filtered YouTube results to normal videos.",
                            success=True,
                            verification="youtube-video-filter",
                            site="youtube",
                        )
            except Exception:
                logger.debug("Semantic YouTube filter controls did not settle", exc_info=True)
        # Compatibility fallback for older snapshots; the semantic path above is primary.
        try:
            page.wait_for_selector('button[aria-label="Search filters"]', timeout=5000)
        except Exception:
            return BrowserResult(
                answer="filter unavailable in current YouTube UI",
                verification="youtube-video-filter-unavailable",
                site="youtube",
            )
        page.locator('button[aria-label="Search filters"]').click()
        try:
            dialog = page.locator('[role="dialog"]').filter(has_text="TYPE").last
            dialog.wait_for(state="visible", timeout=5000)
            options = dialog.locator("ytd-search-filter-renderer")
            options.first.wait_for(state="visible", timeout=5000)
        except Exception:
            return BrowserResult(
                answer="filter unavailable in current YouTube UI",
                verification="youtube-video-filter-unavailable",
                site="youtube",
            )
        chosen = None
        label = ""
        for index in range(options.count()):
            option = options.nth(index)
            try:
                candidate = option.inner_text(timeout=500).strip()
                if option.is_visible() and is_youtube_video_filter_label(candidate):
                    chosen = option.locator("a#endpoint")
                    label = candidate
                    break
            except Exception:
                continue
        if chosen is None:
            return BrowserResult(
                answer="filter unavailable in current YouTube UI",
                verification="youtube-video-filter-unavailable",
                site="youtube",
            )
        chosen.click(timeout=5000)
        try:
            page.wait_for_function("() => location.search.includes('sp=')", timeout=8000)
        except Exception:
            return BrowserResult(
                url=page.url,
                answer="I selected YouTube's Videos filter but couldn't verify the filtered results state.",
                verification="youtube-video-filter-unverified",
                site="youtube",
            )
        session.record_navigation(page.url)
        result_links = page.locator('a[href*="/watch?v="]')
        valid_results = 0
        for index in range(min(result_links.count(), 12)):
            href = result_links.nth(index).get_attribute("href") or ""
            if "/watch?v=" in href and "/shorts/" not in href:
                valid_results += 1
        if valid_results == 0:
            return BrowserResult(
                url=page.url,
                answer="The normal-video tab was selected, but no normal video results were visible.",
                verification="youtube-video-filter-empty",
                site="youtube",
            )
        return BrowserResult(
            url=page.url,
            answer=f"Filtered YouTube results to {label}.",
            success=True,
            verification="youtube-video-filter",
            site="youtube",
        )

    try:
        return controller.run(run, timeout=_RECIPE_TIMEOUT_S)
    except Exception:
        logger.warning("YouTube video filter recipe failed", exc_info=True)
        return None


def youtube_player_command(task: str) -> Optional[str]:
    """Normalize only the safe player commands supported by the YouTube recipe."""
    lowered = task.lower()
    if "pause" in lowered:
        return "pause"
    if "play" in lowered:
        return "play"
    if "skip" in lowered and "forward" in lowered:
        return "seek_forward"
    return None


def is_youtube_video_filter_label(label: str) -> bool:
    """Match a rendered normal-video filter label, never Shorts or a result title."""
    return label.strip().lower() in {"video", "videos", "vod"}


# Product cards and filters are rendered from the current page. The old named
# functions below remain compatibility adapters, but state belongs to the
# canonical browser session and evidence comes from generic semantic helpers.


def _page_state() -> dict[str, object]:
    return session.get_session().page_state


def _flipkart_result(url: Optional[str], answer: str, verification: str, success: bool = False) -> BrowserResult:
    query = session.get_session().search_query
    if not isinstance(query, str):
        query = _page_state().get("query") if isinstance(_page_state().get("query"), str) else None
    return BrowserResult(
        url=url,
        answer=answer,
        success=success,
        verification=verification,
        site="flipkart",
        query=query,
    )


def _flipkart_dismiss_overlays(page) -> None:
    """Close only visible, non-transactional overlays; never click login or purchase controls."""
    for pattern in (r"^\s*[×✕]\s*$", r"^\s*close\s*$", r"^\s*no thanks\s*$"):
        try:
            matches = page.get_by_text(re.compile(pattern, re.IGNORECASE))
            for index in range(min(matches.count(), 3)):
                candidate = matches.nth(index)
                if candidate.is_visible():
                    candidate.click(timeout=1200)
                    session.record_action(f"dismiss Flipkart overlay {pattern}")
                    break
        except Exception:
            continue


def _flipkart_wait_for_products(page, timeout: int = 9000) -> bool:
    deadline = time.monotonic() + timeout / 1000
    while time.monotonic() < deadline:
        try:
            links = page.get_by_role("link")
            for index in range(min(links.count(), 250)):
                link = links.nth(index)
                if link.is_visible() and _flipkart_link_has_product_evidence(page, link):
                    return True
        except Exception:
            pass
        try:
            page.wait_for_timeout(250)
        except Exception:
            break
    return False


def _flipkart_link_has_product_evidence(page, link) -> bool:
    """Identify a rendered product link from its own live evidence, not its URL shape."""
    try:
        evidence = extract_result_evidence(link, page)
        if not evidence["href"] or not evidence["url"] or evidence["url"] == page.url:
            return False
        return len(evidence["text"]) >= 20 and evidence["price"] is not None
    except Exception:
        return False


def _flipkart_link_evidence(link) -> str:
    """Read only the link's current accessible/visible evidence; no card ancestry assumptions."""
    try:
        values = [link.inner_text(timeout=700)]
        for attribute in ("aria-label", "title"):
            value = link.get_attribute(attribute)
            if value:
                values.append(value)
        return " ".join(" ".join(value.split()) for value in values if value).strip()
    except Exception:
        try:
            return link.inner_text(timeout=700).strip()
        except Exception:
            return ""


def _flipkart_price(text: str) -> Optional[int]:
    return parse_price(text.replace("\xa0", " "))


def _flipkart_ram(text: str) -> Optional[int]:
    value = extract_attributes(text).get("ram", "")
    match = re.search(r"\d+", value)
    return int(match.group(0)) if match else None


def _flipkart_visible_products(page, limit: int = 40) -> list[dict]:
    """Extract visible product links and their own rendered evidence, deduplicated by URL."""
    products = []
    for result in discover_results(page, limit=limit, require_price=True):
        products.append(
            {
                "title": result["title"][:240],
                "ram_gb": _flipkart_ram(result["text"]),
                "price": result["price"],
                "url": result["url"],
                "text": result["text"],
                "attributes": result["attributes"],
            }
        )
    return products


def _flipkart_visible_text_matches(page, pattern: re.Pattern, roles: tuple[str, ...]) -> list:
    """Return visible semantic controls whose complete accessible label matches pattern."""
    matches = []
    for role in roles:
        locator = page.get_by_role(role, name=pattern)
        for index in range(min(locator.count(), 250)):
            item = locator.nth(index)
            try:
                if item.is_visible():
                    matches.append(item)
            except Exception:
                continue
        if matches:
            return matches
    try:
        locator = page.get_by_text(pattern)
        for index in range(min(locator.count(), 250)):
            item = locator.nth(index)
            if item.is_visible():
                matches.append(item)
    except Exception:
        pass
    return matches


def _flipkart_click_label(page, patterns: tuple[re.Pattern, ...], roles: tuple[str, ...]) -> Optional[str]:
    for pattern in patterns:
        matches = _flipkart_visible_text_matches(page, pattern, roles)
        if not matches:
            continue
        try:
            matches[0].click(timeout=2500)
            session.record_action(f'click Flipkart label "{matches[0].inner_text(timeout=400).strip()}"')
            return matches[0].inner_text(timeout=400).strip()
        except Exception:
            continue
    return None


def _flipkart_verify_products(
    products: list[dict], ram_gb: Optional[int] = None, max_price: Optional[int] = None
) -> tuple[bool, str]:
    sample = products[:3]
    if not sample:
        return False, "No visible priced Flipkart products were available."
    if ram_gb is not None:
        missing = [item for item in sample if item.get("ram_gb") != ram_gb]
        if missing:
            return False, f"Visible products did not verify {ram_gb} GB RAM."
    if max_price is not None:
        over = [item for item in sample if item.get("price") is None or item["price"] > max_price]
        if over:
            return False, f"Visible products did not verify a price at or below ₹{max_price:,}."
    return True, ""


def _flipkart_wait_for_price_results(page, max_price: int, before_url: str, timeout_s: float = 8.0) -> list[dict]:
    """Fresh-observe rendered product prices until the current price transition settles."""
    deadline = time.monotonic() + timeout_s
    products = []
    while time.monotonic() < deadline:
        products = _flipkart_visible_products(page)
        url_changed = page.url != before_url and "price" in page.url.lower()
        if (
            url_changed
            and products
            and all(item.get("price") is not None and item["price"] <= max_price for item in products[:3])
        ):
            return products
        try:
            page.wait_for_timeout(300)
        except Exception:
            break
    return products


def flipkart_search(query: str) -> BrowserResult:
    """Search Flipkart and verify a real rendered product-results page."""

    def run(page):
        _page_state().clear()
        site_url = resolve_site("flipkart")
        if not site_url:
            return _flipkart_result(None, "Flipkart is not configured as a valid web target.", "site-unavailable")
        actions.navigate(page, site_url)
        _page_state().update({"query": query, "ram_gb": None, "max_price": None, "sort": None})
        _flipkart_dismiss_overlays(page)
        boxes = []
        for role in ("textbox", "combobox"):
            locator = page.get_by_role(role)
            for index in range(min(locator.count(), 30)):
                boxes.append(locator.nth(index))
        search_box = None
        for candidate in boxes:
            try:
                if not candidate.is_visible():
                    continue
                metadata = " ".join(
                    (candidate.get_attribute(name) or "") for name in ("aria-label", "placeholder", "name", "title")
                ).lower()
                if "search" in metadata:
                    search_box = candidate
                    break
                if search_box is None:
                    search_box = candidate
            except Exception:
                continue
        if search_box is None:
            # The current home page can omit its search control while still exposing
            # the normal query-results URL. This is a URL/page-state fallback, not a
            # DOM selector or product-layout assumption.
            actions.navigate(page, f"{site_url.rstrip('/')}/search?q={quote(query)}")
            session.record_action(f"navigate Flipkart query URL for {query}")
        else:
            try:
                search_box.fill(query, timeout=2500)
                search_box.press("Enter")
                session.record_action(f"search Flipkart for {query}")
            except Exception:
                return _flipkart_result(
                    page.url, "I couldn't submit the current Flipkart search field.", "flipkart-search-submit-failed"
                )
        if not _flipkart_wait_for_products(page):
            return _flipkart_result(
                page.url, "Flipkart did not render product results.", "flipkart-results-unavailable"
            )
        products = _flipkart_visible_products(page)
        parsed_query = unquote_plus(parse_qs(urlparse(page.url).query).get("q", [""])[0])
        if "/search" not in page.url or query.lower() not in parsed_query.lower() or not products:
            return _flipkart_result(
                page.url, "I reached Flipkart but couldn't verify laptop results.", "flipkart-results-unverified"
            )
        session.record_observation(
            page.url,
            page_type="search_results",
            search_query=parsed_query,
            results=products,
        )
        _page_state().update({"query": parsed_query, "results_url": page.url})
        return _flipkart_result(
            page.url,
            f"Flipkart results for {parsed_query}: {len(products)} visible priced products.",
            "flipkart-results",
            True,
        )

    try:
        return controller.run(run, timeout=max(_RECIPE_TIMEOUT_S, 30.0))
    except Exception:
        logger.warning("Flipkart search recipe failed for %r", query, exc_info=True)
        return _flipkart_result(None, "I couldn't verify a Flipkart results page.", "flipkart-search-failed")


def _flipkart_filter_ram(page, ram_gb: int) -> BrowserResult:
    _flipkart_dismiss_overlays(page)
    prior_ram = _page_state().get("ram_gb")
    max_price = _page_state().get("max_price") if isinstance(_page_state().get("max_price"), int) else None
    # The section may already be expanded. Clicking only an exact rendered section label
    # avoids treating a product-card string such as "16 GB RAM" as the filter control.
    _flipkart_click_label(
        page,
        (re.compile(r"^RAM$", re.IGNORECASE), re.compile(r"^RAM\s*(?:Capacity|Type)$", re.IGNORECASE)),
        ("button", "checkbox", "option", "radio", "menuitem", "link"),
    )
    selected = None
    # Filter menus can render their semantic options shortly after the section
    # becomes expanded. Re-observe the same live labels a bounded number of times;
    # never guess a selector or a positional child.
    for attempt in range(3):
        selected = _flipkart_click_label(
            page,
            (re.compile(rf"^{ram_gb}\s*GB(?:\s+RAM)?(?:\s*\(\d+\))?$", re.IGNORECASE),),
            ("checkbox", "option", "radio", "button", "menuitem", "link"),
        )
        if selected is not None:
            break
        try:
            page.wait_for_timeout(450)
        except Exception:
            break
    if selected is None:
        _page_state()["ram_gb"] = prior_ram
        _page_state()["filter_failure"] = "ram"
        return _flipkart_result(
            page.url,
            f"Flipkart's current UI did not expose a {ram_gb} GB RAM filter.",
            "flipkart-ram-filter-unavailable",
        )
    before_url = page.url
    products = []
    verified = False
    detail = ""
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        products = _flipkart_visible_products(page)
        verified, detail = _flipkart_verify_products(products, ram_gb=ram_gb, max_price=max_price)
        if verified and page.url != before_url:
            break
        if verified and products:
            # A filter can update the rendered result set without changing the
            # visible URL. The objective evidence is the current sampled products.
            break
        try:
            page.wait_for_timeout(350)
        except Exception:
            break
    if not verified:
        _page_state()["ram_gb"] = prior_ram
        _page_state()["filter_failure"] = "ram"
        return _flipkart_result(page.url, detail, "flipkart-ram-filter-unverified")
    _page_state()["ram_gb"] = ram_gb
    _page_state().pop("filter_failure", None)
    _page_state()["results_url"] = page.url
    return _flipkart_result(
        page.url,
        f"Filtered Flipkart results to {ram_gb} GB RAM; sampled {len(products[:3])} products.",
        "flipkart-ram-filter",
        True,
    )


def _flipkart_filter_price(page, max_price: int) -> BrowserResult:
    _flipkart_dismiss_overlays(page)
    prior_max_price = _page_state().get("max_price")
    before_url = page.url
    price_control = None
    target_label = None
    comboboxes = page.get_by_role("combobox")
    for index in range(min(comboboxes.count(), 30)):
        candidate = comboboxes.nth(index)
        try:
            if not candidate.is_visible():
                continue
            options = candidate.get_by_role("option")
            labels = [
                " ".join(options.nth(option_index).inner_text(timeout=400).split())
                for option_index in range(options.count())
            ]
            numeric = []
            for label in labels:
                digits = re.sub(r"[^\d]", "", label)
                if digits and "min" not in label.lower() and "+" not in label:
                    numeric.append((int(digits), label))
            if not numeric or any("min" in label.lower() for label in labels):
                continue
            eligible = [(value, label) for value, label in numeric if value <= max_price]
            if eligible:
                price_control = candidate
                target_label = max(eligible, key=lambda item: item[0])[1]
                break
        except Exception:
            continue
    if price_control is None or target_label is None:
        return _flipkart_result(
            page.url,
            "Flipkart's current UI did not expose a semantic maximum-price control.",
            "flipkart-price-filter-unavailable",
        )
    try:
        price_control.select_option(label=target_label)
        session.record_action(f"set Flipkart maximum price ₹{max_price:,}")
    except Exception:
        return _flipkart_result(
            page.url, "I couldn't apply the current Flipkart maximum-price control.", "flipkart-price-filter-failed"
        )
    ram_gb = _page_state().get("ram_gb") if isinstance(_page_state().get("ram_gb"), int) else None
    _page_state()["max_price"] = max_price
    products = _flipkart_wait_for_price_results(page, max_price, before_url)
    verified, detail = _flipkart_verify_products(products, ram_gb=ram_gb, max_price=max_price)
    if not verified and ram_gb is not None:
        # Some live Flipkart filter transitions reconstruct the URL and drop a prior
        # dimension. Re-select the requested semantic RAM option, then fresh-observe
        # both constraints instead of accepting a partial filter state.
        ram_result = _flipkart_filter_ram(page, ram_gb)
        if ram_result.success:
            products = _flipkart_visible_products(page)
            verified, detail = _flipkart_verify_products(products, ram_gb=ram_gb, max_price=max_price)
    if not verified:
        _page_state()["max_price"] = prior_max_price
        _page_state()["filter_failure"] = "price"
        return _flipkart_result(page.url, detail, "flipkart-price-filter-unverified")
    _page_state().pop("filter_failure", None)
    _page_state()["results_url"] = page.url
    return _flipkart_result(
        page.url,
        f"Showing Flipkart products at or below ₹{max_price:,}; sampled {len(products[:3])} products.",
        "flipkart-price-filter",
        True,
    )


def _flipkart_sort_low_to_high(page) -> BrowserResult:
    _flipkart_dismiss_overlays(page)
    if _page_state().get("filter_failure"):
        return _flipkart_result(
            page.url,
            "The current Flipkart results do not have a verified combined filter state.",
            "flipkart-sort-filter-state-unverified",
        )
    sort_patterns = (
        re.compile(r"^Price\s*(?:--|-|–)\s*Low\s*to\s*High$", re.IGNORECASE),
        re.compile(r"^Price\s*Low\s*to\s*High$", re.IGNORECASE),
    )

    def select_sort() -> Optional[str]:
        selected = _flipkart_click_label(
            page,
            sort_patterns,
            ("button", "menuitem", "option", "radio", "link"),
        )
        if selected is None:
            _flipkart_click_label(
                page, (re.compile(r"^Sort\s*By$", re.IGNORECASE),), ("button", "menuitem", "option", "link")
            )
            selected = _flipkart_click_label(
                page,
                sort_patterns,
                ("button", "menuitem", "option", "radio", "link"),
            )
        return selected

    selected = select_sort()
    if selected is None:
        return _flipkart_result(
            page.url, "Flipkart's current UI did not expose Price -- Low to High.", "flipkart-sort-unavailable"
        )
    try:
        page.wait_for_timeout(700)
    except Exception:
        pass
    products = _flipkart_visible_products(page)
    ram_gb = _page_state().get("ram_gb") if isinstance(_page_state().get("ram_gb"), int) else None
    max_price = _page_state().get("max_price") if isinstance(_page_state().get("max_price"), int) else None
    verified, detail = _flipkart_verify_products(products, ram_gb=ram_gb, max_price=max_price)
    if not verified:
        # A live sort transition may rebuild the result route without carrying
        # the already-selected dimensions. Restore the last objectively verified
        # results URL, then make one fresh semantic sort attempt.
        filtered_url = _page_state().get("results_url")
        if isinstance(filtered_url, str) and filtered_url and page.url != filtered_url:
            actions.navigate(page, filtered_url)
            _flipkart_wait_for_products(page)
            restored = _flipkart_visible_products(page)
            restored_ok, _ = _flipkart_verify_products(restored, ram_gb=ram_gb, max_price=max_price)
            if restored_ok and select_sort() is not None:
                try:
                    page.wait_for_timeout(700)
                except Exception:
                    pass
                products = _flipkart_visible_products(page)
                verified, detail = _flipkart_verify_products(products, ram_gb=ram_gb, max_price=max_price)
    prices = [item["price"] for item in products[:3] if item.get("price") is not None]
    if not verified:
        return _flipkart_result(page.url, detail, "flipkart-sort-filter-state-unverified")
    if len(prices) >= 2 and prices != sorted(prices):
        return _flipkart_result(
            page.url, "Visible Flipkart prices were not monotonic after sorting.", "flipkart-sort-unverified"
        )
    _page_state()["sort"] = "price_low_to_high"
    _page_state()["results_url"] = page.url
    return _flipkart_result(
        page.url,
        f"Sorted Flipkart products low to high; sampled prices: {', '.join(f'₹{p:,}' for p in prices)}.",
        "flipkart-sort",
        True,
    )


def _flipkart_extract_products(page) -> BrowserResult:
    if _page_state().get("filter_failure"):
        return _flipkart_result(
            page.url,
            "The current Flipkart results do not have a verified combined filter state.",
            "flipkart-product-extraction-unverified",
        )
    products = _flipkart_visible_products(page)
    ram_gb = _page_state().get("ram_gb") if isinstance(_page_state().get("ram_gb"), int) else None
    max_price = _page_state().get("max_price") if isinstance(_page_state().get("max_price"), int) else None
    verified, detail = _flipkart_verify_products(products, ram_gb=ram_gb, max_price=max_price)
    if not verified:
        return _flipkart_result(page.url, detail, "flipkart-product-extraction-unverified")
    lines = []
    for index, item in enumerate(products[:3], start=1):
        lines.append(
            f"{index}. {item['title']} — {item.get('ram_gb') or '?'} GB RAM — ₹{item['price']:,} — {item['url']}"
        )
    return _flipkart_result(page.url, "\n".join(lines), "flipkart-products-extracted", True)


def _flipkart_product_facts(page) -> dict:
    try:
        body = page.locator("body").inner_text(timeout=2500)
    except Exception:
        body = ""
    title = ""
    try:
        headings = page.get_by_role("heading", level=1)
        for index in range(min(headings.count(), 5)):
            item = headings.nth(index)
            if item.is_visible() and item.inner_text(timeout=700).strip():
                title = " ".join(item.inner_text(timeout=700).split())
                break
    except Exception:
        pass
    if not title:
        try:
            title = page.title().split(" | ", 1)[0].strip()
        except Exception:
            title = ""
    price = _flipkart_price(body)
    ram_gb = _flipkart_ram(body)
    storage_match = re.search(r"\b(\d+(?:\.\d+)?)\s*(GB|TB)\s*(?:SSD|HDD|eMMC|storage)\b", body, re.IGNORECASE)
    processor_match = re.search(r"(?:processor|cpu)\s*[:\-]?\s*([^\n|]{3,100})", body, re.IGNORECASE)
    return {
        "title": title,
        "price": price,
        "ram_gb": ram_gb,
        "storage": f"{storage_match.group(1)} {storage_match.group(2)}" if storage_match else None,
        "processor": processor_match.group(1).strip() if processor_match else None,
    }


def _flipkart_open_cheapest(page) -> BrowserResult:
    if _page_state().get("filter_failure"):
        return _flipkart_result(
            page.url,
            "The current Flipkart results do not have a verified combined filter state.",
            "flipkart-cheapest-selection-unverified",
        )
    products = _flipkart_visible_products(page)
    ram_gb = _page_state().get("ram_gb") if isinstance(_page_state().get("ram_gb"), int) else None
    max_price = _page_state().get("max_price") if isinstance(_page_state().get("max_price"), int) else None
    verified, detail = _flipkart_verify_products(products, ram_gb=ram_gb, max_price=max_price)
    if not verified:
        return _flipkart_result(page.url, detail, "flipkart-cheapest-selection-unverified")
    selected = min(products, key=lambda item: item["price"])
    actions.navigate(page, selected["url"])
    try:
        page.wait_for_load_state("domcontentloaded", timeout=8000)
    except Exception:
        pass
    facts = _flipkart_product_facts(page)
    current_path = urlparse(page.url).path.lower()
    if (
        not facts["title"]
        or facts["price"] is None
        or facts["ram_gb"] != ram_gb
        or current_path.rstrip("/") == "/search"
    ):
        return _flipkart_result(
            page.url,
            "I reached a Flipkart URL but couldn't verify the selected product title, price, and RAM.",
            "flipkart-product-unverified",
        )
    if max_price is not None and facts["price"] > max_price:
        return _flipkart_result(
            page.url,
            "The selected Flipkart product did not satisfy the active price constraint.",
            "flipkart-product-price-unverified",
        )
    _page_state()["product_url"] = page.url
    _page_state()["product_title"] = facts["title"]
    return _flipkart_result(
        page.url,
        f"Opened {facts['title']} — ₹{facts['price']:,} — {facts['ram_gb']} GB RAM.",
        "flipkart-product-page",
        True,
    )


def flipkart_action(task: str) -> Optional[BrowserResult]:
    """Dispatch recognized Flipkart continuations without the generic LLM browser loop."""
    lowered = task.lower()

    def run(page):
        _flipkart_dismiss_overlays(page)
        if "go back" in lowered and ("filtered" in lowered or "results" in lowered):
            results_url = _page_state().get("results_url")
            if isinstance(results_url, str) and results_url:
                actions.navigate(page, results_url)
            else:
                actions.back(page)
            if "/search" not in page.url or not _flipkart_wait_for_products(page):
                return _flipkart_result(
                    page.url, "I couldn't restore the Flipkart filtered results.", "flipkart-back-unavailable"
                )
            products = _flipkart_visible_products(page)
            if _page_state().get("filter_failure"):
                return _flipkart_result(
                    page.url,
                    "The current Flipkart results do not have a verified combined filter state.",
                    "flipkart-back-filter-state-unverified",
                )
            ram_gb = _page_state().get("ram_gb") if isinstance(_page_state().get("ram_gb"), int) else None
            max_price = _page_state().get("max_price") if isinstance(_page_state().get("max_price"), int) else None
            verified, detail = _flipkart_verify_products(products, ram_gb=ram_gb, max_price=max_price)
            if not verified:
                return _flipkart_result(page.url, detail, "flipkart-back-filter-state-unverified")
            return _flipkart_result(page.url, "Restored the filtered Flipkart results.", "flipkart-back-filtered", True)
        ram_match = re.search(r"(\d{1,3})\s*gb\s*ram", lowered)
        if "filter" in lowered and ram_match:
            return _flipkart_filter_ram(page, int(ram_match.group(1)))
        price_match = re.search(r"(?:under|below|less\s+than)\s*₹?\s*([\d,]+)", lowered)
        if ("price" in lowered or "under" in lowered or "below" in lowered) and price_match:
            return _flipkart_filter_price(page, int(price_match.group(1).replace(",", "")))
        if "sort" in lowered and "low" in lowered and "high" in lowered:
            return _flipkart_sort_low_to_high(page)
        if "first three" in lowered and ("matching" in lowered or "laptops" in lowered):
            return _flipkart_extract_products(page)
        if "open" in lowered and "cheapest" in lowered and ("laptop" in lowered or "matching" in lowered):
            return _flipkart_open_cheapest(page)
        return None

    try:
        result = controller.run(run, timeout=max(_RECIPE_TIMEOUT_S, 30.0))
        return result
    except Exception:
        logger.warning("Flipkart action recipe failed for %r", task, exc_info=True)
        return _flipkart_result(None, "I couldn't complete that Flipkart action.", "flipkart-action-failed")


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
        if site_name and site_name.lower() == "youtube":
            try:
                page.wait_for_url("**/results**", timeout=8000)
                settled = True
            except Exception:
                settled = False
            if not settled:
                # YouTube can commit the URL and render results just after the first
                # verifier deadline; allow one bounded fresh observation before reporting
                # the search as unverified.
                try:
                    page.wait_for_url("**/results**", timeout=5000)
                    settled = True
                except Exception:
                    settled = False
        else:
            settled = _wait_for_search_results(page, before_url, before_links)
        if not settled:
            if is_wikipedia:
                return wikipedia_fallback(page)
            return BrowserResult(
                url=page.url,
                answer=f"I reached {site_name or resolved_site_url}, but couldn't verify search results for '{query}'.",
                verification="search-not-settled",
                site=site_name,
                query=query,
            )
        refresh_live_url(page)
        if site_name and site_name.lower() == "youtube":
            generic_results = discover_results(page, limit=20)
            links = [
                item["title"]
                for item in generic_results
                if "/watch" in item["url"] and "/shorts/" not in item["url"]
            ][:5]
            if not links:
                links = _youtube_result_links(page)[:5]
            parsed_query = session.get_session().search_query or unquote_plus(
                parse_qs(urlparse(page.url).query).get("search_query", [""])[0]
            )
            if "/results" not in page.url or query.lower() not in parsed_query.lower() or not links:
                return BrowserResult(
                    url=page.url,
                    answer=f"I reached YouTube, but couldn't verify search results for '{query}'.",
                    verification="youtube-results-not-found",
                    site=site_name,
                    query=query,
                )
            return BrowserResult(
                url=page.url,
                answer="; ".join(links)[:400],
                success=True,
                verification="youtube-results",
                site=site_name,
                query=query,
            )
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
    return segments[3] or None


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
    candidates: list[tuple[int, str]] = []
    for href, _name in _github_links(page):
        parsed = urlparse(urljoin(getattr(page, "url", ""), href))
        path = unquote(parsed.path)
        marker = path.lower().find(root_prefix)
        if marker < 0:
            continue
        tail = path[marker + len(root_prefix) :].strip("/")
        if not tail:
            continue
        ref_name = tail.split("/", 1)[0].strip()
        if ref_name:
            candidates.append((tail.count("/") + 1, ref_name))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


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

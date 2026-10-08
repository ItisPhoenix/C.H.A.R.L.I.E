"""Tier-cascade orchestration for the browser_task tool.

Deterministic browser recipes need no LLM and run inline; the fallback agent needs one, so the caller
(Brain.browser_task in core.py) supplies complete/describe_image/approve_click. Never imports
Brain/core.py -- same constraint as agent.py, core.py imports this lazily instead.
"""

import asyncio
import inspect
import ipaddress
import logging
import re
import socket
import time
from typing import Optional
from urllib.parse import urlparse

from charlie import resource_locks
from charlie.browser import agent, controller, intent, recipes, session, stealth
from charlie.browser.errors import BrowserUnavailable
from charlie.browser.recipes import BrowserResult
from charlie.known_apps import APP_REGISTRY, resolve_website_url
from charlie.router import extract_explicit_http_url
from charlie.utils import make_id

logger = logging.getLogger("charlie.browser")

_CAPABILITY = "browser"
_LOCK_POLL_INTERVAL_S = 0.5
_NON_CACHEABLE_NAV_WORDS = (
    "search",
    "find",
    "look up",
    "open",
    "go back",
    "navigate",
    "filter",
    "show only",
    "exclude shorts",
)
_CURRENT_SITE_CUE_RE = re.compile(
    r"\b(?:this\s+site|current\s+site|search\s+here|find\s+this\s+here|on\s+this\s+(?:site|page))\b",
    re.IGNORECASE,
)

_AUTHORITATIVE_DETERMINISTIC_FAILURES = {
    "search-not-settled",
    "page-open-unverified",
    "content-too-short",
    "content-not-found",
    "constraint-unverified",
    "site-state-blocked",
    "media-open-unverified",
    "media-duration-unverified",
    "media-result-unverified",
    "result-open-unverified",
    "back-unverified",
    "content-unreadable",
    "page-http-failure",
    "private-url-blocked",
}


def _cacheable(task: str, freshness_sensitive: bool) -> bool:
    lowered = task.lower()
    return not freshness_sensitive and not any(word in lowered for word in _NON_CACHEABLE_NAV_WORDS)


async def _acquire_browser(owner_id: str, max_wait_s: float) -> Optional[resource_locks.CapabilityLease]:
    """Acquire canonical browser lease without allowing concurrent page mutations."""
    try:
        return await resource_locks.default_lease_manager.acquire(
            _CAPABILITY,
            owner_id,
            timeout=max_wait_s,
        )
    except asyncio.TimeoutError:
        logger.warning("Browser capability lock wait timed out after %.1fs", max_wait_s)
        return None


def _is_private_or_local_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold().rstrip(".")
    if not host or host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        return bool(
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_unspecified
            or address.is_multicast
        )
    try:
        resolved = socket.getaddrinfo(
            host,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except OSError:
        resolved = []
    for _family, _kind, _proto, _canonname, sockaddr in resolved:
        try:
            address = ipaddress.ip_address(sockaddr[0])
        except (ValueError, IndexError):
            continue
        if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
            return True
    return "." not in host


def _resolve_known_site(task: str, *, user_supplied_url: bool = False) -> Optional[str]:
    """Resolve a site hint or arbitrary HTTP(S) target from current intent text."""
    explicit_url = extract_explicit_http_url(task)
    if explicit_url:
        if _is_private_or_local_url(explicit_url) and not user_supplied_url:
            return None
        return resolve_website_url(explicit_url)
    domain_match = re.search(r"\b(?:www\.)?[a-z0-9](?:[a-z0-9-]*\.)+[a-z]{2,}\b", task, re.IGNORECASE)
    if domain_match:
        resolved = resolve_website_url(domain_match.group(0))
        if resolved:
            return resolved
    site_match = re.search(r"\bon\s+([a-z0-9][\w.-]*)", task, re.IGNORECASE)
    if site_match:
        resolved = resolve_website_url(site_match.group(1))
        if resolved:
            return resolved
    lowered = task.casefold()
    for name, entry in sorted(APP_REGISTRY.items(), key=lambda item: len(item[0]), reverse=True):
        if entry.is_website and re.search(rf"\b{re.escape(name)}\b", lowered):
            return resolve_website_url(name)
    return None


async def resolve(
    task: str,
    complete: agent.Complete,
    describe_image: Optional[agent.DescribeImage] = None,
    approve_click: Optional[agent.ApproveClick] = None,
    max_steps: int = 3,
    deadline_s: float = 25.0,
    on_progress=None,
    owner_id: Optional[str] = None,
    user_supplied_url: bool = False,
    user_visible: bool = False,
    run_browser=None,
) -> BrowserResult:
    start_time = time.perf_counter()
    outcome = "success"
    try:
        return await _resolve_inner(
            task,
            complete,
            describe_image,
            approve_click,
            max_steps,
            deadline_s,
            on_progress,
            owner_id,
            user_supplied_url,
            user_visible,
            run_browser,
        )
    except Exception as e:
        outcome = f"error: {type(e).__name__}"
        raise
    finally:
        elapsed = (time.perf_counter() - start_time) * 1000
        logger.debug(f"browser.task.resolve took {elapsed:.2f}ms, outcome: {outcome}")


async def _resolve_inner(
    task: str,
    complete: agent.Complete,
    describe_image: Optional[agent.DescribeImage] = None,
    approve_click: Optional[agent.ApproveClick] = None,
    max_steps: int = 3,
    deadline_s: float = 25.0,
    on_progress=None,
    owner_id: Optional[str] = None,
    user_supplied_url: bool = False,
    user_visible: bool = False,
    run_browser=None,
) -> BrowserResult:
    """Run the tier cascade for `task`, falling through tier by tier, and cache the result."""
    freshness_sensitive = intent.is_freshness_sensitive(task)
    if not user_visible and _cacheable(task, freshness_sensitive):
        cached = session.cache_get(task)
        if cached is not None:
            return cached

    wait_start = time.monotonic()
    owner_id = owner_id or make_id()
    capability_lease = await _acquire_browser(owner_id, max_wait_s=deadline_s)
    if capability_lease is None:
        return BrowserResult(
            answer="The browser is busy with another task. Try again shortly.",
            verification="capability-busy",
        )
    controller_lease = False
    visible_identity: dict = {}
    try:
        controller.acquire_task_lease()
        controller_lease = True
        # deadline_s is a total budget -- subtract lock-wait time already spent, or a slow lock can double it.
        remaining_deadline_s = max(0.0, deadline_s - (time.monotonic() - wait_start))
        loop = asyncio.get_running_loop()
        if user_visible and run_browser is not None:
            try:
                visible_identity = await loop.run_in_executor(None, controller.prepare_user_browser)
            except BrowserUnavailable as exc:
                return BrowserResult(
                    answer=str(exc),
                    verification="interactive-browser-unavailable",
                    evidence={"requested_visible": True, "browser_surface": "not_exposed"},
                )
            explicit_url = extract_explicit_http_url(task)
            if (
                explicit_url
                and re.search(r"\b(?:new\s+tab|open|navigate|go\s+to)\b", task, re.IGNORECASE)
                and not re.search(r"\b(?:fill|type|click|submit|send|pay|purchase|download)\b", task, re.I)
            ):
                try:
                    def _open_read_close(context):
                        page = context.new_page()
                        page.goto(explicit_url, wait_until="domcontentloaded", timeout=10000)
                        observed_url = page.url
                        title = page.title()
                        if re.search(r"\bclose\b", task, re.IGNORECASE):
                            page.close()
                        return observed_url, title

                    observed_url, title = await loop.run_in_executor(
                        None,
                        lambda: controller.run_user_context(_open_read_close, timeout=15.0),
                    )
                    if observed_url.rstrip("/") != explicit_url.rstrip("/"):
                        return BrowserResult(
                            url=observed_url,
                            answer="I couldn't verify the requested visible-browser destination.",
                            verification="visible-url-unverified",
                        )
                    return BrowserResult(
                        url=observed_url,
                        answer=f"Opened {title or observed_url} in Charlie's visible browser.",
                        success=True,
                        verification="visible-url-and-title",
                        evidence={
                            **visible_identity,
                            "title": title,
                            "browser_surface": "charlie_visible_profile",
                        },
                    )
                except Exception:
                    logger.warning("Visible browser open/read fast path failed", exc_info=True)
                    return BrowserResult(
                        answer=(
                            "The browser action may have started, but its result could not be "
                            "verified. It was not retried."
                        ),
                        verification="visible-action-uncertain",
                    )
            result = await agent.run_task(
                task,
                complete,
                describe_image=None,
                approve_click=approve_click,
                max_steps=max_steps,
                deadline_s=remaining_deadline_s,
                on_progress=on_progress,
                run_browser=controller.run_user,
            )
            if result is not None:
                result.evidence = {
                    **(result.evidence or {}),
                    **visible_identity,
                    "browser_surface": "charlie_visible_profile",
                }
            return result
        if user_visible:
            try:
                visible_identity = await loop.run_in_executor(None, controller.prepare_user_visible)
            except BrowserUnavailable as exc:
                return BrowserResult(answer=str(exc), verification="interactive-browser-unavailable",
                                     evidence={"requested_visible": True, "browser_surface": "not_exposed"})
        result: Optional[BrowserResult] = None
        current_url = session.get_session().last_url or ""
        if result is None and current_url:
            try:
                live_url = await loop.run_in_executor(None, lambda: controller.run(lambda page: page.url, timeout=5.0))
                if live_url:
                    current_url = live_url
            except Exception:
                pass
        parsed_intent = intent.parse_browser_intent(
            task,
            urlparse(current_url).hostname or session.get_session().current_domain or "",
        )
        explicit_url = extract_explicit_http_url(task)
        if explicit_url and _is_private_or_local_url(explicit_url) and not user_supplied_url:
            return BrowserResult(
                url=explicit_url,
                answer=(
                    "I can't open a private or loopback URL unless it was explicitly "
                    "supplied for browser navigation."
                ),
                verification="private-url-blocked",
                evidence={"requested_url": explicit_url, "provenance": "not_user_supplied"},
            )
        resolved_site = _resolve_known_site(task, user_supplied_url=user_supplied_url)
        if (
            result is None
            and resolved_site
            and explicit_url
            and parsed_intent.operation in {"OPEN", "READ"}
        ):
            result = await loop.run_in_executor(
                None,
                lambda: recipes.open_site(
                    resolved_site,
                    read_content=parsed_intent.operation == "READ",
                ),
            )
        if (
            result is None
            and current_url
            and not (resolved_site and parsed_intent.operation in {"OPEN", "SEARCH"})
            and parsed_intent.operation in {
                "BACK",
                "OPEN",
                "FILTER",
                "SORT",
                "READ",
                "CURRENT_PAGE_FACT",
                "COMPARE",
                "PRODUCT_SELECT",
                "MEDIA",
            }
        ):
            result = await loop.run_in_executor(None, recipes.apply_current_page_intent, task, parsed_intent)

        if result is None and parsed_intent.operation == "MEDIA":
            site = resolved_site
            if site is None and current_url:
                parsed_current = urlparse(current_url)
                if parsed_current.scheme in {"http", "https"} and parsed_current.netloc:
                    site = f"{parsed_current.scheme}://{parsed_current.netloc}"
            if site:
                site_intent = intent.parse_site_intent(task, site)
                query = site_intent.query if site_intent else parsed_intent.query or task
                media_request = recipes.media_request
                if "deadline_s" in inspect.signature(media_request).parameters:
                    result = await loop.run_in_executor(
                        None,
                        lambda: media_request(site, query, parsed_intent, deadline_s=remaining_deadline_s),
                    )
                else:
                    result = await loop.run_in_executor(None, media_request, site, query, parsed_intent)

        if (
            result is None
            and current_url
            and parsed_intent.operation == "SEARCH"
            and _CURRENT_SITE_CUE_RE.search(task)
        ):
            parsed_current = urlparse(current_url)
            current_site = f"{parsed_current.scheme}://{parsed_current.netloc}"
            result = await loop.run_in_executor(
                None,
                recipes.site_search,
                current_site,
                parsed_intent.query or task,
                None,
            )

        # None means primitive not applicable. A named deterministic failure is
        # authoritative and must be returned without an unrelated Tier-3 chain.
        if result is None or (
            not result.success and result.verification not in _AUTHORITATIVE_DETERMINISTIC_FAILURES
        ):
            site = resolved_site
            if site:
                if parsed_intent.operation == "OPEN":
                    result = await loop.run_in_executor(None, recipes.open_site, site)
                else:
                    site_name = next(
                        (name for name, entry in APP_REGISTRY.items() if entry.is_website and entry.open_cmd == site),
                        None,
                    )
                    site_intent = intent.parse_site_intent(task, site_name or "")
                    if site_intent:
                        query = site_intent.query
                    else:
                        parsed_site_intent = intent.parse_browser_intent(task, urlparse(site).hostname or "")
                        query = parsed_site_intent.query or task
                    result = await loop.run_in_executor(None, recipes.site_search, site, query, site_name)

        if result is None:
            repository_result = await loop.run_in_executor(
                None, recipes.current_repository_search, task, session.get_session().last_url
            )
            if repository_result is not None:
                result = repository_result

        if result is None:
            result = await agent.run_task(
                task, complete, describe_image, approve_click, max_steps, remaining_deadline_s, on_progress
            )
            if result.answer == "blocked":
                blocked_url = session.get_session().last_url
                retried = await loop.run_in_executor(None, stealth.retry_blocked, blocked_url) if blocked_url else None
                result = retried or BrowserResult(answer="That site blocked me and I couldn't get through.")

        if result is not None and user_visible:
            result.evidence = {
                **(result.evidence or {}),
                **visible_identity,
                "browser_surface": "playwright",
            }
        if result is not None and result.success and not user_visible and _cacheable(task, freshness_sensitive):
            session.cache_set(task, result)
        return result
    finally:
        if controller_lease:
            controller.release_task_lease()
        await capability_lease.release()

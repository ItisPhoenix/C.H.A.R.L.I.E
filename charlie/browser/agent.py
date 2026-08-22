"""Tier 3: bounded observe -> LLM picks one action -> execute loop, for tasks no recipe covers.

Never imports Brain/core.py -- core.py lazily imports charlie.browser, so the reverse would be
circular. The caller supplies two async callbacks: complete() for the per-step action decision
and an optional describe_image() for the vision fallback; Brain.browser_task() wires both.
"""

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional
from urllib.parse import urlparse

from charlie.browser import controller, session
from charlie.browser.actions import click, navigate, scroll, type_text
from charlie.browser.observation import is_blocked, observe
from charlie.browser.recipes import BrowserResult

logger = logging.getLogger("charlie.browser")

Complete = Callable[[str], Awaitable[str]]
DescribeImage = Callable[[str], Awaitable[str]]
# (button_name, page_url) -> approved
ApproveClick = Callable[[str, str], Awaitable[bool]]

_VISION_MARK_THRESHOLD = 3
_VISION_FAIL_THRESHOLD = 2
_PROGRESS_SPEECH_MARGIN_S = 4.0
# Bounds one observe/action call so a single hung Playwright op can't stall this coroutine past the overall deadline.
_STEP_CALL_TIMEOUT_S = 15.0
_PURCHASE_KEYWORDS = (
    "buy now", "place order", "checkout", "confirm payment", "pay now",
    "complete purchase", "subscribe",
)

_ACTION_GRAMMAR = (
    'Respond with exactly one line, one of:\n'
    "CLICK <mark_id>\n"
    'TYPE <mark_id> "<text>" [SUBMIT]\n'
    "SCROLL down|up\n"
    "BACK\n"
    "NAVIGATE <url>\n"
    'DONE url="<url or empty>" answer="<answer or empty>"\n'
)
_CLICK_RE = re.compile(r"^CLICK\s+(\d+)$", re.IGNORECASE)
_TYPE_RE = re.compile(r'^TYPE\s+(\d+)\s+"([^"]*)"(\s+SUBMIT)?$', re.IGNORECASE)
_SCROLL_RE = re.compile(r"^SCROLL\s+(down|up)$", re.IGNORECASE)
_BACK_RE = re.compile(r"^BACK$", re.IGNORECASE)
_NAVIGATE_RE = re.compile(r"^NAVIGATE\s+(\S+)$", re.IGNORECASE)
_DONE_RE = re.compile(r'^DONE\s+url="([^"]*)"\s+answer="([^"]*)"$', re.IGNORECASE)


@dataclass
class _Action:
    kind: str
    mark_id: Optional[int] = None
    text: Optional[str] = None
    submit: bool = False
    direction: Optional[str] = None
    url: Optional[str] = None
    answer: Optional[str] = None


def _parse_action(raw: str) -> Optional[_Action]:
    if not raw.strip():
        return None
    line = raw.strip().splitlines()[0].strip()
    if line.startswith("```"):
        lines = [candidate.strip() for candidate in raw.strip().splitlines() if not candidate.strip().startswith("```")]
        line = lines[0] if lines else ""
    if line.lower().startswith("action:"):
        line = line.split(":", 1)[1].strip()
    if line.startswith("{"):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            command = str(payload.get("action", payload.get("command", ""))).strip()
            if command.upper() == "CLICK" and str(payload.get("mark_id", "")).isdigit():
                return _Action(kind="click", mark_id=int(payload["mark_id"]))
            if command.upper() == "TYPE" and str(payload.get("mark_id", "")).isdigit():
                return _Action(
                    kind="type",
                    mark_id=int(payload["mark_id"]),
                    text=str(payload.get("text", "")),
                    submit=bool(payload.get("submit", False)),
                )
            if command.upper() == "SCROLL" and str(payload.get("direction", "")).lower() in {"up", "down"}:
                return _Action(kind="scroll", direction=str(payload["direction"]).lower())
            if command.upper() == "NAVIGATE" and payload.get("url"):
                return _Action(kind="navigate", url=str(payload["url"]))
            if command.upper() == "DONE":
                return _Action(
                    kind="done",
                    url=str(payload.get("url", "")) or None,
                    answer=str(payload.get("answer", "")) or None,
                )
    match = _DONE_RE.match(line)
    if match:
        return _Action(kind="done", url=match.group(1) or None, answer=match.group(2) or None)
    match = _CLICK_RE.match(line)
    if match:
        return _Action(kind="click", mark_id=int(match.group(1)))
    match = _TYPE_RE.match(line)
    if match:
        return _Action(kind="type", mark_id=int(match.group(1)), text=match.group(2), submit=bool(match.group(3)))
    match = _SCROLL_RE.match(line)
    if match:
        return _Action(kind="scroll", direction=match.group(1).lower())
    if _BACK_RE.match(line):
        return _Action(kind="back")
    match = _NAVIGATE_RE.match(line)
    if match:
        return _Action(kind="navigate", url=match.group(1))
    return None


def _build_prompt(task: str, observation: str) -> str:
    # No step-count framing -- telling the model its budget is running low tends to make it quit the task early.
    return (
        f"You are controlling a headless browser to complete this task: {task}\n\n"
        f"Current page:\n{observation}\n\n"
        f"{_ACTION_GRAMMAR}\n"
        "Pick DONE only after verifying the requested result in TEXT or the current URL. "
        "You may return the same command as a fenced line, an Action: line, or a JSON object, "
        "but never include an explanation before the command."
    )


def _observe_page(page) -> tuple:
    observation, marks, text = observe(page)
    session.record_marks(marks)  # actions.click/type_text and the purchase-gate check both read this
    return observation, marks, is_blocked(marks, text)


def _apply_action(page, action: _Action) -> None:
    if action.kind == "click":
        click(page, action.mark_id)
    elif action.kind == "type":
        type_text(page, action.mark_id, action.text, submit=action.submit)
    elif action.kind == "scroll":
        scroll(page, action.direction)
    elif action.kind == "back":
        from charlie.browser.actions import back
        back(page)
    elif action.kind == "navigate":
        navigate(page, action.url)


def _same_site_or_related_host(current_url: str, target_url: str) -> bool:
    current = urlparse(current_url).netloc.lower().split(":", 1)[0]
    target = urlparse(target_url).netloc.lower().split(":", 1)[0]
    return bool(current and target and (current == target or target.endswith(f".{current}")))


def _is_site_continuation(task: str) -> bool:
    lowered = task.lower()
    return any(
        phrase in lowered
        for phrase in (
            "these results", "this page", "current page", "on this page", "on amazon", "on flipkart",
            "on youtube", "on wikipedia", "sort these", "filter these", "first matching",
        )
    )


def _controller_run(fn, *, timeout: float, retry_on_stale: bool = True):
    """Call controller with compatibility for focused tests' simple monkeypatches."""
    try:
        return controller.run(fn, timeout=timeout, retry_on_stale=retry_on_stale)
    except TypeError as exc:
        if "retry_on_stale" not in str(exc):
            raise
        return controller.run(fn, timeout=timeout)


def _grab_annotated_screenshot(page, marks: list) -> bytes:
    """Screenshot with numbered boxes over each mark, reusing desktop's set-of-marks annotator."""
    from charlie.desktop import vision as desktop_vision
    from charlie.desktop.uia import Element
    controller.set_resource_blocking(False)
    try:
        elements = []
        for mark in marks:
            try:
                box = page.locator(f"aria-ref={mark.ref}").bounding_box(timeout=1000)
            except Exception:
                box = None
            if box:
                bounds = (int(box["x"]), int(box["y"]), int(box["x"] + box["width"]), int(box["y"] + box["height"]))
                elements.append(Element(mark.mark_id, mark.name, mark.role, bounds, False, False))
        png = page.screenshot(full_page=False)
        if desktop_vision.VISION_AVAILABLE and elements:
            png = desktop_vision.annotate_som(png, elements)
        return png
    finally:
        controller.set_resource_blocking(True)


async def _augment_with_vision(
    loop: asyncio.AbstractEventLoop, observation: str, marks: list, describe_image: DescribeImage
) -> str:
    try:
        from charlie.desktop import vision as desktop_vision
        def _run(page):
            return _grab_annotated_screenshot(page, marks)

        png = await loop.run_in_executor(None, lambda: _controller_run(_run, timeout=_STEP_CALL_TIMEOUT_S))
        data_url = desktop_vision.to_data_url(png)
        description = await describe_image(data_url)
        return f"{observation}\n\n[Vision] {description}"
    except Exception:
        logger.warning("Tier 3 vision fallback failed", exc_info=True)
        return observation


async def run_task(
    task: str,
    complete: Complete,
    describe_image: Optional[DescribeImage] = None,
    approve_click: Optional[ApproveClick] = None,
    max_steps: int = 3,
    deadline_s: float = 25.0,
    on_progress: Optional[Callable[[], None]] = None,
) -> BrowserResult:
    """Bounded observe/act loop. Never raises -- returns a best-effort BrowserResult at the deadline."""
    loop = asyncio.get_running_loop()
    deadline = time.monotonic() + deadline_s
    fail_count = 0
    progress_spoken = False

    for step in range(max_steps):
        now = time.monotonic()
        if now > deadline:
            break
        if not progress_spoken and on_progress and deadline - now < deadline_s - _PROGRESS_SPEECH_MARGIN_S:
            on_progress()
            progress_spoken = True

        try:
            observation, marks, blocked = await loop.run_in_executor(
                None, lambda: _controller_run(_observe_page, timeout=_STEP_CALL_TIMEOUT_S)
            )
        except Exception:
            logger.warning("Tier 3 observation failed on step %d", step, exc_info=True)
            break
        # step 0 reflects leftover browser state, not this task's own navigation -- ignore it there
        if blocked and step > 0:
            return BrowserResult(answer="blocked")
        if describe_image and (len(marks) < _VISION_MARK_THRESHOLD or fail_count >= _VISION_FAIL_THRESHOLD):
            observation = await _augment_with_vision(loop, observation, marks, describe_image)

        try:
            raw = await complete(_build_prompt(task, observation))
        except Exception:
            logger.warning("Tier 3 LLM step failed on step %d", step, exc_info=True)
            break

        action = _parse_action(raw)
        if action is None:
            fail_count += 1
            continue
        if action.kind == "done":
            if not action.url and not action.answer:
                fail_count += 1
                continue
            return BrowserResult(
                url=action.url or None,
                answer=action.answer or None,
                success=True,
                verification="agent-confirmed",
            )
        if action.kind == "navigate" and action.url and _is_site_continuation(task):
            current_url = session.get_session().last_url or ""
            if current_url and not _same_site_or_related_host(current_url, action.url):
                return BrowserResult(
                    answer="I couldn't complete that without leaving the current site.",
                    verification="site-containment",
                )
        if action.kind == "click" and approve_click is not None:
            mark = session.get_session().marks.get(action.mark_id)
            if mark and any(k in mark.name.lower() for k in _PURCHASE_KEYWORDS):
                page_url = session.get_session().last_url or ""
                if not await approve_click(mark.name, page_url):
                    return BrowserResult(answer=f'Stopped before clicking "{mark.name}" -- needs your approval.')
        try:
            await loop.run_in_executor(
                None,
                lambda: _controller_run(
                    lambda page, a=action: _apply_action(page, a),
                    timeout=_STEP_CALL_TIMEOUT_S,
                    retry_on_stale=action.kind != "click",
                ),
            )
            fail_count = 0
        except Exception:
            logger.debug("Tier 3 action %s failed on step %d", action.kind, step, exc_info=True)
            fail_count += 1

    return BrowserResult(answer="I ran out of time before finishing that.")

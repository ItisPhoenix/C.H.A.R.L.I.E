import asyncio
import json
import time

import pytest

from charlie.browser import intent, session, task
from charlie.browser.agent import run_task
from charlie.browser.observation import Mark, is_blocked, parse_snapshot, rank_and_cap
from charlie.browser.recipes import BrowserResult


@pytest.fixture(autouse=True)
def _reset_session():
    session.reset_session()
    session._task_cache.clear()
    yield
    session.reset_session()
    session._task_cache.clear()


# --- fastpath (tier 0) -------------------------------------------------------

def _yt_html(items):
    data = {
        "contents": {
            "twoColumnSearchResultsRenderer": {
                "primaryContents": {
                    "sectionListRenderer": {
                        "contents": [{"itemSectionRenderer": {"contents": items}}]
                    }
                }
            }
        }
    }
    return f"<html><script>var ytInitialData = {json.dumps(data)};</script></html>"


def _video_item(video_id, length="3:45", channel="Some Channel"):
    return {
        "videoRenderer": {
            "videoId": video_id,
            "lengthText": {"simpleText": length},
            "longBylineText": {"runs": [{"text": channel}]},
        }
    }


def test_fastpath_youtube_play_picks_matching_channel(monkeypatch):
    from charlie.browser import fastpath

    html = _yt_html([_video_item("short1", "0:30"), _video_item("abc123", "4:00", "DL91 Official")])

    class FakeResp:
        status = 200
        body = html.encode()

    monkeypatch.setattr("scrapling.fetchers.Fetcher.get", lambda *a, **k: FakeResp())
    assert fastpath.youtube_play("DL91") == "https://www.youtube.com/watch?v=abc123"


def test_fastpath_youtube_play_skips_short_videos(monkeypatch):
    from charlie.browser import fastpath

    html = _yt_html([_video_item("tooshort", "0:45")])

    class FakeResp:
        status = 200
        body = html.encode()

    monkeypatch.setattr("scrapling.fetchers.Fetcher.get", lambda *a, **k: FakeResp())
    assert fastpath.youtube_play("anything") is None


def test_fastpath_youtube_play_empty_html_falls_through(monkeypatch):
    from charlie.browser import fastpath

    class FakeResp:
        status = 200
        body = b"<html>no data here</html>"

    monkeypatch.setattr("scrapling.fetchers.Fetcher.get", lambda *a, **k: FakeResp())
    assert fastpath.youtube_play("anything") is None


def test_fastpath_youtube_play_http_error_returns_none(monkeypatch):
    from charlie.browser import fastpath

    class FakeResp:
        status = 429
        body = b""

    monkeypatch.setattr("scrapling.fetchers.Fetcher.get", lambda *a, **k: FakeResp())
    assert fastpath.youtube_play("anything") is None


# --- observation --------------------------------------------------------------

_SNAPSHOT = """
- generic:
  - textbox "Search" [ref=e1]
  - link "First Result" [ref=e2]:
    - /url: /watch?v=1
  - link "First Result" [ref=e3]:
    - /url: /watch?v=1
  - button "More actions" [ref=e4]
  - button "More actions" [ref=e5]
"""


def test_parse_snapshot_assigns_marks_and_refs():
    marks = parse_snapshot(_SNAPSHOT)
    assert [m.role for m in marks] == ["textbox", "link", "link", "button", "button"]
    assert [m.mark_id for m in marks] == [1, 2, 3, 4, 5]
    assert marks[1].href == "/watch?v=1"


def test_rank_and_cap_dedupes_and_orders_inputs_first():
    marks = parse_snapshot(_SNAPSHOT)
    ranked = rank_and_cap(marks)
    # duplicate (link, "First Result") and (button, "More actions") collapse to one each
    assert [(m.role, m.name) for m in ranked] == [
        ("textbox", "Search"), ("link", "First Result"), ("button", "More actions"),
    ]
    assert [m.mark_id for m in ranked] == [1, 2, 3]


def test_rank_and_cap_respects_max_marks():
    marks = [Mark(mark_id=0, role="button", name=f"btn{i}", ref=f"e{i}") for i in range(10)]
    ranked = rank_and_cap(marks, max_marks=3)
    assert len(ranked) == 3


def test_is_blocked_on_status_code():
    assert is_blocked([], "", status=403) is True
    assert is_blocked([], "", status=429) is True
    assert is_blocked([], "", status=200) is False


def test_is_blocked_on_challenge_phrase():
    assert is_blocked([], "Please verify you are human to continue") is True


def test_is_blocked_false_for_sparse_legitimate_page():
    # example.com is genuinely this sparse -- must not be flagged as blocked.
    assert is_blocked([], "This domain is for use in illustrative examples.") is False


# --- session -------------------------------------------------------------------

def test_resolve_mark_raises_with_message_for_unknown_id():
    from charlie.browser.errors import MarkNotFound

    with pytest.raises(MarkNotFound, match="not found"):
        session.resolve_mark(99)


def test_resolve_mark_returns_recorded_mark():
    mark = Mark(mark_id=1, role="button", name="Go", ref="e1")
    session.record_marks([mark])
    assert session.resolve_mark(1) is mark


def test_cache_get_set_round_trip():
    result = BrowserResult(url="https://example.com")
    session.cache_set("play x", result)
    assert session.cache_get("play x") is result


def test_cache_expires_after_ttl(monkeypatch):
    result = BrowserResult(url="https://example.com")
    session.cache_set("play x", result)
    original_monotonic = time.monotonic
    monkeypatch.setattr(time, "monotonic", lambda: original_monotonic() + 700)
    assert session.cache_get("play x") is None


# --- intent ----------------------------------------------------------------

def test_has_open_intent_on_verbs_and_phrases():
    assert intent.has_open_intent("play DL91 on youtube") is True
    assert intent.has_open_intent("show me the results") is True
    assert intent.has_open_intent("search mechanical keyboards on amazon") is False


def test_is_bare_followup():
    assert intent.is_bare_followup("open it") is True
    assert intent.is_bare_followup("open it please") is False


def test_is_freshness_sensitive():
    assert intent.is_freshness_sensitive("what's the price of bitcoin") is True
    assert intent.is_freshness_sensitive("play DL91 on youtube") is False


# --- agent (tier 3) ----------------------------------------------------------

def _fake_observation(marks=None):
    marks = marks or []
    lines = [f'[{m.mark_id}] {m.role} "{m.name}"' for m in marks] or ["(no marked elements)"]
    return "URL: https://example.com\nTITLE: t\n" + "\n".join(lines), marks, False


@pytest.mark.asyncio
async def test_run_task_done_action_returns_result(monkeypatch):
    from charlie.browser import controller

    monkeypatch.setattr(controller, "run", lambda fn, timeout=None: _fake_observation())

    async def complete(prompt):
        return 'DONE url="https://x.com" answer="found it"'

    result = await run_task("do something", complete)
    assert result == BrowserResult(url="https://x.com", answer="found it")


@pytest.mark.asyncio
async def test_run_task_exhausts_steps_returns_fallback(monkeypatch):
    from charlie.browser import controller

    calls = {"n": 0}

    def fake_run(fn, timeout=None):
        calls["n"] += 1
        return _fake_observation()

    monkeypatch.setattr(controller, "run", fake_run)

    async def complete(prompt):
        return "SCROLL down"

    result = await run_task("do something", complete, max_steps=2, deadline_s=100)
    assert result.answer == "I ran out of time before finishing that."
    assert calls["n"] == 4  # 2 steps x (observe + scroll action)


@pytest.mark.asyncio
async def test_run_task_blocked_after_first_step_stops(monkeypatch):
    from charlie.browser import controller

    def fake_run(fn, timeout=None):
        return "URL: x\nTITLE: t\n[1] link \"a\"", [], "verify you are human"

    monkeypatch.setattr(controller, "run", fake_run)

    async def complete(prompt):
        return "SCROLL down"

    result = await run_task("do something", complete, max_steps=3, deadline_s=100)
    assert result.answer == "blocked"


@pytest.mark.asyncio
async def test_run_task_purchase_click_declined_stops_without_clicking(monkeypatch):
    from charlie.browser import controller

    mark = Mark(mark_id=1, role="button", name="Buy Now", ref="e1")
    monkeypatch.setattr(controller, "run", lambda fn, timeout=None: _fake_observation([mark]))
    session.record_marks([mark])

    async def complete(prompt):
        return "CLICK 1"

    async def approve_click(name, url):
        return False

    result = await run_task("buy it", complete, approve_click=approve_click, max_steps=1)
    assert "needs your approval" in result.answer


@pytest.mark.asyncio
async def test_run_task_click_without_purchase_keyword_skips_gate(monkeypatch):
    from charlie.browser import actions, controller

    mark = Mark(mark_id=1, role="link", name="Some Result", ref="e1")
    monkeypatch.setattr(controller, "run", lambda fn, timeout=None: _fake_observation([mark]))
    session.record_marks([mark])
    monkeypatch.setattr(actions, "click", lambda page, mark_id: "clicked")

    approve_calls = []

    async def approve_click(name, url):
        approve_calls.append(name)
        return False

    async def complete(prompt):
        return "CLICK 1"

    await run_task("click it", complete, approve_click=approve_click, max_steps=1, deadline_s=0.001)
    assert approve_calls == []


# --- tier-cascade orchestration (task.py) -------------------------------------

@pytest.mark.asyncio
async def test_resolve_uses_cache_when_available(monkeypatch):
    cached = BrowserResult(url="https://cached.example")
    session.cache_set("play cached thing", cached)

    async def complete(prompt):
        raise AssertionError("should not reach the LLM when cached")

    result = await task.resolve("play cached thing", complete)
    assert result is cached


@pytest.mark.asyncio
async def test_resolve_skips_cache_for_freshness_sensitive_tasks(monkeypatch):
    cached = BrowserResult(url="https://cached.example")
    session.cache_set("what's the latest news", cached)

    async def fake_agent_run_task(*a, **k):
        return BrowserResult(answer="fresh result")

    monkeypatch.setattr(task.agent, "run_task", fake_agent_run_task)

    async def complete(prompt):
        return ""

    result = await task.resolve("what's the latest news", complete)
    assert result.answer == "fresh result"


@pytest.mark.asyncio
async def test_resolve_falls_through_to_tier3_when_no_tier_matches(monkeypatch):
    async def fake_agent_run_task(*a, **k):
        return BrowserResult(answer="tier3 handled it")

    monkeypatch.setattr(task.agent, "run_task", fake_agent_run_task)

    async def complete(prompt):
        return ""

    # No "youtube" mention and no known-site name -- tiers 0-2 all have nothing to match.
    result = await task.resolve("find a good recipe for pancakes", complete)
    assert result.answer == "tier3 handled it"


@pytest.mark.asyncio
async def test_resolve_escalates_blocked_tier3_to_tier4(monkeypatch):
    async def fake_agent_run_task(*a, **k):
        return BrowserResult(answer="blocked")

    monkeypatch.setattr(task.agent, "run_task", fake_agent_run_task)
    session.record_navigation("https://blocked.example")
    monkeypatch.setattr(task.stealth, "retry_blocked", lambda url: BrowserResult(url=url, answer="stealth got it"))

    async def complete(prompt):
        return ""

    result = await task.resolve("find quantum computing news", complete)
    assert result.answer == "stealth got it"


@pytest.mark.asyncio
async def test_resolve_reports_when_stealth_also_fails(monkeypatch):
    async def fake_agent_run_task(*a, **k):
        return BrowserResult(answer="blocked")

    monkeypatch.setattr(task.agent, "run_task", fake_agent_run_task)
    session.record_navigation("https://blocked.example")
    monkeypatch.setattr(task.stealth, "retry_blocked", lambda url: None)

    async def complete(prompt):
        return ""

    result = await task.resolve("find quantum computing news", complete)
    assert "blocked" in result.answer.lower()


# --- browser capability lock (charlie.resource_locks) -------------------------

@pytest.mark.asyncio
async def test_resolve_holds_and_releases_the_browser_capability(monkeypatch):
    from charlie import resource_locks
    resource_locks._owners.pop("browser", None)
    owner_seen = {}

    async def fake_agent_run_task(*a, **k):
        owner_seen["owner"] = resource_locks.current_owner("browser")
        return BrowserResult(answer="ok")

    monkeypatch.setattr(task.agent, "run_task", fake_agent_run_task)

    async def complete(prompt):
        return ""

    await task.resolve("find a good recipe for tacos", complete)

    assert owner_seen["owner"] is not None  # held during the call
    assert resource_locks.current_owner("browser") is None  # released after


@pytest.mark.asyncio
async def test_resolve_serializes_two_concurrent_browser_tasks(monkeypatch):
    from charlie import resource_locks
    resource_locks._owners.pop("browser", None)
    monkeypatch.setattr(task, "_LOCK_POLL_INTERVAL_S", 0.01)
    resource_locks.acquire("browser", "someone-else")

    async def fake_agent_run_task(*a, **k):
        return BrowserResult(answer="ok")

    monkeypatch.setattr(task.agent, "run_task", fake_agent_run_task)

    async def complete(prompt):
        return ""

    async def _release_after_one_poll():
        await asyncio.sleep(0.02)
        resource_locks.release("browser", "someone-else")

    releaser = asyncio.create_task(_release_after_one_poll())
    result = await task.resolve("find a good recipe for lasagna", complete, deadline_s=5.0)
    await releaser

    assert result.answer == "ok"
    assert resource_locks.current_owner("browser") is None


@pytest.mark.asyncio
async def test_resolve_fails_open_when_lock_wait_exceeds_deadline(monkeypatch):
    from charlie import resource_locks
    resource_locks._owners.pop("browser", None)
    monkeypatch.setattr(task, "_LOCK_POLL_INTERVAL_S", 0.01)
    resource_locks.acquire("browser", "someone-else")  # never released this test

    async def fake_agent_run_task(*a, **k):
        return BrowserResult(answer="proceeded anyway")

    monkeypatch.setattr(task.agent, "run_task", fake_agent_run_task)

    async def complete(prompt):
        return ""

    result = await task.resolve("find a good recipe for soup", complete, deadline_s=0.03)

    assert result.answer == "proceeded anyway"
    resource_locks.release("browser", "someone-else")


# --- tools.py gate -----------------------------------------------------------

def test_browser_tools_disabled_message(monkeypatch):
    from charlie import tools

    monkeypatch.setattr(tools.config, "browser_enabled", False)
    assert tools.browser_read("https://example.com") == tools._BROWSER_DISABLED_MSG


def test_browser_task_direct_call_errors():
    from charlie import tools

    assert "Brain.browser_task" in tools.browser_task("anything")


# --- core.py routing: website + leftover text defers instead of launching ----

def test_match_open_app_defers_website_with_leftover():
    """router.py split match_open_app (pure) / execute_open_app (side-effecting) after the
    Phase 0-2 rewrite -- the deferred case now returns empty apps/commands plus the full
    original query as the 'leftover', instead of core.py's old combined (msg, leftover) shape."""
    from charlie import router

    result = router.match_open_app("open youtube and search for cats")
    assert result is not None
    apps, commands, leftover = result
    assert apps == []
    assert commands == []
    assert leftover and "search for cats" in leftover


def test_match_open_app_bare_website_still_launches(monkeypatch):
    from charlie import router

    class FakePopen:
        def __init__(self, *a, **k):
            pass

    monkeypatch.setattr("subprocess.Popen", FakePopen)
    result = router.match_open_app("open youtube")
    assert result is not None
    apps, commands, leftover = result
    assert leftover is None
    msg = router.execute_open_app(apps, commands)
    assert "youtube" in msg.lower()


# --- router.py: deterministic "<verb> ... on <site>" browser-task fast-path -----

def test_match_browser_task_matches_play_on_site():
    from charlie import router

    assert router.match_browser_task("play DL91 on youtube") == "play DL91 on youtube"
    assert router.match_browser_task("Play DL91 on YouTube") == "Play DL91 on YouTube"


def test_match_browser_task_matches_search_on_site():
    from charlie import router

    assert router.match_browser_task("search mechanical keyboards on amazon") is not None


def test_match_browser_task_ignores_unknown_site():
    from charlie import router

    assert router.match_browser_task("search for cats on my desktop") is None


def test_match_browser_task_ignores_non_matching_verb():
    from charlie import router

    assert router.match_browser_task("what is the weather on my phone") is None


# --- recipes.site_search: fall back past an unfillable combobox (e.g. Amazon's category select) ---

def test_site_search_skips_unfillable_combobox_falls_back_to_textbox(monkeypatch):
    from charlie.browser import recipes

    dropdown = Mark(mark_id=1, role="combobox", name="Search in", ref="e1")
    real_box = Mark(mark_id=2, role="textbox", name="Search", ref="e2")
    monkeypatch.setattr(recipes, "_observe", lambda page: [dropdown, real_box])

    fill_calls = []

    def fake_type_text(page, mark_id, query, submit=False):
        fill_calls.append(mark_id)
        if mark_id == dropdown.mark_id:
            raise Exception("Element is not an <input>, <textarea> or [contenteditable] element")

    monkeypatch.setattr(recipes.actions, "type_text", fake_type_text)
    monkeypatch.setattr(recipes.actions, "navigate", lambda page, url: None)

    class FakePage:
        url = "https://amazon.com/s?k=x"

        def wait_for_load_state(self, *a, **k):
            pass

        def wait_for_timeout(self, *a):
            pass

    monkeypatch.setattr(recipes.controller, "run", lambda fn, timeout=None: fn(FakePage()))

    result = recipes.site_search("https://amazon.com", "mechanical keyboards")
    # textbox/searchbox marks are tried before any combobox, so the dropdown is never even attempted here.
    assert fill_calls == [real_box.mark_id]
    assert result is not None


def test_site_search_returns_none_when_no_candidate_fillable(monkeypatch):
    from charlie.browser import recipes

    dropdown = Mark(mark_id=1, role="combobox", name="Search in", ref="e1")
    monkeypatch.setattr(recipes, "_observe", lambda page: [dropdown])
    monkeypatch.setattr(recipes.actions, "navigate", lambda page, url: None)

    def fake_type_text(page, mark_id, query, submit=False):
        raise Exception("not fillable")

    monkeypatch.setattr(recipes.actions, "type_text", fake_type_text)
    monkeypatch.setattr(recipes.controller, "run", lambda fn, timeout=None: fn(object()))

    assert recipes.site_search("https://amazon.com", "mechanical keyboards") is None


# --- stealth.retry_blocked: Patchright needs Proactor, swap must restore the prior policy -----

def test_retry_blocked_restores_event_loop_policy(monkeypatch):
    import asyncio

    from charlie.browser import stealth

    class FakeResp:
        status = 200

        def get_all_text(self, ignore_tags=()):
            return "some page text"

    class FakeStealthyFetcher:
        @staticmethod
        def fetch(url, **kwargs):
            assert isinstance(asyncio.get_event_loop_policy(), asyncio.WindowsProactorEventLoopPolicy)
            return FakeResp()

    fake_module = type("mod", (), {"StealthyFetcher": FakeStealthyFetcher})
    monkeypatch.setitem(__import__("sys").modules, "scrapling.fetchers", fake_module)

    prior_policy = asyncio.get_event_loop_policy()
    result = stealth.retry_blocked("https://example.com")
    assert asyncio.get_event_loop_policy() is prior_policy
    assert result is not None and result.answer == "some page text"

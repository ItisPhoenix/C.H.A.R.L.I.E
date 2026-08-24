import asyncio
import sys
import time
from contextlib import contextmanager
from types import ModuleType

import pytest

if "scrapling" not in sys.modules:
    _m_scrapling = ModuleType("scrapling")
    _m_fetchers = ModuleType("scrapling.fetchers")

    class _Fetcher:
        @staticmethod
        def get(*args, **kwargs):
            raise NotImplementedError

    _m_fetchers.Fetcher = _Fetcher
    _m_scrapling.fetchers = _m_fetchers
    sys.modules["scrapling"] = _m_scrapling
    sys.modules["scrapling.fetchers"] = _m_fetchers

from charlie.browser import intent, recipes, session, task
from charlie.browser.agent import run_task
from charlie.browser.observation import Mark, is_blocked, parse_snapshot, rank_and_cap
from charlie.browser.recipes import BrowserResult


@contextmanager
def _local_browser_page():
    from playwright.sync_api import sync_playwright

    prior_policy = asyncio.get_event_loop_policy()
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                yield browser.new_page()
            finally:
                browser.close()
    finally:
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(prior_policy)


@pytest.fixture(autouse=True)
def _reset_session():
    session.reset_session()
    session._task_cache.clear()
    session.clear_verified_state()
    yield
    session.reset_session()
    session._task_cache.clear()
    session.clear_verified_state()


def test_youtube_duration_parser_accepts_minute_and_hour_formats():
    assert recipes._duration_text_to_seconds("05:23") == 323
    assert recipes._duration_text_to_seconds("1:02:03") == 3723
    assert recipes._duration_text_to_seconds("12 minutes, 4 seconds") == 724


def test_media_result_candidates_use_rendered_duration_and_query_relevance():
    class Link:
        def __init__(self, href, text):
            self.href = href
            self.text = text

        def is_visible(self):
            return True

        def get_attribute(self, name):
            return self.href if name == "href" else None

        def inner_text(self, timeout=None):
            return self.text

    class Locator:
        def __init__(self, items):
            self.items = items

        def count(self):
            return len(self.items)

        def nth(self, index):
            return self.items[index]

    class Page:
        url = "https://media.example/search?q=asyncio"

        def get_by_role(self, role, **kwargs):
            if role == "link":
                return Locator(
                    [
                        Link("/long", "24:59 Asyncio in Python - Full Tutorial"),
                        Link("/short", "02:00 Asyncio quick tip"),
                    ]
                )
            return Locator([])

    candidates = recipes.media_result_candidates(
        Page(),
        "Python asyncio tutorial",
        constraints=(intent.Constraint("duration", "gt", "5 minutes", "MINUTES"),),
    )
    assert [(item["title"], item["duration"]) for item in candidates] == [
        ("24:59 Asyncio in Python - Full Tutorial", 1499),
    ]


def test_media_result_candidates_have_no_implicit_duration_floor():
    class Link:
        def __init__(self, href, text):
            self.href, self.text = href, text

        def is_visible(self):
            return True

        def get_attribute(self, name):
            return self.href if name == "href" else None

        def inner_text(self, timeout=None):
            return self.text

    class Locator:
        def __init__(self, items):
            self.items = items

        def count(self):
            return len(self.items)

        def nth(self, index):
            return self.items[index]

    class Page:
        url = "https://media.example/results"

        def get_by_role(self, role, **kwargs):
            return Locator(
                [Link("/short", "02:00 Short trailer"), Link("/unknown", "Relevant video")]
                if role == "link"
                else []
            )

    candidates = recipes.media_result_candidates(Page(), "relevant video", constraints=())
    assert {item["url"] for item in candidates} == {
        "https://media.example/short",
        "https://media.example/unknown",
    }


def test_media_duration_constraints_do_not_reclassify_media_as_product_filter():
    under = intent.parse_browser_intent("Open a relevant video under 10 minutes.")
    exact = intent.parse_browser_intent("Play a 2 minute trailer.")

    assert under.operation == "MEDIA"
    assert {(item.attribute, item.operator, item.value) for item in under.constraints} == {
        ("duration", "lte", "10 MINUTES")
    }
    assert exact.operation == "MEDIA"
    assert {(item.attribute, item.operator, item.value) for item in exact.constraints} == {
        ("duration", "eq", "2 MINUTE")
    }


@pytest.mark.parametrize("tag", ["video", "audio"])
def test_local_html_media_actions_use_html_media_state(tag):
    from charlie.browser import actions

    with _local_browser_page() as page:
        page.set_content(f"<{tag} aria-label='Primary media'></{tag}>")
        page.eval_on_selector(
            tag,
            """element => {
                let paused = true;
                let currentTime = 12;
                Object.defineProperties(element, {
                    paused: {get: () => paused},
                    currentTime: {get: () => currentTime, set: value => currentTime = value},
                    duration: {get: () => 120},
                });
                element.play = async () => { paused = false; };
                element.pause = () => { paused = true; };
            }""",
        )

        assert actions.media_player_action(page, "play")["verified"]
        assert actions.media_player_action(page, "pause")["verified"]
        seek = actions.media_player_action(page, "seek", value=10)
        assert seek["verified"] and seek["after"]["currentTime"] == pytest.approx(22, abs=1)
        assert actions.media_player_action(page, "mute")["verified"]
        assert actions.media_player_action(page, "unmute")["verified"]
        page.eval_on_selector(
            tag,
            "element => { element.play = async () => { throw new DOMException('blocked', 'NotAllowedError'); }; }",
        )
        rejected = actions.media_player_action(page, "play")
        assert not rejected["verified"] and rejected["reason"] == "NotAllowedError"


def test_local_html_media_selection_fails_ambiguous_and_prefers_playing_element():
    from charlie.browser import actions

    with _local_browser_page() as page:
        page.set_content("<video></video><video></video>")
        page.locator("video").evaluate_all(
            """elements => elements.forEach(element => Object.defineProperties(element, {
                paused: {get: () => element.dataset.playing !== 'true'},
                currentTime: {get: () => 0}, duration: {get: () => 120}
            }))"""
        )
        assert actions.media_player_state(page)["ambiguous"] is True
        page.locator("video").nth(1).evaluate("element => { element.dataset.playing = 'true'; }")
        active = actions.media_player_state(page)
        assert active["media"] is True and active["index"] == 1 and active["ambiguous"] is False


@pytest.mark.parametrize(
    ("html", "found"),
    [
        ("<input role='searchbox'>", True),
        ("<input placeholder='Search products'>", True),
        ("<form role='search'><input><button>Search</button></form>", True),
        (
            "<label>Email <input type='email'></label>"
            "<label>Password <input type='password'></label><button>Login</button>",
            False,
        ),
    ],
)
def test_local_html_search_discovery_cross_dom_shapes(html, found):
    with _local_browser_page() as page:
        page.set_content(html)
        assert (recipes.discover_search_control(page) is not None) is found
@pytest.mark.asyncio
async def test_resolve_current_media_open_never_falls_to_tier3(monkeypatch):
    from charlie.browser import controller

    session.record_navigation("https://media.example/results?q=Python+asyncio+tutorial")
    monkeypatch.setattr(
        controller,
        "run",
        lambda fn, timeout=None: "https://media.example/results?q=Python+asyncio+tutorial",
    )
    calls = {"open": 0}

    def fake_open(site_url, query, parsed=None):
        calls["open"] += 1
        return BrowserResult(url="https://media.example/item/abc", success=True)

    monkeypatch.setattr(task.recipes, "media_request", fake_open)

    async def fail_tier3(*args, **kwargs):
        raise AssertionError("recognized YouTube open must not reach tier 3")

    monkeypatch.setattr(task.agent, "run_task", fail_tier3)
    result = await task.resolve(
        "Play a relevant normal video longer than 5 minutes on media.example.",
        lambda prompt: "",
    )

    assert result.success is True
    assert calls["open"] == 1


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
        ("textbox", "Search"),
        ("link", "First Result"),
        ("button", "More actions"),
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


def test_is_blocked_on_generic_interstitial_markup():
    markup = '<script>triggerInterstitialChallenge(); fetch("/_sec/verify?provider=interstitial")</script>'
    assert is_blocked([], markup) is True


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


def test_is_search_intent_separates_results_search_from_play():
    assert intent.is_search_intent("Search YouTube for Python asyncio tutorial") is True
    assert intent.is_search_intent("Play a Python asyncio tutorial on YouTube") is False


def test_parse_site_intent_removes_command_words_and_site_name():
    assert intent.parse_site_intent("search for Ada Lovelace on wikipedia", "wikipedia").query == "Ada Lovelace"
    assert intent.parse_site_intent("search for lo-fi music on youtube", "youtube").query == "lo-fi music"
    assert intent.parse_site_intent("open youtube and search for synthwave", "youtube").query == "synthwave"


@pytest.mark.parametrize(
    ("task_text", "site", "query"),
    [
        ("search Wikipedia for Alan Turing", "wikipedia", "Alan Turing"),
        ("find Wikipedia for Apollo 11", "wikipedia", "Apollo 11"),
        ("look up Alan Turing on Wikipedia", "wikipedia", "Alan Turing"),
        ("search YouTube for Python asyncio tutorial", "youtube", "Python asyncio tutorial"),
        ("search Amazon for laptops", "amazon", "laptops"),
        ("search Amazon India for laptops", "amazon", "laptops"),
        ("search Flipkart for laptops", "flipkart", "laptops"),
    ],
)
def test_parse_site_intent_supports_site_first_and_site_last_forms(task_text, site, query):
    assert intent.parse_site_intent(task_text, site).query == query


# --- agent (tier 3) ----------------------------------------------------------


def test_parse_action_accepts_common_structured_variants():
    from charlie.browser.agent import _parse_action

    assert _parse_action("Action: CLICK 2").kind == "click"
    assert _parse_action('```json\n{"action":"TYPE","mark_id":3,"text":"hello","submit":true}\n```').submit is True
    assert _parse_action('{"action":"DONE","url":"https://x.com","answer":"ready"}').answer == "ready"
    assert _parse_action("BACK").kind == "back"


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
    assert result == BrowserResult(url="https://x.com", answer="found it", success=True, verification="agent-confirmed")


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
        return 'URL: x\nTITLE: t\n[1] link "a"', [], "verify you are human"

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
async def test_run_task_blocks_off_site_navigation_for_current_site_continuation(monkeypatch):
    from charlie.browser import controller

    session.record_navigation("https://www.amazon.in/s?k=laptops")
    monkeypatch.setattr(controller, "run", lambda fn, timeout=None: _fake_observation())

    async def complete(prompt):
        return "NAVIGATE https://www.google.com/search?q=laptops"

    result = await run_task("Filter these results on Amazon.", complete, max_steps=1, deadline_s=100)
    assert result.verification == "site-containment"


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
async def test_resolve_does_not_cache_navigation_tasks(monkeypatch):
    cached = BrowserResult(url="https://cached.example")
    session.cache_set("search a page for Ada", cached)
    calls = {"n": 0}

    async def fake_agent_run_task(*a, **k):
        calls["n"] += 1
        return BrowserResult(answer="fresh navigation")

    monkeypatch.setattr(task.agent, "run_task", fake_agent_run_task)
    result = await task.resolve("search a page for Ada", lambda prompt: None)
    assert result.answer == "fresh navigation"
    assert calls["n"] == 1


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
async def test_resolve_routes_youtube_search_to_results_recipe(monkeypatch):
    calls = []

    def fake_site_search(site_url, query, site_name=None):
        calls.append((site_url, query, site_name))
        return BrowserResult(url="https://www.youtube.com/results?search_query=asyncio", success=True)

    monkeypatch.setattr(task.recipes, "site_search", fake_site_search)

    async def complete(prompt):
        return ""

    result = await task.resolve("Search YouTube for Python asyncio tutorial", complete)
    assert result.success is True
    assert calls == [("https://youtube.com", "Python asyncio tutorial", "youtube")]


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
async def test_browser_acquire_returns_canonical_capability_lease():
    from charlie import resource_locks

    resource_locks._owners.pop("browser", None)
    lease = await task._acquire_browser("gate-turn", max_wait_s=0.1)

    assert isinstance(lease, resource_locks.CapabilityLease)
    assert resource_locks.current_owner("browser") == "gate-turn"
    await lease.release()
    assert resource_locks.current_owner("browser") is None


def test_idle_check_does_not_shutdown_during_active_browser_task(monkeypatch):
    from charlie.browser import controller

    submitted = []
    original_page = controller._page
    original_leases = controller._active_task_leases
    original_operations = controller._active_operations
    original_executor = controller.BROWSER_EXECUTOR
    try:
        controller._page = object()
        controller._active_task_leases = 0
        controller._active_operations = 0
        controller._last_used_at = 0.0
        monkeypatch.setattr(controller.config, "browser_idle_timeout_s", 0.0)
        controller.BROWSER_EXECUTOR = type(
            "Executor", (), {"submit": lambda self, *args, **kwargs: submitted.append(args)}
        )()
        controller.acquire_task_lease()
        controller._idle_check()
        assert submitted == []
    finally:
        controller._page = None
        controller.release_task_lease()
        controller._page = original_page
        controller._active_task_leases = original_leases
        controller._active_operations = original_operations
        controller.BROWSER_EXECUTOR = original_executor


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
async def test_resolve_reports_busy_when_lock_wait_exceeds_deadline(monkeypatch):
    from charlie import resource_locks

    resource_locks._owners.pop("browser", None)
    monkeypatch.setattr(task, "_LOCK_POLL_INTERVAL_S", 0.01)
    resource_locks.acquire("browser", "someone-else")  # never released this test

    async def complete(prompt):
        return ""

    result = await task.resolve("find a good recipe for soup", complete, deadline_s=0.03)

    assert result.answer == "The browser is busy with another task. Try again shortly."
    resource_locks.release("browser", "someone-else")


# --- tools.py gate -----------------------------------------------------------


def test_browser_tools_disabled_message(monkeypatch):
    from charlie import tools

    monkeypatch.setattr(tools.config, "browser_enabled", False)
    assert tools.browser_read("https://example.com") == tools._BROWSER_DISABLED_MSG


def test_browser_task_direct_call_errors():
    from charlie import tools

    assert "Brain.browser_task" in tools.browser_task("anything")


@pytest.mark.asyncio
async def test_brain_browser_task_emits_task_scoped_presentation(monkeypatch):
    from charlie import recovery
    from charlie.config import Config
    from charlie.core import Brain

    emitted = []

    class Bus:
        async def emit(self, event_type, payload, meta=None):
            emitted.append((event_type, payload, meta))

    async def fake_resolve(*args, **kwargs):
        return BrowserResult(
            url="https://shop.example/search?q=laptops",
            answer="Verified laptop results.",
            success=True,
            verification="content-links",
            site="shop.example",
            query="laptops",
        )

    monkeypatch.setattr(recovery, "_event_bus", Bus())
    monkeypatch.setattr(task, "resolve", fake_resolve)
    brain = Brain(Config(llm_url="http://localhost", llm_key="x", llm_model="dummy", browser_enabled=True))

    result = await brain.browser_task(
        "Search shop.example for laptops.",
        platform="web",
        task_id="turn-1",
        session_id="session-1",
    )

    assert result == "Verified laptop results."
    by_type = {event_type: (payload, meta) for event_type, payload, meta in emitted}
    assert {"browser_task_started", "browser_task_done", "presentation_intent"} <= by_type.keys()
    assert all(by_type[event_type][1].task_id == "turn-1" for event_type in by_type)
    assert by_type["presentation_intent"][0]["session_id"] == "session-1"


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


def test_match_open_app_bare_website_defers_to_verified_browser():
    from charlie import router

    result = router.match_open_app("open youtube")
    assert result is not None
    apps, commands, leftover = result
    assert apps == []
    assert commands == []
    assert leftover == "open youtube"


# --- router.py: deterministic "<verb> ... on <site>" browser-task fast-path -----


def test_match_browser_task_matches_play_on_site():
    from charlie import router

    assert router.match_browser_task("play DL91 on youtube") == "play DL91 on youtube"
    assert router.match_browser_task("Play DL91 on YouTube") == "Play DL91 on YouTube"


def test_match_browser_task_matches_search_on_site():
    from charlie import router

    assert router.match_browser_task("search mechanical keyboards on amazon") is not None


@pytest.mark.parametrize(
    "utterance",
    [
        "Search Amazon India for laptops.",
        "Search Flipkart for laptops.",
        "Search YouTube for Python asyncio tutorial.",
        "Search Wikipedia for Alan Turing.",
        "Search docs.python.org for asyncio.",
    ],
)
def test_match_browser_task_matches_generic_site_first_search(utterance):
    from charlie import router

    assert router.match_browser_task(utterance) == utterance


def test_match_browser_task_ignores_unknown_site():
    from charlie import router

    assert router.match_browser_task("search for cats on my desktop") is None


def test_match_browser_task_ignores_non_matching_verb():
    from charlie import router

    assert router.match_browser_task("what is the weather on my phone") is None


def test_browser_continuation_matches_github_repository_code_lookup():
    from charlie import router

    url = "https://github.com/ItisPhoenix/C.H.A.R.L.I.E"
    session.reset_session()
    session.record_observation(url, page_type="repository", capabilities=["repository"])
    assert (
        router.match_browser_continuation(
            "Find where TaskJournal is implemented and tell me which file contains it.", url
        )
        is not None
    )


def test_browser_continuation_matches_explicit_repository_search():
    from charlie import router

    url = "https://github.com/ItisPhoenix/C.H.A.R.L.I.E"
    assert router.match_browser_continuation("Search this repository for CapabilityLease.", url) is not None
    assert router.match_browser_continuation("Open the most relevant result.", url) is not None


def test_browser_continuation_does_not_hijack_unrelated_requests():
    from charlie import router

    url = "https://github.com/ItisPhoenix/C.H.A.R.L.I.E"
    assert router.match_browser_continuation("Research the latest AI news.", url) is None
    assert router.match_browser_continuation("Find my running apps.", url) is None
    assert router.match_browser_continuation("What is TaskJournal?", url) is None


def test_browser_continuation_requires_active_page():
    from charlie import router

    assert router.match_browser_continuation("Search this repository for CapabilityLease.", None) is None


def test_github_repository_context_extracts_arbitrary_owner_and_repo():
    from charlie.browser.recipes import github_repository_context

    assert github_repository_context("https://github.com/acme/widget/tree/main") == ("acme", "widget")
    assert github_repository_context("https://gitlab.com/acme/widget") is None


def test_repository_search_query_extracts_symbols():
    from charlie.browser.recipes import repository_search_query

    assert repository_search_query("Find where TaskJournal is implemented") == "TaskJournal"
    assert repository_search_query("Search this repository for CapabilityLease") == "CapabilityLease"


def test_repository_recipe_requires_github_repository_context():
    from charlie.browser.recipes import current_repository_search

    assert current_repository_search("Search this repository for CapabilityLease", None) is None
    assert current_repository_search("Search this repository for CapabilityLease", "https://example.com/page") is None


def test_github_auth_wall_is_detected_without_claiming_success():
    from charlie.browser.recipes import _github_auth_wall

    assert _github_auth_wall("You must be signed in to search code", "https://github.com/login") is True
    assert _github_auth_wall("Public repository files", "https://github.com/acme/widget") is False


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
    monkeypatch.setattr(recipes, "_wait_for_search_results", lambda page, before_url, before_links: True)
    monkeypatch.setattr(recipes, "_content_links", lambda page: ["Mechanical keyboards"])
    monkeypatch.setattr(recipes, "_content_text", lambda page: "Verified search result content " * 3)

    class FakePage:
        url = "https://amazon.com/s?k=x"

        def wait_for_load_state(self, *a, **k):
            pass

    monkeypatch.setattr(recipes.controller, "run", lambda fn, timeout=None: fn(FakePage()))

    result = recipes.site_search("https://amazon.com", "mechanical keyboards")
    # textbox/searchbox marks are tried before any combobox, so the dropdown is never even attempted here.
    assert fill_calls == [real_box.mark_id]
    assert result is not None


def test_site_search_reports_site_state_when_no_candidate_fillable(monkeypatch):
    from charlie.browser import recipes

    dropdown = Mark(mark_id=1, role="combobox", name="Search in", ref="e1")
    monkeypatch.setattr(recipes, "_observe", lambda page: [dropdown])
    monkeypatch.setattr(recipes.actions, "navigate", lambda page, url: None)

    def fake_type_text(page, mark_id, query, submit=False):
        raise Exception("not fillable")

    monkeypatch.setattr(recipes.actions, "type_text", fake_type_text)
    class FakePage:
        url = "https://amazon.com/"

    monkeypatch.setattr(recipes.controller, "run", lambda fn, timeout=None: fn(FakePage()))

    result = recipes.site_search("https://amazon.com", "mechanical keyboards")
    assert result is not None
    assert result.verification == "site-state-blocked"


def test_site_search_classifies_rendered_challenge_as_site_state_blocked(monkeypatch):
    from charlie.browser import recipes

    monkeypatch.setattr(recipes.actions, "navigate", lambda page, url: None)
    monkeypatch.setattr(recipes, "_observe", lambda page: [])
    monkeypatch.setattr(recipes, "submit_search", lambda page, query: True)
    monkeypatch.setattr(recipes, "_link_count", lambda page: 0)
    monkeypatch.setattr(recipes, "_wait_for_search_results", lambda *args: False)
    monkeypatch.setattr(recipes, "_content_text", lambda page: "Please verify you are human to continue")

    class FakePage:
        url = "https://shop.example/search?q=laptops"

        def wait_for_load_state(self, *args, **kwargs):
            return None

    monkeypatch.setattr(recipes.controller, "run", lambda fn, timeout=None: fn(FakePage()))

    result = recipes.site_search("https://shop.example", "laptops")

    assert result is not None
    assert result.success is False
    assert result.verification == "site-state-blocked"
    assert "blocked" in result.answer.casefold()


def test_media_player_commands_are_deterministic_and_narrow():
    assert recipes.media_player_command("Pause the media.") == "pause"
    assert recipes.media_player_command("Play the media.") == "play"
    assert recipes.media_player_command("Skip forward 10 seconds.") == "seek"
    assert recipes.media_player_command("Mute the media.") == "mute"
    assert recipes.media_player_command("Unmute the media.") == "unmute"
    assert recipes.media_player_command("Open the media.") is None


def _search_evidence(**overrides):
    values = {
        "url": "https://example.test/",
        "query": None,
        "search_value": "",
        "signature": "before",
        "result_urls": (),
    }
    values.update(overrides)
    return values


@pytest.mark.parametrize(
    "after",
    [
        _search_evidence(
            url="https://example.test/search?q=asyncio",
            query="asyncio",
            signature="traditional-results",
            result_urls=("https://example.test/a", "https://example.test/b"),
        ),
        _search_evidence(
            url="https://example.test/results?query=asyncio",
            query="asyncio",
            signature="spa-url-results",
            result_urls=("https://example.test/a", "https://example.test/b"),
        ),
        _search_evidence(
            search_value="asyncio",
            signature="spa-dom-results",
            result_urls=("https://example.test/a", "https://example.test/b"),
        ),
    ],
)
def test_generic_search_postcondition_accepts_navigation_and_spa_evidence(after):
    before = _search_evidence()

    assert recipes._search_postcondition_satisfied(before, after, "asyncio") is True


def test_generic_search_postcondition_rejects_unchanged_page():
    before = _search_evidence()

    assert recipes._search_postcondition_satisfied(before, before, "asyncio") is False


def test_generic_search_postcondition_accepts_one_result_and_sparse_rendered_evidence():
    before = _search_evidence()
    after = _search_evidence(
        url="https://example.test/search?q=asyncio",
        query="asyncio",
        signature="one-result",
        result_urls=("https://example.test/a",),
    )
    assert recipes._search_postcondition_satisfied(before, after, "asyncio") is True


def test_generic_search_postcondition_rejects_query_change_without_result_evidence():
    before = _search_evidence()
    after = _search_evidence(
        url="https://example.test/search?q=asyncio",
        query="asyncio",
        signature="blank",
    )
    assert recipes._search_postcondition_satisfied(before, after, "asyncio") is False


def test_generic_search_postcondition_rejects_home_links_without_query_relevance():
    before = _search_evidence()
    after = _search_evidence(
        url="https://shop.example/search?q=laptops",
        query="laptops",
        signature="homepage-navigation",
        result_urls=("https://shop.example/login", "https://shop.example/cart"),
    )
    after.update(
        content="Explore Plus Login Become a Seller Cart Flights",
        result_titles=("Explore Plus", "Login", "Cart", "Flights"),
        result_like_count=4,
        page_type="search_results",
        marks=(("link", "Explore Plus"), ("link", "Cart")),
    )
    assert recipes._search_postcondition_satisfied(before, after, "laptops") is False


def test_query_relevance_rejects_generic_home_navigation():
    assert recipes._query_token_match("Explore Plus Login Cart Flights", "laptops") is False
    assert recipes._query_token_match("Laptops 16GB Ryzen", "laptops") is True


def test_generic_search_wait_accepts_lazy_result_appearance(monkeypatch):
    before = _search_evidence()
    observations = iter(
        [
            before,
            _search_evidence(search_value="asyncio", signature="loading"),
            _search_evidence(
                search_value="asyncio",
                signature="results",
                result_urls=("https://example.test/a", "https://example.test/b"),
            ),
        ]
    )

    class Page:
        def wait_for_timeout(self, _milliseconds):
            return None

    monkeypatch.setattr(recipes, "_capture_search_evidence", lambda page: next(observations))

    assert recipes._wait_for_search_results(Page(), before, "asyncio", timeout_ms=100) is True


def test_browser_intent_recognizes_contextual_media_controls():
    for utterance in ("Pause it.", "Play it.", "Resume it.", "Skip forward 10 seconds.", "Mute it."):
        assert intent.parse_browser_intent(utterance, "video.example").operation == "MEDIA"


def test_generic_media_trial_opens_next_candidate_after_duration_failure(monkeypatch):
    class Page:
        url = "https://media.example/results"

        def title(self):
            return "Candidate"

    page = Page()
    candidates = [
        {"url": "https://media.example/short", "title": "Short", "duration": None},
        {"url": "https://media.example/long", "title": "Long", "duration": None},
    ]
    states = iter(
        [
            {"media": True, "duration": 120},
            {"media": True, "duration": 900},
        ]
    )
    monkeypatch.setattr(recipes, "media_result_candidates", lambda *args, **kwargs: candidates)
    monkeypatch.setattr(
        recipes.actions,
        "navigate",
        lambda current, url: setattr(current, "url", url),
    )
    monkeypatch.setattr(
        recipes.actions,
        "back",
        lambda current: setattr(current, "url", "https://media.example/results"),
    )
    monkeypatch.setattr(recipes.actions, "media_player_state", lambda current: next(states))

    result = recipes._open_verified_media_result(
        page,
        "asyncio tutorial",
        (intent.Constraint("duration", "gt", "5 minutes", "MINUTES"),),
    )
    assert result.success is True
    assert result.url == "https://media.example/long"


def test_browser_media_continuation_requires_current_verified_media_surface():
    from charlie import router

    url = "https://video.example/watch/123"
    session.record_observation(url, page_type="media_surface", capabilities=["media_controls"])
    assert router.match_browser_media_continuation("Pause it.", url) == "Pause it."
    assert router.match_browser_media_continuation("Pause Spotify.", url) is None

    session.record_observation(url, page_type="page", capabilities=[])
    assert router.match_browser_media_continuation("Pause it.", url) is None


@pytest.mark.asyncio
async def test_active_browser_media_precedes_global_media_fastpath(monkeypatch):
    from charlie import fastpaths
    from charlie.config import Config
    from charlie.core import Brain

    url = "https://video.example/watch/123"
    session.record_observation(url, page_type="media_surface", capabilities=["media_controls"])
    brain = Brain(Config(llm_url="http://localhost", llm_key="x", llm_model="dummy", browser_enabled=True))
    browser_calls = []

    async def fake_browser(task_text, platform, **kwargs):
        browser_calls.append(task_text)
        return "Media pause verified."

    monkeypatch.setattr(brain, "_browser_task_bounded", fake_browser)
    monkeypatch.setattr(
        fastpaths,
        "match_fast_path",
        lambda _query: pytest.fail("global media fastpath must not run for active browser media"),
    )

    chunks = [chunk async for chunk in brain.chat_stream("Pause it.", platform="web")]

    assert chunks == ["Media pause verified."]
    assert browser_calls == ["Pause it."]


@pytest.mark.asyncio
async def test_unverified_browser_open_never_narrates_opened(monkeypatch):
    from charlie import recovery
    from charlie.config import Config
    from charlie.core import Brain

    opened = []
    session.record_observation("https://example.test/search", page_type="search_results")

    async def fake_resolve(*args, **kwargs):
        return BrowserResult(
            url="https://example.test/result",
            answer="The selected result could not be verified.",
            verification="result-open-unverified",
        )

    monkeypatch.setattr(recovery, "_event_bus", None)
    monkeypatch.setattr(task, "resolve", fake_resolve)
    monkeypatch.setattr("charlie.browser.actions.open_in_real_browser", lambda url: opened.append(url) or True)
    brain = Brain(Config(llm_url="http://localhost", llm_key="x", llm_model="dummy", browser_enabled=True))

    result = await brain.browser_task("Show it to me.", platform="web")

    assert result == "The selected result could not be verified."
    assert opened == []


@pytest.mark.asyncio
async def test_verified_browser_media_control_never_opens_external_browser(monkeypatch):
    from charlie import recovery
    from charlie.config import Config
    from charlie.core import Brain

    opened = []
    session.record_observation(
        "https://video.example/watch/123",
        page_type="media_surface",
        capabilities=["media_controls"],
    )

    async def fake_resolve(*args, **kwargs):
        return BrowserResult(
            url="https://video.example/watch/123",
            answer="Media play verified.",
            success=True,
            verification="media-play",
        )

    monkeypatch.setattr(recovery, "_event_bus", None)
    monkeypatch.setattr(task, "resolve", fake_resolve)
    monkeypatch.setattr("charlie.browser.actions.open_in_real_browser", lambda url: opened.append(url) or True)
    brain = Brain(Config(llm_url="http://localhost", llm_key="x", llm_model="dummy", browser_enabled=True))

    result = await brain.browser_task("Play it.", platform="web")

    assert result == "Media play verified."
    assert opened == []


def test_generic_result_open_then_back_uses_real_page_history(monkeypatch):
    page_a = "https://example.test/search?q=moon"
    page_b = "https://example.test/result-b"

    class Page:
        url = page_a

    page = Page()
    selected = {"title": "Result B", "url": page_b, "price": None}
    session.record_observation(page_a, page_type="search_results", results=[selected])
    monkeypatch.setattr(recipes.controller, "run", lambda fn, timeout=None: fn(page))
    monkeypatch.setattr(recipes, "discover_results", lambda page, limit=10: [selected])
    monkeypatch.setattr(
        recipes.actions,
        "navigate",
        lambda page, url: (setattr(page, "url", url), session.record_navigation(url)),
    )
    monkeypatch.setattr(
        recipes,
        "extract_structured_facts",
        lambda page: {"title": "Result B", "text": "Result B details", "price": None},
    )

    opened = recipes.apply_current_page_intent(
        "Open Result B.", intent.parse_browser_intent("Open Result B.", "example.test")
    )
    assert opened is not None and opened.success is True
    assert opened.verification == "result-opened"

    monkeypatch.setattr(
        recipes.actions,
        "back",
        lambda page: (setattr(page, "url", page_a), session.record_navigation(page_a)),
    )
    returned = recipes.apply_current_page_intent("Go back.", intent.parse_browser_intent("Go back.", "example.test"))
    assert returned is not None and returned.success is True
    assert returned.url == page_a
    assert session.get_session().current_url == page_a


@pytest.mark.asyncio
async def test_search_this_site_stays_on_current_verified_domain(monkeypatch):
    current = "https://docs.example.test/guide/"
    session.record_observation(current, page_type="page", capabilities=[])
    searched = []

    async def complete(_prompt):
        raise AssertionError("agent fallback must not replace active-site context")

    monkeypatch.setattr(task.controller, "run", lambda fn, timeout=None: current)

    def fake_search(site_url, query, site_name=None):
        searched.append((site_url, query, site_name))
        return BrowserResult(url=current, answer="Search unavailable.", verification="site-state-blocked")

    monkeypatch.setattr(task.recipes, "site_search", fake_search)

    result = await task.resolve("Search this site for asyncio.", complete)

    assert result.verification == "site-state-blocked"
    assert searched == [("https://docs.example.test", "asyncio", None)]


@pytest.mark.asyncio
async def test_explicit_unseen_site_open_uses_verified_charlie_browser_navigation(monkeypatch):
    opened = []

    async def complete(_prompt):
        raise AssertionError("agent fallback must not replace explicit site navigation")

    def fake_open(site_url):
        opened.append(site_url)
        return BrowserResult(
            url="https://docs.example.test/",
            answer="Opened Documentation.",
            success=True,
            verification="page-opened",
        )

    monkeypatch.setattr(task.recipes, "open_site", fake_open)
    monkeypatch.setattr(task.recipes, "site_search", lambda *args: pytest.fail("open must not become search"))

    result = await task.resolve("Open docs.example.test.", complete)

    assert result.success is True
    assert result.verification == "page-opened"
    assert opened == ["https://docs.example.test"]


def test_open_site_accepts_docs_style_rendered_page_without_main(monkeypatch):
    class Page:
        url = "https://docs.python.org/3/"

        def title(self):
            return "Python 3 Documentation"

    page = Page()
    monkeypatch.setattr(recipes, "resolve_website_url", lambda value: "https://docs.python.org/3/")
    monkeypatch.setattr(recipes.actions, "navigate", lambda current, url: setattr(current, "url", url))
    monkeypatch.setattr(recipes, "_observe", lambda current: [Mark(1, "heading", "Python Documentation", "e1")])
    monkeypatch.setattr(
        recipes,
        "_content_text",
        lambda current: "Welcome to the Python documentation and library reference.",
    )
    monkeypatch.setattr(recipes.controller, "run", lambda fn, timeout=None: fn(page))

    result = recipes.open_site("docs.python.org")
    assert result is not None and result.success is True
    assert result.verification == "page-opened"


def test_open_site_rejects_blank_same_origin_document(monkeypatch):
    class Page:
        url = "https://docs.python.org/"

        def title(self):
            return ""

    monkeypatch.setattr(recipes, "resolve_website_url", lambda value: "https://docs.python.org/")
    monkeypatch.setattr(recipes.actions, "navigate", lambda current, url: None)
    monkeypatch.setattr(recipes, "_observe", lambda current: [])
    monkeypatch.setattr(recipes, "_content_text", lambda current: "")
    monkeypatch.setattr(recipes.controller, "run", lambda fn, timeout=None: fn(Page()))

    result = recipes.open_site("docs.python.org")
    assert result is not None and result.success is False
    assert result.verification == "page-open-unverified"


def test_bare_website_open_defers_to_charlie_browser():
    from charlie import router

    apps, commands, leftover = router.match_open_app("Open docs.example.test.")

    assert apps == []
    assert commands == []
    assert leftover == "Open docs.example.test."


@pytest.mark.asyncio
async def test_authoritative_deterministic_failure_skips_tier3(monkeypatch):
    session.record_navigation("https://example.test/results")
    monkeypatch.setattr(task.controller, "run", lambda fn, timeout=None: "https://example.test/results")
    monkeypatch.setattr(
        task.recipes,
        "apply_current_page_intent",
        lambda *args: BrowserResult(answer="Search did not settle.", verification="search-not-settled"),
    )

    async def fail_tier3(*args, **kwargs):
        raise AssertionError("authoritative deterministic failure must not invoke Tier 3")

    monkeypatch.setattr(task.agent, "run_task", fail_tier3)
    result = await task.resolve("Open the result.", lambda _prompt: "")
    assert result.verification == "search-not-settled"


def test_missing_active_site_search_control_is_explicit_site_state_block(monkeypatch):
    monkeypatch.setattr(recipes.actions, "navigate", lambda page, url: None)
    monkeypatch.setattr(recipes, "_observe", lambda page: [])
    monkeypatch.setattr(recipes, "submit_search", lambda page, query: False)

    class Page:
        url = "https://docs.example.test/"

    monkeypatch.setattr(recipes.controller, "run", lambda fn, timeout=None: fn(Page()))

    result = recipes.site_search("https://docs.example.test", "asyncio")

    assert result is not None
    assert result.verification == "site-state-blocked"
    assert "SITE_STATE_BLOCKED" in result.answer


def test_missing_rendered_filter_is_explicit_site_state_block(monkeypatch):
    class Page:
        url = "https://shop.example.test/search?q=laptops"

    session.record_observation(Page.url, page_type="search_results")
    monkeypatch.setattr(recipes.controller, "run", lambda fn, timeout=None: fn(Page()))
    monkeypatch.setattr(recipes, "discover_results", lambda page, limit=10: [])
    monkeypatch.setattr(recipes, "apply_constraint", lambda page, constraint: False)
    parsed = intent.parse_browser_intent("Filter these results under $800.", "shop.example.test")

    result = recipes.apply_current_page_intent("Filter these results under $800.", parsed)

    assert result is not None
    assert result.verification == "site-state-blocked"
    assert "SITE_STATE_BLOCKED" in result.answer


def test_unexplained_search_failure_is_not_site_state_blocked(monkeypatch):
    monkeypatch.setattr(recipes.actions, "navigate", lambda page, url: None)
    monkeypatch.setattr(recipes, "_observe", lambda page: [])
    monkeypatch.setattr(recipes, "submit_search", lambda page, query: True)
    monkeypatch.setattr(recipes, "_capture_search_evidence", lambda page: _search_evidence())
    monkeypatch.setattr(recipes, "_wait_for_search_results", lambda *args, **kwargs: False)
    monkeypatch.setattr(recipes, "_content_text", lambda page: "ordinary page with no positive blocker evidence")

    class Page:
        url = "https://example.test/"

        def wait_for_load_state(self, *args, **kwargs):
            return None

        def content(self):
            return "<main>ordinary page</main>"

    monkeypatch.setattr(recipes.controller, "run", lambda fn, timeout=None: fn(Page()))

    result = recipes.site_search("https://example.test", "asyncio")

    assert result is not None
    assert result.verification == "search-not-settled"


def test_current_page_question_is_browser_continuation():
    from charlie import router

    current = "https://example.test/result"
    session.record_observation(current, page_type="page")

    assert router.match_browser_continuation("What page am I on?", current) is not None
    assert intent.parse_browser_intent("What page am I on?", "example.test").operation == "CURRENT_PAGE_FACT"


def test_media_player_state_reads_actual_media_element():
    class Page:
        def evaluate(self, script):
            assert "document.querySelectorAll('video, audio')" in script
            return {
                "media": True,
                "paused": True,
                "currentTime": 12.0,
                "duration": 600.0,
                "muted": False,
            }

    from charlie.browser import actions

    assert actions.media_player_state(Page()) == {
        "media": True,
        "paused": True,
        "currentTime": 12.0,
        "duration": 600.0,
        "muted": False,
    }


def test_navigation_timeout_retries_only_until_document_commit():
    from charlie.browser import actions

    class Page:
        url = "about:blank"

        def __init__(self):
            self.calls = []

        def goto(self, url, wait_until, timeout):
            self.calls.append((url, wait_until, timeout))
            if wait_until == "domcontentloaded":
                raise TimeoutError("DOM load remained busy")
            self.url = url

    page = Page()

    actions.navigate(page, "https://dynamic.example/search")

    assert [call[1] for call in page.calls] == ["domcontentloaded", "commit"]
    assert session.get_session().current_url == "https://dynamic.example/search"
# --- Generic rendered product facts -----------------------------------------


def test_rendered_facts_keep_arbitrary_labels_and_numeric_values():
    facts = recipes.extract_rendered_facts(
        "16 GB RAM, 512 GB SSD\nDisplay Type: OLED\nPrice: ₹79,990"
    )
    by_key = {fact["normalized_key"]: fact["value"] for fact in facts}
    assert by_key["ram"] == "16 GB"
    assert by_key["storage"] == "512 GB"
    assert by_key["display type"] == "OLED"
    assert by_key["price"] == "₹79,990"


def test_discover_results_deduplicates_product_links_and_reads_rendered_facts():
    class Link:
        def __init__(self, href, title):
            self.href = href
            self.title = title

        def is_visible(self):
            return True

        def get_attribute(self, name):
            return self.href if name == "href" else None

        def inner_text(self, timeout=None):
            return self.title

    class Locator:
        def __init__(self, items):
            self.items = items

        def count(self):
            return len(self.items)

        def nth(self, index):
            return self.items[index]

    class Page:
        url = "https://shop.example/search?q=devices"

        def get_by_role(self, role, name=None):
            assert role == "link"
            return Locator(
                [
                    Link("/item/one", "Device Alpha\n16 GB RAM\n₹79,990"),
                    Link("/item/one", "Device Alpha\n16 GB RAM\n₹79,990"),
                    Link("/item/two", "Device Beta\n8 GB RAM\n₹54,990"),
                ]
            )

    products = recipes.discover_results(Page(), require_price=True)
    assert [(item["url"], item["price"]) for item in products] == [
        ("https://shop.example/item/one", 79990),
        ("https://shop.example/item/two", 54990),
    ]
    assert products[0]["attributes"]["ram"] == "16 GB"
    assert products[1]["attributes"]["ram"] == "8 GB"


def test_filter_verification_requires_all_visible_sampled_constraints():
    products = [
        {"attributes": {"ram": "16 GB"}, "price": 70000},
        {"attributes": {"ram": "16 GB"}, "price": 79999},
        {"attributes": {"ram": "16 GB"}, "price": 60000},
    ]
    constraints = (
        recipes.Constraint("ram", "eq", "16 GB"),
        recipes.Constraint("price", "lte", "₹80,000"),
    )
    assert recipes.verify_constraints(products, constraints)[0] is True
    products[1]["price"] = 81000
    assert recipes.verify_constraints(products, constraints)[0] is False


@pytest.mark.asyncio
async def test_resolve_routes_generic_result_continuations_without_tier3(monkeypatch):
    session.record_navigation("https://shop.example/search?q=laptops")
    calls = []

    def fake_action(task_text, parsed_intent):
        calls.append(task_text)
        return BrowserResult(
            url="https://shop.example/search?q=laptops",
            answer="filtered",
            success=True,
            verification="generic-test",
        )

    monkeypatch.setattr(task.recipes, "apply_current_page_intent", fake_action)

    async def complete(prompt):
        raise AssertionError("recognized Flipkart continuation must not reach tier 3")

    result = await task.resolve("Filter these results to 16 GB RAM.", complete)
    assert result.verification == "generic-test"
    assert calls == ["Filter these results to 16 GB RAM."]


def test_router_matches_generic_result_continuation_phrases():
    from charlie import router

    current = "https://shop.example/search?q=laptops"
    assert router.match_browser_continuation("Filter these results to 16 GB RAM.", current) is not None
    assert router.match_browser_continuation("Go back to the filtered laptop results.", current) is not None


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


def test_launch_skips_windows_policy_swap_off_windows(monkeypatch):
    from charlie.browser import controller

    monkeypatch.setattr(controller.sys, "platform", "linux")
    monkeypatch.setattr(controller, "_context", None)
    monkeypatch.setattr(controller, "_page", None)

    class FakeContext:
        def add_init_script(self, script):
            pass

        def new_page(self):
            class FakePage:
                def route(self, pattern, handler):
                    pass

            return FakePage()

    class FakeChromium:
        def launch_persistent_context(self, **kwargs):
            return FakeContext()

    class FakePlaywright:
        chromium = FakeChromium()

    class FakeSyncPlaywright:
        def start(self):
            return FakePlaywright()

    fake_module = type("mod", (), {"sync_playwright": lambda: FakeSyncPlaywright()})
    monkeypatch.setitem(__import__("sys").modules, "playwright.sync_api", fake_module)

    def fail_if_called(*a, **kw):
        raise AssertionError("WindowsProactorEventLoopPolicy must not be touched off-Windows")

    monkeypatch.setattr(asyncio, "set_event_loop_policy", fail_if_called)
    controller._launch()

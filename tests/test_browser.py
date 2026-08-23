import asyncio
import sys
import time
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

    candidates = recipes.media_result_candidates(Page(), "Python asyncio tutorial", minimum_duration_s=300)
    assert [(item["title"], item["duration"]) for item in candidates] == [
        ("24:59 Asyncio in Python - Full Tutorial", 1499),
    ]
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


def test_media_player_commands_are_deterministic_and_narrow():
    assert recipes.media_player_command("Pause the media.") == "pause"
    assert recipes.media_player_command("Play the media.") == "play"
    assert recipes.media_player_command("Skip forward 10 seconds.") == "seek_forward"
    assert recipes.media_player_command("Open the media.") is None


def test_media_player_state_reads_actual_media_element():
    class Page:
        def evaluate(self, script):
            assert "document.querySelector('video')" in script
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

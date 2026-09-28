"""A2.2 canonical browser production-path and provenance contracts."""

import pytest

from charlie import core, router
from charlie.browser import recipes, task
from charlie.browser.recipes import BrowserResult
from charlie.config import Config
from charlie.research.fetch import validate_public_url
from charlie.turn_contracts import ResultEnvelope, TurnRequest


def _config() -> Config:
    return Config(
        llm_url="http://127.0.0.1:1",
        llm_key="test-key",
        llm_model="dummy",
        browser_enabled=True,
        browser_headless=True,
        browser_deadline_s=5.0,
    )


def test_compound_explicit_url_routes_to_browser_action_with_user_provenance(monkeypatch):
    decisions = []
    calls = []
    seen_decisions = []
    brain = core.Brain(_config(), on_intent_decision=decisions.append, register_panic_hotkey=False)
    original_remember = brain._remember_intent_decision

    def remember(decision):
        seen_decisions.append(decision)
        return original_remember(decision)

    monkeypatch.setattr(brain, "_remember_intent_decision", remember)

    async def fake_browser(task_text, platform, **kwargs):
        calls.append((task_text, platform, kwargs))
        return ResultEnvelope(
            request=task_text,
            task_id=kwargs.get("task_id"),
            session_id=kwargs.get("session_id"),
            turn_id=kwargs.get("turn_id"),
            capability="browser",
            operation="read",
            status="completed",
            result="CHARLIE-A22-TOKEN",
            verification={"verified": True},
            source="test",
        )

    monkeypatch.setattr(brain, "_browser_task_bounded", fake_browser)

    async def fail_llm(*_args, **_kwargs):
        raise AssertionError("explicit deterministic browser request must not call the LLM")

    monkeypatch.setattr(brain, "_stream_completion", fail_llm)
    request = TurnRequest(
        "turn-a22",
        "session-a22",
        "Open http://127.0.0.1:8765/ and read the page.",
        "web",
        task_id="task-a22",
    )

    async def run():
        return [
            chunk
            async for chunk in brain.chat_stream(
                request.input,
                turn_request=request,
            )
        ]

    import asyncio

    try:
        chunks = asyncio.run(run())
    finally:
        asyncio.run(brain.close())

    assert chunks == ["CHARLIE-A22-TOKEN"]
    assert calls and calls[0][2]["user_supplied_url"] is True
    assert seen_decisions[0].execution_policy == "action"
    assert seen_decisions[0].capabilities == ("browser",)


def test_research_loopback_policy_remains_blocked():
    with pytest.raises(ValueError, match="private or local"):
        validate_public_url("http://127.0.0.1:8765/")


@pytest.mark.asyncio
async def test_model_generated_private_browser_url_is_rejected(monkeypatch):
    monkeypatch.setattr(
        task.recipes,
        "open_site",
        lambda *_args, **_kwargs: pytest.fail("model-generated private URL reached browser navigation"),
    )

    result = await task.resolve(
        "Open http://127.0.0.1:8765/ and read the page.",
        lambda _prompt: "",
        user_supplied_url=False,
    )

    assert result.success is False
    assert result.verification == "private-url-blocked"


@pytest.mark.asyncio
async def test_explicit_user_private_browser_url_uses_deterministic_open_read(monkeypatch):
    calls = []

    def fake_open(site_url, *, read_content=False):
        calls.append((site_url, read_content))
        return BrowserResult(
            url=site_url,
            answer="CHARLIE-A22-TOKEN",
            success=True,
            verification="page-read",
            evidence={
                "requested_url": site_url,
                "observed_url": site_url,
                "content": "CHARLIE-A22-TOKEN",
            },
        )

    monkeypatch.setattr(task.recipes, "open_site", fake_open)
    monkeypatch.setattr(
        task.recipes,
        "site_search",
        lambda *_args, **_kwargs: pytest.fail("open-and-read must not use site search"),
    )

    async def fail_llm(_prompt):
        raise AssertionError("deterministic open-and-read must not use the LLM")

    result = await task.resolve(
        "Open http://127.0.0.1:8765/ and read the page.",
        fail_llm,
        user_supplied_url=True,
    )

    assert result.success is True
    assert result.answer == "CHARLIE-A22-TOKEN"
    assert calls == [("http://127.0.0.1:8765/", True)]


@pytest.mark.asyncio
async def test_interactive_browser_task_preserves_visible_playwright_identity(monkeypatch):
    from charlie.browser import session

    session.reset_session()
    prepared = []

    def prepare_user_visible():
        prepared.append(True)
        return {
            "browser_id": "browser-a22",
            "browser_context_id": "context-a22",
            "target_id": "target-a22",
            "headless": False,
        }

    def fake_open(site_url, *, read_content=False):
        assert read_content is False
        return BrowserResult(
            url=site_url,
            answer="Opened in Charlie browser.",
            success=True,
            verification="page-opened",
            evidence={"observed_url": site_url},
        )

    async def fail_llm(_prompt):
        raise AssertionError("deterministic open must not use the LLM")

    monkeypatch.setattr(task.controller, "prepare_user_visible", prepare_user_visible)
    monkeypatch.setattr(task.recipes, "open_site", fake_open)

    try:
        result = await task.resolve(
            "Open https://example.test/",
            fail_llm,
            user_supplied_url=True,
            user_visible=True,
        )
    finally:
        session.reset_session()

    assert prepared == [True]
    assert result.success is True
    assert result.evidence["browser_surface"] == "playwright"
    assert result.evidence["browser_id"] == "browser-a22"
    assert result.evidence["browser_context_id"] == "context-a22"
    assert result.evidence["target_id"] == "target-a22"
    assert result.evidence["headless"] is False


@pytest.mark.asyncio
async def test_generic_open_browser_request_reaches_visible_launch_authority(monkeypatch):
    seen = []
    brain = core.Brain(_config(), register_panic_hotkey=False)

    async def fake_resolve(*_args, **kwargs):
        seen.append(kwargs)
        return BrowserResult(
            url="https://example.com/",
            answer="Read Example Domain.",
            success=True,
            verification="page-read",
            evidence={"browser_surface": "playwright"},
        )

    monkeypatch.setattr(task, "resolve", fake_resolve)
    try:
        result = await brain.browser_task(
            "Open https://example.com and read the page.",
            platform="web",
            task_id="task-a22-open",
            session_id="session-a22-open",
            turn_id="turn-a22-open",
            user_supplied_url=True,
            return_envelope=True,
        )
    finally:
        await brain.close()

    assert seen and seen[0]["user_visible"] is True
    assert result.data["browser_evidence"]["browser_surface"] == "playwright"


def test_browser_process_pid_selects_root_not_renderer_utility_or_gpu(monkeypatch, tmp_path):
    from types import SimpleNamespace

    import psutil

    from charlie.browser import controller

    profile = str(tmp_path / "browser_profile")

    def process(pid, ppid, *, role=None, create_time):
        args = ["chrome.exe", f"--user-data-dir={profile}", "--remote-debugging-pipe"]
        if role:
            args.append(f"--type={role}")
        return SimpleNamespace(
            info={"pid": pid, "ppid": ppid, "name": "chrome.exe", "cmdline": args, "create_time": create_time}
        )

    observed = [
        process(301, 200, role="renderer", create_time=2.0),
        process(302, 200, role="utility", create_time=2.1),
        process(303, 200, role="gpu-process", create_time=2.2),
        process(200, 100, create_time=1.0),
    ]
    monkeypatch.setattr(controller.config, "browser_profile_path", profile)
    monkeypatch.setattr(psutil, "process_iter", lambda _attrs: iter(observed))

    assert controller._browser_process_pid() == 200


def test_browser_process_pid_fails_on_missing_or_ambiguous_root(monkeypatch, tmp_path):
    from types import SimpleNamespace

    import psutil

    from charlie.browser import controller

    profile = str(tmp_path / "browser_profile")

    def process(pid, ppid, *, role=None, create_time):
        args = ["chrome.exe", f"--user-data-dir={profile}", "--remote-debugging-pipe"]
        if role:
            args.append(f"--type={role}")
        return SimpleNamespace(
            info={"pid": pid, "ppid": ppid, "name": "chrome.exe", "cmdline": args, "create_time": create_time}
        )

    monkeypatch.setattr(controller.config, "browser_profile_path", profile)

    missing_root = [process(301, 200, role="renderer", create_time=2.0)]
    monkeypatch.setattr(psutil, "process_iter", lambda _attrs: iter(missing_root))
    assert controller._browser_process_pid() is None

    ambiguous_roots = [
        process(200, 100, create_time=1.0),
        process(201, 101, create_time=1.1),
        process(302, 200, role="renderer", create_time=2.0),
        process(303, 201, role="renderer", create_time=2.1),
    ]
    monkeypatch.setattr(psutil, "process_iter", lambda _attrs: iter(ambiguous_roots))
    assert controller._browser_process_pid() is None


def test_browser_process_pid_preserves_headless_root_identity(monkeypatch, tmp_path):
    from types import SimpleNamespace

    import psutil

    from charlie.browser import controller

    profile = str(tmp_path / "browser_profile")
    root = SimpleNamespace(
        info={
            "pid": 400,
            "ppid": 100,
            "name": "chrome-headless-shell.exe",
            "cmdline": ["chrome-headless-shell.exe", f"--user-data-dir={profile}"],
            "create_time": 1.0,
        }
    )
    renderer = SimpleNamespace(
        info={
            "pid": 401,
            "ppid": 400,
            "name": "chrome-headless-shell.exe",
            "cmdline": ["chrome-headless-shell.exe", f"--user-data-dir={profile}", "--type=renderer"],
            "create_time": 2.0,
        }
    )
    monkeypatch.setattr(controller.config, "browser_profile_path", profile)
    monkeypatch.setattr(psutil, "process_iter", lambda _attrs: iter([renderer, root]))

    assert controller._browser_process_pid() == 400


def test_prepare_user_visible_launches_the_controller_page_headful(monkeypatch):
    from charlie.browser import controller

    class Page:
        def __init__(self):
            self.brought_to_front = False

        def bring_to_front(self):
            self.brought_to_front = True

    page = Page()
    launched = []
    monkeypatch.setattr(controller, "_page", None)
    monkeypatch.setattr(controller, "_headless_mode", None)

    def fake_launch(*, headless=None):
        launched.append(headless)
        controller._page = page
        controller._headless_mode = headless

    monkeypatch.setattr(controller, "_launch", fake_launch)
    monkeypatch.setattr(controller, "runtime_identity", lambda _page: {"target_id": "target-a22", "headless": False})

    identity = controller._prepare_user_visible_on_thread()

    assert launched == [False]
    assert page.brought_to_front is True
    assert identity == {"target_id": "target-a22", "headless": False}


def test_prepare_user_visible_rejects_stateful_headless_page(monkeypatch):
    from charlie.browser import controller, session
    from charlie.browser.errors import BrowserUnavailable

    session.reset_session()
    session.record_navigation("https://example.test/stateful")
    monkeypatch.setattr(controller, "_page", object())
    monkeypatch.setattr(controller, "_headless_mode", True)
    monkeypatch.setattr(controller, "_page_is_alive", lambda: True)

    try:
        with pytest.raises(BrowserUnavailable, match="already has state"):
            controller._prepare_user_visible_on_thread()
    finally:
        session.reset_session()


def test_open_site_read_returns_observed_content_and_provenance(monkeypatch):
    class FakePage:
        url = "about:blank"

        def title(self):
            return "Charlie A2.2"

    page = FakePage()
    monkeypatch.setattr(recipes.controller, "run", lambda fn, timeout=None: fn(page))
    monkeypatch.setattr(recipes.actions, "navigate", lambda current, url: setattr(current, "url", url))
    monkeypatch.setattr(recipes, "_observe", lambda _page: [])
    monkeypatch.setattr(recipes, "_content_text", lambda _page: "CHARLIE-A22-TOKEN")
    monkeypatch.setattr(
        recipes.controller,
        "runtime_identity",
        lambda _page: {
            "browser_pid": 1234,
            "windows_session_id": 1,
            "browser_session_id": "browser-session-a22",
            "target_id": "target-a22",
        },
    )

    result = recipes.open_site("http://127.0.0.1:8765/", read_content=True)

    assert result.success is True
    assert result.url == "http://127.0.0.1:8765/"
    assert "CHARLIE-A22-TOKEN" in result.answer
    assert result.evidence["observed_url"] == "http://127.0.0.1:8765/"
    assert result.evidence["content"] == "CHARLIE-A22-TOKEN"
    assert result.evidence["target_id"] == "target-a22"


def test_browser_result_preserves_actual_url_not_expected_url():
    result = BrowserResult(
        url="http://127.0.0.1:8765/actual",
        answer="read",
        success=True,
        verification="page-read",
        evidence={
            "requested_url": "http://127.0.0.1:8765/",
            "observed_url": "http://127.0.0.1:8765/actual",
        },
    )

    assert result.evidence["observed_url"] != result.evidence["requested_url"]
    assert result.url == result.evidence["observed_url"]


def test_open_site_does_not_claim_success_for_http_failure(monkeypatch):
    class FakePage:
        url = "about:blank"

        def title(self):
            return "Not Found"

    class FakeResponse:
        status = 404

    page = FakePage()
    monkeypatch.setattr(recipes.controller, "run", lambda fn, timeout=None: fn(page))
    monkeypatch.setattr(
        recipes.actions,
        "navigate",
        lambda current, url: (setattr(current, "url", url), FakeResponse())[1],
    )
    monkeypatch.setattr(recipes, "_observe", lambda _page: [])
    monkeypatch.setattr(recipes, "_content_text", lambda _page: "missing")
    monkeypatch.setattr(recipes.controller, "runtime_identity", lambda _page: {})

    result = recipes.open_site("http://127.0.0.1:8765/missing", read_content=True)

    assert result.success is False
    assert result.verification == "page-http-failure"
    assert "Read " not in (result.answer or "")


def test_existing_public_browser_recipe_contract_remains_available():
    assert router.match_browser_task("Search mechanical keyboards on amazon") is not None
    assert router.match_browser_task("Research the latest AI news") is None

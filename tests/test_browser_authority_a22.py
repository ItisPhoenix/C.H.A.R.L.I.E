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

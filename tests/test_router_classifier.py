"""Tests for the router-classifier fallback (Phase 0 plan 0.1): the cheap LLM
call that catches short phrasings the regex table in router.py misses.
"""

import json

import pytest

from charlie import router
from charlie.config import Config
from charlie.core import Brain


class TestIsRouterClassifierCandidate:
    def test_short_imperative_is_candidate(self):
        assert router.is_router_classifier_candidate("fire up spotify") is True

    def test_question_is_not_candidate(self):
        assert router.is_router_classifier_candidate("what is spotify?") is False

    def test_too_long_is_not_candidate(self):
        text = " ".join(["word"] * 13)
        assert router.is_router_classifier_candidate(text) is False

    def test_empty_is_not_candidate(self):
        assert router.is_router_classifier_candidate("   ") is False


class TestKnownAppAccessors:
    def test_known_app_names_includes_chrome(self):
        assert "chrome" in router.known_app_names()

    def test_open_command_for_known_app(self):
        assert router.open_command_for("chrome") is not None

    def test_open_command_for_unknown_app(self):
        assert router.open_command_for("not-a-real-app") is None

    def test_close_process_for_known_app(self):
        assert router.close_process_for("chrome") is not None

    def test_close_process_for_unknown_app(self):
        assert router.close_process_for("not-a-real-app") is None


class TestCurrentTaskStatusText:
    def test_no_active_task_returns_none(self, monkeypatch):
        import charlie.background_task as background_task

        monkeypatch.setattr(background_task, "get_current_task", lambda: None)
        assert router.current_task_status_text() is None


@pytest.fixture
def brain_config():
    return Config(llm_url="https://example.com/v1", llm_key="test-key", llm_model="dummy")


def _fake_classifier_client(content: str):
    class _Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": content}}]}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **kw):
            return _Response()

    return _Client


class TestClassifyRouterIntent:
    @pytest.mark.asyncio
    async def test_open_app_known(self, monkeypatch, brain_config):
        brain = Brain(brain_config)
        content = json.dumps({"intent": "open_app", "app": "spotify"})
        monkeypatch.setattr("charlie.core.httpx.AsyncClient", lambda *a, **kw: _fake_classifier_client(content)())
        match = await brain._classify_router_intent("fire up spotify")
        assert match is not None
        assert match.name == "open_app"
        assert match.args["app"] == "spotify"

    @pytest.mark.asyncio
    async def test_open_app_unknown_app_rejected(self, monkeypatch, brain_config):
        brain = Brain(brain_config)
        content = json.dumps({"intent": "open_app", "app": "not-a-real-app"})
        monkeypatch.setattr("charlie.core.httpx.AsyncClient", lambda *a, **kw: _fake_classifier_client(content)())
        match = await brain._classify_router_intent("fire up not-a-real-app")
        assert match is None

    @pytest.mark.asyncio
    async def test_intent_none_returns_none(self, monkeypatch, brain_config):
        brain = Brain(brain_config)
        content = json.dumps({"intent": "none", "app": ""})
        monkeypatch.setattr("charlie.core.httpx.AsyncClient", lambda *a, **kw: _fake_classifier_client(content)())
        match = await brain._classify_router_intent("tell me a joke")
        assert match is None

    @pytest.mark.asyncio
    async def test_malformed_json_returns_none(self, monkeypatch, brain_config):
        brain = Brain(brain_config)
        monkeypatch.setattr("charlie.core.httpx.AsyncClient", lambda *a, **kw: _fake_classifier_client("not json")())
        match = await brain._classify_router_intent("fire up spotify")
        assert match is None

    @pytest.mark.asyncio
    async def test_timeout_returns_none(self, monkeypatch, brain_config):
        brain = Brain(brain_config)

        class _HangingClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, *a, **kw):
                import asyncio
                await asyncio.sleep(10)

        monkeypatch.setattr("charlie.core.httpx.AsyncClient", lambda *a, **kw: _HangingClient())
        monkeypatch.setattr("charlie.core._ROUTER_CLASSIFIER_TIMEOUT_S", 0.01)
        match = await brain._classify_router_intent("fire up spotify")
        assert match is None

    @pytest.mark.asyncio
    async def test_no_llm_url_configured_skips_call(self, monkeypatch):
        brain = Brain(Config(llm_url="", llm_key="test-key", llm_model="dummy"))

        def fail_if_called(*a, **kw):
            raise AssertionError("Classifier must not fire an HTTP call with no LLM URL configured")

        monkeypatch.setattr("charlie.core.httpx.AsyncClient", fail_if_called)
        match = await brain._classify_router_intent("fire up spotify")
        assert match is None

    @pytest.mark.asyncio
    async def test_background_task_status_intent(self, monkeypatch, brain_config):
        import charlie.background_task as background_task

        task = type(
            "T", (), {"status": "running", "text": "cleanup", "steps": ["a", "b"], "current_step": 0}
        )()
        monkeypatch.setattr(background_task, "get_current_task", lambda: task)
        brain = Brain(brain_config)
        content = json.dumps({"intent": "background_task_status", "app": ""})
        monkeypatch.setattr("charlie.core.httpx.AsyncClient", lambda *a, **kw: _fake_classifier_client(content)())
        match = await brain._classify_router_intent("any update")
        assert match is not None
        assert match.name == "background_task_status"
        assert "cleanup" in match.args["answer"]

import json

import pytest

from charlie.research import github


class _Response:
    status = 200

    def __init__(self, payload):
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        pass

    def raise_for_status(self):
        pass

    async def aiter_bytes(self):
        yield self.payload


class _Client:
    call = None

    def __init__(self, **kwargs):
        self.options = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        pass

    def stream(self, method, url, **kwargs):
        self.call = (method, url, kwargs)
        type(self).call = self.call
        return _Response(json.dumps({"items": [
            {"full_name": "owner/project", "html_url": "https://github.com/owner/project", "description": "Public repository"},
            {"full_name": "owner/spoof", "html_url": "https://github.com.evil.test/owner/spoof"},
            {"full_name": "owner/private-path", "html_url": "https://github.com/owner/private-path/issues"},
        ]}).encode())


@pytest.mark.asyncio
async def test_search_uses_anonymous_public_api_and_only_canonical_repo_urls(monkeypatch):
    _Client.call = None
    monkeypatch.setattr(github.httpx, "AsyncClient", _Client)

    results = await github.search_github("charlie agent")

    assert len(results) == 1
    assert results[0].title == "owner/project"
    assert results[0].url == results[0].canonical_url == "https://github.com/owner/project"
    assert results[0].provider == "github"
    assert results[0].domain == "github.com"
    assert results[0].snippet == ""
    method, url, options = _Client.call
    assert method == "GET"
    assert url == "https://api.github.com/search/repositories"
    assert options["params"] == {"q": "charlie agent", "per_page": 5}
    assert "Authorization" not in options["headers"]


@pytest.mark.asyncio
async def test_search_rejects_invalid_queries_without_network(monkeypatch):
    def fail_if_called(**_kwargs):
        raise AssertionError("invalid query must not make a request")

    monkeypatch.setattr(github.httpx, "AsyncClient", fail_if_called)
    assert await github.search_github("") == []
    assert await github.search_github("x" * 257) == []
    assert await github.search_github("valid", timeout_s=float("inf")) == []


@pytest.mark.asyncio
async def test_search_discards_oversized_public_api_response(monkeypatch):
    class OversizedClient(_Client):
        def stream(self, method, url, **kwargs):
            return _Response(b"{" + b" " * (65 * 1024))

    monkeypatch.setattr(github.httpx, "AsyncClient", OversizedClient)
    assert await github.search_github("query") == []

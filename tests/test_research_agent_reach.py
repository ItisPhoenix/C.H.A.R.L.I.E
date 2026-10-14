import json

import pytest

from charlie.research import agent_reach


class _Response:
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

    def __init__(self, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        pass

    def stream(self, method, url, **kwargs):
        type(self).call = (method, url, kwargs)
        return _Response(json.dumps({"data": {"result": [
            {"result_type": "video", "data": [
                {"bvid": "BV1xx411c7mD", "title": "<em>Public</em> video", "description": "search metadata"},
                {"bvid": "bad id", "title": "invalid"},
            ]}
        ]}}).encode())


@pytest.mark.asyncio
async def test_bilibili_public_search_returns_fetchable_urls_without_metadata_as_evidence(monkeypatch):
    monkeypatch.setattr(agent_reach.httpx, "AsyncClient", _Client)
    results = await agent_reach.search_bilibili("python tutorial")

    assert len(results) == 1
    assert results[0].url == "https://www.bilibili.com/video/BV1xx411c7mD"
    assert results[0].title == "Public video"
    assert results[0].snippet == ""
    assert results[0].provider == "agent_reach_bilibili"
    assert results[0].domain == "www.bilibili.com"
    method, url, options = _Client.call
    assert method == "GET"
    assert url == "https://api.bilibili.com/x/web-interface/search/all/v2"
    assert options["params"]["keyword"] == "python tutorial"
    assert "Authorization" not in options["headers"]


def test_public_platform_statuses_name_auth_and_setup_blockers(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_reach.shutil, "which", lambda name: None)
    monkeypatch.setattr(agent_reach.sysconfig, "get_path", lambda kind: str(tmp_path / "Scripts"))
    statuses = agent_reach.platform_availability()

    assert statuses["github"]["status"] == "public_no_auth"
    assert statuses["bilibili"]["status"] == "public_no_auth"
    assert statuses["youtube"]["status"] == "missing_prerequisite"
    assert statuses["twitter"]["status"] == "requires_user_auth"
    assert "credentials are read" in statuses["twitter"]["blocker"]
    assert statuses["agent_reach_cli"]["status"] == "missing"


def test_mapped_platforms_report_indexed_public_discovery_separately_from_direct_access(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_reach.shutil, "which", lambda name: None)
    monkeypatch.setattr(agent_reach.sysconfig, "get_path", lambda kind: str(tmp_path / "Scripts"))
    statuses = agent_reach.platform_availability()

    for platform in agent_reach._PLATFORM_DOMAINS:
        status = statuses["twitter" if platform == "x" else platform]
        assert status["public_discovery"]["status"] == "provider_dependent"
        assert "publicly indexed" in status["public_discovery"]["blocker"]
    assert statuses["twitter"]["status"] == "requires_user_auth"
    assert statuses["reddit"]["status"] == "requires_user_auth"
    assert statuses["instagram"]["status"] == "requires_user_auth"
    assert statuses["linkedin"]["status"] == "public_read_only"


def test_agent_reach_cli_is_found_in_current_venv_scripts_directory(monkeypatch, tmp_path):
    scripts = tmp_path / "Scripts"
    scripts.mkdir()
    (scripts / "agent-reach.exe").touch()
    monkeypatch.setattr(agent_reach.shutil, "which", lambda name: None)
    monkeypatch.setattr(agent_reach.sys, "platform", "win32")
    monkeypatch.setattr(agent_reach.sysconfig, "get_path", lambda kind: str(scripts))

    assert agent_reach.platform_availability()["agent_reach_cli"]["status"] == "installed"


def test_explicit_platform_mentions_scope_public_research_domains():
    assert agent_reach.platform_domains_in_query("What's trending on Twitter right now?") == [
        "x.com", "twitter.com"
    ]
    assert agent_reach.platform_domains_in_query("Search GitHub for Charlie plugins") == ["github.com"]
    assert agent_reach.platform_domains_in_query("Compare public Reddit and YouTube discussions") == [
        "reddit.com", "youtube.com", "youtu.be"
    ]

"""LLM endpoint configuration contracts."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from charlie.config import Config
from charlie.core import Brain


def _env_values() -> dict[str, str]:
    values = {}
    for line in Path(__file__).resolve().parents[1].joinpath(".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


@pytest.mark.asyncio
async def test_brain_client_keeps_configured_api_prefix_for_completion_calls():
    config = Config(
        llm_url="https://integrate.api.nvidia.com/v1",
        llm_key="test-key",
        llm_model="nvidia/test-model",
    )
    brain = Brain(config, register_panic_hotkey=False)
    try:
        assert str(brain.client.base_url.join("chat/completions")) == (
            "https://integrate.api.nvidia.com/v1/chat/completions"
        )
        assert config.llm_model == "nvidia/test-model"
        assert brain.client.headers["authorization"] == "Bearer test-key"
    finally:
        await brain.close()


@pytest.mark.parametrize(
    ("endpoint", "expected_url"),
    [
        (
            "https://provider-a.example/v1",
            "https://provider-a.example/v1/chat/completions",
        ),
        (
            "https://provider-b.example/openai/v1",
            "https://provider-b.example/openai/v1/chat/completions",
        ),
        (
            "https://provider-b.example/openai/v1/",
            "https://provider-b.example/openai/v1/chat/completions",
        ),
    ],
)
@pytest.mark.asyncio
async def test_brain_client_resolves_provider_base_prefixes_without_path_duplication(endpoint, expected_url):
    brain = Brain(
        Config(
            llm_url=endpoint,
            llm_key="test-key",
            llm_model="test-model",
        ),
        register_panic_hotkey=False,
    )
    try:
        assert str(brain.client.base_url.join("chat/completions")) == expected_url
        assert "/chat/completions/chat/completions" not in str(brain.client.base_url)
    finally:
        await brain.close()


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://integrate.api.nvidia.com/v1",
        "https://api.openai.com/v1",
        "http://127.0.0.1:11434/v1",
    ],
)
def test_openai_compatible_payload_uses_provider_defaults(endpoint):
    config = Config(
        llm_url=endpoint,
        llm_key="test-key",
        llm_model="nvidia/test-model",
    )
    brain = Brain(config, register_panic_hotkey=False)
    try:
        payload = brain._build_payload([{"role": "user", "content": "ping"}], skip_tools=True)
        assert set(payload) == {"model", "messages", "stream"}
        assert payload["stream"] is True
        assert "temperature" not in payload
        assert "max_tokens" not in payload
        assert "reasoning" not in payload
        assert "chat_template_kwargs" not in payload
    finally:
        import asyncio

        asyncio.run(brain.close())


@pytest.mark.asyncio
async def test_openai_compatible_native_tools_payload_uses_portable_fields():
    brain = Brain(
        Config(
            llm_url="https://provider-b.example/openai/v1",
            llm_key="test-key",
            llm_model="test-model",
        ),
        register_panic_hotkey=False,
    )
    try:
        payload = brain._build_payload([{"role": "user", "content": "ping"}])
        assert set(payload) == {"model", "messages", "stream", "tools", "tool_choice"}
        assert payload["tool_choice"] == "auto"
        assert payload["tools"]
        assert all(item["type"] == "function" for item in payload["tools"])
        assert "temperature" not in payload
        assert "max_tokens" not in payload
        assert "reasoning" not in payload
        assert "chat_template_kwargs" not in payload
    finally:
        await brain.close()


@pytest.mark.asyncio
async def test_startup_probe_uses_provider_default_generation_fields():
    captured = {}

    class Response:
        status_code = 200

    class Client:
        async def post(self, path, **kwargs):
            captured["path"] = path
            captured.update(kwargs)
            return Response()

        async def aclose(self):
            pass

    brain = Brain(
        Config(
            llm_url="https://provider-a.example/v1",
            llm_key="test-key",
            llm_model="test-model",
        ),
        register_panic_hotkey=False,
    )
    brain.client = Client()
    try:
        assert await brain.probe_primary_llm()
    finally:
        await brain.close()

    assert captured["path"] == "chat/completions"
    assert set(captured["json"]) == {"model", "messages", "stream"}
    assert captured["json"]["stream"] is False


@pytest.mark.asyncio
async def test_native_tool_followup_preserves_ids_without_generation_tuning():
    brain = Brain(
        Config(
            llm_url="https://provider-b.example/openai/v1",
            llm_key="test-key",
            llm_model="test-model",
        ),
        register_panic_hotkey=False,
    )
    messages = [
        {"role": "user", "content": "Search for the answer."},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "web_search", "arguments": '{"query":"answer"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call-1", "name": "web_search", "content": "result"},
    ]
    try:
        payload = brain._build_payload(messages)
    finally:
        await brain.close()

    assert payload["messages"] == messages
    assert payload["tool_choice"] == "auto"
    assert payload["tools"]
    assert "temperature" not in payload
    assert "max_tokens" not in payload
    assert "reasoning" not in payload


def test_config_loads_repository_env_when_started_outside_repository(tmp_path):
    expected = _env_values()
    clean_env = os.environ.copy()
    for key in ("CHARLIE_TEST_MODE", "LLM_URL", "LLM_API_KEY", "LLM_MODEL"):
        clean_env.pop(key, None)
    clean_env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    result = subprocess.run(
        [sys.executable, "-c", "from charlie.config import config; print(config.llm_url); print(config.llm_model)"],
        cwd=tmp_path,
        env=clean_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [expected["LLM_URL"], expected["LLM_MODEL"]]

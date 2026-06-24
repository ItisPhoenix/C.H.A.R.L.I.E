import pytest
from charlie.core import Brain
from charlie.config import Config


@pytest.fixture
def brain_config():
    return Config(
        llm_url="http://localhost:11434",
        llm_key="no-key",
        llm_model="dummy",
        iteration_budget_max=3,
    )


@pytest.mark.asyncio
async def test_budget_exhaustion(monkeypatch, brain_config):
    brain = Brain(brain_config)

    followup_count = 0

    def mock_stream(*args, **kwargs):
        nonlocal followup_count
        followup_count += 1

        class MockResponse:
            def raise_for_status(self):
                pass

            async def aiter_lines(self):
                if followup_count <= 4:
                    yield 'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"123","function":{"name":"web_search","arguments":"{\\"query\\":\\"test\\"}"}}]}}]}'
                else:
                    yield 'data: {"choices":[{"delta":{"content":"done"}}]}'
                yield 'data: [DONE]'

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

        return MockResponse()

    monkeypatch.setattr(brain.client, "stream", mock_stream)

    monkeypatch.setattr(
        "charlie.tools.registry.execute_tool",
        lambda name, args: "mock result",
    )

    results = []
    async for chunk in brain.chat_stream("test"):
        results.append(chunk)

    assert any("tool limit" in str(r) for r in results)

def test_extract_bare_tool_calls():
    """Local LLMs output bare tool_name(args) without TOOL: prefix."""
    from charlie.config import Config
    brain = Brain(Config(llm_url="http://localhost:11434", llm_key="no-key", llm_model="dummy"))
    text = 'shell_execute(command="start https://youtube.com")\nshell_execute(command="start https://twitter.com")'
    calls = brain._extract_tool_calls(text)
    assert len(calls) == 2
    assert calls[0]["name"] == "shell_execute"
    assert calls[0]["arguments"]["command"] == "start https://youtube.com"
    assert calls[1]["name"] == "shell_execute"
    assert calls[1]["arguments"]["command"] == "start https://twitter.com"


def test_extract_bare_tool_dedup():
    """Bare and TOOL: prefixed matches should deduplicate."""
    from charlie.config import Config
    brain = Brain(Config(llm_url="http://localhost:11434", llm_key="no-key", llm_model="dummy"))
    text = 'TOOL: web_search("test")\nweb_search("test")'
    calls = brain._extract_tool_calls(text)
    assert len(calls) == 1
    assert calls[0]["name"] == "web_search"


def test_extract_mixed_tool_formats():
    """Mixed TOOL: and bare formats in same response."""
    from charlie.config import Config
    brain = Brain(Config(llm_url="http://localhost:11434", llm_key="no-key", llm_model="dummy"))
    text = 'TOOL: web_search("news")\nshell_execute(command="dir")'
    calls = brain._extract_tool_calls(text)
    assert len(calls) == 2
    names = {c["name"] for c in calls}
    assert names == {"web_search", "shell_execute"}

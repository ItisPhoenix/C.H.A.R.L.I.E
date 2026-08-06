import pytest

from charlie.config import Config
from charlie.core import Brain

pytestmark = pytest.mark.asyncio


def _make_brain(tmp_path, memory_capacity_threshold=0.8):
    memory_file = tmp_path / "MEMORY.md"
    return Brain(
        Config(
            llm_url="http://localhost:11434",
            llm_key="no-key",
            llm_model="dummy",
            memory_file=str(memory_file),
            user_file=str(tmp_path / "USER.md"),
            opinions_file=str(tmp_path / "OPINIONS.md"),
            project_file=str(tmp_path / "PROJECT.md"),
            memory_capacity_threshold=memory_capacity_threshold,
        ),
        register_panic_hotkey=False,
    ), memory_file


def _make_full_memory_entries(count=30, entry_len=65):
    # _consolidate_memory only fires once current_len/max_chars >= 0.8 (memory's
    # cap is 2200 chars), so the fixture content must actually cross that line.
    return "§".join([f"fact {i}: {'x' * entry_len}" for i in range(count)])


class _FakeResponse:
    def __init__(self, content: str):
        self.status_code = 200
        self._content = content

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


class _FakeAsyncClient:
    def __init__(self, response_text):
        self._response_text = response_text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, *args, **kwargs):
        return _FakeResponse(self._response_text)


async def test_malformed_consolidation_response_does_not_wipe_memory(tmp_path, monkeypatch):
    """A model that ignores the '§-delimited entries only' instruction and
    returns prose/empty output must not destroy the real memory content."""
    brain, memory_file = _make_brain(tmp_path)
    original = _make_full_memory_entries()
    memory_file.write_text(original, encoding="utf-8")

    import httpx
    prose = "Sure! Here is a summary of your memories:"
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _FakeAsyncClient(prose))

    await brain._consolidate_memory()

    assert memory_file.read_text(encoding="utf-8") == original, "malformed LLM output must not overwrite real memory"


async def test_oversized_consolidation_response_is_discarded(tmp_path, monkeypatch):
    brain, memory_file = _make_brain(tmp_path)
    original = _make_full_memory_entries()
    memory_file.write_text(original, encoding="utf-8")

    import httpx
    bloated = "§".join(["x" * 500 for _ in range(20)])
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _FakeAsyncClient(bloated))

    await brain._consolidate_memory()

    assert memory_file.read_text(encoding="utf-8") == original, "oversized result must be discarded, not written"


async def test_valid_consolidation_response_is_written_and_backed_up(tmp_path, monkeypatch):
    brain, memory_file = _make_brain(tmp_path)
    original = _make_full_memory_entries()
    memory_file.write_text(original, encoding="utf-8")

    import httpx
    consolidated = "§".join(["merged fact one", "merged fact two"])
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _FakeAsyncClient(consolidated))

    await brain._consolidate_memory()

    assert memory_file.read_text(encoding="utf-8") == consolidated
    backup_path = memory_file.with_name(memory_file.name + ".bak")
    assert backup_path.read_text(encoding="utf-8") == original

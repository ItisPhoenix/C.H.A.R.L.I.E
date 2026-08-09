"""Tests for charlie/web_server.py:_stage_proposed_extension -- the
web-server-side half of the tier-3 chat trigger (charlie.core.Brain._handle_propose_new_tool
emits, this stages it into ExtensionManager and re-broadcasts with a pending_id)."""

import pytest

import charlie.web_server as web_server

_VALID_CODE = '''
def double_it(n):
    """Doubles the given number and returns it as a string."""
    return str(int(n) * 2)
'''


@pytest.mark.asyncio
async def test_stages_into_extension_manager_and_broadcasts_pending_id(monkeypatch):
    broadcasts = []

    async def fake_broadcast(data):
        broadcasts.append(data)

    monkeypatch.setattr(web_server, "broadcast", fake_broadcast)
    web_server._extension_manager._pending.clear()

    await web_server._stage_proposed_extension({
        "kind": "generated", "name": "double_it", "source": "chat",
        "raw_text": _VALID_CODE, "declared_tools": ["double_it"],
    })

    assert len(broadcasts) == 1
    event = broadcasts[0]
    assert event["type"] == "extension_pending"
    pending_id = event["payload"]["pending_id"]
    assert pending_id in web_server._extension_manager._pending
    assert event["payload"]["name"] == "double_it"


@pytest.mark.asyncio
async def test_missing_name_or_raw_text_is_a_no_op(monkeypatch):
    broadcasts = []

    async def fake_broadcast(data):
        broadcasts.append(data)

    monkeypatch.setattr(web_server, "broadcast", fake_broadcast)
    await web_server._stage_proposed_extension({"kind": "generated", "name": "", "raw_text": ""})
    assert broadcasts == []

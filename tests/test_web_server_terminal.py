import pytest

import charlie.web_server as web_server


@pytest.mark.asyncio
async def test_terminal_input_routes_through_main_approval_channel(monkeypatch):
    class Manager:
        def snapshot(self, session_id):
            return {"session_id": session_id, "status": "running", "output": ""}

    class Bus:
        def __init__(self):
            self.commands = []

        async def send_command(self, command):
            self.commands.append(command)

    bus = Bus()
    monkeypatch.setattr(web_server, "_terminal_manager", Manager())
    monkeypatch.setattr(web_server, "event_bus", bus)

    result = await web_server.terminal_input("s1", {"line": "echo hi", "confirmed": True})

    assert result["status"] == "approval_pending"
    assert bus.commands[0]["type"] == "terminal_command_request"

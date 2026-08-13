import asyncio

import pytest

from charlie.terminal_service import TerminalManager


class FakeStream:
    def __init__(self, chunks):
        self.chunks = iter(chunks)

    async def read(self, _size):
        try:
            return next(self.chunks)
        except StopIteration:
            return b""


class FakeStdin:
    def __init__(self):
        self.writes = []

    def write(self, data):
        self.writes.append(data)

    async def drain(self):
        return None


class FakeProcess:
    def __init__(self):
        self.stdout = FakeStream([b"ready\r\n", b""])
        self.stderr = FakeStream([b""])
        self.stdin = FakeStdin()
        self.returncode = 0
        self.pid = 42

    async def wait(self):
        return self.returncode

    def terminate(self):
        self.returncode = 1


@pytest.mark.asyncio
async def test_terminal_session_streams_output_and_accepts_input(monkeypatch):
    process = FakeProcess()

    async def fake_create(*_args, **_kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)
    manager = TerminalManager()
    session = await manager.create()
    await manager.write(session.session_id, "dir")
    await asyncio.sleep(0)
    snapshot = manager.snapshot(session.session_id)

    assert snapshot["status"] in {"running", "exited"}
    assert "ready" in snapshot["output"]
    assert process.stdin.writes == [b"dir\r\n"]
    await manager.close(session.session_id)

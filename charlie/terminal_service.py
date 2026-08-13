"""Managed interactive local terminal sessions for the dashboard."""

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Dict, Optional

logger = logging.getLogger("charlie.terminal_service")

_MAX_OUTPUT_CHARS = 200_000
_READ_CHUNK_SIZE = 4096


@dataclass
class TerminalSession:
    session_id: str
    process: asyncio.subprocess.Process
    output: str = ""
    status: str = "running"
    exit_code: Optional[int] = None
    readers: set[asyncio.Task[None]] = field(default_factory=set)

    def append(self, text: str) -> None:
        self.output = (self.output + text)[-_MAX_OUTPUT_CHARS:]


class TerminalManager:
    """Own a bounded set of interactive child shells and their truth state."""

    def __init__(self) -> None:
        self._sessions: Dict[str, TerminalSession] = {}

    async def create(self) -> TerminalSession:
        if os.name == "nt":
            process = await asyncio.create_subprocess_exec(
                "powershell.exe", "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        else:
            process = await asyncio.create_subprocess_exec(
                "bash", "--noprofile", "--norc",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        session = TerminalSession(session_id=uuid.uuid4().hex, process=process)
        self._sessions[session.session_id] = session
        reader = asyncio.create_task(self._read_output(session))
        session.readers.add(reader)
        return session

    async def _read_output(self, session: TerminalSession) -> None:
        stream = session.process.stdout
        if stream is None:
            return
        try:
            while True:
                chunk = await stream.read(_READ_CHUNK_SIZE)
                if not chunk:
                    break
                session.append(chunk.decode("utf-8", errors="replace"))
            session.exit_code = await session.process.wait()
            session.status = "exited"
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Terminal output reader failed", exc_info=True)
            session.status = "failed"

    def snapshot(self, session_id: str) -> dict:
        session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(session_id)
        return {
            "session_id": session.session_id,
            "status": session.status,
            "exit_code": session.exit_code,
            "output": session.output,
        }

    async def write(self, session_id: str, line: str) -> None:
        session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(session_id)
        if session.status != "running" or session.process.stdin is None:
            raise RuntimeError("terminal session is not running")
        session.process.stdin.write((line.rstrip("\r\n") + "\r\n").encode("utf-8"))
        await session.process.stdin.drain()

    async def close(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session is None:
            return
        for reader in session.readers:
            reader.cancel()
        if session.status == "running":
            session.process.terminate()
        try:
            await asyncio.wait_for(session.process.wait(), timeout=2.0)
        except (asyncio.TimeoutError, ProcessLookupError):
            logger.warning("Terminal process did not exit promptly", extra={"session_id": session_id})
        session.status = "closed"

    async def close_all(self) -> None:
        await asyncio.gather(*(self.close(session_id) for session_id in tuple(self._sessions)), return_exceptions=True)

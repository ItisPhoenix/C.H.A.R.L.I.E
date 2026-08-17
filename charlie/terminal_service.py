"""Managed interactive local terminal sessions with Windows ConPTY support."""

import asyncio
import ctypes
from ctypes import wintypes
import logging
import os
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, Dict, Optional, Set

from charlie.autonomy import RiskClass, classify_action
from charlie.resource_locks import default_lease_manager

logger = logging.getLogger("charlie.terminal_service")

_MAX_OUTPUT_CHARS = 200_000
_READ_CHUNK_SIZE = 4096

# Win32 definitions for ConPTY
if sys.platform == "win32":
    kernel32 = ctypes.windll.kernel32

    class COORD(ctypes.Structure):
        _fields_ = [("X", wintypes.SHORT), ("Y", wintypes.SHORT)]

    class STARTUPINFO(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR),
            ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD),
            ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD),
            ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD),
            ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD),
            ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.c_char_p),
            ("hStdInput", wintypes.HANDLE),
            ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE),
        ]

    class STARTUPINFOEX(ctypes.Structure):
        _fields_ = [
            ("StartupInfo", STARTUPINFO),
            ("lpAttributeList", ctypes.c_void_p),
        ]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("hProcess", wintypes.HANDLE),
            ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD),
            ("dwThreadId", wintypes.DWORD),
        ]

    PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
    EXTENDED_STARTUPINFO_PRESENT = 0x00080000

_HAS_CONPTY = sys.platform == "win32"


class WindowsConPTY:
    """True Windows ConPTY host wrapper using Win32 PseudoConsole APIs."""

    def __init__(self, cols: int = 80, rows: int = 24, cwd: Optional[str] = None) -> None:
        self.cols = cols
        self.rows = rows
        self.cwd = cwd or os.getcwd()
        self.pid: Optional[int] = None
        self.shell_name: str = "powershell.exe"

        self._hpcon = ctypes.c_void_p()
        self._h_in_w = wintypes.HANDLE()
        self._h_out_r = wintypes.HANDLE()
        self._pi = PROCESS_INFORMATION()
        self._attr_list: Optional[ctypes.Array] = None
        self._closed = False
        self._lock = threading.Lock()

    def start(self) -> None:
        h_in_r = wintypes.HANDLE()
        h_out_w = wintypes.HANDLE()

        if not kernel32.CreatePipe(ctypes.byref(h_in_r), ctypes.byref(self._h_in_w), None, 0):
            raise OSError(f"CreatePipe (in) failed: {ctypes.GetLastError()}")

        if not kernel32.CreatePipe(ctypes.byref(self._h_out_r), ctypes.byref(h_out_w), None, 0):
            kernel32.CloseHandle(h_in_r)
            kernel32.CloseHandle(self._h_in_w)
            raise OSError(f"CreatePipe (out) failed: {ctypes.GetLastError()}")

        res = kernel32.CreatePseudoConsole(
            COORD(self.cols, self.rows),
            h_in_r,
            h_out_w,
            0,
            ctypes.byref(self._hpcon),
        )

        # Close child handle copies owned now by ConPTY
        kernel32.CloseHandle(h_in_r)
        kernel32.CloseHandle(h_out_w)

        if res != 0:
            kernel32.CloseHandle(self._h_in_w)
            kernel32.CloseHandle(self._h_out_r)
            raise OSError(f"CreatePseudoConsole failed with HRESULT 0x{res:X}")

        size = ctypes.c_size_t()
        kernel32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
        self._attr_list = ctypes.create_string_buffer(size.value)
        if not kernel32.InitializeProcThreadAttributeList(self._attr_list, 1, 0, ctypes.byref(size)):
            self.close()
            raise OSError(f"InitializeProcThreadAttributeList failed: {ctypes.GetLastError()}")

        if not kernel32.UpdateProcThreadAttribute(
            self._attr_list,
            0,
            PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE,
            self._hpcon,
            ctypes.sizeof(self._hpcon),
            None,
            None,
        ):
            self.close()
            raise OSError(f"UpdateProcThreadAttribute failed: {ctypes.GetLastError()}")

        si = STARTUPINFOEX()
        si.StartupInfo.cb = ctypes.sizeof(STARTUPINFOEX)
        si.lpAttributeList = ctypes.cast(self._attr_list, ctypes.c_void_p)

        cmd = "powershell.exe -NoLogo -NoProfile"
        success = kernel32.CreateProcessW(
            None,
            cmd,
            None,
            None,
            False,
            EXTENDED_STARTUPINFO_PRESENT,
            None,
            self.cwd,
            ctypes.byref(si.StartupInfo),
            ctypes.byref(self._pi),
        )

        if not success:
            err = ctypes.GetLastError()
            self.close()
            raise OSError(f"CreateProcessW failed for PowerShell: {err}")

        self.pid = self._pi.dwProcessId

    def read(self, max_bytes: int = _READ_CHUNK_SIZE) -> bytes:
        if self._closed or not self._h_out_r:
            return b""
        buf = ctypes.create_string_buffer(max_bytes)
        bytes_read = wintypes.DWORD()
        ok = kernel32.ReadFile(self._h_out_r, buf, max_bytes, ctypes.byref(bytes_read), None)
        if not ok or bytes_read.value == 0:
            return b""
        return buf.raw[: bytes_read.value]

    def write(self, data: bytes) -> int:
        with self._lock:
            if self._closed or not self._h_in_w:
                return 0
            written = wintypes.DWORD()
            ok = kernel32.WriteFile(self._h_in_w, data, len(data), ctypes.byref(written), None)
            return written.value if ok else 0

    def resize(self, cols: int, rows: int) -> None:
        with self._lock:
            if self._closed or not self._hpcon:
                return
            self.cols = max(1, cols)
            self.rows = max(1, rows)
            kernel32.ResizePseudoConsole(self._hpcon, COORD(self.cols, self.rows))

    def get_exit_code(self) -> Optional[int]:
        with self._lock:
            if not self._pi.hProcess:
                return None
            code = wintypes.DWORD()
            if kernel32.GetExitCodeProcess(self._pi.hProcess, ctypes.byref(code)):
                STILL_ACTIVE = 259
                if code.value == STILL_ACTIVE:
                    # Check if process is actually terminated
                    wait_res = kernel32.WaitForSingleObject(self._pi.hProcess, 0)
                    WAIT_OBJECT_0 = 0
                    if wait_res == WAIT_OBJECT_0:
                        kernel32.GetExitCodeProcess(self._pi.hProcess, ctypes.byref(code))
                        return int(code.value)
                    return None
                return int(code.value)
            return None

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True

            # 1. Close PseudoConsole to signal EOF to reader thread
            if self._hpcon and self._hpcon.value:
                try:
                    kernel32.ClosePseudoConsole(self._hpcon)
                except Exception:
                    pass
                self._hpcon = ctypes.c_void_p()

            # 2. Close input handle
            if self._h_in_w and self._h_in_w.value:
                try:
                    kernel32.CloseHandle(self._h_in_w)
                except Exception:
                    pass
                self._h_in_w = wintypes.HANDLE()

            # 3. Close output handle
            if self._h_out_r and self._h_out_r.value:
                try:
                    kernel32.CloseHandle(self._h_out_r)
                except Exception:
                    pass
                self._h_out_r = wintypes.HANDLE()

            # 4. Terminate process and cleanup process handles
            if self._pi.hProcess:
                try:
                    kernel32.TerminateProcess(self._pi.hProcess, 0)
                    kernel32.WaitForSingleObject(self._pi.hProcess, 1000)
                    kernel32.CloseHandle(self._pi.hProcess)
                    kernel32.CloseHandle(self._pi.hThread)
                except Exception:
                    pass
                self._pi.hProcess = wintypes.HANDLE()
                self._pi.hThread = wintypes.HANDLE()

            # 5. Delete attribute list
            if self._attr_list:
                try:
                    kernel32.DeleteProcThreadAttributeList(self._attr_list)
                except Exception:
                    pass
                self._attr_list = None


class FallbackPTY:
    """Non-Windows fallback using async subprocess pipes."""

    def __init__(self, cols: int = 80, rows: int = 24, cwd: Optional[str] = None) -> None:
        self.cols = cols
        self.rows = rows
        self.cwd = cwd or os.getcwd()
        self.pid: Optional[int] = None
        self.shell_name: str = "bash"
        self._process: Optional[asyncio.subprocess.Process] = None
        self._closed = False

    async def start_async(self) -> None:
        self._process = await asyncio.create_subprocess_exec(
            "bash",
            "--noprofile",
            "--norc",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=self.cwd,
        )
        self.pid = self._process.pid

    def write(self, data: bytes) -> int:
        if self._closed or not self._process or not self._process.stdin:
            return 0
        self._process.stdin.write(data)
        return len(data)

    def resize(self, cols: int, rows: int) -> None:
        self.cols = cols
        self.rows = rows

    def close(self) -> None:
        self._closed = True
        if self._process:
            try:
                self._process.terminate()
            except Exception:
                pass


class TerminalSession:
    """One persistent terminal session with real ConPTY / PTY I/O and scrollback buffer."""

    def __init__(
        self,
        session_id: str,
        backend: WindowsConPTY | FallbackPTY,
        loop: Optional[asyncio.AbstractEventLoop] = None,
    ) -> None:
        self.session_id = session_id
        self.backend = backend
        self.loop = loop or asyncio.get_event_loop()
        self.pid: Optional[int] = backend.pid
        self.shell_name: str = backend.shell_name
        self.cols: int = backend.cols
        self.rows: int = backend.rows
        self.status: str = "running"
        self.exit_code: Optional[int] = None
        self.lease_holder: str = "idle"
        self._last_user_input_time: float = 0.0
        self._user_active_timeout: float = 1.5

        self._history: str = ""
        self._subscribers: Set[asyncio.Queue[str]] = set()
        self._reader_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._closed = False

    def start_reader(self) -> None:
        if isinstance(self.backend, WindowsConPTY):
            self._reader_thread = threading.Thread(
                target=self._conpty_read_loop,
                name=f"ConPTY-Reader-{self.session_id}",
                daemon=True,
            )
            self._reader_thread.start()
        elif isinstance(self.backend, FallbackPTY):
            asyncio.create_task(self._fallback_read_loop())

    def _conpty_read_loop(self) -> None:
        conpty = self.backend
        assert isinstance(conpty, WindowsConPTY)
        try:
            while not self._stop_event.is_set():
                raw = conpty.read(_READ_CHUNK_SIZE)
                if not raw:
                    break
                text = raw.decode("utf-8", errors="replace")
                self._append_output(text)
        except Exception:
            logger.warning("ConPTY read loop encountered an error", exc_info=True)
        finally:
            if conpty._pi.hProcess:
                try:
                    kernel32.WaitForSingleObject(conpty._pi.hProcess, 500)
                except Exception:
                    pass
            code = conpty.get_exit_code()
            self.exit_code = code
            if self._closed:
                self.status = "closed"
            elif code is not None:
                self.status = "exited" if code == 0 else "failed"
            else:
                self.status = "exited"
            self._broadcast_event({"type": "exit", "exit_code": self.exit_code})

    async def _fallback_read_loop(self) -> None:
        pty = self.backend
        assert isinstance(pty, FallbackPTY)
        stream = pty._process.stdout if pty._process else None
        if stream is None:
            return
        try:
            while not self._stop_event.is_set():
                raw = await stream.read(_READ_CHUNK_SIZE)
                if not raw:
                    break
                text = raw.decode("utf-8", errors="replace")
                self._append_output(text)
            self.exit_code = await pty._process.wait()
            if self._closed:
                self.status = "closed"
            elif self.exit_code is not None:
                self.status = "exited" if self.exit_code == 0 else "failed"
            else:
                self.status = "exited"
        except Exception:
            logger.warning("Fallback PTY reader encountered an error", exc_info=True)
            self.status = "failed"
        finally:
            self._broadcast_event({"type": "exit", "exit_code": self.exit_code})

    def _append_output(self, text: str) -> None:
        self._history = (self._history + text)[-_MAX_OUTPUT_CHARS:]
        self._broadcast_event({"type": "output", "data": text})

    def _broadcast_event(self, event_data: dict) -> None:
        for q in list(self._subscribers):
            try:
                self.loop.call_soon_threadsafe(q.put_nowait, event_data)
            except Exception:
                pass

    def write_bytes(self, data: bytes | str, source: str = "user") -> int:
        if self.status != "running" or self._closed:
            raise RuntimeError("terminal session is not running")

        if source == "user":
            self._last_user_input_time = time.monotonic()
            current = default_lease_manager.current_owner("terminal")
            if current is not None and current != "user":
                default_lease_manager.manual_takeover(["terminal"])
            self.lease_holder = "user"

        if isinstance(data, str):
            data = data.encode("utf-8")
        return self.backend.write(data)

    def write(self, line: str, source: str = "charlie") -> int:
        clean_line = line.rstrip("\r\n") + "\r\n"
        return self.write_bytes(clean_line.encode("utf-8"), source=source)

    def resize(self, cols: int, rows: int) -> None:
        self.cols = max(1, cols)
        self.rows = max(1, rows)
        self.backend.resize(self.cols, self.rows)

    def interrupt(self) -> None:
        # Ctrl+C = 0x03
        default_lease_manager.manual_takeover(["terminal"])
        self.lease_holder = "user"
        self._last_user_input_time = time.monotonic()
        self.backend.write(b"\x03")

    def subscribe(self) -> asyncio.Queue[dict]:
        q: asyncio.Queue[dict] = asyncio.Queue()
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict]) -> None:
        self._subscribers.discard(q)

    def get_scrollback(self) -> str:
        return self._history

    def snapshot(self) -> dict:
        return {
            "session_id": self.session_id,
            "status": self.status,
            "exit_code": self.exit_code,
            "pid": self.pid,
            "shell": self.shell_name,
            "cols": self.cols,
            "rows": self.rows,
            "lease_holder": self.lease_holder,
            "output": self._history,
        }

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.status = "closed"
        self._stop_event.set()
        self.backend.close()
        for q in list(self._subscribers):
            try:
                self.loop.call_soon_threadsafe(q.put_nowait, {"type": "closed"})
            except Exception:
                pass
        self._subscribers.clear()


class TerminalManager:
    """Manages the lifecycle, persistence, and arbitration of terminal sessions."""

    def __init__(self) -> None:
        self._sessions: Dict[str, TerminalSession] = {}
        self._primary_id: str = "primary"
        self._lock = asyncio.Lock()

    async def get_or_create_primary(self, cols: int = 80, rows: int = 24) -> TerminalSession:
        async with self._lock:
            existing = self._sessions.get(self._primary_id)
            if existing and existing.status == "running":
                return existing
            return await self._create_internal(session_id=self._primary_id, cols=cols, rows=rows)

    async def create(self, session_id: Optional[str] = None, cols: int = 80, rows: int = 24) -> TerminalSession:
        async with self._lock:
            sid = session_id or uuid.uuid4().hex
            return await self._create_internal(session_id=sid, cols=cols, rows=rows)

    async def _create_internal(self, session_id: str, cols: int = 80, rows: int = 24) -> TerminalSession:
        loop = asyncio.get_running_loop()
        if sys.platform == "win32":
            backend = WindowsConPTY(cols=cols, rows=rows)
            # Run startup in thread to ensure synchronous Win32 pipes don't block
            await loop.run_in_executor(None, backend.start)
            session = TerminalSession(session_id=session_id, backend=backend, loop=loop)
        else:
            backend_fallback = FallbackPTY(cols=cols, rows=rows)
            await backend_fallback.start_async()
            session = TerminalSession(session_id=session_id, backend=backend_fallback, loop=loop)

        session.start_reader()
        self._sessions[session.session_id] = session
        return session

    def get_session(self, session_id: str) -> Optional[TerminalSession]:
        return self._sessions.get(session_id)

    def snapshot(self, session_id: str) -> dict:
        session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(session_id)
        return session.snapshot()

    async def execute_charlie_command(
        self,
        session_id: str,
        command: str,
        task_id: str = "charlie-agent",
        audit_store: Optional[object] = None,
        approved: bool = False,
    ) -> dict:
        """Execute a command as Charlie with capability lease arbitration, autonomy policy, and audit."""
        session = self._sessions.get(session_id)
        if session is None:
            if session_id == self._primary_id:
                session = await self.get_or_create_primary()
            else:
                raise KeyError(session_id)

        # 1. User contention check: if user interacted very recently, reject
        if time.monotonic() - session._last_user_input_time < session._user_active_timeout:
            raise RuntimeError("Terminal lease conflict: user is actively interacting with terminal")

        # 2. Acquire terminal lease
        lease = await default_lease_manager.acquire("terminal", owner_id=task_id, timeout=2.0)
        try:
            # 3. Autonomy policy evaluation
            risk_class, reason = classify_action("shell_execute", {"command": command})

            if risk_class == RiskClass.IRREVERSIBLE:
                if audit_store is not None and hasattr(audit_store, "record"):
                    audit_store.record(
                        "terminal_exec",
                        {"command": command, "task_id": task_id, "source": "charlie"},
                        f"BLOCKED: {reason}",
                    )
                raise PermissionError(f"Command blocked by security policy: {reason}")

            if risk_class in (RiskClass.DESTRUCTIVE, RiskClass.SECURITY_SENSITIVE) and not approved:
                if audit_store is not None and hasattr(audit_store, "record"):
                    audit_store.record(
                        "terminal_exec",
                        {"command": command, "task_id": task_id, "source": "charlie", "risk_class": str(risk_class)},
                        "APPROVAL_REQUIRED",
                    )
                raise PermissionError(f"Approval required for command execution: {reason or str(risk_class)}")

            # 4. Write command to terminal
            session.lease_holder = task_id
            session.write(command, source="charlie")

            # 5. Audit completed execution
            if audit_store is not None and hasattr(audit_store, "record"):
                audit_store.record(
                    "terminal_exec",
                    {"command": command, "task_id": task_id, "source": "charlie"},
                    "COMPLETED",
                )

            return {
                "status": "ok",
                "session_id": session.session_id,
                "task_id": task_id,
                "command": command,
                "risk_class": str(risk_class),
            }
        finally:
            await lease.release()
            session.lease_holder = "idle"

    async def write(self, session_id: str, line: str, source: str = "charlie", task_id: str = "charlie-agent") -> None:
        session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(session_id)
        if source == "charlie":
            owner = default_lease_manager.current_owner("terminal")
            if owner is not None and owner != task_id:
                raise RuntimeError(f"Terminal lease conflict: owned by {owner}")
            session.lease_holder = task_id
        session.write(line, source=source)
        if source == "charlie":
            session.lease_holder = "idle"

    async def write_bytes(self, session_id: str, data: bytes | str, source: str = "user") -> None:
        session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(session_id)
        session.write_bytes(data, source=source)

    async def resize(self, session_id: str, cols: int, rows: int) -> None:
        session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(session_id)
        session.resize(cols, rows)

    async def interrupt(self, session_id: str) -> None:
        session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(session_id)
        session.interrupt()

    async def close(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session is not None:
            session.close()

    async def close_all(self) -> None:
        for sid in list(self._sessions.keys()):
            await self.close(sid)

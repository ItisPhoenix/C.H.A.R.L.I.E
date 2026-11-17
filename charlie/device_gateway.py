"""Windows device gateway for Ubuntu Charlie's deployed UserDevices WSS protocol.

An execution endpoint only: no Brain, no planning, no local LLM. All authority
comes from the paired device token, the local policy/lease boundary, and a fresh
action-specific consent decision for modifying or sensitive operations.

Protocol source of truth (deployed image, verified 2026-11-16):
  packages/adapters/src/user-device-protocol.ts
  packages/adapters/src/host-computer-protocol.ts
  packages/adapters/src/user-device-gateway.ts
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

import websockets

from charlie.capabilities import capability_index
from charlie.tools import ToolExecutionResult, ToolRegistry

logger = logging.getLogger("charlie.device_gateway")

CONNECT_PATH = "/api/devices/connect"
PING_INTERVAL_S = 25.0
RECONNECT_MIN_S = 1.0
RECONNECT_MAX_S = 60.0

# Non-destructive bootstrap/read methods run without a fresh prompt but still
# pass the local capability/lease policy. provision/prepare only create an empty
# per-bot workspace directory; they are not remote execution.
READONLY_METHODS = frozenset({
    "environment", "isRunning", "snapshot", "listFiles", "readFile",
    "provision", "prepare", "screenObserve",
})
# Everything else requires a fresh, action-specific consent decision.
SENSITIVE_METHODS = frozenset({
    "execute", "writeFile", "screenAct", "cancelRunWork",
    "stop", "destroy", "workspaceDirectories", "exportWorkspace",
    # Browser control is never covered by shell/app standing consent. It needs
    # an explicit browser scope plus per-action grants for private/state-changing work.
    "browserObserve", "browserAct", "browserFind", "browserSession",
})


def device_state_path() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "Charlie" / "device.json"


def load_device_state() -> dict:
    from charlie.secret_store import _dpapi

    path = device_state_path()
    if not path.exists():
        raise RuntimeError("No paired Charlie device. Run device enrollment first.")
    return json.loads(_dpapi(path.read_bytes(), protect=False).decode("utf-8"))


@dataclass(frozen=True)
class ConsentDecision:
    allowed: bool
    reason: str


class ConsentProvider:
    """Owner-facing consent. Default refuses; override for a real prompt."""

    def __init__(self) -> None:
        self._callback: Optional[Callable[[dict], Awaitable[ConsentDecision]]] = None

    def set_callback(self, callback: Callable[[dict], Awaitable[ConsentDecision]]) -> None:
        self._callback = callback

    async def decide(self, request: dict) -> ConsentDecision:
        method = request.get("method", "unknown")
        if method in READONLY_METHODS:
            return ConsentDecision(True, "non-destructive read/bootstrap method")
        if self._callback is not None:
            try:
                return await self._callback(request)
            except Exception as exc:
                return ConsentDecision(False, f"consent error: {type(exc).__name__}")
        # Durable owner policy: explicit standing/bounded consent, default deny.
        from charlie.consent_policy import decide as policy_decide

        allowed, reason = policy_decide(request)
        return ConsentDecision(allowed, reason)


def deadline_ms(request: dict) -> int:
    """Matches hostComputerDeadlineMs in the deployed protocol."""
    method = request.get("method", "")
    if method == "execute":
        timeout = request.get("command", {}).get("timeoutMs", 300_000)
        timeout = max(1_000, min(int(timeout), 3_600_000))
        return timeout + 30_000
    if method.startswith("screen"):
        return 120_000
    if method.startswith("browser"):
        return 180_000
    return 600_000


class DeviceGateway:
    def __init__(self, *, consent: Optional[ConsentProvider] = None) -> None:
        self.state = load_device_state()
        self.consent = consent or ConsentProvider()
        self.tools = ToolRegistry()
        self._active: dict[str, asyncio.Task] = {}
        self._task_ws: dict[str, Any] = {}
        self._seen: set[str] = set()
        self._stop = asyncio.Event()

    def _url(self) -> str:
        from urllib.parse import urlsplit

        parsed = urlsplit(self.state["url"])
        scheme = "wss" if parsed.scheme == "https" else "ws"
        return f"{scheme}://{parsed.netloc}{CONNECT_PATH}"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": "Bearer " + self.state["token"]}

    async def _send(self, ws, message: dict) -> None:
        await ws.send(json.dumps(message, separators=(",", ":")))

    async def _execute_request(self, ws, request_id: str, request: dict) -> None:
        """Runs one host-computer request and emits frames on its owning socket only."""
        method = request.get("method")
        try:
            bot_id = request.get("botId", "")
            run_id = request.get("runId")
            if not bot_id or len(bot_id) > 256:
                raise ValueError("invalid botId")
            if method not in READONLY_METHODS and method not in SENSITIVE_METHODS:
                raise ValueError(f"unsupported method: {method}")

            decision = await self.consent.decide(request)
            if not decision.allowed:
                raise PermissionError(f"consent denied: {decision.reason}")

            if method == "execute":
                # host-computer-sandbox expects a stream of ProcessEvent values,
                # terminated by an exit event, then {done:true}.
                async for event in self._execute_stream(request):
                    await self._emit(ws, request_id, {"value": event})
            else:
                value = await self._dispatch(method, request, request_id=request_id)
                await self._emit(ws, request_id, {"value": value})
            await self._emit(ws, request_id, {"done": True})
        except asyncio.CancelledError:
            await self._emit(ws, request_id, {"error": "cancelled"})
            raise
        except Exception as exc:
            logger.warning("device request failed | id=%s method=%s error=%s: %s",
                           request_id, method, type(exc).__name__, str(exc)[:200])
            await self._emit(ws, request_id, {"error": f"{type(exc).__name__}: {exc}"})

    async def _emit(self, ws, request_id: str, frame: dict) -> None:
        """A response must never land on a replacement or closing connection."""
        if ws is None or getattr(ws, "closed", False):
            return
        try:
            await self._send(ws, {"type": "frame", "id": request_id, "frame": frame})
        except websockets.exceptions.ConnectionClosed:
            # Emergency stop closes the socket before the cancelled task emits.
            # There is no receiver left; dropping the frame is the truthful end.
            logger.info("device frame dropped | id=%s reason=socket_closed", request_id)

    _ws: Any = None

    async def _dispatch(self, method: str, request: dict, *, request_id: Optional[str] = None) -> Any:
        from charlie.native_executor import get_executor

        ex = get_executor()

        if method == "environment":
            return {
                "platform": sys.platform,
                "home": str(Path.home()),
                "shell": "powershell.exe" if sys.platform == "win32" else "/bin/sh",
            }
        if method == "isRunning":
            return True
        if method == "snapshot":
            return {"platform": sys.platform, "hostname": os.environ.get("COMPUTERNAME", "")}
        if method == "provision":
            from hashlib import sha256

            bot_id = request.get("botId", "")
            key = sha256(bot_id.encode()).hexdigest()
            home = Path(os.environ["LOCALAPPDATA"]) / "Charlie" / "computers" / key
            home.mkdir(parents=True, exist_ok=True)
            return {"id": f"desktop-{bot_id}", "botId": key, "kind": "desktop",
                    "providerRef": f"host:{key}", "fresh": True}
        if method == "prepare":
            return None
        if method == "listFiles":
            r = ex.dispatch("list_files", {"path": request.get("path", "")})
            if not r.ok:
                raise RuntimeError(r.error or "listFiles failed")
            return r.value
        if method == "readFile":
            r = ex.dispatch("read_file", {"path": request.get("path", ""),
                                          "max_bytes": int(request.get("maxBytes", 8 * 1024 * 1024))})
            if not r.ok:
                raise RuntimeError(r.error or "readFile failed")
            return r.value
        if method == "writeFile":
            file_info = request.get("file", {})
            r = ex.dispatch("write_file", {"path": file_info.get("path", ""),
                                           "content": file_info.get("content", "")})
            if not r.ok:
                raise RuntimeError(r.error or "writeFile failed")
            return r.value
        if method in {"screenObserve", "screenAct"}:
            return await self._dispatch_screen(method, request, ex, request_id=request_id)
        if method in {"browserObserve", "browserAct", "browserFind", "browserSession"}:
            return await self._dispatch_browser(method, request, ex)
        if method == "execute":
            raise RuntimeError("execute uses the streaming path")
        if method in {"cancelRunWork", "stop", "destroy", "workspaceDirectories", "exportWorkspace"}:
            raise NotImplementedError(f"{method} not enabled in this Windows gateway")
        raise ValueError(f"unsupported method: {method}")

    async def _dispatch_browser(self, method: str, request: dict, ex) -> Any:
        """Map browser methods to the single Playwright browser capability (Tier 3)."""
        session = str(request.get("session") or "private")
        grant_id = str(request.get("grantId") or "")
        if session not in {"private", "visible"}:
            raise ValueError("invalid browser session")
        if method == "browserSession":
            op = request.get("op", "status")
            r = ex.dispatch("browser_status", {}) if op == "status" else ex.dispatch(
                "browser_reset", {"session": session})
        elif method == "browserObserve":
            if request.get("url"):
                nav = ex.dispatch("browser_navigate", {
                    "url": request["url"], "session": session, "grant_id": grant_id,
                    "allow_domains": request.get("allowDomains"),
                })
                if not nav.ok:
                    raise RuntimeError(nav.error or "browser navigate failed")
            r = ex.dispatch("browser_observe", {
                "session": session, "grant_id": grant_id,
                "max_marks": int(request.get("maxMarks", 40)),
                "text_budget": int(request.get("textBudget", 600)),
                "host": str(request.get("host", "")),
            })
        elif method == "browserFind":
            r = ex.dispatch("browser_find", {
                "locator": request.get("locator", {}), "session": session,
                "grant_id": grant_id, "limit": int(request.get("limit", 10)),
            })
        elif method == "browserAct":
            r = ex.dispatch("browser_act", {
                "actions": request.get("actions", []), "session": session, "grant_id": grant_id,
            })
        else:
            raise ValueError(f"unsupported browser method {method}")
        if not r.ok:
            raise RuntimeError(r.error or "browser operation failed")
        return r.value

    async def _dispatch_screen(self, method: str, request: dict, ex,
                               *, request_id: Optional[str] = None) -> Any:
        """Map deployed HostScreen methods to the fast native executor."""
        app = request.get("app", "")
        rid = request_id or "-"
        if method == "screenObserve":
            query = request.get("query", "")
            # Prefer focused UIA tree (fast, ~34ms) over full screenshot (~38ms+encode).
            uia = ex.dispatch("uia_observe", {"query": query})
            tree = uia.value if isinstance(uia.value, str) else str(uia.value or "")
            observation = {"app": app, "tree": tree}
            if request.get("includeImage") or not tree.strip():
                shot = ex.dispatch("screenshot")
                if shot.ok and isinstance(shot.value, dict):
                    observation["image"] = shot.value
            logger.info("screen_observe | id=%s app=%s tier=%s tree_ms=%.1f chars=%d",
                        rid, app, uia.tier, uia.duration_ms, len(tree))
            return observation
        # screenAct
        actions = request.get("actions", [])
        if not isinstance(actions, list) or not (1 <= len(actions) <= 24):
            raise ValueError("invalid actions")
        completed = 0
        errors = []
        started = time.perf_counter()
        for action in actions:
            kind = action.get("kind")
            try:
                if kind == "click":
                    mark = action.get("element")
                    if mark is not None:
                        r = ex.dispatch("uia_invoke", {"mark_id": int(mark), "action": "click"})
                    elif action.get("x") is not None:
                        from charlie.tools import desktop_click_at
                        desktop_click_at(int(action["x"]), int(action.get("y", 0)),
                                         button=action.get("button", "left"),
                                         double=bool(action.get("double", False)))
                        r = None
                    else:
                        raise ValueError("click needs element or coordinates")
                elif kind == "type":
                    mark = action.get("element")
                    if mark is not None:
                        r = ex.dispatch("uia_invoke", {"mark_id": int(mark), "action": "type",
                                                       "text": action.get("text", "")})
                    else:
                        from charlie.tools import desktop_key
                        desktop_key(action.get("text", ""))
                        r = None
                elif kind == "key":
                    from charlie.tools import desktop_key

                    # Deployed HostScreen sends modifiers separately. Dropping them
                    # sent a bare Escape where Ctrl+Escape was requested.
                    remap = {"cmd": "win", "option": "alt", "ctrl": "ctrl", "shift": "shift"}
                    mods = [remap.get(str(m).lower(), str(m).lower())
                            for m in action.get("modifiers", [])]
                    chord = "+".join([*mods, str(action.get("key", ""))]).strip("+")
                    desktop_key(chord)
                    r = None
                elif kind == "wait":
                    await asyncio.sleep(max(0, min(int(action.get("ms", 0)), 5000)) / 1000)
                    r = None
                elif kind in {"menu", "set", "scroll"}:
                    # Never count an unimplemented action as completed.
                    raise NotImplementedError(f"{kind} is not supported by this Windows gateway")
                else:
                    raise ValueError(f"unsupported action kind {kind}")
                if r is not None and not r.ok:
                    errors.append(f"{kind}: {r.error}")
                else:
                    completed += 1
            except Exception as exc:
                errors.append(f"{kind}: {type(exc).__name__}: {exc}")
                break  # protocol: later actions are not attempted after a failure
        result = {"completed": completed}
        if errors:
            result["error"] = "; ".join(errors)[:1000]
        # Focused post-action observation, no forced 300ms sleep (native UIA readback).
        obs = ex.dispatch("uia_observe", {"query": ""})
        result["observation"] = {"app": app, "tree": obs.value if isinstance(obs.value, str) else str(obs.value or "")}
        logger.info("screen_act | id=%s app=%s kinds=%s completed=%d/%d error=%s ms=%.1f",
                    rid, app, [a.get("kind") for a in actions], completed, len(actions),
                    result.get("error", "")[:200], (time.perf_counter() - started) * 1000)
        return result

    async def _execute_stream(self, request: dict):
        """Yield ProcessEvent-shaped values for an execute request."""
        import subprocess

        command = request.get("command", {})
        argv = command.get("argv") or []
        if not argv or len(argv) > 1024:
            raise ValueError("invalid argv")
        timeout = command.get("timeoutMs", 60_000)
        timeout = max(1_000, min(int(timeout), 600_000))
        cwd = command.get("cwd") or str(Path.home())
        logger.info("device_exec | argv=%s | cwd=%s", [str(a)[:120] for a in argv][:8], cwd[:160])
        try:
            result = subprocess.run(
                [str(a) for a in argv],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout / 1000,
                shell=False,
            )
            stdout = result.stdout[:100_000]
            stderr = result.stderr[:100_000]
            if stdout:
                yield {"type": "stdout", "data": stdout}
            if stderr:
                yield {"type": "stderr", "data": stderr}
            yield {"type": "exit", "code": result.returncode}
        except subprocess.TimeoutExpired:
            yield {"type": "stderr", "data": f"command timed out after {timeout}ms"}
            yield {"type": "exit", "code": 124}
        except Exception as exc:
            yield {"type": "stderr", "data": f"{type(exc).__name__}: {exc}"}
            yield {"type": "exit", "code": 1}

    async def _handle_message(self, ws, raw: str) -> None:
        try:
            message = json.loads(raw)
        except (ValueError, TypeError):
            logger.warning("ignored non-JSON device message")
            return
        kind = message.get("type")
        if kind == "revoked":
            logger.error("device revoked by Ubuntu | reason=%s", message.get("reason"))
            _write_status({"state": "revoked", "pid": os.getpid(),
                           "reason": str(message.get("reason") or "")[:200]})
            self._stop.set()
            return
        if kind == "cancel":
            request_id = message.get("id", "")
            task = self._active.get(request_id)
            if task and not task.done():
                task.cancel()
                logger.info("device request cancelled | id=%s", request_id)
            else:
                logger.info("device cancel ignored | id=%s reason=not_active", request_id)
            return
        if kind != "request":
            return
        request_id = message.get("id", "")
        request = message.get("request")
        if not isinstance(request, dict) or not request_id:
            return
        # Dedupe: a reconnect must never repeat an already-seen request.
        if request_id in self._seen:
            logger.info("duplicate device request ignored | id=%s", request_id)
            await self._emit(ws, request_id, {"error": "duplicate request"})
            return
        self._seen.add(request_id)
        if len(self._seen) > 5000:
            self._seen = set(list(self._seen)[-2500:])
        logger.info("device request accepted | id=%s method=%s app=%s bot=%s run=%s",
                    request_id, request.get("method", ""), str(request.get("app", ""))[:80],
                    str(request.get("botId", ""))[:40], str(request.get("runId", ""))[:40])
        self._ws = ws
        task = asyncio.create_task(
            self._execute_request(ws, request_id, request),
            name=f"device-request-{request_id}",
        )
        self._active[request_id] = task
        self._task_ws[request_id] = ws

        def _finished(_task: asyncio.Task, rid: str = request_id) -> None:
            self._active.pop(rid, None)
            self._task_ws.pop(rid, None)

        task.add_done_callback(_finished)

    async def run(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            try:
                async with websockets.connect(
                    self._url(),
                    additional_headers=self._headers(),
                    ping_interval=PING_INTERVAL_S,
                    ping_timeout=PING_INTERVAL_S * 2,
                    max_size=12 * 1024 * 1024 + 64 * 1024,
                    open_timeout=15,
                ) as ws:
                    self._ws = ws
                    attempt = 0
                    logger.info("device gateway connected | device=%s", self.state.get("deviceId"))
                    _write_status({"state": "connected", "pid": os.getpid(),
                                   "device_id": self.state.get("deviceId")})
                    # Bounded recv: an emergency stop must end the loop promptly so the
                    # lifecycle can write an accurate stopped status.
                    last_heartbeat_write = 0.0
                    try:
                        while not self._stop.is_set():
                            try:
                                raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                            except asyncio.TimeoutError:
                                if time.monotonic() - last_heartbeat_write >= 15.0:
                                    _write_status({"state": "connected", "pid": os.getpid(),
                                                   "device_id": self.state.get("deviceId")})
                                    last_heartbeat_write = time.monotonic()
                                continue
                            await self._handle_message(ws, raw)
                    finally:
                        await self._cancel_socket_tasks(ws)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("device gateway disconnect | error=%s", type(exc).__name__)
                _write_status({"state": "reconnecting", "pid": os.getpid(),
                               "error": type(exc).__name__})
                if getattr(exc, "response", None) is not None:
                    status = getattr(exc.response, "status_code", None)
                    if status in (401, 403):
                        logger.error("device token rejected; stopping gateway")
                        _write_status({"state": "rejected", "pid": os.getpid()})
                        break
            if self._stop.is_set():
                break
            delay = min(RECONNECT_MAX_S, RECONNECT_MIN_S * 2 ** attempt)
            delay = delay / 2 + random.random() * delay
            attempt += 1
            await asyncio.sleep(delay)

    async def _cancel_socket_tasks(self, ws) -> None:
        """In-flight work dies with its socket; a reconnect cannot inherit it."""
        for request_id, task in list(self._active.items()):
            if self._task_ws.get(request_id) is not ws:
                continue
            if not task.done():
                task.cancel()
                logger.info("device request aborted on disconnect | id=%s", request_id)

    async def stop(self) -> None:
        self._stop.set()
        ws = self._ws
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass
        for task in list(self._active.values()):
            task.cancel()


def status_path() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "Charlie" / "device-gateway.status.json"


def emergency_stop_path() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "Charlie" / "device-gateway.stop"


def _write_status(status: dict) -> None:
    try:
        path = status_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(status)
        payload["heartbeat_at"] = int(time.time())
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, separators=(",", ":")))
        os.replace(temp, path)
    except Exception:
        pass


def read_gateway_status() -> dict:
    """Status corrected by process liveness; a crashed gateway is not 'connected'."""
    try:
        data = json.loads(status_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"state": "unknown"}
    if data.get("state") in {"connected", "running", "reconnecting"}:
        import psutil

        pid = data.get("pid")
        if not pid or not psutil.pid_exists(int(pid)):
            return {**data, "state": "stale", "reason": "gateway process not running"}
    return data


async def run_device_gateway() -> int:
    """Canonical device-gateway lifecycle: single instance, status, emergency stop."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    from charlie.single_instance import SingleInstanceError, SingleInstanceLock

    try:
        lock = SingleInstanceLock(path="data/charlie-device-gateway.lock")
    except TypeError:
        lock = SingleInstanceLock()
    try:
        try:
            lock.acquire()
        except SingleInstanceError:
            logger.error("device gateway already running (single-instance)")
            return 1
        try:
            # Single-instance lock is held, so any stop file here is stale from a prior run.
            stale_stop = emergency_stop_path()
            if stale_stop.exists():
                stale_stop.unlink(missing_ok=True)
                logger.info("stale emergency-stop file cleared at startup")
            _write_status({"state": "starting", "pid": os.getpid(),
                           "device_id": load_device_state().get("deviceId")})
            gateway = DeviceGateway()

            async def watch_stop():
                path = emergency_stop_path()
                while not gateway._stop.is_set():
                    if path.exists():
                        logger.warning("emergency stop requested")
                        await gateway.stop()
                        try:
                            path.unlink()
                        except OSError:
                            pass
                        break
                    await asyncio.sleep(1.0)

            stop_task = asyncio.create_task(watch_stop())
            try:
                _write_status({"state": "running", "pid": os.getpid(),
                               "device_id": gateway.state.get("deviceId")})
                await gateway.run()
            finally:
                stop_task.cancel()
                logger.info("device gateway exiting | pid=%s", os.getpid())
                _write_status({"state": "stopped", "pid": os.getpid()})
            return 0
        finally:
            lock.release()
    except Exception as exc:
        logger.error("device gateway lifecycle failed | %s", type(exc).__name__)
        _write_status({"state": "error", "error": type(exc).__name__})
        return 2


async def _main() -> int:
    return await run_device_gateway()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))

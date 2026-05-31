"""
charlie/watchdog/brain_rpc.py

Cross-process Brain RPC over multiprocessing queues.

The daemon-process ``ControlServer`` runs in a different process from the
``Brain``. Direct attribute access (``getattr(self.daemon, "_brain", None)``)
therefore always yields ``None`` and the dashboard data pages come back empty.
This module provides a small request/response RPC so the ControlServer can
fetch live data from the separate Brain process over a pair of
``multiprocessing.Manager().Queue()`` objects.

Two halves:

``BrainRPCServer``  — runs as a daemon thread inside the Brain process. It
                      drains the request queue, dispatches each ``op`` to a
                      handler that reads from the live ``brain`` object, and
                      puts an :class:`RPCResponse` on the response queue. Every
                      handler is defensively guarded so a missing attribute
                      yields ``ok=False`` rather than crashing the loop.

``BrainRPCClient``  — lives in the daemon (ControlServer) process. A background
                      daemon thread drains the response queue and resolves
                      pending requests correlated by ``request_id``. The
                      synchronous :meth:`BrainRPCClient.request` blocks up to a
                      timeout; :meth:`BrainRPCClient.request_async` wraps it in
                      ``asyncio.to_thread`` so aiohttp handlers can await it
                      without blocking the event loop.

Supported ops:
    GET_TASKS            → brain.task_mgr / brain.task_manager
    GET_TOOL_LOG         → brain.outcome_tracker.get_tool_exec_log
    GET_AUTOMATION_RULES → brain.rule_engine.get_all_rules
    TOGGLE_RULE          → brain.rule_engine
    GET_AGENT_STATUS     → brain.orchestrator (best-effort)
    GET_OUTCOMES         → brain.outcome_tracker.get_recent_outcomes
    SEARCH               → tool exec log + tasks
    GET_SETTINGS         → charlie.config.settings (JSON-safe subset)
"""

from __future__ import annotations

import asyncio
import json
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from charlie.utils.logger import get_logger

logger = get_logger("BrainRPC")


# ── Data models ───────────────────────────────────────────────────────────────


@dataclass
class RPCRequest:
    """A typed request sent from the ControlServer → Brain.

    All fields are JSON-safe so the request can travel over a manager queue or
    be serialized for logging.
    """

    request_id: str
    op: str
    params: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"request_id": self.request_id, "op": self.op, "params": self.params}

    @classmethod
    def from_dict(cls, d: dict) -> "RPCRequest":
        return cls(
            request_id=d.get("request_id", ""),
            op=d.get("op", ""),
            params=d.get("params") or {},
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, s: str) -> "RPCRequest":
        return cls.from_dict(json.loads(s))


@dataclass
class RPCResponse:
    """A typed response sent from the Brain → ControlServer.

    ``data`` only ever contains JSON-serializable structures — the server-side
    handlers convert objects to dicts before populating it.
    """

    request_id: str
    ok: bool
    data: Any = None
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "ok": self.ok,
            "data": self.data,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RPCResponse":
        return cls(
            request_id=d.get("request_id", ""),
            ok=bool(d.get("ok", False)),
            data=d.get("data"),
            error=d.get("error"),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, s: str) -> "RPCResponse":
        return cls.from_dict(json.loads(s))


def _risk_tier_name(value: Any) -> Any:
    """Best-effort JSON-safe rendering of a RiskTier-like value."""
    if value is None:
        return None
    name = getattr(value, "name", None)
    if name is not None:
        return name
    return value


# ── Server (Brain process) ────────────────────────────────────────────────────


class BrainRPCServer:
    """Daemon thread that services RPC requests inside the Brain process.

    Usage::

        server = BrainRPCServer(brain, req_q, res_q)
        server.start()
        ...
        server.stop()
    """

    def __init__(self, brain: Any, req_q: Any, res_q: Any) -> None:
        self.brain = brain
        self.req_q = req_q
        self.res_q = res_q
        self._running = False
        self._thread: threading.Thread | None = None

        # Dispatch table: op string → bound handler. Each handler takes the
        # request params dict and returns a JSON-safe result.
        self._handlers: dict[str, Callable[[dict], Any]] = {
            "PING": self._handle_ping,
            "GET_TASKS": self._handle_get_tasks,
            "GET_TOOL_LOG": self._handle_get_tool_log,
            "GET_AUTOMATION_RULES": self._handle_get_automation_rules,
            "TOGGLE_RULE": self._handle_toggle_rule,
            "GET_AGENT_STATUS": self._handle_get_agent_status,
            "GET_OUTCOMES": self._handle_get_outcomes,
            "SEARCH": self._handle_search,
            "GET_SETTINGS": self._handle_get_settings,
        }

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> threading.Thread:
        """Launch ``serve_forever`` on a daemon thread."""
        if self._thread is not None and self._thread.is_alive():
            return self._thread
        self._running = True
        self._thread = threading.Thread(
            target=self.serve_forever, name="BrainRPCServer", daemon=True
        )
        self._thread.start()
        return self._thread

    def stop(self) -> None:
        """Signal the serve loop to exit on its next iteration."""
        self._running = False

    # ── Serve loop ──────────────────────────────────────────────────────────-

    def serve_forever(self) -> None:
        """Drain the request queue and answer each request.

        Wrapped so a single bad request or handler exception never kills the
        server: any failure is reported back as ``RPCResponse(ok=False)``.
        """
        logger.info("brain_rpc_server_started")
        while self._running:
            try:
                item = self.req_q.get(timeout=0.5)
            except queue.Empty:
                continue
            except Exception:
                # Manager-proxy timeouts and the like surface as generic
                # exceptions; treat them like an empty poll and keep looping.
                continue

            request_id = "unknown"
            try:
                req = self._coerce_request(item)
                if req is None:
                    logger.warning(
                        "brain_rpc_bad_request_item | type=%s", type(item).__name__
                    )
                    continue
                request_id = req.request_id

                handler = self._handlers.get(req.op)
                if handler is None:
                    resp = RPCResponse(
                        request_id=request_id,
                        ok=False,
                        error=f"unknown_op:{req.op}",
                    )
                else:
                    data = handler(req.params or {})
                    resp = RPCResponse(request_id=request_id, ok=True, data=data)
            except Exception as e:
                logger.error("brain_rpc_dispatch_failed | %s", e, exc_info=True)
                resp = RPCResponse(request_id=request_id, ok=False, error=str(e))

            try:
                self.res_q.put(resp)
            except Exception as e:
                logger.error("brain_rpc_response_put_failed | %s", e)

        logger.info("brain_rpc_server_stopped")

    @staticmethod
    def _coerce_request(item: Any) -> RPCRequest | None:
        """Accept an RPCRequest, a dict, or a JSON string."""
        if isinstance(item, RPCRequest):
            return item
        if isinstance(item, dict):
            return RPCRequest.from_dict(item)
        if isinstance(item, str):
            try:
                return RPCRequest.from_json(item)
            except Exception:
                return None
        return None

    # ── Op handlers (all defensively guarded) ──────────────────────────────────

    @staticmethod
    def _handle_ping(params: dict) -> dict:
        """PING → simple liveness probe.  Returns immediately."""
        return {"pong": True}

    def _handle_get_tasks(self, params: dict) -> list:
        """GET_TASKS → serialized task dicts, or [] if unavailable."""
        brain = self.brain
        tm = getattr(brain, "task_mgr", None) or getattr(brain, "task_manager", None)
        if tm is None or not hasattr(tm, "get_all_tasks"):
            return []
        tasks = []
        for t in tm.get_all_tasks() or []:
            tasks.append(t.to_dict() if hasattr(t, "to_dict") else t)
        return tasks

    def _handle_get_tool_log(self, params: dict) -> list:
        """GET_TOOL_LOG → brain.outcome_tracker.get_tool_exec_log(limit=...)."""
        brain = self.brain
        limit = int(params.get("limit", 50))
        tracker = getattr(brain, "outcome_tracker", None)
        if tracker is None or not hasattr(tracker, "get_tool_exec_log"):
            return []
        log = tracker.get_tool_exec_log(limit=limit)
        return log if log is not None else []

    def _handle_get_automation_rules(self, params: dict) -> list:
        """GET_AUTOMATION_RULES → list of rule dicts."""
        brain = self.brain
        engine = getattr(brain, "rule_engine", None)
        if engine is None or not hasattr(engine, "get_all_rules"):
            return []
        rules = engine.get_all_rules() or []
        out = []
        for r in rules:
            if isinstance(r, dict):
                out.append(r)
            elif hasattr(r, "to_dict"):
                out.append(r.to_dict())
            else:
                out.append(
                    {
                        "name": getattr(r, "name", ""),
                        "trigger": getattr(r, "trigger", ""),
                        "action": getattr(r, "action", ""),
                        "enabled": getattr(r, "enabled", True),
                        "risk_tier": _risk_tier_name(getattr(r, "risk_tier", None)),
                    }
                )
        return out

    def _handle_toggle_rule(self, params: dict) -> dict:
        """TOGGLE_RULE → flip the named rule and report the new state."""
        brain = self.brain
        name = params.get("name", "")
        engine = getattr(brain, "rule_engine", None)
        if engine is None:
            raise RuntimeError("rule_engine_unavailable")
        rule = engine.get_rule(name) if hasattr(engine, "get_rule") else None
        if rule is None:
            raise ValueError(f"rule_not_found:{name}")
        new_state = not getattr(rule, "enabled", False)
        if hasattr(engine, "update_rule"):
            engine.update_rule(name, enabled=new_state)
        if hasattr(engine, "save_rules"):
            engine.save_rules()
        return {"name": name, "enabled": new_state}

    def _handle_get_agent_status(self, params: dict) -> list:
        """GET_AGENT_STATUS → best-effort agent list from brain.orchestrator."""
        brain = self.brain
        orch = getattr(brain, "orchestrator", None)
        if orch is None:
            return []
        for attr in ("get_agent_status", "get_status", "agent_status"):
            method = getattr(orch, attr, None)
            if callable(method):
                try:
                    result = method()
                    return result if result is not None else []
                except Exception:
                    return []
        # Fall back to a plain ``agents`` collection if exposed.
        agents = getattr(orch, "agents", None)
        if agents is None:
            return []
        out = []
        try:
            for a in agents:
                if isinstance(a, dict):
                    out.append(a)
                elif hasattr(a, "to_dict"):
                    out.append(a.to_dict())
                else:
                    out.append(
                        {
                            "id": getattr(a, "id", getattr(a, "name", "")),
                            "name": getattr(a, "name", ""),
                            "status": getattr(a, "status", "idle"),
                        }
                    )
        except Exception:
            return []
        return out

    def _handle_get_outcomes(self, params: dict) -> list:
        """GET_OUTCOMES → recent outcomes mapped to dicts."""
        brain = self.brain
        limit = int(params.get("limit", 50))
        tracker = getattr(brain, "outcome_tracker", None)
        if tracker is None or not hasattr(tracker, "get_recent_outcomes"):
            return []
        outcomes = tracker.get_recent_outcomes(limit=limit) or []
        out = []
        for o in outcomes:
            if hasattr(o, "to_dict"):
                out.append(o.to_dict())
            elif isinstance(o, dict):
                out.append(o)
            else:
                out.append(str(o))
        return out

    def _handle_search(self, params: dict) -> list:
        """SEARCH → matches across the tool exec log and tasks."""
        brain = self.brain
        query = str(params.get("query", "")).strip().lower()
        if not query:
            return []

        results: list[dict] = []

        # Tool execution log
        tracker = getattr(brain, "outcome_tracker", None)
        if tracker is not None and hasattr(tracker, "get_tool_exec_log"):
            try:
                for entry in tracker.get_tool_exec_log(limit=100) or []:
                    if isinstance(entry, dict):
                        d = entry
                    elif hasattr(entry, "to_dict"):
                        d = entry.to_dict()
                    else:
                        d = {}
                    tool_name = str(d.get("tool_name", d.get("tool", "")))
                    content = str(
                        d.get("detail", d.get("details", d.get("outcome", "")))
                    )
                    if query in tool_name.lower() or query in content.lower():
                        results.append(
                            {
                                "source": "tools",
                                "category": tool_name,
                                "content": content[:300],
                            }
                        )
            except Exception:
                pass

        # Tasks
        tm = getattr(brain, "task_mgr", None) or getattr(brain, "task_manager", None)
        if tm is not None and hasattr(tm, "get_all_tasks"):
            try:
                for task in tm.get_all_tasks() or []:
                    name = str(getattr(task, "name", "") or "")
                    desc = str(getattr(task, "description", "") or "")
                    if query in name.lower() or query in desc.lower():
                        results.append(
                            {
                                "source": "tasks",
                                "category": name,
                                "content": desc[:300],
                            }
                        )
            except Exception:
                pass

        return results

    def _handle_get_settings(self, params: dict) -> dict:
        """GET_SETTINGS → JSON-safe subset of key settings, built defensively."""
        try:
            from charlie.config import settings
        except Exception as e:
            logger.warning("brain_rpc_settings_import_failed | %s", e)
            return {}

        def g(root: Any, *path: str, default: Any = None) -> Any:
            cur = root
            for attr in path:
                cur = getattr(cur, attr, None)
                if cur is None:
                    return default
            return cur

        return {
            "llm": {
                "primary_model": g(settings, "llm", "primary_model"),
            },
            "audio": {
                "voice_mode": g(settings, "audio", "voice_mode"),
            },
            "security": {
                "self_modify_enabled": g(
                    settings, "security", "self_modify_enabled", default=False
                ),
                "auto_patcher_enabled": g(
                    settings, "security", "auto_patcher_enabled", default=False
                ),
                "require_confirmation_tier1": g(
                    settings, "security", "require_confirmation_tier1", default=True
                ),
            },
            "resources": {
                "vram_budget_mb": g(settings, "resources", "vram_budget_mb"),
            },
            "persona": {
                "address_user_as": g(settings, "persona", "address_user_as"),
            },
        }


# ── Client (daemon / ControlServer process) ───────────────────────────────────


class BrainRPCClient:
    """Request/response client correlated by ``request_id``.

    A background daemon thread drains the response queue and hands each
    response to the waiting caller through a per-request ``queue.Queue``.

    Usage::

        client = BrainRPCClient(req_q, res_q, timeout=5.0)
        client.start()
        resp = client.request("GET_TASKS")          # sync
        resp = await client.request_async("GET_TASKS")  # async
        client.stop()
    """

    # Retry parameters for transient Manager proxy failures.
    _MAX_RETRIES = 2
    _RETRY_BACKOFF = 0.5  # seconds; doubles each retry

    def __init__(self, req_q: Any, res_q: Any, timeout: float = 10.0) -> None:
        self.req_q = req_q
        self.res_q = res_q
        self.timeout = timeout
        # request_id → queue.Queue(maxsize=1) holding the single response.
        self._pending: dict[str, queue.Queue] = {}
        self._pending_lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background response-draining thread.

        No-op when the queues are unavailable (the client degrades to returning
        ``brain_rpc_unavailable`` from :meth:`request`).
        """
        if self.req_q is None or self.res_q is None:
            logger.warning("brain_rpc_client_no_queues | drain_thread_not_started")
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._drain_loop, name="BrainRPCClient", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Signal the drain thread to exit on its next iteration."""
        self._running = False

    # ── Drain loop ──────────────────────────────────────────────────────────-

    def _drain_loop(self) -> None:
        logger.debug("brain_rpc_client_drain_started")
        while self._running:
            try:
                item = self.res_q.get(timeout=0.5)
            except queue.Empty:
                continue
            except Exception:
                continue

            if isinstance(item, RPCResponse):
                resp = item
            elif isinstance(item, dict):
                resp = RPCResponse.from_dict(item)
            elif isinstance(item, str):
                try:
                    resp = RPCResponse.from_json(item)
                except Exception:
                    continue
            else:
                continue

            with self._pending_lock:
                waiter = self._pending.get(resp.request_id)
            if waiter is not None:
                try:
                    waiter.put_nowait(resp)
                except queue.Full:
                    pass
        logger.debug("brain_rpc_client_drain_stopped")

    # ── Public API ────────────────────────────────────────────────────────────

    def request(self, op: str, params: dict | None = None) -> RPCResponse:
        """Send a request and block for the matching response.

        Returns ``RPCResponse(ok=False, error="brain_rpc_unavailable")`` if the
        queues are not configured, or ``error="brain_rpc_timeout"`` if no
        response arrives within ``self.timeout`` seconds.

        Automatically retries on timeout up to ``_MAX_RETRIES`` times with
        exponential backoff to survive transient Manager proxy delays
        (especially during Brain startup).
        """
        if self.req_q is None or self.res_q is None:
            return RPCResponse(request_id="", ok=False, error="brain_rpc_unavailable")

        last_error = "brain_rpc_timeout"
        backoff = self._RETRY_BACKOFF

        for attempt in range(1 + self._MAX_RETRIES):
            request_id = uuid.uuid4().hex
            req = RPCRequest(request_id=request_id, op=op, params=params or {})

            waiter: queue.Queue = queue.Queue(maxsize=1)
            with self._pending_lock:
                self._pending[request_id] = waiter

            try:
                try:
                    self.req_q.put(req, timeout=2.0)
                except Exception as e:
                    logger.error("brain_rpc_request_put_failed | op=%s | attempt=%d | %s", op, attempt, e)
                    return RPCResponse(
                        request_id=request_id,
                        ok=False,
                        error=f"brain_rpc_put_failed:{e}",
                    )

                try:
                    resp = waiter.get(timeout=self.timeout)
                    # Success or application-level error — return immediately.
                    return resp
                except queue.Empty:
                    last_error = "brain_rpc_timeout"
                    if attempt < self._MAX_RETRIES:
                        logger.warning(
                            "brain_rpc_timeout_retry | op=%s | attempt=%d/%d | backoff=%.1fs",
                            op, attempt + 1, 1 + self._MAX_RETRIES, backoff,
                        )
                        time.sleep(backoff)
                        backoff *= 2
                    else:
                        logger.warning("brain_rpc_timeout | op=%s | attempts_exhausted", op)
            finally:
                with self._pending_lock:
                    self._pending.pop(request_id, None)

        return RPCResponse(request_id="", ok=False, error=last_error)

    async def request_async(
        self, op: str, params: dict | None = None
    ) -> RPCResponse:
        """Await :meth:`request` on a worker thread without blocking the loop."""
        return await asyncio.to_thread(self.request, op, params)

    def wait_until_ready(self, timeout: float = 15.0, interval: float = 1.0) -> bool:
        """Block until the Brain RPC server responds to a PING, or *timeout* elapses.

        Called by the ControlServer (once, in a background thread) after the
        Brain process has been started so that subsequent RPC calls do not
        race against Brain initialization.

        Returns ``True`` when the server is ready, ``False`` on timeout.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            resp = self.request("PING")
            if resp.ok:
                logger.info("brain_rpc_server_ready")
                return True
            time.sleep(interval)
        logger.warning("brain_rpc_ready_timeout | timeout=%.1fs", timeout)
        return False

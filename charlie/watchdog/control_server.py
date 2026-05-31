"""
charlie/watchdog/control_server.py

ControlServer — HTTP REST API + WebSocket for daemon control.
Runs on localhost:8090 (separate from globe's 8089).
Binds to 127.0.0.1 only — no external exposure.
"""

import asyncio
import json
import os
import secrets
import time

from aiohttp import web

from charlie.utils.logger import get_logger
from charlie.watchdog.status_events import STATUS_EVENT_MAP

logger = get_logger("ControlServer")

# Message types that get forwarded from status_q to WS clients.
# Imported from the single canonical definition so there is exactly one map.
WS_FORWARD_TYPES = set(STATUS_EVENT_MAP.keys())


class ControlServer:
    """
    Daemon control server. REST + WebSocket on localhost:8090.

    Provides:
    - /api/status — daemon and subsystem health
    - /api/subsystems — detailed per-process health
    - /api/subsystems/{name}/restart — restart a subsystem
    - /api/approvals — pending approval queue
    - /api/control/shutdown|reboot
    - /ws/events — real-time event stream
    """

    def __init__(self, daemon=None, port: int = 8090):
        self.daemon = daemon
        self.port = port
        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None
        self._ws_clients: list[web.WebSocketResponse] = []
        self._running = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pending_approvals: dict = {}
        self._last_briefing: dict = {}
        self._token = os.environ.get("CONTROL_SERVER_TOKEN") or secrets.token_urlsafe(32)
        self._token_endpoint_added = False  # deferred to first request

        # Cross-process Brain RPC client (Design §D, Reqs 7.1-7.10)
        self.brain_rpc: "BrainRPCClient | None" = None
        if daemon is not None:
            req_q = getattr(daemon, "brain_req_q", None)
            res_q = getattr(daemon, "brain_res_q", None)
            if req_q is not None and res_q is not None:
                from charlie.watchdog.brain_rpc import BrainRPCClient
                self.brain_rpc = BrainRPCClient(req_q, res_q)
                self.brain_rpc.start()

                # Probe the Brain RPC server in the background so the first
                # real request does not race against Brain initialization.
                # wait_until_ready sends PINGs until the server responds or
                # a timeout elapses; the result is only logged.
                import threading as _thr
                _thr.Thread(
                    target=self.brain_rpc.wait_until_ready,
                    kwargs={"timeout": 20.0},
                    daemon=True,
                    name="BrainRPCReadyProbe",
                ).start()

    def start(self):
        """Start the control server (blocking, run in thread)."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._start_async())
        except Exception as e:
            logger.error("control_server_start_failed", error=str(e))

    @web.middleware
    async def _rate_limit_middleware(self, request, handler):
        """Simple rate limiting: 100 requests per minute per IP."""
        now = time.time()
        remote = request.remote or "unknown"
        if not hasattr(self, '_rate_limits'):
            self._rate_limits = {}
        # Clean old entries
        self._rate_limits = {k: v for k, v in self._rate_limits.items() if now - v[-1] < 60}
        if remote not in self._rate_limits:
            self._rate_limits[remote] = []
        self._rate_limits[remote].append(now)
        # Keep only last 100 timestamps
        self._rate_limits[remote] = self._rate_limits[remote][-100:]
        if len(self._rate_limits[remote]) >= 100:
            oldest = self._rate_limits[remote][0]
            if now - oldest < 60:
                return web.json_response({"error": "Rate limit exceeded"}, status=429)
        return await handler(request)

    async def _start_async(self):
        """Async startup."""
        self._app = web.Application(middlewares=[self._rate_limit_middleware, self._token_auth_middleware, self._cors_middleware])
        self._setup_routes()

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", self.port)
        try:
            await site.start()
            self._running = True
            logger.info(f"control_server_started | http://127.0.0.1:{self.port}")
        except OSError as e:
            logger.error(
                f"control_server_port_failed | port={self.port} | error={e} | "
                f"hint='Is another CHARLIE instance already running?'"
            )
            self._running = False
            # Push SRE status alert if supervisor queues are available
            if self.daemon and hasattr(self.daemon, "status_q") and self.daemon.status_q:
                try:
                    self.daemon.status_q.put_nowait({
                        "type": "PHOENIX_ALERT",
                        "content": f"ControlServer bind failed on port {self.port}. Is another instance running?"
                    })
                except Exception:
                    pass
            return

        # Keep running until stopped
        try:
            while self._running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass

    async def stop(self):
        """Stop the control server."""
        self._running = False
        if self._runner:
            await self._runner.cleanup()
        logger.info("control_server_stopped")

    @web.middleware
    async def _cors_middleware(self, request, handler):
        """CORS middleware for dashboard cross-origin access."""
        if request.method == "OPTIONS":
            response = web.Response()
        else:
            try:
                response = await handler(request)
            except web.HTTPException as exc:
                response = exc
        origin = request.headers.get("Origin", "")
        # Allow any localhost/127.0.0.1 origin for dev flexibility
        if origin and ("localhost" in origin or "127.0.0.1" in origin):
            response.headers["Access-Control-Allow-Origin"] = origin
        else:
            response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Control-Token"
        response.headers["Access-Control-Allow-Credentials"] = "true"
        return response

    @web.middleware
    async def _token_auth_middleware(self, request, handler):
        """Token auth: all requests require X-Control-Token.

        The server binds to 127.0.0.1, so only localhost processes can connect.
        Token auth ensures only the dashboard (which knows the token) can call
        privileged endpoints like /api/control/shutdown.
        """
        # Allow unauthenticated access to the token bootstrap endpoint
        if request.path == "/api/token":
            return await handler(request)

        # Allow CORS preflight without auth
        if request.method == "OPTIONS":
            return await handler(request)

        # Allow dashboard requests from localhost without token
        remote = request.remote or ""
        if remote in ("127.0.0.1", "::1", "localhost"):
            return await handler(request)

        # Accept token from header OR query param (for WebSocket)
        token = request.headers.get("X-Control-Token", "")
        if not token:
            token = request.query.get("token", "")
        if not secrets.compare_digest(token, self._token):
            remote = request.remote or "unknown"
            logger.warning(f"unauthorized_control_server_access | ip={remote} | path={request.path}")
            # Return 401 with CORS headers so browser shows clean error
            response = web.json_response({"error": "Invalid or missing X-Control-Token"}, status=401)
            response.headers["Access-Control-Allow-Origin"] = "http://localhost:3000"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Control-Token"
            response.headers["Access-Control-Allow-Credentials"] = "true"
            return response
        return await handler(request)

    def _setup_routes(self):
        """Set up HTTP routes and WebSocket."""
        # Status endpoints
        self._app.router.add_get("/api/status", self._handle_status)
        self._app.router.add_get("/api/subsystems", self._handle_subsystems)

        # Doctor self-check (read-only, fast)
        self._app.router.add_get("/api/doctor", self._handle_doctor)

        # Subsystem control
        self._app.router.add_post("/api/subsystems/{name}/restart", self._handle_restart_subsystem)
        self._app.router.add_post("/api/subsystems/{name}/stop", self._handle_stop_subsystem)

        # Approval endpoints
        self._app.router.add_get("/api/approvals", self._handle_get_approvals)
        self._app.router.add_post("/api/approvals/{id}/approve", self._handle_approve)
        self._app.router.add_post("/api/approvals/{id}/deny", self._handle_deny)

        # Control endpoints
        self._app.router.add_post("/api/control/shutdown", self._handle_shutdown)
        self._app.router.add_post("/api/control/reboot", self._handle_reboot)

        # Settings
        self._app.router.add_get("/api/settings", self._handle_get_settings)
        self._app.router.add_post("/api/settings", self._handle_post_settings)

        # Unified search
        self._app.router.add_get("/api/search", self._handle_unified_search)

        # Automation rules toggle
        self._app.router.add_post("/api/automation/rules/{name}/toggle", self._handle_toggle_rule)

        # Memory timeline
        self._app.router.add_get("/api/memory/search", self._handle_memory_search)

        # Integrations
        self._app.router.add_get("/api/integrations", self._handle_get_integrations)

        # Automation
        self._app.router.add_get("/api/automation/rules", self._handle_get_rules)

        # Briefing
        self._app.router.add_get("/api/briefing", self._handle_get_briefing)
        self._app.router.add_post("/api/briefing/run", self._handle_run_briefing)

        # Token (no auth required — used by dashboard to bootstrap)
        self._app.router.add_get("/api/token", self._handle_get_token)

        # Chat history
        self._app.router.add_get("/api/chat/history", self._handle_chat_history)
        self._app.router.add_post("/api/chat/message", self._handle_chat_message)
        self._app.router.add_post("/api/chat/send", self._handle_chat_message)  # alias for dashboard

        # Globe data
        self._app.router.add_get("/api/globe/data", self._handle_globe_data)
        self._app.router.add_post("/api/globe/refresh", self._handle_globe_refresh)

        # Agents, skills, tools
        self._app.router.add_get("/api/agents/status", self._handle_agents_status)
        self._app.router.add_get("/api/skills", self._handle_skills)
        self._app.router.add_get("/api/tools/log", self._handle_tools_log)
        self._app.router.add_get("/api/logs", self._handle_logs)
        self._app.router.add_get("/api/evolution", self._handle_evolution)

        # Voice, globe, tasks, MCP
        self._app.router.add_get("/api/voice/status", self._handle_voice_status)
        self._app.router.add_get("/api/globe/status", self._handle_globe_status)
        self._app.router.add_post("/api/control/globe/launch", self._handle_globe_launch)
        self._app.router.add_get("/api/tasks", self._handle_tasks)
        self._app.router.add_post("/api/tasks/{task_id}/cancel", self._handle_cancel_task)
        self._app.router.add_get("/api/mcp/servers", self._handle_mcp_servers)
        self._app.router.add_post("/api/mcp/{server_id}/toggle", self._handle_toggle_mcp)

        # WebSocket
        self._app.router.add_get("/ws/events", self._handle_ws)

    # ── Status endpoints ──

    async def _handle_status(self, request):
        """GET /api/status — daemon and subsystem health."""
        if not self.daemon:
            return web.json_response({"error": "daemon_not_attached"}, status=503)

        try:
            status = await asyncio.to_thread(self.daemon.get_daemon_status)
            return web.json_response(status)
        except Exception as e:
            logger.error("status_error", error=str(e))
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_subsystems(self, request):
        """GET /api/subsystems — detailed per-process health."""
        if not self.daemon:
            return web.json_response({"error": "daemon_not_attached"}, status=503)

        try:
            status = await asyncio.to_thread(self.daemon.get_daemon_status)
            return web.json_response(status.get("subsystems", {}))
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_doctor(self, request):
        """GET /api/doctor — run Doctor self-check and return JSON report."""
        try:
            from charlie.utils.doctor import run_self_check
            from dataclasses import asdict

            report = await asyncio.to_thread(run_self_check)
            return web.json_response(asdict(report))
        except Exception as e:
            logger.error("doctor_endpoint_failed", error=str(e))
            return web.json_response({"error": str(e)}, status=500)

    # ── Subsystem control ──

    async def _handle_restart_subsystem(self, request):
        """POST /api/subsystems/{name}/restart — restart a subsystem."""
        name = request.match_info["name"]
        if not self.daemon:
            return web.json_response({"error": "daemon_not_attached"}, status=503)

        try:
            # Stop first if running
            if name in self.daemon.processes:
                p = self.daemon.processes[name]["process"]
                if p.is_alive():
                    p.terminate()
                    p.join(timeout=5)
                    if p.is_alive():
                        p.kill()

            # Restart using the appropriate entry point
            entry_points = {
                "Audio": self.daemon._run_audio_safe,
                "Brain": self.daemon._run_brain_safe,
                "Browser": self.daemon._run_browser_safe,
                "Telegram": self.daemon._run_telegram_safe,
                "Vision": self.daemon._run_vision_safe,
            }

            if name in entry_points:
                await asyncio.to_thread(self.daemon.start_process, name, entry_points[name])
            else:
                return web.json_response({"error": f"unknown_subsystem: {name}"}, status=400)

            await self._broadcast_ws("subsystem_recovered", {"name": name})
            return web.json_response({"ok": True, "status": "restarted", "name": name})
        except Exception as e:
            logger.error("restart_failed", name=name, error=str(e))
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_stop_subsystem(self, request):
        """POST /api/subsystems/{name}/stop — stop a subsystem."""
        name = request.match_info["name"]
        if not self.daemon:
            return web.json_response({"error": "daemon_not_attached"}, status=503)

        try:
            if name in self.daemon.processes:
                p = self.daemon.processes[name]["process"]
                if p.is_alive():
                    p.terminate()
                    p.join(timeout=5)
                    if p.is_alive():
                        p.kill()
            else:
                return web.json_response({"error": f"unknown_subsystem: {name}"}, status=400)

            return web.json_response({"ok": True, "status": "stopped", "name": name})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    # ── Approval endpoints ──

    async def _handle_get_approvals(self, request):
        """GET /api/approvals — pending approval queue."""
        pending = [
            {**a, "id": aid}
            for aid, a in self._pending_approvals.items()
            if a.get("status") == "pending"
        ]
        return web.json_response({"pending": pending})

    async def _handle_approve(self, request):
        """POST /api/approvals/{id}/approve — approve a pending action."""
        aid = request.match_info["id"]
        if aid in self._pending_approvals:
            self._pending_approvals[aid]["status"] = "approved"
            await self._broadcast_ws("approval_resolved", {"id": aid, "decision": "approved"})
            return web.json_response({"ok": True, "status": "approved", "id": aid})
        return web.json_response({"error": "not_found"}, status=404)

    async def _handle_deny(self, request):
        """POST /api/approvals/{id}/deny — deny a pending action."""
        aid = request.match_info["id"]
        if aid in self._pending_approvals:
            self._pending_approvals[aid]["status"] = "denied"
            await self._broadcast_ws("approval_resolved", {"id": aid, "decision": "denied"})
            return web.json_response({"ok": True, "status": "denied", "id": aid})
        return web.json_response({"error": "not_found"}, status=404)

    # ── Control endpoints ──

    async def _handle_shutdown(self, request):
        """POST /api/control/shutdown — graceful daemon shutdown.

        Routes the shutdown through the supervisor's main monitor thread by
        setting ``shutdown_event`` instead of calling ``daemon.stop`` directly
        from this ControlServer thread (Reqs 14.3, 14.4). Falls back to the
        legacy direct call if the daemon predates the event.
        """
        try:
            if self.daemon:
                shutdown_event = getattr(self.daemon, "shutdown_event", None)
                if shutdown_event is not None:
                    shutdown_event.set()
                else:
                    asyncio.get_running_loop().call_later(0.5, self.daemon.stop)
            return web.json_response({"ok": True, "status": "shutting_down"})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_reboot(self, request):
        """POST /api/control/reboot — reboot daemon.

        Routes the reboot through the supervisor's main monitor thread by
        setting ``reboot_event`` instead of calling ``daemon.reboot`` directly
        from this ControlServer thread (Reqs 14.3, 14.4). Falls back to the
        legacy direct call if the daemon predates the event.
        """
        try:
            if self.daemon:
                reboot_event = getattr(self.daemon, "reboot_event", None)
                if reboot_event is not None:
                    reboot_event.set()
                else:
                    asyncio.get_running_loop().call_later(0.5, self.daemon.reboot)
            return web.json_response({"ok": True, "status": "rebooting"})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    # ── Settings endpoints ──

    async def _handle_get_settings(self, request):
        """GET /api/settings — read settings via Brain RPC."""
        data = {}
        if self.brain_rpc:
            resp = await self.brain_rpc.request_async("GET_SETTINGS")
            if resp.ok:
                data = resp.data or {}
            elif resp.error == "brain_rpc_timeout":
                return web.json_response(
                    {"status": "unavailable", "reason": "brain_rpc_timeout"}, status=503
                )
        if not data:
            try:
                from charlie.config import settings
                data = settings.to_dict() if hasattr(settings, 'to_dict') else {}
            except Exception as e:
                return web.json_response({"error": str(e)}, status=500)
        return web.json_response(data)

    async def _handle_post_settings(self, request):
        """POST /api/settings — write daemon settings."""
        return web.json_response({"status": "not_implemented"}, status=501)


    # ── WebSocket ──

    async def _handle_ws(self, request):
        """WebSocket /ws/events — real-time event stream."""
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self._ws_clients.append(ws)
        logger.info(f"ws_connected | clients={len(self._ws_clients)}")

        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        await self._handle_ws_message(ws, data)
                    except json.JSONDecodeError:
                        await ws.send_json({"error": "invalid_json"})
                elif msg.type == web.WSMsgType.ERROR:
                    break
        finally:
            if ws in self._ws_clients:
                self._ws_clients.remove(ws)
            logger.info(f"ws_disconnected | clients={len(self._ws_clients)}")

        return ws

    async def _handle_ws_message(self, ws, data):
        """Handle incoming WebSocket message from client."""
        action = data.get("action")
        if not action:
            await ws.send_json({"error": "missing_action"})
            return

        # Route actions
        if action == "ping":
            await ws.send_json({"type": "pong", "timestamp": time.time()})

        elif action == "get_status":
            if self.daemon:
                status = await asyncio.to_thread(self.daemon.get_daemon_status)
                await ws.send_json({"type": "status", "data": status})

        elif action == "restart_subsystem":
            name = data.get("name")
            if name:
                # Reuse REST handler logic
                fake_request = type("Request", (), {"match_info": {"name": name}})()
                result = await self._handle_restart_subsystem(fake_request)
                await ws.send_json({"type": "restart_result", "data": json.loads(result.body)})

        elif action == "confirm_result":
            # Forward confirmation result to brain_task_q
            if self.daemon and hasattr(self.daemon, 'brain_task_q'):
                self.daemon.brain_task_q.put({
                    "type": "CONFIRMATION_RESULT",
                    "content": data.get("result"),
                    "source": "ws_client",
                })
                await ws.send_json({"type": "confirm_ack"})

        else:
            await ws.send_json({"error": f"unknown_action: {action}"})

    # ── Memory timeline ──

    async def _handle_memory_search(self, request):
        """GET /api/memory/search — search timeline via Brain RPC (Req 7.8)."""
        query = request.query.get("q", "")
        if not query.strip():
            return web.json_response({"results": [], "count": 0})

        # Pass all query-string params to the Brain-side SEARCH handler
        params = dict(request.query)
        params["query"] = query

        if self.brain_rpc:
            resp = await self.brain_rpc.request_async("SEARCH", params)
            if resp.ok:
                return web.json_response({
                    "results": resp.data or [],
                    "count": len(resp.data or []),
                })
            if resp.error == "brain_rpc_timeout":
                return web.json_response(
                    {"status": "unavailable", "reason": "brain_rpc_timeout"}, status=503
                )

        # Fallback: local timeline search
        try:
            from charlie.intelligence.timeline import TimelineIndexer
            indexer = TimelineIndexer()
            await asyncio.to_thread(indexer.build_index)
            results = await asyncio.to_thread(indexer.search, query=query, limit=50)
            return web.json_response({
                "results": [e.to_dict() for e in results],
                "count": len(results),
            })
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    # ── Unified search ──

    async def _handle_unified_search(self, request):
        """GET /api/search?q=... — search via Brain RPC (Req 7.8: filters preserved)."""
        query = request.query.get("q", "").strip()
        if not query:
            return web.json_response({"results": []})

        # Pass all query-string params to the Brain-side SEARCH handler
        params = dict(request.query)
        params["query"] = query

        if self.brain_rpc:
            resp = await self.brain_rpc.request_async("SEARCH", params)
            if resp.ok:
                return web.json_response({"results": resp.data or [], "count": len(resp.data or [])})
            if resp.error == "brain_rpc_timeout":
                return web.json_response(
                    {"status": "unavailable", "reason": "brain_rpc_timeout"}, status=503
                )

        # Fallback: local search (chat history only, since Brain is unreachable)
        results = []
        try:
            history_file = os.path.join(os.getcwd(), "scratch", "conversation_history.json")
            if os.path.exists(history_file):
                with open(history_file) as f:
                    messages = json.load(f)
                    if isinstance(messages, list):
                        for msg in messages:
                            content = msg.get("content", "")
                            if query.lower() in content.lower():
                                results.append({
                                    "source": "chat",
                                    "category": msg.get("role", "message"),
                                    "content": content[:300],
                                    "timestamp": msg.get("timestamp"),
                                })
        except Exception:
            pass

        return web.json_response({"results": results, "count": len(results)})

    # ── Automation rule toggle ──

    async def _handle_toggle_rule(self, request):
        """POST /api/automation/rules/{name}/toggle — toggle a rule via Brain RPC."""
        name = request.match_info["name"]
        if self.brain_rpc:
            resp = await self.brain_rpc.request_async("TOGGLE_RULE", {"name": name})
            if resp.ok:
                data = resp.data or {}
                logger.info(f"rule_toggled | name={name} | enabled={data.get('enabled')}")
                return web.json_response({"ok": True, **data})
            if resp.error == "brain_rpc_timeout":
                return web.json_response(
                    {"status": "unavailable", "reason": "brain_rpc_timeout"}, status=503
                )
            return web.json_response({"error": resp.error or "rpc_failed"}, status=500)
        return web.json_response({"error": "brain_rpc_not_available"}, status=503)

    # ── Integrations ──

    async def _handle_get_integrations(self, request):
        """GET /api/integrations — list integration health."""
        if not self.daemon:
            return web.json_response({"integrations": []})
        try:
            from charlie.integrations.health_tracker import IntegrationHealthTracker
            tracker = IntegrationHealthTracker()
            # Register known integrations if brain has them
            brain = getattr(self.daemon, '_brain', None)
            if brain:
                for attr in ('gmail', 'github', 'calendar', 'notion'):
                    integration = getattr(brain, attr, None)
                    if integration:
                        tracker.register(integration)
            health = tracker.get_all_health()
            return web.json_response({
                "integrations": [h.to_dict() for h in health],
            })
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    # ── Automation ──

    async def _handle_get_rules(self, request):
        """GET /api/automation/rules — list automation rules via Brain RPC."""
        if self.brain_rpc:
            resp = await self.brain_rpc.request_async("GET_AUTOMATION_RULES")
            if resp.ok:
                return web.json_response({"rules": resp.data or []})
            if resp.error == "brain_rpc_timeout":
                return web.json_response(
                    {"status": "unavailable", "reason": "brain_rpc_timeout"}, status=503
                )
        return web.json_response({"rules": []})

    async def _handle_get_token(self, request):
        """Return the server token (no auth required). Used by dashboard to bootstrap."""
        return web.json_response({"token": self._token})

    # ── Chat history ──

    async def _handle_chat_history(self, request):
        """GET /api/chat/history — conversation history."""
        try:
            history_file = os.path.join(os.getcwd(), "scratch", "conversation_history.json")
            if os.path.exists(history_file):
                with open(history_file) as f:
                    data = json.load(f)
                    messages = data if isinstance(data, list) else []
                    return web.json_response({"messages": messages})
            return web.json_response({"messages": []})
        except Exception as e:
            logger.error("chat_history_read_failed", error=str(e))
            return web.json_response({"messages": []}, status=200)

    async def _handle_chat_message(self, request):
        """POST /api/chat/message — send a message to CHARLIE."""
        try:
            body = await request.json()
            message = body.get("message", body.get("content", "")).strip()
            if not message:
                return web.json_response({"error": "empty_message"}, status=400)

            # Forward to Brain
            if self.daemon and hasattr(self.daemon, 'brain_task_q'):
                self.daemon.brain_task_q.put({
                    "type": "TEXT",
                    "content": message,
                    "source": "dashboard",
                })
                return web.json_response({"status": "queued", "message": "Message sent"})
            return web.json_response({"error": "brain_not_available"}, status=503)
        except Exception as e:
            logger.error("chat_message_failed", error=str(e))
            return web.json_response({"error": str(e)}, status=500)

    # ── Globe data ───────────────────────────────────────────────────────────────

    async def _handle_globe_data(self, request):
        """GET /api/globe/data — all globe data (calendar, memory, workspace, user)."""
        import os

        from charlie.integrations.adapter import call_integration

        result = {
            "calendar": [],
            "memory": [],
            "workspace": [],
            "user_position": {"lat": 40.7128, "lng": -74.0060, "label": "New York"},
        }

        # User position from env vars (fallback to hardcoded NYC)
        try:
            lat = float(os.getenv("CHARLIE_GLOBE_LAT", "40.7128"))
            lng = float(os.getenv("CHARLIE_GLOBE_LNG", "-74.0060"))
            label = os.getenv("CHARLIE_GLOBE_LABEL", "Home")
            result["user_position"] = {"lat": lat, "lng": lng, "label": label}
        except Exception:
            pass

        # Calendar events via Google Calendar integration
        try:
            from charlie.integrations.google_calendar import GoogleCalendarIntegration
            cal = GoogleCalendarIntegration()
            events = await call_integration(cal.fetch, max_results=20)
            result["calendar"] = [
                {
                    "id": e.get("id", str(i)),
                    "title": e.get("summary", "Untitled"),
                    "start": e.get("start", None),
                    "location": e.get("location", ""),
                    "lat": e.get("lat", None),
                    "lng": e.get("lng", None),
                    "color": "#00d4ff",
                }
                for i, e in enumerate(events) if e.get("lat")
            ]
        except Exception as e:
            logger.debug("globe_calendar_failed", error=str(e))

        # Memory nodes with location
        try:
            from charlie.intelligence.memory_graph import MemoryGraph
            mg = MemoryGraph()
            nodes = mg.get_nodes_with_location()
            result["memory"] = [
                {
                    "id": n.get("id", str(i)),
                    "content": n.get("content", "")[:200],
                    "lat": n.get("lat"),
                    "lng": n.get("lng"),
                }
                for i, n in enumerate(nodes) if n.get("lat") and n.get("lng")
            ]
        except Exception as e:
            logger.debug("globe_memory_failed", error=str(e))

        return web.json_response(result)

    async def _handle_globe_refresh(self, request):
        """POST /api/globe/refresh — fetch latest data and push to globe WS clients."""
        import os
        from charlie.integrations.google_calendar import GoogleCalendarIntegration
        from charlie.integrations.adapter import call_integration
        from charlie.intelligence.memory_graph import MemoryGraph

        result = {"calendar": [], "memory": [], "workspace": [], "user_position": None}

        # User position
        try:
            lat = float(os.getenv("CHARLIE_GLOBE_LAT", "40.7128"))
            lng = float(os.getenv("CHARLIE_GLOBE_LNG", "-74.0060"))
            label = os.getenv("CHARLIE_GLOBE_LABEL", "Home")
            result["user_position"] = {"lat": lat, "lng": lng, "label": label}
        except Exception:
            pass

        # Calendar
        try:
            cal = GoogleCalendarIntegration()
            events = await call_integration(cal.fetch, max_results=20)
            result["calendar"] = [
                {
                    "id": e.get("id", str(i)),
                    "title": e.get("summary", "Untitled"),
                    "start": e.get("start", None),
                    "location": e.get("location", ""),
                    "lat": e.get("lat", None),
                    "lng": e.get("lng", None),
                }
                for i, e in enumerate(events) if e.get("lat")
            ]
        except Exception:
            pass

        # Memory
        try:
            mg = MemoryGraph()
            nodes = mg.get_nodes_with_location()
            result["memory"] = [
                {
                    "id": n.get("id", str(i)),
                    "content": n.get("content", "")[:200],
                    "lat": n.get("lat"),
                    "lng": n.get("lng"),
                }
                for i, n in enumerate(nodes) if n.get("lat") and n.get("lng")
            ]
        except Exception:
            pass

        # Broadcast to globe WS clients
        await self._broadcast_ws("globe_data", result)
        return web.json_response({"status": "ok", "refreshed": True})

    # ── Agents, skills, tools ──────────────────────────────────────────────────

    async def _handle_agents_status(self, request):
        """GET /api/agents/status — orchestrator status via Brain RPC."""
        if self.brain_rpc:
            resp = await self.brain_rpc.request_async("GET_AGENT_STATUS")
            if resp.ok:
                agents = resp.data or []
                active_count = sum(1 for a in agents if isinstance(a, dict) and a.get("status") == "busy")
                return web.json_response({
                    "orchestrator": {
                        "status": "executing" if active_count > 0 else "idle",
                        "active_agents": active_count,
                        "current_plan": "",
                    },
                    "agents": agents,
                })
            if resp.error == "brain_rpc_timeout":
                return web.json_response(
                    {"status": "unavailable", "reason": "brain_rpc_timeout"}, status=503
                )
        # Fallback: scan agent manifests on disk
        try:
            import json
            import pathlib
            agents_dir = pathlib.Path("charlie/agents")
            agents = []
            if agents_dir.is_dir():
                for agent_path in agents_dir.iterdir():
                    manifest_path = agent_path / "agent.json"
                    if manifest_path.is_file():
                        try:
                            with open(manifest_path) as f:
                                manifest = json.load(f)
                            agent_name = manifest.get("name", agent_path.name)
                            agents.append({
                                "id": agent_name,
                                "name": agent_name,
                                "role": manifest.get("role", "") or agent_path.name,
                                "status": "idle",
                                "current_task": "",
                            })
                        except Exception:
                            pass
            return web.json_response({
                "orchestrator": {"status": "idle", "active_agents": 0, "current_plan": ""},
                "agents": agents,
            })
        except Exception as e:
            logger.warning("agents_status_failed | error=%s", e)
            return web.json_response({"orchestrator": {"status": "error", "active_agents": 0, "current_plan": ""}, "agents": []})

    async def _handle_skills(self, request):
        """GET /api/skills — list all available skills from charlie/skills/."""
        try:
            from charlie.brain.skill_loader import SkillLoader
            loader = SkillLoader(skills_dir="charlie/skills")
            skill_specs = loader.load_all()
            return web.json_response({
                "skills": [
                    {
                        "name": s.name,
                        "description": getattr(s, "description", ""),
                        "tags": getattr(s, "tags", []),
                        "enabled": getattr(s, "enabled", True),
                        "inject_mode": getattr(s, "inject_mode", "default"),
                    }
                    for s in skill_specs
                ]
            })
        except Exception as e:
            logger.debug("skills_load_failed | error=%s", e)
            return web.json_response({"skills": []})

    async def _handle_tools_log(self, request):
        """GET /api/tools/log — recent tool executions via Brain RPC."""
        params = {"limit": int(request.query.get("limit", "50"))}
        if self.brain_rpc:
            resp = await self.brain_rpc.request_async("GET_TOOL_LOG", params)
            if resp.ok:
                return web.json_response({"executions": resp.data or []})
            if resp.error == "brain_rpc_timeout":
                return web.json_response(
                    {"status": "unavailable", "reason": "brain_rpc_timeout"}, status=503
                )
        return web.json_response({"executions": []})

    async def _handle_logs(self, request):
        """GET /api/logs — recent log entries from all processes."""
        limit = int(request.query.get("limit", "200"))
        process = request.query.get("process")
        level = request.query.get("level")

        logs = []
        try:
            log_dir = os.path.join(os.getcwd(), "logs")
            if os.path.isdir(log_dir):
                for fname in sorted(os.listdir(log_dir), reverse=True):
                    if not fname.endswith(".log"):
                        continue
                    fpath = os.path.join(log_dir, fname)
                    proc_name = fname.replace(".log", "")
                    if process and proc_name != process:
                        continue
                    try:
                        with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                            lines = f.readlines()[-50:]  # last 50 lines per file
                            for line in lines:
                                line = line.strip()
                                if not line:
                                    continue
                                # Parse structured log: timestamp | level | message
                                parts = line.split(" | ", 2)
                                entry = {
                                    "timestamp": parts[0].strip() if len(parts) > 0 else "",
                                    "process": proc_name,
                                    "level": (parts[1].strip().lower() if len(parts) > 1 else "info"),
                                    "message": parts[2].strip() if len(parts) > 2 else line,
                                }
                                if level and entry["level"] != level:
                                    continue
                                logs.append(entry)
                    except Exception:
                        continue
        except Exception as e:
            logger.debug("logs_read_failed | error=%s", e)

        # Sort by timestamp descending, limit
        logs.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return web.json_response({"logs": logs[:limit]})

    async def _handle_evolution(self, request):
        """GET /api/evolution — self-evolution history from EvolutionEngine."""
        try:
            from charlie.intelligence.evolution_engine import EvolutionEngine
            engine = EvolutionEngine()
            entries = engine._runs if hasattr(engine, "_runs") else []
            return web.json_response({"entries": entries})
        except Exception as e:
            logger.debug("evolution_load_failed | error=%s", e)
            return web.json_response({"entries": []})

    async def _handle_get_briefing(self, request):
        """GET /api/briefing — get latest briefing."""
        return web.json_response({"briefing": self._last_briefing or {}})

    async def _handle_run_briefing(self, request):
        """POST /api/briefing/run — generate new briefing."""
        try:
            from charlie.intelligence.briefing import BriefingAssembler
            brain = getattr(self.daemon, '_brain', None)
            assembler = BriefingAssembler(brain=brain)
            briefing = await assembler.assemble()
            self._last_briefing = briefing.to_dict()
            return web.json_response({"briefing": self._last_briefing})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_voice_status(self, request):
        """GET /api/voice/status — voice pipeline status."""
        try:
            brain = getattr(self.daemon, '_brain', None)
            if brain:
                voice_state = getattr(brain, 'voice_state', {})
                return web.json_response({
                    "stt_model": getattr(brain, 'stt_model', 'unknown'),
                    "tts_model": getattr(brain, 'tts_model', 'unknown'),
                    "tts_speed": getattr(brain, 'tts_speed', 1.0),
                    "is_listening": voice_state.get('is_listening', False),
                    "is_speaking": voice_state.get('is_speaking', False),
                })
        except Exception:
            pass
        return web.json_response({
            "stt_model": "unknown", "tts_model": "unknown", "tts_speed": 1.0,
            "is_listening": False, "is_speaking": False,
        })

    async def _handle_globe_status(self, request):
        """GET /api/globe/status — globe server status."""
        import socket
        port = 8089
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.5)
            result = sock.connect_ex(('127.0.0.1', port))
            sock.close()
            running = result == 0
        except Exception:
            running = False
        return web.json_response({"running": running, "port": port})

    async def _handle_globe_launch(self, request):
        """POST /api/control/globe/launch — start globe server."""
        try:
            import subprocess
            subprocess.Popen(
                ['uv', 'run', 'python', 'charlie/browser/headless_browser.py', '--globe'],
                cwd=os.getcwd(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return web.json_response({"ok": True})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)})

    async def _handle_tasks(self, request):
        """GET /api/tasks — task queue via Brain RPC."""
        if self.brain_rpc:
            resp = await self.brain_rpc.request_async("GET_TASKS")
            if resp.ok:
                return web.json_response({"tasks": resp.data or []})
            if resp.error == "brain_rpc_timeout":
                return web.json_response(
                    {"status": "unavailable", "reason": "brain_rpc_timeout"}, status=503
                )
        return web.json_response({"tasks": []})

    async def _handle_cancel_task(self, request):
        """POST /api/tasks/{task_id}/cancel — cancel a task."""
        task_id = request.match_info['task_id']
        try:
            brain = getattr(self.daemon, '_brain', None)
            if brain and hasattr(brain, 'task_manager'):
                tm = brain.task_manager
                if hasattr(tm, 'cancel_task'):
                    await tm.cancel_task(task_id)
                    return web.json_response({"ok": True})
        except Exception:
            pass
        return web.json_response({"ok": False})

    async def _handle_mcp_servers(self, request):
        """GET /api/mcp/servers — MCP server list."""
        try:
            from charlie.mcp.manager import MCPManager
            manager = MCPManager()
            servers = []
            if hasattr(manager, 'get_servers'):
                servers = manager.get_servers()
            elif hasattr(manager, '_servers'):
                servers = [
                    {
                        "id": s.get('name', 'unknown'),
                        "name": s.get('name', 'unknown'),
                        "status": "connected" if s.get('enabled', True) else "disconnected",
                        "enabled": s.get('enabled', True),
                        "tools": s.get('tools', []),
                        "config": s.get('config', {}),
                    }
                    for s in manager._servers
                ]
            return web.json_response({"servers": servers})
        except Exception:
            return web.json_response({"servers": []})

    async def _handle_toggle_mcp(self, request):
        """POST /api/mcp/{server_id}/toggle — toggle MCP server."""
        server_id = request.match_info['server_id']
        try:
            from charlie.mcp.manager import MCPManager
            manager = MCPManager()
            if hasattr(manager, 'toggle_server'):
                manager.toggle_server(server_id)
                return web.json_response({"ok": True})
        except Exception:
            pass
        return web.json_response({"ok": False})

    async def _broadcast_ws(self, event_type: str, data: dict):
        """Push event to all connected WS clients."""
        if not self._ws_clients:
            return

        message = {"type": event_type, "data": data}
        dead = []
        for ws in self._ws_clients:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)

        for ws in dead:
            if ws in self._ws_clients:
                self._ws_clients.remove(ws)

    def broadcast_sync(self, event_type: str, data: dict):
        """Thread-safe broadcast. Schedules on control server's event loop."""
        if not self._running or not self._loop:
            return
        try:
            asyncio.run_coroutine_threadsafe(
                self._broadcast_ws(event_type, data), self._loop
            )
        except Exception as e:
            logger.debug(f"broadcast_sync_failed | {e}")

    # ── Approval queue management ──

    def add_pending_approval(self, approval_id: str, approval_data: dict):
        """Add a pending approval to the queue."""
        self._pending_approvals[approval_id] = {
            **approval_data,
            "status": "pending",
            "timestamp": time.time(),
        }
        self.broadcast_sync("approval_pending", {
            "id": approval_id,
            **approval_data,
        })

    def resolve_approval(self, approval_id: str, decision: str):
        """Resolve a pending approval."""
        if approval_id in self._pending_approvals:
            self._pending_approvals[approval_id]["status"] = decision
            self.broadcast_sync("approval_resolved", {
                "id": approval_id,
                "decision": decision,
            })

    @property
    def is_running(self) -> bool:
        return self._running

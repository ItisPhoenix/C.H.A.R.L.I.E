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

logger = get_logger("ControlServer")

# Message types that get forwarded from status_q to WS clients
WS_FORWARD_TYPES = {
    "PHASE", "CHAT_MSG", "VOICE_ACTIVITY", "VRAM",
    "INTEGRATION_UPDATE", "PHOENIX_ALERT",
    "RESEARCH_STATUS", "RESEARCH_LOG", "RESEARCH_PARTIAL",
    "RESEARCH_RESULT", "RESEARCH_FOLLOWUP",
    "CONFIRM_REQUIRED",
    "STATUS_UPDATE", "SUBSYSTEM_STATUS",
    "TOOL_EXECUTION",
    "TASK_UPDATE", "TASK_COMPLETE", "TASK_FAIL",
    "VOICE_COMMAND", "USER_TRANSCRIPT",
}


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

    def start(self):
        """Start the control server (blocking, run in thread)."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._start_async())
        except Exception as e:
            logger.error("control_server_start_failed", error=str(e))

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

        # Start status_q → WS forwarding task
        forward_task = asyncio.create_task(self._forward_status_queue())

        # Keep running until stopped
        try:
            while self._running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            forward_task.cancel()
            try:
                await forward_task
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
        self._app.router.add_post("/api/setup", self._handle_setup)

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
        """POST /api/control/shutdown — graceful daemon shutdown."""
        try:
            if self.daemon:
                asyncio.get_running_loop().call_later(0.5, self.daemon.stop)
            return web.json_response({"ok": True, "status": "shutting_down"})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_reboot(self, request):
        """POST /api/control/reboot — reboot daemon."""
        try:
            if self.daemon:
                asyncio.get_running_loop().call_later(0.5, self.daemon.reboot)
            return web.json_response({"ok": True, "status": "rebooting"})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    # ── Settings endpoints ──

    async def _handle_get_settings(self, request):
        """GET /api/settings — read daemon settings."""
        try:
            from charlie.config import settings
            return web.json_response(settings.to_dict() if hasattr(settings, 'to_dict') else {})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_post_settings(self, request):
        """POST /api/settings — write daemon settings."""
        return web.json_response({"status": "not_implemented"}, status=501)

    async def _handle_setup(self, request):
        """POST /api/setup — write setup wizard configuration to charlie_config.json."""
        try:
            import json
            config_path = os.path.join(os.getcwd(), "charlie_config.json")
            config = {}
            if os.path.exists(config_path):
                with open(config_path) as f:
                    config = json.load(f)

            body = await request.json()

            # Map wizard data to config keys
            if "llm_api_key" in body:
                config.setdefault("llm", {})["api_key"] = body["llm_api_key"]
            if "llm_model" in body:
                config.setdefault("llm", {})["model"] = body["llm_model"]
            if "llm_provider" in body:
                config.setdefault("llm", {})["provider"] = body["llm_provider"]
            if "voice_enabled" in body:
                config.setdefault("audio", {})["enabled"] = body["voice_enabled"]
            if "wake_word" in body:
                config.setdefault("audio", {})["wake_word"] = body["wake_word"]
            if "stt_model" in body:
                config.setdefault("audio", {})["stt_model"] = body["stt_model"]
            if "tts_model" in body:
                config.setdefault("audio", {})["tts_model"] = body["tts_model"]
            if "integrations" in body:
                config["integrations"] = body["integrations"]
            if "risk_tier" in body:
                config.setdefault("security", {})["risk_tier"] = body["risk_tier"]
            if "guardian_enabled" in body:
                config.setdefault("security", {})["guardian_enabled"] = body["guardian_enabled"]
            if "auto_approve_threshold" in body:
                config.setdefault("security", {})["auto_approve_threshold"] = body["auto_approve_threshold"]

            config["setup_complete"] = body.get("setup_complete", True)

            with open(config_path, "w") as f:
                json.dump(config, f, indent=2)

            return web.json_response({"status": "ok"})
        except Exception as e:
            logger.error("setup_failed | error=%s", e)
            return web.json_response({"status": "error", "error": str(e)}, status=500)

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
        """GET /api/memory/search — search timeline."""
        query = request.query.get("q", "")
        if not query.strip():
            return web.json_response({"results": [], "count": 0})
        try:
            from charlie.intelligence.timeline import TimelineIndexer
            indexer = TimelineIndexer()
            # Non-blocking async-to-thread database indexing & searching
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
        """GET /api/search?q=... — search across chat, memory, tools, and tasks."""
        query = request.query.get("q", "").strip().lower()
        if not query:
            return web.json_response({"results": []})

        results = []

        # Search chat history
        try:
            history_file = os.path.join(os.getcwd(), "scratch", "conversation_history.json")
            if os.path.exists(history_file):
                with open(history_file) as f:
                    messages = json.load(f) if os.path.exists(history_file) else []
                    if isinstance(messages, list):
                        for msg in messages:
                            content = msg.get("content", "")
                            if query in content.lower():
                                results.append({
                                    "source": "chat",
                                    "category": msg.get("role", "message"),
                                    "content": content[:300],
                                    "timestamp": msg.get("timestamp"),
                                })
        except Exception:
            pass

        # Search memory/timeline
        try:
            from charlie.intelligence.timeline import TimelineIndexer
            indexer = TimelineIndexer()
            await asyncio.to_thread(indexer.build_index)
            memory_results = await asyncio.to_thread(indexer.search, query=query, limit=20)
            for entry in memory_results:
                d = entry.to_dict() if hasattr(entry, 'to_dict') else {}
                results.append({
                    "source": "memory",
                    "category": d.get("category", "memory"),
                    "content": d.get("content", "")[:300],
                    "timestamp": d.get("timestamp"),
                })
        except Exception:
            pass

        # Search tool executions
        try:
            brain = self.daemon
            if brain and hasattr(brain, "outcome_tracker"):
                outcomes = brain.outcome_tracker.get_recent_outcomes(limit=100)
                for o in outcomes:
                    tool_name = o.get("tool_name", "")
                    detail = o.get("detail", "")
                    if query in tool_name.lower() or query in detail.lower():
                        results.append({
                            "source": "tools",
                            "category": tool_name,
                            "content": detail[:300],
                            "timestamp": o.get("timestamp"),
                        })
        except Exception:
            pass

        # Search tasks
        try:
            brain_obj = getattr(self.daemon, '_brain', None)
            if brain_obj and hasattr(brain_obj, 'task_manager'):
                tm = brain_obj.task_manager
                if hasattr(tm, 'get_all_tasks'):
                    for task in tm.get_all_tasks():
                        name = getattr(task, 'name', '') or ''
                        desc = getattr(task, 'description', '') or ''
                        if query in name.lower() or query in desc.lower():
                            td = task.to_dict() if hasattr(task, 'to_dict') else {}
                            results.append({
                                "source": "tasks",
                                "category": name or td.get("id", "task"),
                                "content": desc[:300] or f"Status: {td.get('status', 'unknown')}",
                                "timestamp": td.get("timestamp"),
                            })
        except Exception:
            pass

        return web.json_response({"results": results, "count": len(results)})

    # ── Automation rule toggle ──

    async def _handle_toggle_rule(self, request):
        """POST /api/automation/rules/{name}/toggle — toggle a rule's enabled state."""
        name = request.match_info["name"]
        try:
            brain = self.daemon
            if not brain:
                return web.json_response({"error": "Daemon not available"}, status=503)

            # Find the rule engine
            rule_engine = None
            if hasattr(brain, "rule_engine"):
                rule_engine = brain.rule_engine
            elif hasattr(brain, "_brain") and hasattr(brain._brain, "rule_engine"):
                rule_engine = brain._brain.rule_engine

            if not rule_engine:
                return web.json_response({"error": "Rule engine not available"}, status=503)

            rule = rule_engine.get_rule(name)
            if not rule:
                return web.json_response({"error": f"Rule '{name}' not found"}, status=404)

            new_enabled = not rule.enabled
            rule_engine.update_rule(name, enabled=new_enabled)
            rule_engine.save_rules()

            logger.info(f"rule_toggled | name={name} | enabled={new_enabled}")
            return web.json_response({
                "ok": True,
                "name": name,
                "enabled": new_enabled,
            })
        except Exception as e:
            logger.error(f"toggle_rule_failed | {e}")
            return web.json_response({"error": str(e)}, status=500)

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
        """GET /api/automation/rules — list automation rules."""
        if not self.daemon:
            return web.json_response({"rules": []})
        try:
            brain = getattr(self.daemon, '_brain', None)
            if brain and hasattr(brain, 'rule_engine'):
                rules = brain.rule_engine.get_all_rules()
                return web.json_response({"rules": rules})
            return web.json_response({"rules": []})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

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
            events = await cal.fetch(max_results=20)
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
            events = await cal.fetch(max_results=20)
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
        """GET /api/agents/status — orchestrator status and loaded agent manifests."""
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
                            # Check if agent has running tasks via task_manager
                            agent_name = manifest.get("name", agent_path.name)
                            agent_status = "idle"
                            current_task = ""
                            if self.daemon and hasattr(self.daemon, '_brain'):
                                brain = self.daemon._brain
                                if hasattr(brain, 'task_manager') and brain.task_manager:
                                    for task in brain.task_manager.get_all_tasks():
                                        if hasattr(task, 'agent_id') and task.agent_id == agent_name:
                                            if task.status in ('running', 'active'):
                                                agent_status = 'busy'
                                                current_task = task.name or task.id
                                                break
                            agents.append({
                                "id": agent_name,
                                "name": agent_name,
                                "role": manifest.get("role", "") or agent_path.name,
                                "status": agent_status,
                                "current_task": current_task,
                            })
                        except Exception:
                            pass
            # Check orchestrator status
            orch_status = "idle"
            active_count = sum(1 for a in agents if a["status"] == "busy")
            if active_count > 0:
                orch_status = "executing"
            return web.json_response({
                "orchestrator": {
                    "status": orch_status,
                    "active_agents": active_count,
                    "current_plan": "",
                },
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
        """GET /api/tools/log — recent tool executions from OutcomeTracker."""
        try:
            brain = self.daemon
            if brain and hasattr(brain, "outcome_tracker"):
                outcomes = brain.outcome_tracker.get_recent_outcomes(limit=50)
                return web.json_response({"executions": outcomes})
            return web.json_response({"executions": []})
        except Exception as e:
            logger.debug("tools_log_failed | error=%s", e)
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
        """GET /api/tasks — task queue."""
        try:
            brain = getattr(self.daemon, '_brain', None)
            if brain and hasattr(brain, 'task_manager'):
                tm = brain.task_manager
                tasks = []
                if hasattr(tm, 'get_all_tasks'):
                    tasks = [t.to_dict() if hasattr(t, 'to_dict') else t for t in tm.get_all_tasks()]
                return web.json_response({"tasks": tasks})
        except Exception:
            pass
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

    async def _forward_status_queue(self):
        """Read from daemon's status_q and forward matching events to WS clients."""
        if not self.daemon or not hasattr(self.daemon, "status_q"):
            return
        while self._running:
            try:
                # Non-blocking get with timeout
                try:
                    msg = self.daemon.status_q.get(timeout=0.5)
                except Exception:
                    await asyncio.sleep(0.5)
                    continue

                if not isinstance(msg, dict):
                    continue

                msg_type = msg.get("type", "")
                if msg_type in WS_FORWARD_TYPES:
                    # Map internal types to frontend event names
                    event_map = {
                        "PHASE": "phase",
                        "CHAT_MSG": "chat_reply",
                        "VOICE_ACTIVITY": "voice_activity",
                        "VRAM": "vram_update",
                        "INTEGRATION_UPDATE": "integration_update",
                        "PHOENIX_ALERT": "phoenix_alert",
                        "RESEARCH_STATUS": "research_status",
                        "RESEARCH_LOG": "research_log",
                        "RESEARCH_PARTIAL": "research_partial",
                        "RESEARCH_RESULT": "research_result",
                        "RESEARCH_FOLLOWUP": "research_followup",
                        "CONFIRM_REQUIRED": "confirm_required",
                        "VOICE_COMMAND": "voice_command",
                        "USER_TRANSCRIPT": "user_transcript",
                        "STATUS_UPDATE": "status_update",
                        "SUBSYSTEM_STATUS": "status_update",
                        "TOOL_EXECUTION": "tool_execution",
                        "TASK_UPDATE": "task_update",
                        "TASK_COMPLETE": "task_update",
                        "TASK_FAIL": "task_update",
                    }
                    ws_type = event_map.get(msg_type, msg_type.lower())
                    await self._broadcast_ws(ws_type, msg)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("status_q_forward_error | %s", e)
                await asyncio.sleep(0.5)

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

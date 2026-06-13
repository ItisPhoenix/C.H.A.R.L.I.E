"""
charlie/watchdog/control_server.py

ControlServer — HTTP REST API + WebSocket for daemon control.
Runs on localhost:8090.
Binds to 127.0.0.1 only — no external exposure.
"""

import asyncio
import json
import os
import secrets
import time
from pathlib import Path

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
        # Underlying approval queue (thread-safe, auto-expires, has
        # listener API for WS push). Persistence is via the queue's
        # own get_all() view serialized to JSON.
        from charlie.watchdog.approval_queue import ApprovalQueue

        self._approval_queue = ApprovalQueue()
        self._approval_queue.on_change(self._on_approval_change)
        self._last_briefing: dict = {}
        self._approvals_path = Path(__file__).parent.parent.parent / "scratch" / "approvals.json"
        self._token = os.environ.get("CONTROL_SERVER_TOKEN") or secrets.token_urlsafe(32)
        self._token_endpoint_added = False  # deferred to first request

        # Shared MCP manager (lazy-initialized singleton so connections persist
        # across requests — previously each handler created a fresh instance)
        self._mcp_manager = None

        # Docker MCP gateway container tracking. If the user starts the gateway
        # via POST /api/control/docker/gateway/start, the container ID is stored
        # here so /api/control/docker/gateway/stop can clean it up. None means
        # the gateway is either not running or was started out-of-band.
        self._docker_gateway_container_id: str | None = None

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
        """Simple rate limiting: 300 requests per minute per IP.
        Exempts localhost (dashboard) since it polls frequently."""
        remote = request.remote or "unknown"
        if remote in ("127.0.0.1", "::1"):
            return await handler(request)
        now = time.time()
        if not hasattr(self, "_rate_limits"):
            self._rate_limits = {}
        # Clean old entries
        self._rate_limits = {k: v for k, v in self._rate_limits.items() if now - v[-1] < 60}
        if remote not in self._rate_limits:
            self._rate_limits[remote] = []
        self._rate_limits[remote].append(now)
        # Keep only last 100 timestamps
        self._rate_limits[remote] = self._rate_limits[remote][-300:]
        if len(self._rate_limits[remote]) >= 300:
            oldest = self._rate_limits[remote][0]
            if now - oldest < 60:
                return web.json_response({"error": "Rate limit exceeded"}, status=429)
        return await handler(request)

    async def _start_async(self):
        """Async startup."""
        self._app = web.Application(
            middlewares=[self._rate_limit_middleware, self._token_auth_middleware, self._cors_middleware]
        )
        self._setup_routes()
        self._load_approvals()

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", self.port)
        try:
            await site.start()
        except OSError as e:
            logger.error(
                f"control_server_port_failed | port={self.port} | error={e} | "
                f"hint='Is another CHARLIE instance already running?'"
            )
            self._running = False
            if self.daemon and hasattr(self.daemon, "status_q") and self.daemon.status_q:
                try:
                    self.daemon.status_q.put_nowait(
                        {
                            "type": "PHOENIX_ALERT",
                            "content": f"ControlServer bind failed on port {self.port}. Is another instance running?",
                        }
                    )
                except Exception as ex:
                    logger.warning("phoenix_alert_push_failed | %s", ex)
            return

        # Also bind IPv6 loopback to prevent ::1 bypass (best-effort)
        try:
            site6 = web.TCPSite(self._runner, "::1", self.port)
            await site6.start()
            logger.info(f"ipv6_bind_ok | [::1]:{self.port}")
        except OSError:
            logger.debug("ipv6_bind_skipped | ::1 not available on this system")

        self._running = True
        logger.info(f"control_server_started | http://127.0.0.1:{self.port}")

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
        """CORS middleware for dashboard cross-origin access.

        The control server is authenticated via the ``X-Control-Token``
        header (token-based, not cookie-based), so we never set
        ``Access-Control-Allow-Credentials``. Origins are pinned to a
        trusted allowlist (``localhost``/``127.0.0.1``/``[::1]``) to
        avoid the unsafe ``*`` + credentials combination.
        """
        if request.method == "OPTIONS":
            response = web.Response()
        else:
            try:
                response = await handler(request)
            except web.HTTPException as exc:
                response = exc
        origin = request.headers.get("Origin", "")
        # Pin the origin to a known-safe value (or omit it entirely if
        # the origin is untrusted). Wildcard is only emitted when the
        # request has no Origin header (i.e. a non-browser client).
        if origin and ("localhost" in origin or "127.0.0.1" in origin or "[::1]" in origin):
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"
        elif not origin:
            response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Control-Token"
        # Intentionally NOT setting Access-Control-Allow-Credentials:
        # auth is via X-Control-Token header, not cookies, so the
        # credentials flag is unnecessary and unsafe to combine with *.
        return response

    @web.middleware
    async def _token_auth_middleware(self, request, handler):
        """Token auth: every request must carry the ``X-Control-Token``.

        The server binds to 127.0.0.1, so only localhost processes can
        connect, but **any** local process can connect — so we always
        require the token. The dashboard proxy at ``charlie/dashboard``
        already injects the token on behalf of the browser, so removing
        the localhost bypass does not break the legitimate dashboard
        flow.
        """
        # Allow unauthenticated access to the token bootstrap endpoint
        if request.path == "/api/token":
            return await handler(request)

        # Allow CORS preflight without auth
        if request.method == "OPTIONS":
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
        # Docker MCP gateway lifecycle (manual user-initiated start/stop)
        self._app.router.add_get("/api/control/docker/gateway/status", self._handle_docker_gateway_status)
        self._app.router.add_post("/api/control/docker/gateway/start", self._handle_docker_gateway_start)
        self._app.router.add_post("/api/control/docker/gateway/stop", self._handle_docker_gateway_stop)

        # Settings
        self._app.router.add_get("/api/settings", self._handle_get_settings)
        self._app.router.add_post("/api/settings", self._handle_post_settings)

        # Unified search
        self._app.router.add_get("/api/search", self._handle_unified_search)

        # Automation rules toggle
        self._app.router.add_post("/api/automation/rules/{name}/toggle", self._handle_toggle_rule)

        # Memory timeline
        self._app.router.add_get("/api/memory/search", self._handle_memory_search)
        self._app.router.add_get("/api/memory/stats", self._handle_memory_stats)

        # Integrations
        self._app.router.add_get("/api/integrations", self._handle_get_integrations)

        # Automation
        self._app.router.add_get("/api/automation/rules", self._handle_get_rules)
        self._app.router.add_post("/api/automation/rules", self._handle_create_rule)
        self._app.router.add_put("/api/automation/rules/{name}", self._handle_update_rule)
        self._app.router.add_delete("/api/automation/rules/{name}", self._handle_delete_rule)

        # Briefing
        self._app.router.add_get("/api/briefing", self._handle_get_briefing)
        self._app.router.add_post("/api/briefing/run", self._handle_run_briefing)

        # Token (no auth required — used by dashboard to bootstrap)
        self._app.router.add_get("/api/token", self._handle_get_token)

        # Chat history
        self._app.router.add_get("/api/chat/history", self._handle_chat_history)
        self._app.router.add_post("/api/chat/message", self._handle_chat_message)
        self._app.router.add_post("/api/chat/send", self._handle_chat_message)  # alias for dashboard

        # Agents, skills, tools
        self._app.router.add_get("/api/agents/status", self._handle_agents_status)
        self._app.router.add_post("/api/agents", self._handle_create_agent)
        self._app.router.add_put("/api/agents/{name}", self._handle_update_agent)
        self._app.router.add_delete("/api/agents/{name}", self._handle_delete_agent)
        self._app.router.add_get("/api/skills", self._handle_skills)
        self._app.router.add_post("/api/skills", self._handle_create_skill)
        self._app.router.add_put("/api/skills/{name}", self._handle_update_skill)
        self._app.router.add_delete("/api/skills/{name}", self._handle_delete_skill)
        self._app.router.add_get("/api/tools/log", self._handle_tools_log)
        self._app.router.add_get("/api/logs", self._handle_logs)
        self._app.router.add_get("/api/evolution", self._handle_evolution)

        # Voice, tasks, MCP
        self._app.router.add_get("/api/voice/status", self._handle_voice_status)
        self._app.router.add_post("/api/audio/mute", self._handle_audio_mute)
        self._app.router.add_get("/api/audio/mute", self._handle_audio_mute_status)
        self._app.router.add_get("/api/audio/config", self._handle_audio_config)
        self._app.router.add_post("/api/audio/config", self._handle_audio_config_set)
        self._app.router.add_get("/api/metrics", self._handle_metrics)
        self._app.router.add_get("/api/tasks", self._handle_tasks)
        self._app.router.add_post("/api/tasks/{task_id}/cancel", self._handle_cancel_task)
        self._app.router.add_get("/api/mcp/servers", self._handle_mcp_servers)
        self._app.router.add_post("/api/mcp/servers", self._handle_mcp_add_server)
        self._app.router.add_post("/api/mcp/{server_id}/toggle", self._handle_toggle_mcp)
        self._app.router.add_post("/api/mcp/{server_id}/connect", self._handle_mcp_connect)
        self._app.router.add_post("/api/mcp/{server_id}/disconnect", self._handle_mcp_disconnect)
        self._app.router.add_post("/api/mcp/{server_id}/tools/{tool_name}/call", self._handle_mcp_call_tool)
        self._app.router.add_delete("/api/mcp/{server_id}", self._handle_mcp_delete_server)

        # Orchestrator endpoints
        self._app.router.add_post("/api/orchestrator/plan", self._handle_orchestrator_plan)
        self._app.router.add_get("/api/orchestrator/learning", self._handle_orchestrator_learning)
        self._app.router.add_post("/api/orchestrator/execute", self._handle_orchestrator_execute)

        # Prometheus metrics exposition
        self._app.router.add_get("/api/metrics", self._handle_metrics)

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

            # Restart using the appropriate entry point.
            # IMPORTANT: look up the method via getattr at call time —
            # eager dict-eval of unbound attribute references would raise
            # AttributeError for subsystems not implemented on this supervisor
            # (e.g. Audio/Telegram/Vision are wired in Phoenix but not Daemon mode).
            entry_point_methods = {
                "Brain": "_run_brain_safe",
                "Browser": "_run_browser_safe",
            }

            if name in entry_point_methods:
                method = getattr(self.daemon, entry_point_methods[name], None)
                if method is None:
                    return web.json_response(
                        {"error": f"subsystem_not_implemented: {name}"}, status=501
                    )
                await asyncio.to_thread(self.daemon.start_process, name, method)
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
        pending = [pa.to_dict() for pa in self._approval_queue.get_pending()]
        return web.json_response({"pending": pending})

    async def _handle_approve(self, request):
        """POST /api/approvals/{id}/approve — approve a pending action."""
        aid = request.match_info["id"]
        if self._approval_queue.approve(aid):
            return web.json_response({"ok": True, "status": "approved", "id": aid})
        return web.json_response({"error": "not_found"}, status=404)

    async def _handle_deny(self, request):
        """POST /api/approvals/{id}/deny — deny a pending action."""
        aid = request.match_info["id"]
        if self._approval_queue.deny(aid):
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

    # ── Docker MCP Gateway lifecycle (manual start/stop) ────────────────

    @staticmethod
    def _docker_gateway_probe(url: str = "http://127.0.0.1:8080/sse", timeout: float = 0.5) -> bool:
        """True if the Docker MCP gateway SSE endpoint is accepting connections."""
        import socket

        try:
            # urlparse to get host/port
            from urllib.parse import urlparse

            u = urlparse(url)
            host = u.hostname or "127.0.0.1"
            port = u.port or 80
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(timeout)
                return sock.connect_ex((host, port)) == 0
        except Exception:
            return False

    async def _handle_docker_gateway_status(self, request):
        """GET /api/control/docker/gateway/status — is the gateway reachable?

        Reports:
          - reachable: bool (port 8080 is open)
          - managed_here: bool (we started it via /start, so /stop will work)
          - container_id: str | None (only set if we started it)
        """
        reachable = await asyncio.to_thread(self._docker_gateway_probe)
        return web.json_response(
            {
                "ok": True,
                "reachable": reachable,
                "managed_here": self._docker_gateway_container_id is not None,
                "container_id": self._docker_gateway_container_id,
                "port": 8080,
            }
        )

    async def _handle_docker_gateway_start(self, request):
        """POST /api/control/docker/gateway/start — start the gateway container.

        Shells out to ``docker run -d --rm -p 8080:8080 ...``. Records the
        container ID so /stop can clean up. No-op with a clear error if
        Docker is unavailable or the container fails to start.
        """
        # If already reachable, don't double-start
        if await asyncio.to_thread(self._docker_gateway_probe):
            return web.json_response(
                {
                    "ok": True,
                    "already_running": True,
                    "reachable": True,
                }
            )

        # Pull the token from charlie_config.json so the gateway's auth matches
        # what MCPClient sends. Fall back to a placeholder if not configured.
        token = ""
        try:
            cfg_path = Path(__file__).parent.parent.parent / "charlie_config.json"
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            token = cfg.get("mcp_servers", {}).get("docker-mcp-gateway", {}).get("token", "")
        except Exception as e:
            logger.warning("docker_gateway_token_lookup_failed | %s", e)

        # Best-effort image — pin a recent version but allow override via env.
        image = os.environ.get("CHARLIE_DOCKER_MCP_IMAGE", "docker/mcp-gateway:latest")
        # Container listens on :8080 by default; --rm so it cleans up on stop.
        cmd = [
            "docker",
            "run",
            "-d",
            "--rm",
            "-p",
            "8080:8080",
        ]
        if token:
            cmd.extend(["-e", f"MCP_GATEWAY_TOKEN={token}"])
        cmd.append(image)
        # Command to run inside the container (gateway runs SSE on :8080)
        cmd.extend(["--transport", "sse", "--port", "8080"])

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            return web.json_response(
                {"ok": False, "error": "docker_cli_not_found"},
                status=503,
            )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=15)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            return web.json_response(
                {"ok": False, "error": "docker_run_timeout"},
                status=504,
            )
        if proc.returncode != 0:
            err = (stderr_b or b"").decode("utf-8", errors="replace").strip()
            return web.json_response(
                {"ok": False, "error": err or "docker_run_failed", "exit_code": proc.returncode},
                status=500,
            )
        container_id = (stdout_b or b"").decode("utf-8", errors="replace").strip()
        if not container_id:
            return web.json_response(
                {"ok": False, "error": "no_container_id_returned"},
                status=500,
            )
        # Strip Docker's default 12-char prefix if present
        self._docker_gateway_container_id = container_id[:64]

        # Give the container a moment to bind the port, then probe.
        await asyncio.sleep(2)
        reachable = await asyncio.to_thread(self._docker_gateway_probe)
        return web.json_response(
            {
                "ok": True,
                "reachable": reachable,
                "container_id": self._docker_gateway_container_id,
                "port": 8080,
            }
        )

    async def _handle_docker_gateway_stop(self, request):
        """POST /api/control/docker/gateway/stop — stop a gateway we started."""
        cid = self._docker_gateway_container_id
        if not cid:
            return web.json_response(
                {"ok": False, "error": "not_managed_here"},
                status=400,
            )
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker",
                "rm",
                "-f",
                cid,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=10)
        except FileNotFoundError:
            return web.json_response(
                {"ok": False, "error": "docker_cli_not_found"},
                status=503,
            )
        except asyncio.TimeoutError:
            return web.json_response(
                {"ok": False, "error": "docker_rm_timeout"},
                status=504,
            )
        self._docker_gateway_container_id = None
        if proc.returncode != 0:
            err = (stderr_b or b"").decode("utf-8", errors="replace").strip()
            # Don't fail on "no such container" — could have died on its own
            if "no such container" in err.lower():
                return web.json_response({"ok": True, "warning": "already_gone"})
            return web.json_response(
                {"ok": False, "error": err or "docker_rm_failed"},
                status=500,
            )
        return web.json_response({"ok": True})

    # ── Settings endpoints ──

    async def _handle_get_settings(self, request):
        """GET /api/settings — read settings via Brain RPC."""
        data = {}
        if self.brain_rpc:
            resp = await self.brain_rpc.request_async("GET_SETTINGS")
            if resp.ok:
                data = resp.data or {}
            elif resp.error == "brain_rpc_timeout":
                return web.json_response({"status": "unavailable", "reason": "brain_rpc_timeout"}, status=503)
        if not data:
            try:
                from charlie.config import settings

                data = settings.to_dict() if hasattr(settings, "to_dict") else {}
            except Exception as e:
                return web.json_response({"error": str(e)}, status=500)
        return web.json_response(data)

    async def _handle_post_settings(self, request):
        """POST /api/settings — write daemon settings to charlie_config.json."""
        try:
            config_path = Path(__file__).parent.parent.parent / "charlie_config.json"
            body = await request.json()
            if not config_path.exists():
                return web.json_response({"ok": False, "error": "config_not_found"}, status=404)
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            # Merge top-level sections
            for section, values in body.items():
                if isinstance(values, dict) and section in config and isinstance(config[section], dict):
                    config[section].update(values)
                else:
                    config[section] = values
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2)
            return web.json_response({"ok": True})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=500)

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
            if self.daemon and hasattr(self.daemon, "brain_task_q"):
                self.daemon.brain_task_q.put(
                    {
                        "type": "CONFIRMATION_RESULT",
                        "content": data.get("result"),
                        "source": "ws_client",
                    }
                )
                await ws.send_json({"type": "confirm_ack"})

        else:
            await ws.send_json({"error": f"unknown_action: {action}"})

    # ── Memory timeline ──

    async def _handle_memory_stats(self, request):
        """GET /api/memory/stats — memory layer statistics."""
        try:
            if self.brain_rpc:
                resp = await self.brain_rpc.request_async("GET_MEMORY_STATS", {})
                if resp.ok and resp.data:
                    return web.json_response(resp.data)
        except Exception:
            pass
        return web.json_response({"working_turns": 0, "episodic": {}, "semantic": {}, "procedural": {}})

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
                return web.json_response(
                    {
                        "results": resp.data or [],
                        "count": len(resp.data or []),
                    }
                )
            if resp.error == "brain_rpc_timeout":
                return web.json_response({"status": "unavailable", "reason": "brain_rpc_timeout"}, status=503)

        # Fallback: local timeline search
        try:
            from charlie.intelligence.timeline import TimelineIndexer

            indexer = TimelineIndexer()
            await asyncio.to_thread(indexer.build_index)
            results = await asyncio.to_thread(indexer.search, query=query, limit=50)
            return web.json_response(
                {
                    "results": [e.to_dict() for e in results],
                    "count": len(results),
                }
            )
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
                return web.json_response({"status": "unavailable", "reason": "brain_rpc_timeout"}, status=503)

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
                                results.append(
                                    {
                                        "source": "chat",
                                        "category": msg.get("role", "message"),
                                        "content": content[:300],
                                        "timestamp": msg.get("timestamp"),
                                    }
                                )
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
                return web.json_response({"status": "unavailable", "reason": "brain_rpc_timeout"}, status=503)
            return web.json_response({"error": resp.error or "rpc_failed"}, status=500)
        return web.json_response({"error": "brain_rpc_not_available"}, status=503)

    # ── Integrations ──

    async def _handle_get_integrations(self, request):
        """GET /api/integrations — list integration health."""
        return web.json_response({"integrations": []})

    # ── Automation ──

    async def _handle_get_rules(self, request):
        """GET /api/automation/rules — list automation rules via Brain RPC with local fallback."""
        if self.brain_rpc:
            resp = await self.brain_rpc.request_async("GET_AUTOMATION_RULES")
            if resp.ok:
                return web.json_response({"rules": resp.data or []})
        # Fallback: read automation_rules from charlie_config.json
        try:
            config_path = Path(__file__).parent.parent.parent / "charlie_config.json"
            if config_path.exists():
                with open(config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                rules = data.get("automation_rules", [])
                return web.json_response({"rules": rules if isinstance(rules, list) else []})
        except Exception:
            pass
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
                    # Ensure every message has a valid timestamp and id
                    now = time.time()
                    for i, msg in enumerate(messages):
                        ts = msg.get("timestamp")
                        if ts is None or not isinstance(ts, (int, float)):
                            # Spread messages 30s apart, most recent near now
                            msg["timestamp"] = now - (len(messages) - 1 - i) * 30
                        if not msg.get("id"):
                            msg["id"] = f"hist-{i}-{hash(msg.get('content', '')[:32]) & 0xFFFFFFFF:08x}"
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
            if self.daemon and hasattr(self.daemon, "brain_task_q"):
                self.daemon.brain_task_q.put(
                    {
                        "type": "TEXT",
                        "content": message,
                        "source": "dashboard",
                    }
                )
                return web.json_response({"status": "queued", "message": "Message sent"})
            return web.json_response({"error": "brain_not_available"}, status=503)
        except Exception as e:
            logger.error("chat_message_failed", error=str(e))
            return web.json_response({"error": str(e)}, status=500)

    # ── Agents, skills, tools ──────────────────────────────────────────────────

    async def _handle_agents_status(self, request):
        """GET /api/agents/status — orchestrator status via Brain RPC."""
        if self.brain_rpc:
            resp = await self.brain_rpc.request_async("GET_AGENT_STATUS")
            if resp.ok:
                agents = resp.data or []
                active_count = sum(1 for a in agents if isinstance(a, dict) and a.get("status") == "busy")
                return web.json_response(
                    {
                        "orchestrator": {
                            "status": "executing" if active_count > 0 else "idle",
                            "active_agents": active_count,
                            "current_plan": "",
                        },
                        "agents": agents,
                    }
                )
            if resp.error == "brain_rpc_timeout":
                return web.json_response({"status": "unavailable", "reason": "brain_rpc_timeout"}, status=503)
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
                            agents.append(
                                {
                                    "id": agent_name,
                                    "name": agent_name,
                                    "role": manifest.get("role", "") or agent_path.name,
                                    "status": "idle",
                                    "current_task": "",
                                }
                            )
                        except Exception:
                            pass
            return web.json_response(
                {
                    "orchestrator": {"status": "idle", "active_agents": 0, "current_plan": ""},
                    "agents": agents,
                }
            )
        except Exception as e:
            logger.warning("agents_status_failed | error=%s", e)
            return web.json_response(
                {"orchestrator": {"status": "error", "active_agents": 0, "current_plan": ""}, "agents": []}
            )

    async def _handle_skills(self, request):
        """GET /api/skills — list all available skills from charlie/skills/."""
        try:
            from charlie.brain.skill_loader import SkillLoader

            loader = SkillLoader(skills_dir="charlie/skills")
            skill_specs = loader.load_all()
            return web.json_response(
                {
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
                }
            )
        except Exception as e:
            logger.debug("skills_load_failed | error=%s", e)
            return web.json_response({"skills": []})

    async def _handle_tools_log(self, request):
        """GET /api/tools/log — recent tool executions via Brain RPC with local fallback."""
        params = {"limit": int(request.query.get("limit", "50"))}
        if self.brain_rpc:
            resp = await self.brain_rpc.request_async("GET_TOOL_LOG", params)
            if resp.ok:
                return web.json_response({"executions": resp.data or []})
        # Fallback: read from scratch/tool_log.json
        try:
            log_file = os.path.join(os.getcwd(), "scratch", "tool_log.json")
            if os.path.exists(log_file):
                with open(log_file, "r", encoding="utf-8") as f:
                    log = json.load(f)
                    limit = params["limit"]
                    return web.json_response({"executions": (log if isinstance(log, list) else [])[:limit]})
        except Exception:
            pass
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
        """GET /api/evolution — self-evolution history from evolution log."""
        try:
            log_file = os.path.join(os.getcwd(), "scratch", "evolution_log.json")
            if os.path.exists(log_file):
                with open(log_file, "r", encoding="utf-8") as f:
                    entries = json.load(f)
                    return web.json_response({"entries": entries if isinstance(entries, list) else []})
        except Exception as e:
            logger.debug("evolution_load_failed | error=%s", e)
        return web.json_response({"entries": []})

    async def _handle_get_briefing(self, request):
        """GET /api/briefing — get latest briefing."""
        return web.json_response({"briefing": self._last_briefing or {}})

    async def _handle_run_briefing(self, request):
        """POST /api/briefing/run — generate new briefing."""
        return web.json_response({"briefing": {}})

    async def _handle_voice_status(self, request):
        """GET /api/voice/status — voice pipeline status."""
        try:
            from charlie.config import settings

            # Check if Audio subsystem is running via daemon
            audio_running = False
            if self.daemon:
                try:
                    status = await asyncio.to_thread(self.daemon.get_daemon_status)
                    audio_status = status.get("subsystems", {}).get("Audio", {})
                    audio_running = audio_status.get("status") == "running"
                except Exception as e:
                    logger.warning("voice_status_audio_check_failed | %s", e)
            # Read real TTS/STT booleans from the IPC bridge's voice cache.
            # Falls back to False if no recent VOICE_ACTIVITY event.
            is_speaking = False
            is_listening = audio_running
            ipc_bridge = getattr(self.daemon, "_ipc_bridge", None) if self.daemon else None
            if ipc_bridge is not None:
                ipc_speaking, ipc_listening = ipc_bridge.get_voice_state()
                is_speaking = ipc_speaking
                is_listening = is_listening and ipc_listening
            return web.json_response(
                {
                    "stt_model": getattr(settings.audio, "stt_model", "unknown"),
                    "tts_model": "kokoro",
                    "tts_speed": getattr(settings.audio, "kokoro_speed", 1.0),
                    "voice_mode": getattr(settings.audio, "voice_mode", "local"),
                    "is_listening": is_listening,
                    "is_speaking": is_speaking,
                }
            )
        except Exception as e:
            logger.error("voice_status_error | %s", e)
        return web.json_response(
            {
                "stt_model": "unknown",
                "tts_model": "unknown",
                "tts_speed": 1.0,
                "is_listening": False,
                "is_speaking": False,
            }
        )

    async def _handle_audio_mute(self, request):
        """POST /api/audio/mute — toggle microphone mute.

        Body: {"muted": true|false}
        Sends a MUTE/UNMUTE command to the audio engine via audio_cmd_q.
        """
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        muted = bool(payload.get("muted", False))
        cmd_type = "MUTE" if muted else "UNMUTE"
        sent = False
        # Try the daemon's audio_cmd_q first
        if self.daemon is not None:
            audio_cmd_q = getattr(self.daemon, "audio_cmd_q", None)
            if audio_cmd_q is not None:
                try:
                    audio_cmd_q.put_nowait({"type": cmd_type})
                    sent = True
                except Exception as e:
                    logger.warning("audio_mute_queue_put_failed | %s", e)
        # Fallback: BrainRPC
        if not sent and self.brain_rpc is not None:
            try:
                resp = await self.brain_rpc.request_async(cmd_type)
                sent = resp.ok
            except Exception as e:
                logger.warning("audio_mute_brainrpc_failed | %s", e)
        return web.json_response({"muted": muted, "sent": sent})

    async def _handle_audio_mute_status(self, request):
        """GET /api/audio/mute — return current mute state.

        Reads the cached voice state from IPCBridge. The IPCBridge caches
        `muted` from the latest VOICE_ACTIVITY event with a 1.5s max-age
        (matches the existing voice-state cache).
        """
        try:
            ipc_bridge = getattr(self.daemon, "_ipc_bridge", None) if self.daemon else None
            if ipc_bridge is not None and hasattr(ipc_bridge, "get_mute_state"):
                muted = ipc_bridge.get_mute_state(max_age=1.5)
            else:
                muted = False
            return web.json_response({"muted": muted})
        except Exception as e:
            logger.error("audio_mute_status_error | %s", e)
            return web.json_response({"muted": False})

    async def _handle_audio_config(self, request):
        """GET /api/audio/config — return current audio configuration."""
        audio_engine = getattr(self.daemon, "audio_engine", None) if self.daemon else None
        if audio_engine is not None and hasattr(audio_engine, "get_config"):
            try:
                return web.json_response(audio_engine.get_config())
            except Exception as e:
                logger.error("audio_config_read_failed | %s", e)
        return web.json_response(
            {
                "muted": False,
                "wake_word": "charlie",
                "wake_word_sensitivity": 0.5,
                "mute_hotkey": "Ctrl+Shift+M",
            }
        )

    async def _handle_audio_config_set(self, request):
        """POST /api/audio/config — update audio config (subset of fields)."""
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"error": "invalid json"}, status=400)
        audio_engine = getattr(self.daemon, "audio_engine", None) if self.daemon else None
        if audio_engine is not None and hasattr(audio_engine, "set_config"):
            try:
                return web.json_response(audio_engine.set_config(payload))
            except Exception as e:
                logger.error("audio_config_set_failed | %s", e)
                return web.json_response({"error": str(e)}, status=500)
        return web.json_response({"error": "audio engine not available"}, status=503)

    async def _handle_metrics(self, request):
        """GET /api/metrics — Prometheus exposition format."""
        from charlie.watchdog.metrics import get_collector
        try:
            text = get_collector().render()
            return web.Response(
                text=text,
                content_type="text/plain; version=0.0.4; charset=utf-8",
            )
        except Exception as e:
            logger.error("metrics_render_failed | %s", e)
            return web.Response(text="# render error\n", status=500)

    async def _handle_tasks(self, request):
        """GET /api/tasks — task queue via Brain RPC with local fallback."""
        if self.brain_rpc:
            resp = await self.brain_rpc.request_async("GET_TASKS")
            if resp.ok:
                return web.json_response({"tasks": resp.data or []})
        # Fallback: read from scratch/tasks.json
        try:
            tasks_file = os.path.join(os.getcwd(), "scratch", "tasks.json")
            if os.path.exists(tasks_file):
                with open(tasks_file, "r", encoding="utf-8") as f:
                    tasks = json.load(f)
                    return web.json_response({"tasks": tasks if isinstance(tasks, list) else []})
        except Exception:
            pass
        return web.json_response({"tasks": []})

    async def _handle_cancel_task(self, request):
        """POST /api/tasks/{task_id}/cancel — cancel a task.

        Routes through BrainRPCClient.request_async so the HTTP response
        reflects the actual outcome of AsyncTaskManager.cancel, not just
        that the request was queued.
        """
        task_id = request.match_info["task_id"]
        if not self.brain_rpc:
            return web.json_response({"ok": False, "error": "brain_rpc_unavailable"}, status=503)
        try:
            resp = await self.brain_rpc.request_async("CANCEL_TASK", {"task_id": task_id})
        except Exception as e:
            logger.error("cancel_task_rpc_failed | %s", e)
            return web.json_response({"ok": False, "error": str(e)}, status=500)
        if resp.error:
            return web.json_response(
                {"ok": False, "task_id": task_id, "error": resp.error},
                status=503,
            )
        return web.json_response(resp.data or {"ok": False, "error": "no_data"})

    # ── MCP helpers ─────────────────────────────────────────────────────────────

    def _get_mcp_manager(self):
        """Return the shared MCPManager, creating it on first call.

        This ensures that connected state and discovered tools persist across
        HTTP requests. Previously each handler created a fresh MCPManager(),
        so connections were silently lost between page refreshes.
        """
        if self._mcp_manager is None:
            from charlie.mcp.manager import MCPManager

            self._mcp_manager = MCPManager()
        return self._mcp_manager

    # ── Skills CRUD ───────────────────────────────────────────────
    async def _handle_create_skill(self, request):
        """POST /api/skills — create a new skill."""
        try:
            body = await request.json()
            name = body.get("name", "").strip()
            if not name:
                return web.json_response({"ok": False, "error": "Name required"}, status=400)
            skills_dir = Path(__file__).parent.parent / "charlie" / "skills" / name
            if skills_dir.exists():
                return web.json_response({"ok": False, "error": "Skill already exists"}, status=409)
            skills_dir.mkdir(parents=True, exist_ok=True)
            # Write SKILL.md
            (skills_dir / "SKILL.md").write_text(f"# {name}\n\n{body.get('description', '')}\n", encoding="utf-8")
            # Write manifest if provided
            if body.get("manifest"):
                (skills_dir / "manifest.json").write_text(json.dumps(body["manifest"], indent=2), encoding="utf-8")
            return web.json_response({"ok": True, "skill": {"name": name}})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    async def _handle_update_skill(self, request):
        """PUT /api/skills/{name} — update a skill."""
        try:
            name = request.match_info["name"]
            body = await request.json()
            skills_dir = Path(__file__).parent.parent / "charlie" / "skills" / name
            if not skills_dir.exists():
                return web.json_response({"ok": False, "error": "Skill not found"}, status=404)
            if body.get("description"):
                (skills_dir / "SKILL.md").write_text(f"# {name}\n\n{body['description']}\n", encoding="utf-8")
            if body.get("manifest"):
                (skills_dir / "manifest.json").write_text(json.dumps(body["manifest"], indent=2), encoding="utf-8")
            return web.json_response({"ok": True})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    async def _handle_delete_skill(self, request):
        """DELETE /api/skills/{name} — delete a skill."""
        try:
            name = request.match_info["name"]
            skills_dir = Path(__file__).parent.parent / "charlie" / "skills" / name
            if not skills_dir.exists():
                return web.json_response({"ok": False, "error": "Skill not found"}, status=404)
            import shutil

            shutil.rmtree(skills_dir)
            return web.json_response({"ok": True})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    # ── Agents CRUD ───────────────────────────────────────────────
    async def _handle_create_agent(self, request):
        """POST /api/agents — create a new agent manifest."""
        try:
            body = await request.json()
            name = body.get("name", "").strip()
            if not name:
                return web.json_response({"ok": False, "error": "Name required"}, status=400)
            agent_dir = Path(__file__).parent.parent / "charlie" / "agents" / name
            if agent_dir.exists():
                return web.json_response({"ok": False, "error": "Agent already exists"}, status=409)
            agent_dir.mkdir(parents=True, exist_ok=True)
            manifest = {
                "name": name,
                "description": body.get("description", ""),
                "prompt": body.get("prompt", ""),
                "model": body.get("model", "nim/primary"),
                "risk_tier": body.get("risk_tier", 0),
                "auto_approve": body.get("auto_approve", False),
                "tools": body.get("tools", []),
                "skills": body.get("skills", []),
            }
            (agent_dir / "agent.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            return web.json_response({"ok": True, "agent": manifest})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    async def _handle_update_agent(self, request):
        """PUT /api/agents/{name} — update an agent manifest."""
        try:
            name = request.match_info["name"]
            body = await request.json()
            agent_dir = Path(__file__).parent.parent / "charlie" / "agents" / name
            manifest_file = agent_dir / "agent.json"
            if not manifest_file.exists():
                return web.json_response({"ok": False, "error": "Agent not found"}, status=404)
            manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
            manifest.update({k: v for k, v in body.items() if v is not None})
            manifest_file.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            return web.json_response({"ok": True, "agent": manifest})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    async def _handle_delete_agent(self, request):
        """DELETE /api/agents/{name} — delete an agent."""
        try:
            name = request.match_info["name"]
            agent_dir = Path(__file__).parent.parent / "charlie" / "agents" / name
            if not agent_dir.exists():
                return web.json_response({"ok": False, "error": "Agent not found"}, status=404)
            import shutil

            shutil.rmtree(agent_dir)
            return web.json_response({"ok": True})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    # ── Automation Rules CRUD ─────────────────────────────────────
    async def _handle_create_rule(self, request):
        """POST /api/automation/rules — add a new rule."""
        try:
            body = await request.json()
            from charlie.automation.models import AutomationRule

            rule = AutomationRule.from_dict(body)
            config_path = Path(__file__).parent.parent.parent / "charlie_config.json"
            data = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
            rules = data.get("automation_rules", [])
            if any(r.get("name") == rule.name for r in rules):
                return web.json_response({"ok": False, "error": "Rule already exists"}, status=409)
            rules.append(rule.to_dict())
            data["automation_rules"] = rules
            config_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            return web.json_response({"ok": True, "rule": rule.to_dict()})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    async def _handle_update_rule(self, request):
        """PUT /api/automation/rules/{name} — update a rule."""
        try:
            name = request.match_info["name"]
            body = await request.json()
            config_path = Path(__file__).parent.parent.parent / "charlie_config.json"
            data = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
            rules = data.get("automation_rules", [])
            idx = next((i for i, r in enumerate(rules) if r.get("name") == name), None)
            if idx is None:
                return web.json_response({"ok": False, "error": "Rule not found"}, status=404)
            rules[idx].update({k: v for k, v in body.items() if v is not None})
            data["automation_rules"] = rules
            config_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            return web.json_response({"ok": True, "rule": rules[idx]})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    async def _handle_delete_rule(self, request):
        """DELETE /api/automation/rules/{name} — delete a rule."""
        try:
            name = request.match_info["name"]
            config_path = Path(__file__).parent.parent.parent / "charlie_config.json"
            data = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
            rules = data.get("automation_rules", [])
            new_rules = [r for r in rules if r.get("name") != name]
            if len(new_rules) == len(rules):
                return web.json_response({"ok": False, "error": "Rule not found"}, status=404)
            data["automation_rules"] = new_rules
            config_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            return web.json_response({"ok": True})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    # ── MCP endpoints ───────────────────────────────────────────────────────────

    async def _handle_mcp_servers(self, request):
        """GET /api/mcp/servers — MCP server list."""
        try:
            config_path = Path(__file__).parent.parent.parent / "charlie_config.json"
            servers = []
            if config_path.exists():
                with open(config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                mcp_config = data.get("mcp_servers", {})
                for name, cfg in mcp_config.items():
                    if isinstance(cfg, dict):
                        servers.append(
                            {
                                "id": name,
                                "name": name,
                                "status": "configured" if cfg.get("enabled", True) else "disabled",
                                "enabled": cfg.get("enabled", True),
                                "tools": [],
                                "config": {k: v for k, v in cfg.items() if k not in ("token",)},
                            }
                        )
            # Also try live manager for connected servers
            try:
                manager = self._get_mcp_manager()
                for name, client in manager.servers.items():
                    # Update existing or add new
                    existing = next((s for s in servers if s["id"] == name), None)
                    if existing:
                        existing["status"] = "connected" if client.connected else existing["status"]
                        existing["tools"] = client.tools if client.connected else []
                    else:
                        servers.append(
                            {
                                "id": name,
                                "name": name,
                                "status": "connected" if client.connected else "disconnected",
                                "enabled": client.enabled,
                                "tools": client.tools if client.connected else [],
                                "config": getattr(client, "config", {}),
                            }
                        )
            except Exception:
                pass  # Config-based list is still valid
            return web.json_response({"servers": servers})
        except Exception:
            return web.json_response({"servers": []})

    async def _handle_toggle_mcp(self, request):
        """POST /api/mcp/{server_id}/toggle — toggle MCP server enabled state."""
        server_id = request.match_info["server_id"]
        try:
            manager = self._get_mcp_manager()
            new_state = manager.toggle_server(server_id)
            return web.json_response({"ok": True, "enabled": new_state})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)})

    async def _handle_mcp_connect(self, request):
        """POST /api/mcp/{server_id}/connect — connect to MCP server and discover tools."""
        server_id = request.match_info["server_id"]
        try:
            manager = self._get_mcp_manager()
            tools = await manager.start_server(server_id)
            return web.json_response({"ok": True, "connected": True, "tools": tools})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)})

    async def _handle_mcp_disconnect(self, request):
        """POST /api/mcp/{server_id}/disconnect — disconnect from MCP server."""
        server_id = request.match_info["server_id"]
        try:
            manager = self._get_mcp_manager()
            await manager.stop_server(server_id)
            return web.json_response({"ok": True, "connected": False})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)})

    async def _handle_mcp_call_tool(self, request):
        """POST /api/mcp/{server_id}/tools/{tool_name}/call — call an MCP tool."""
        server_id = request.match_info["server_id"]
        tool_name = request.match_info["tool_name"]
        try:
            body = await request.json()
        except Exception:
            body = {}
        try:
            manager = self._get_mcp_manager()
            if server_id not in manager.servers:
                return web.json_response({"ok": False, "error": "Server not found"}, status=404)
            client = manager.servers[server_id]
            if not client.connected:
                return web.json_response({"ok": False, "error": "Server not connected"}, status=400)
            result = await client.call_tool(tool_name, body.get("arguments", {}))
            return web.json_response({"ok": True, "result": result})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)})

    async def _handle_mcp_add_server(self, request):
        """POST /api/mcp/servers — add a new MCP server to config."""
        try:
            body = await request.json()
            name = body.get("name", "").strip()
            if not name:
                return web.json_response({"ok": False, "error": "Name required"}, status=400)
            config = body.get("config", {})
            config_path = Path(__file__).parent.parent.parent / "charlie_config.json"
            data = {}
            if config_path.exists():
                with open(config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            mcp = data.get("mcp_servers", {})
            mcp[name] = {"enabled": True, **config}
            data["mcp_servers"] = mcp
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            # Reload config into the shared manager
            manager = self._get_mcp_manager()
            manager.reload_config()
            return web.json_response(
                {"ok": True, "server": {"id": name, "name": name, "status": "configured", "enabled": True}}
            )
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)})

    async def _handle_mcp_delete_server(self, request):
        """DELETE /api/mcp/{server_id} — remove MCP server from config."""
        server_id = request.match_info["server_id"]
        try:
            config_path = Path(__file__).parent.parent.parent / "charlie_config.json"
            data = {}
            if config_path.exists():
                with open(config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            mcp = data.get("mcp_servers", {})
            if server_id in mcp:
                del mcp[server_id]
                data["mcp_servers"] = mcp
                with open(config_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
            # Remove from the shared manager
            manager = self._get_mcp_manager()
            if server_id in manager.servers:
                await manager.remove_server(server_id)
            return web.json_response({"ok": True})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)})

    # ── Orchestrator endpoints ──

    def _get_orchestrator(self):
        """Get or create orchestrator singleton."""
        if not hasattr(self, "_orchestrator"):
            from charlie.brain.learning import AgentLearningTracker
            from charlie.brain.orchestrator import TaskOrchestrator

            learning = AgentLearningTracker()
            brain = getattr(self.daemon, "_brain", None) if self.daemon else None
            self._orchestrator = TaskOrchestrator(brain=brain, learning_tracker=learning)
        return self._orchestrator

    # ── Metrics endpoint ──

    async def _handle_metrics(self, request):
        """GET /api/metrics — Prometheus exposition format."""
        from charlie.watchdog.metrics import get_collector
        try:
            text = get_collector().render()
            return web.Response(
                text=text,
                content_type="text/plain; version=0.0.4; charset=utf-8",
            )
        except Exception as e:
            logger.error("metrics_render_failed | %s", e)
            return web.Response(text="# render error\n", status=500)

    async def _handle_orchestrator_plan(self, request):
        """POST /api/orchestrator/plan — decompose a goal into subtasks."""
        try:
            body = await request.json()
            goal = body.get("goal", "").strip()
            if not goal:
                return web.json_response({"ok": False, "error": "No goal provided"}, status=400)
            orchestrator = self._get_orchestrator()
            agents = (
                list(orchestrator.planner._pick_agent.__code__.co_consts)
                if False
                else ["research", "coding", "writer", "comms", "system", "vision"]
            )
            subtasks = await orchestrator.plan(goal, agents)
            return web.json_response(
                {
                    "ok": True,
                    "subtasks": [
                        {
                            "id": t.id,
                            "description": t.description,
                            "suggested_agent": t.suggested_agent,
                            "dependencies": t.dependencies,
                            "required_tools": t.required_tools,
                            "status": t.status.value,
                        }
                        for t in subtasks
                    ],
                }
            )
        except Exception as e:
            logger.error("orchestrator_plan_error | %s", e)
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    async def _handle_orchestrator_learning(self, request):
        """GET /api/orchestrator/learning — get learning data."""
        try:
            orchestrator = self._get_orchestrator()
            stats = orchestrator.learning.get_stats()
            history = orchestrator.learning.get_history(limit=50)
            return web.json_response({"ok": True, "stats": stats, "history": history})
        except Exception as e:
            logger.error("orchestrator_learning_error | %s", e)
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    async def _handle_orchestrator_execute(self, request):
        """POST /api/orchestrator/execute — plan and execute a goal."""
        try:
            body = await request.json()
            goal = body.get("goal", "").strip()
            if not goal:
                return web.json_response({"ok": False, "error": "No goal provided"}, status=400)
            orchestrator = self._get_orchestrator()
            agents = ["research", "coding", "writer", "comms", "system", "vision"]
            subtasks = await orchestrator.plan(goal, agents)
            # Get agent registry from brain if available
            agent_registry = None
            if self.daemon and hasattr(self.daemon, "_brain"):
                brain = self.daemon._brain
                if hasattr(brain, "agent_registry"):
                    agent_registry = brain.agent_registry
            results = await orchestrator.execute_plan(subtasks, agent_registry)
            return web.json_response({"ok": True, "results": results})
        except Exception as e:
            logger.error("orchestrator_execute_error | %s", e)
            return web.json_response({"ok": False, "error": str(e)}, status=500)

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
            asyncio.run_coroutine_threadsafe(self._broadcast_ws(event_type, data), self._loop)
        except Exception as e:
            logger.debug(f"broadcast_sync_failed | {e}")

    # ── Approval queue management ──

    def _on_approval_change(self, event_type: str, approval):
        """ApprovalQueue change listener: push to WS + persist."""
        try:
            self.broadcast_sync(
                "approval_resolved" if event_type in ("approved", "denied", "expired") else "approval_pending",
                {
                    "id": approval.id,
                    "decision": event_type,
                    **{k: v for k, v in approval.to_dict().items() if k != "id"},
                },
            )
        except Exception:
            pass
        self._save_approvals()

    def _load_approvals(self):
        """Load persisted approvals from disk and re-seed the queue."""
        try:
            if not self._approvals_path.exists():
                return
            with open(self._approvals_path, "r", encoding="utf-8") as f:
                persisted = json.load(f)
            # Persisted format is {id: {status, action, ...}}; re-add to
            # the live queue. The queue's PendingApproval dataclass needs
            # the same fields the loader has been writing.
            from charlie.watchdog.approval_queue import PendingApproval

            for aid, data in persisted.items():
                if not isinstance(data, dict):
                    continue
                pa = PendingApproval(
                    id=aid,
                    action=data.get("action", ""),
                    args=data.get("args", {}),
                    risk_tier=int(data.get("risk_tier", 1)),
                    description=data.get("description", ""),
                    source=data.get("source", "unknown"),
                    timestamp=float(data.get("timestamp", time.time())),
                    timeout=float(data.get("timeout", 60.0)),
                    status=data.get("status", "pending"),
                )
                # Skip expired or already-resolved entries on load
                if pa.is_expired or pa.status != "pending":
                    continue
                self._approval_queue.add(pa)
            logger.info(
                "approvals_loaded | count=%d",
                self._approval_queue.pending_count,
            )
        except Exception as e:
            logger.warning("approvals_load_failed | %s", e)

    def _save_approvals(self):
        """Persist approvals to disk using the queue's get_all() view."""
        try:
            self._approvals_path.parent.mkdir(parents=True, exist_ok=True)
            persisted: dict = {}
            for pa in self._approval_queue.get_all():
                persisted[pa.id] = pa.to_dict()
            with open(self._approvals_path, "w", encoding="utf-8") as f:
                json.dump(persisted, f, indent=2)
        except Exception as e:
            logger.warning("approvals_save_failed | %s", e)

    def add_pending_approval(self, approval_id: str, approval_data: dict):
        """Stage a pending approval on the queue (no legacy dict mirror).

        The ``on_change`` listener (``_on_approval_change``) handles the WS
        broadcast and persistence, so callers do not need to do either.
        """
        from charlie.watchdog.approval_queue import PendingApproval

        pa = PendingApproval(
            id=approval_id,
            action=approval_data.get("action", ""),
            args=approval_data.get("args", {}),
            risk_tier=int(approval_data.get("risk_tier", 1)),
            description=approval_data.get("description", ""),
            source=approval_data.get("source", "unknown"),
            timestamp=time.time(),
            timeout=float(approval_data.get("timeout", 60.0)),
            status="pending",
        )
        self._approval_queue.add(pa)

    def resolve_approval(self, approval_id: str, decision: str):
        """Resolve a pending approval (approve / deny)."""
        if decision == "approved":
            self._approval_queue.approve(approval_id)
        else:
            self._approval_queue.deny(approval_id)

    @property
    def is_running(self) -> bool:
        return self._running

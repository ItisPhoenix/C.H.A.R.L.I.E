"""
charlie/watchdog/daemon_supervisor.py

DaemonSupervisor extends PhoenixSupervisor to run Charlie as a headless daemon.
Dashboard is the sole interface.
"""

import asyncio
import logging as _logging
import threading
import time
from queue import Queue

from charlie.watchdog.phoenix import PhoenixSupervisor, logger


class DaemonSupervisor(PhoenixSupervisor):
    """Headless daemon supervisor. Starts all processes, dashboard is the sole UI."""

    def __init__(self, interrupt_event, reboot_event=None):
        super().__init__(interrupt_event, reboot_event)
        self._control_server = None
        self._ipc_bridge = None
        self._start_time = time.time()
        # Bounded buffer for live log entries. The DashboardLogHandler
        # pushes onto this queue; the IPCBridge drains it in a dedicated
        # thread and forwards entries to the dashboard WebSocket. Sized
        # for ~10s of INFO traffic at chat rate before backpressure kicks
        # in — anything beyond is dropped (counted on the handler).
        self.log_q: Queue = Queue(maxsize=500)
        # Reference is held so we can read dropped_count from the WS/REST
        # endpoints later. Stays alive for the lifetime of the daemon.
        self._log_handler = None
        self._install_dashboard_log_handler()

    def _install_dashboard_log_handler(self) -> None:
        """Install the DashboardLogHandler on the root logger.

        Idempotent: re-installing a daemon is rare but possible after a
        reboot, so we detach any previous instance first.
        """
        try:
            from charlie.watchdog.log_broadcaster import DashboardLogHandler
        except Exception as e:  # pragma: no cover — defensive only
            logger.warning("log_broadcaster_import_failed | %s", e)
            return

        handler = DashboardLogHandler(self.log_q, level=_logging.INFO)
        handler.setFormatter(_logging.Formatter("%(message)s"))

        root_logger = _logging.getLogger()
        if self._log_handler is not None:
            try:
                root_logger.removeHandler(self._log_handler)
            except Exception:
                pass
        root_logger.addHandler(handler)
        self._log_handler = handler
        logger.info("dashboard_log_handler_installed")

    def _start_mcp_gateway(self):
        """Start MCP gateway via docker-compose."""
        import os
        import secrets
        import subprocess

        # Generate token if not set
        if not os.environ.get("MCP_GATEWAY_TOKEN"):
            os.environ["MCP_GATEWAY_TOKEN"] = secrets.token_urlsafe(32)

        try:
            proc = subprocess.Popen(
                ["docker-compose", "up", "-d", "mcp-gateway"],
                cwd=os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._mcp_gateway_proc = proc
            logger.info("mcp_gateway_started | pid=%d", proc.pid)
        except FileNotFoundError:
            logger.warning("mcp_gateway_skipped | docker-compose not found")
            self._mcp_gateway_proc = None
        except Exception as e:
            logger.error("mcp_gateway_failed | %s", e)
            self._mcp_gateway_proc = None

    def _start_playwright_mcp(self):
        """Start Playwright MCP server for browser automation."""
        import subprocess
        import os

        npx_cmd = "npx.cmd" if os.name == "nt" else "npx"
        try:
            proc = subprocess.Popen(
                [npx_cmd, "@playwright/mcp@latest", "--port", "8081"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._playwright_mcp_proc = proc
            logger.info("playwright_mcp_started | pid=%d", proc.pid)
        except FileNotFoundError:
            logger.warning("playwright_mcp_skipped | npx not found")
            self._playwright_mcp_proc = None
        except Exception as e:
            logger.error("playwright_mcp_failed | %s", e)
            self._playwright_mcp_proc = None

    def start(self):
        """Start processes."""
        logger.info("daemon_supervisor_ignited")

        self.start_process("Brain", self._run_brain_safe)
        self.start_process("Browser", self._run_browser_safe)
        # Start MCP gateway
        self._start_mcp_gateway()
        # Start Playwright MCP for browser automation
        self._start_playwright_mcp()

        # Start control server if available
        self._start_control_server()

        # Start system tray icon
        self._start_tray()

        try:
            self.monitor()
        except KeyboardInterrupt:
            self.stop()
        except Exception as e:
            logger.error("daemon_runtime_error", error=str(e), exc_info=True)
            self.stop()

    def _start_control_server(self):
        """Start the HTTP/WS control server and IPC bridge in background threads."""
        try:
            from charlie.watchdog.control_server import ControlServer

            self._control_server = ControlServer(daemon=self)
            thread = threading.Thread(target=self._control_server.start, daemon=True, name="ControlServer")
            thread.start()
            logger.info("control_server_started")
        except ImportError:
            logger.warning("control_server_not_available | skipping")
        except Exception as e:
            logger.error("control_server_start_failed", error=str(e))

        # Start IPC bridge (status_q → WS)
        try:
            from charlie.watchdog.ipc_bridge import IPCBridge

            self._ipc_bridge = IPCBridge(
                status_q=self.status_q,
                brain_task_q=self.brain_task_q,
                control_server=self._control_server,
                log_q=self.log_q,
            )
            self._ipc_bridge.start()
            logger.info("ipc_bridge_started")
        except ImportError:
            logger.warning("ipc_bridge_not_available | skipping")
        except Exception as e:
            logger.error("ipc_bridge_start_failed", error=str(e))

    def _teardown_servers(self) -> None:
        """Stop the IPC bridge and Control_Server during supervisor teardown.

        Overrides the base no-op hook (Reqs 14.2, 14.8). Each server is torn
        down under its own try/except so a failure stopping one does not block
        the other — or the manager shutdown that follows in ``stop()``.

        The Control_Server's ``stop()`` is an async coroutine running on its own
        event loop in a separate thread. We schedule it onto that loop via
        ``run_coroutine_threadsafe`` and wait briefly for it to release port
        8090 (Req 14.5). If the loop is gone or scheduling fails, we fall back
        to flipping the server's ``_running`` flag so its run loop unwinds.
        """
        # Stop the IPC bridge (sole status_q consumer) first so it stops
        # forwarding to a Control_Server that is about to close.
        try:
            if getattr(self, "_ipc_bridge", None) is not None:
                self._ipc_bridge.stop()
                logger.info("ipc_bridge_stopped_on_teardown")
        except Exception as e:
            logger.error("ipc_bridge_teardown_failed", error=str(e))

        # Detach the dashboard log handler so the root logger does not
        # retain a queue that the IPCBridge thread is no longer draining.
        try:
            handler = getattr(self, "_log_handler", None)
            if handler is not None:
                _logging.getLogger().removeHandler(handler)
        except Exception as e:
            logger.error("log_handler_detach_failed | %s", e)

        # Stop the Control_Server (async coroutine on its own loop/thread).
        try:
            cs = getattr(self, "_control_server", None)
            if cs is not None:
                loop = getattr(cs, "_loop", None)
                stopped = False
                if loop is not None and loop.is_running():
                    try:
                        future = asyncio.run_coroutine_threadsafe(cs.stop(), loop)
                        future.result(timeout=5)
                        stopped = True
                        logger.info("control_server_stopped_on_teardown")
                    except Exception as e:
                        logger.error("control_server_stop_coro_failed", error=str(e))
                if not stopped:
                    # Fall back to signalling the run loop to unwind.
                    try:
                        cs._running = False
                        logger.info("control_server_stop_fallback")
                    except Exception as e:
                        logger.error("control_server_stop_fallback_failed", error=str(e))
        except Exception as e:
            logger.error("control_server_teardown_failed", error=str(e))

        # Stop MCP gateway container
        try:
            import subprocess
            import os

            proc = getattr(self, "_mcp_gateway_proc", None)
            if proc is not None:
                subprocess.run(
                    ["docker-compose", "down"],
                    cwd=os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=15,
                )
                logger.info("mcp_gateway_stopped_on_teardown")
        except Exception as e:
            logger.error("mcp_gateway_teardown_failed | %s", e)

        # Stop Playwright MCP server
        try:
            proc = getattr(self, "_playwright_mcp_proc", None)
            if proc is not None and proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=5)
                logger.info("playwright_mcp_stopped_on_teardown")
        except Exception as e:
            logger.error("playwright_mcp_teardown_failed | %s", e)

    def _start_tray(self):
        """Start system tray icon in a background thread."""
        try:
            from charlie.watchdog.tray import TrayIcon

            self._tray = TrayIcon(daemon=self)
            self._tray.start()
            logger.info("tray_icon_started")
        except ImportError:
            logger.warning("tray_not_available | skipping")
        except Exception as e:
            logger.error("tray_start_failed", error=str(e))

    @property
    def uptime(self) -> float:
        """Daemon uptime in seconds."""
        return time.time() - self._start_time

    @property
    def control_server(self):
        """Access the control server instance."""
        return self._control_server

    def get_daemon_status(self) -> dict:
        """Get full daemon status for the control API."""
        vitals = self.doctor.obs.get_vitals()
        subsystems = {}

        for name, data in self.processes.items():
            p = data["process"]
            pid = p.pid
            proc_vitals = vitals.get("processes", {}).get(pid, {})
            subsystems[name] = {
                "status": "running" if p.is_alive() else "stopped",
                "pid": pid,
                "restarts": data["restarts"],
                "cpu": proc_vitals.get("cpu", 0.0),
                "ram_mb": proc_vitals.get("ram_mb", 0.0),
            }

        return {
            "uptime_seconds": self.uptime,
            "subsystems": subsystems,
            "system": {
                "cpu": vitals.get("system_cpu", 0),
                "ram": vitals.get("system_ram_percent", 0),
            },
        }

    # ── Safe entry points ──

    @staticmethod
    def _run_brain_safe(
        brain_task_q,
        tts_q,
        status_q,
        audio_cmd_q,
        browser_req_q,
        browser_res_q,
        telegram_q,
        heartbeat,
        interrupt_event,
        reboot_event,
        brain_req_q=None,
        brain_res_q=None,
    ):
        from dotenv import load_dotenv

        load_dotenv(override=True)
        from charlie.config import ensure_initialized

        ensure_initialized()

        import pythoncom
        from charlie.brain.core import Brain

        pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
        brain = Brain(
            brain_task_q=brain_task_q,
            tts_q=tts_q,
            status_q=status_q,
            audio_cmd_q=audio_cmd_q,
            browser_req_q=browser_req_q,
            browser_res_q=browser_res_q,
            telegram_q=telegram_q,
            heartbeat=heartbeat,
            interrupt_event=interrupt_event,
            reboot_event=reboot_event,
            brain_req_q=brain_req_q,
            brain_res_q=brain_res_q,
        )
        brain.run()

    @staticmethod
    def _run_browser_safe(browser_req_q, browser_res_q, status_q, heartbeat):
        from dotenv import load_dotenv

        load_dotenv(override=True)
        from charlie.config import ensure_initialized

        ensure_initialized()

        from charlie.browser.headless_browser import HeadlessBrowserProcess

        proc = HeadlessBrowserProcess(browser_req_q, browser_res_q, heartbeat, status_q=status_q)
        proc.run()

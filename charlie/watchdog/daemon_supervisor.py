"""
charlie/watchdog/daemon_supervisor.py

DaemonSupervisor extends PhoenixSupervisor to run Charlie as a headless daemon.
Dashboard is the sole interface.
"""

import threading
import time

from charlie.watchdog.phoenix import PhoenixSupervisor, logger


class DaemonSupervisor(PhoenixSupervisor):
    """Headless daemon supervisor. Starts all processes, dashboard is the sole UI."""

    def __init__(self, interrupt_event, reboot_event=None):
        super().__init__(interrupt_event, reboot_event)
        self._control_server = None
        self._ipc_bridge = None
        self._start_time = time.time()

    def start(self):
        """Start all processes."""
        logger.info("daemon_supervisor_ignited")

        self.start_process("Audio", self._run_audio_safe)
        self.start_process("Brain", self._run_brain_safe)
        self.start_process("Browser", self._run_browser_safe)
        self.start_process("Telegram", self._run_telegram_safe)
        self.start_process("Vision", self._run_vision_safe)

        # Start control server if available
        self._start_control_server()

        # Start dashboard web server on port 3000
        self._start_dashboard()

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
            thread = threading.Thread(
                target=self._control_server.start, daemon=True, name="ControlServer"
            )
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
            )
            self._ipc_bridge.start()
            logger.info("ipc_bridge_started")
        except ImportError:
            logger.warning("ipc_bridge_not_available | skipping")
        except Exception as e:
            logger.error("ipc_bridge_start_failed", error=str(e))

    def _start_dashboard(self):
        """Start the dashboard FastAPI server on port 3005 in a background thread."""
        try:
            def _run_dashboard():
                import uvicorn
                from charlie.dashboard.main import app
                uvicorn.run(app, host="0.0.0.0", port=3005, log_level="warning")

            thread = threading.Thread(
                target=_run_dashboard, daemon=True, name="Dashboard"
            )
            thread.start()
            logger.info("dashboard_started | port=3005")
        except ImportError:
            logger.warning("dashboard_not_available | skipping")
        except Exception as e:
            logger.error("dashboard_start_failed", error=str(e))

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

    # ── Safe entry points that handle pythoncom ──

    @staticmethod
    def _run_audio_safe(audio_q, brain_task_q, tts_q, status_q, audio_cmd_q,
                        heartbeat, interrupt_event):
        from dotenv import load_dotenv
        load_dotenv()
        from charlie.config import ensure_initialized
        ensure_initialized()

        import pythoncom
        from charlie.audio_proc import AudioEngine
        pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
        engine = AudioEngine(brain_task_q, tts_q, status_q, audio_cmd_q,
                             heartbeat, interrupt_event)
        engine.run()

    @staticmethod
    def _run_brain_safe(brain_task_q, tts_q, status_q, audio_cmd_q,
                        browser_req_q, browser_res_q, telegram_q, heartbeat,
                        interrupt_event, reboot_event):
        from dotenv import load_dotenv
        load_dotenv()
        from charlie.config import ensure_initialized
        ensure_initialized()

        import pythoncom
        from charlie.brain.core import Brain
        pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
        brain = Brain(
            brain_task_q=brain_task_q, tts_q=tts_q, status_q=status_q,
            audio_cmd_q=audio_cmd_q, browser_req_q=browser_req_q,
            browser_res_q=browser_res_q, telegram_q=telegram_q,
            heartbeat=heartbeat, interrupt_event=interrupt_event,
            reboot_event=reboot_event,
        )
        brain.run()

    @staticmethod
    def _run_browser_safe(browser_req_q, browser_res_q, status_q, heartbeat):
        from dotenv import load_dotenv
        load_dotenv()
        from charlie.config import ensure_initialized
        ensure_initialized()

        from charlie.browser.headless_browser import HeadlessBrowserProcess
        proc = HeadlessBrowserProcess(browser_req_q, browser_res_q, heartbeat,
                                      status_q=status_q)
        proc.run()

    @staticmethod
    def _run_telegram_safe(brain_task_q, status_q, telegram_q, audio_cmd_q,
                           heartbeat):
        from dotenv import load_dotenv
        load_dotenv()
        from charlie.config import ensure_initialized
        ensure_initialized()

        import pythoncom
        from charlie.telegram.bridge import run_bridge
        pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
        run_bridge(brain_task_q, status_q, telegram_q, audio_cmd_q, heartbeat)

    @staticmethod
    def _run_vision_safe(brain_task_q, status_q, heartbeat):
        from dotenv import load_dotenv
        load_dotenv()
        from charlie.config import ensure_initialized
        ensure_initialized()

        import os
        from charlie.vision.activity_sentinel import ActivitySentinel
        if os.name == 'nt':
            try:
                null_fd = os.open('NUL', os.O_WRONLY)
                os.dup2(null_fd, 2)
            except Exception:
                pass
        sentinel = ActivitySentinel(brain_task_q, status_q, heartbeat)
        sentinel.run()

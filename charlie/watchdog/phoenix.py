import multiprocessing
import multiprocessing as mp
import os
import platform
import subprocess
import sys
import threading
import time

import requests

from charlie.config import settings
from charlie.utils.doctor import Doctor
from charlie.utils.logger import get_logger

logger = get_logger("Phoenix")

# ── Heartbeat Protocol Constants ─────────────────────────────────────────────
HEARTBEAT_TIMEOUT = 120.0  # INCREASED: Allows for slow starts and heavy VRAM usage
MONITOR_INTERVAL = 2.5  # Frequency of watchdog checks
# Non-blocking restart cooldown (Req 15.5): after a crash we defer the respawn by
# this many seconds using a per-process ``restart_not_before`` timestamp instead
# of a blocking ``time.sleep`` that would stall detection of other failures.
RESTART_COOLDOWN = 10


def _consume_disabled_statuses(
    status_queues: dict[str, "multiprocessing.Queue"],
    disabled: set[str],
) -> None:
    """Drain SUBSYSTEM_STATUS messages from status queues.

    - ``state=disabled`` adds the child name to *disabled*.
    - ``state=ok`` removes the child name from *disabled* (recovery).
    - All other message types (and non-dict payloads) are silently dropped.
    """
    import queue as _queue

    for name, q in status_queues.items():
        while True:
            try:
                msg = q.get_nowait()
            except (_queue.Empty, EOFError, OSError):
                break
            if not isinstance(msg, dict):
                continue
            if msg.get("type") != "SUBSYSTEM_STATUS":
                continue
            child_name = msg.get("name", name)
            state = msg.get("state", "")
            if state == "disabled":
                disabled.add(child_name)
            elif state == "ok":
                disabled.discard(child_name)


# ── Shared Entry Points ──────────────────────────────────────────────────────


def run_browser(browser_req_q, browser_res_q, status_q, heartbeat):
    from dotenv import load_dotenv

    load_dotenv(override=True)
    from charlie.config import ensure_initialized

    ensure_initialized()

    from charlie.browser.headless_browser import HeadlessBrowserProcess

    proc = HeadlessBrowserProcess(browser_req_q, browser_res_q, heartbeat, status_q=status_q)
    proc.run()


def run_brain(
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


# ── Self-Repair Engine ───────────────────────────────────────────────────────


class PhoenixSupervisor:
    def __init__(self, interrupt_event, reboot_event=None):
        self.interrupt_event = interrupt_event
        self.reboot_event = reboot_event or multiprocessing.Event()
        # Shutdown is routed through the main monitor thread, identical to reboot
        # (Reqs 14.3, 14.4). Any thread (ControlServer, tray) sets this event;
        # only monitor() — which runs on the main thread — acts on it so the
        # whole process is torn down instead of just the calling thread.
        self.shutdown_event = multiprocessing.Event()
        # Keep a reference to the Manager so stop() can shut it down (Req 14.2).
        # A LOCAL var would leak the manager process on teardown.
        self._manager = multiprocessing.Manager()
        self.brain_task_q = self._manager.Queue(maxsize=100)
        self.tts_q = self._manager.Queue(maxsize=100)
        # Note: status_q swap to local Queue was reverted — Manager proxy needed
        # for cross-process sharing across Brain + Browser producers.
        self.status_q = self._manager.Queue(maxsize=200)
        # Real multiprocessing.Queue for the audio command channel (100 Hz poll).
        self.audio_cmd_q = multiprocessing.Queue(maxsize=100)
        self.telegram_q = self._manager.Queue(maxsize=100)

        # Heartbeats (shared multiprocessing values)
        self.heartbeats = {
            "Brain": multiprocessing.Value("d", time.time()),
            "Browser": multiprocessing.Value("d", time.time()),
        }

        self.browser_req_q = self._manager.Queue(maxsize=50)
        self.browser_res_q = self._manager.Queue(maxsize=50)

        # Cross-process Brain RPC queues (Req 7 / Design §D)
        # Capacity 200 (up from 50) — the dashboard home page fires
        # fetchTasks + fetchToolLog + fetchStatus every 5s across the
        # `Promise.all` fan-out, and any extra open tabs (e.g. /tasks,
        # /briefing) compound the request rate. With a 50-slot queue and
        # Manager-proxy latency on the response round-trip, the request
        # queue fills faster than BrainRPCServer can drain it, and the
        # blocking put() back-pressures the aiohttp handler for ~13s
        # per call (3 retries × backoff), eventually tripping the
        # 120s HEARTBEAT_TIMEOUT in the monitor. 200 slots = ~2 minutes
        # of buffer at the observed 1.5 req/s burst rate.
        self.brain_req_q = self._manager.Queue(maxsize=200)
        self.brain_res_q = self._manager.Queue(maxsize=200)

        self.processes = {}
        self._processes_lock = threading.RLock()
        self.running = True
        self.doctor = Doctor(status_q=self.status_q)

        # SUBSYSTEM_STATUS gate (Core Loop §C): each subsystem can post
        # state="disabled" / state="ok" on status_q. Disabled children are
        # exempt from heartbeat-staleness checks in monitor().
        self.disabled_children: set[str] = set()
        self.status_queues: dict[str, "mp.Queue"] = {
            "audio": self.status_q,
            "brain": self.status_q,
            "browser": self.status_q,
            "telegram": self.status_q,
            "vision": self.status_q,
        }

    def _safe_put(self, q, msg):
        """Put message to queue with 1.0s timeout to prevent deadlocks from clogged queues."""
        try:
            if q is not None:
                q.put(msg, timeout=1.0)
        except Exception as e:
            logger.warning(f"queue_put_failed | {e}")

    def _send_direct_telegram(self, text: str):
        """Synchronous fallback loop directly posting SRE alerts to Telegram API."""
        token = settings.supervisor.telegram_token
        chat_id = settings.supervisor.telegram_chat_id
        if not token or not chat_id:
            logger.error("telegram_direct_fallback_failed | token or chat_id not configured")
            return

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        try:
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code == 200:
                logger.info("telegram_direct_fallback_delivered")
            else:
                logger.error(f"telegram_direct_fallback_error | status={resp.status_code} | response={resp.text}")
        except Exception as e:
            logger.error(f"telegram_direct_fallback_failed | {e}")

    def start(self):
        """Unified entry point to launch processes and begin monitoring."""
        logger.info("phoenix_supervisor_ignited")

        self.start_process("Brain", run_brain)
        self.start_process("Browser", run_browser)

        try:
            self.monitor()
        except KeyboardInterrupt:
            self.stop()
        except Exception as e:
            logger.error("supervisor_runtime_error", error=str(e), exc_info=True)
            self.stop()

    def start_process(self, name, target, restarts=0, args=None, daemon=True):
        heartbeat = self.heartbeats[name]
        heartbeat.value = time.time()  # Reset heartbeat on start

        if args is not None:
            args = args + (heartbeat,)
        elif name == "Brain":
            args = (
                self.brain_task_q,
                self.tts_q,
                self.status_q,
                self.audio_cmd_q,
                self.browser_req_q,
                self.browser_res_q,
                self.telegram_q,
                heartbeat,
                self.interrupt_event,
                self.reboot_event,
                self.brain_req_q,
                self.brain_res_q,
            )
        elif name == "Browser":
            args = (
                self.browser_req_q,
                self.browser_res_q,
                self.status_q,
                heartbeat,
            )
        else:
            logger.warning(f"unknown_process_type | name={name}")
            return

        p = multiprocessing.Process(target=target, args=args, name=name, daemon=daemon)
        p.start()
        with self._processes_lock:
            self.processes[name] = {"process": p, "target": target, "restarts": restarts, "started_at": time.time()}
        logger.info(f"process_started: {name} (PID: {p.pid})")

    def monitor(self):
        print("\n" + "=" * 50)
        print("  P H O E N I X   S U P E R V I S O R")
        print("=" * 50)

        while self.running:
            time.sleep(MONITOR_INTERVAL)
            now = time.time()

            # ── Check for Manual Reboot Signal ──
            if self.reboot_event.is_set():
                logger.info("reboot_signal_detected | restarting_engine")
                self.reboot()
                return

            # ── Check for Manual Shutdown Signal ──
            # Routed here from any thread (ControlServer/tray) via shutdown_event
            # so the teardown executes on the main monitor thread (Reqs 14.3, 14.4).
            if self.shutdown_event.is_set():
                logger.info("shutdown_signal_detected | stopping_engine")
                self.stop()
                return

            # Locked snapshot of the current role names; the lock is NOT held for
            # the whole loop body so a slow restart/cooldown cannot block monitoring.
            with self._processes_lock:
                names = list(self.processes.keys())

            # ── SUBSYSTEM_STATUS gate (Core Loop §C) ──
            # Drain any pending SUBSYSTEM_STATUS payloads from children so we
            # know which subsystems are self-declared disabled (and which have
            # recovered to "ok") BEFORE we evaluate heartbeat staleness.
            _consume_disabled_statuses(self.status_queues, self.disabled_children)

            for name in names:
                # Re-acquire briefly to read the specific entry (it may have been
                # replaced/removed by a concurrent restart_subsystem call).
                with self._processes_lock:
                    data = self.processes.get(name)
                if data is None:
                    continue

                # ── Quarantine enforcement (Req 15.3) ──
                # A quarantined process is never re-processed: no restart, no
                # traceback re-extraction, no re-alert. Skip it every cycle.
                if data.get("quarantined"):
                    continue

                # ── SUBSYSTEM_STATUS gate (Core Loop §C) ──
                # A child that self-declared state="disabled" (e.g. telegram
                # without a token) should NOT be reaped for heartbeat staleness.
                # Recovery is signalled by the child posting state="ok".
                if name in self.disabled_children:
                    continue

                p = data["process"]
                heartbeat = self.heartbeats[name]

                # Strike decay: if process alive 5+ min, reset strikes
                if p.is_alive() and data.get("started_at") and data["restarts"] > 0:
                    uptime = now - data["started_at"]
                    if uptime > 300:
                        logger.info(f"strike_decay | service={name} | strikes_reset | uptime={uptime:.0f}s")
                        data["restarts"] = 0

                # Check for crash (is_alive) or hang (heartbeat timeout)
                is_hung = (now - heartbeat.value) > HEARTBEAT_TIMEOUT
                if not p.is_alive() or is_hung:
                    # ── Non-blocking cooldown gate (Req 15.5) ──
                    # If this crash has already been processed (strike counted,
                    # alerted, patch attempted) we are simply waiting out the
                    # restart cooldown. Don't re-process; either restart now if
                    # the cooldown elapsed, or skip this process THIS cycle so
                    # the rest of the fleet is still monitored.
                    not_before = data.get("restart_not_before", 0)
                    if not_before:
                        if now < not_before:
                            # Still cooling down — never block the loop.
                            continue
                        logger.info(f"phoenix_cooldown_elapsed | service={name} | restarting")
                        self.start_process(name, data["target"], restarts=data["restarts"])
                        continue

                    if p.is_alive() and is_hung:
                        logger.warning(
                            f"process_hung: {name}. Heartbeat timeout ({now - heartbeat.value:.1f}s). Terminating..."
                        )
                        p.terminate()
                        p.join(timeout=2)
                        if p.is_alive():
                            p.kill()

                    if p.exitcode in (0, 99):
                        # exitcode 0 or 99 = intentional shutdown, not a crash
                        logger.info(f"intentional_shutdown_received: {name} (exit={p.exitcode})")
                        self.stop()
                        return

                    logger.warning(f"process_failure: {name}. Strike {data['restarts'] + 1} of 3.")

                    # Track restart and check for 3-strike limit (Quarantine)
                    if data["restarts"] >= 3:
                        logger.critical(f"HALTING_RECOVERY | service={name} | strikes_exceeded | placing in quarantine")
                        data["quarantined"] = True

                        # Exactly one quarantine alert per quarantine event
                        # (Req 15.4), guarded so later cycles never re-alert.
                        if not data.get("quarantine_alerted"):
                            self._safe_put(
                                self.tts_q,
                                {
                                    "type": "SPEAK",
                                    "content": f"{name} service has crashed consecutively and is now quarantined, Sir.",
                                },
                            )
                            self._safe_put(self.status_q, {"type": "PHASE", "content": "ALERT"})

                            # Direct Telegram fall-back alert dispatch
                            alert_text = (
                                f"<b>SRE QUARANTINE</b>\n\n"
                                f"Service <b>{name}</b> has crashed consecutively (3 strikes) and is now quarantined."
                            )
                            self._send_direct_telegram(alert_text)
                            data["quarantine_alerted"] = True
                        continue

                    # ── Auto-patcher gate (Req 15.6 / 17.4) ──
                    # Auto-patcher removed — restart-only recovery — we
                    # NEVER modify live source in response to a crash. Use a
                    # defensive getattr so a missing flag fails closed (no patch).
                    # Auto-patcher removed — restart-only recovery
                    logger.info("process_crash | service=%s | restart_only", name)

                    data["restarts"] += 1

                    # Non-blocking cooldown before restart (Req 15.5): record a
                    # per-process timestamp instead of sleeping. The process is
                    # restarted on a later cycle once the cooldown elapses, so
                    # other processes keep being monitored in the meantime.
                    data["restart_not_before"] = now + RESTART_COOLDOWN
                    logger.info(
                        f"phoenix_cooldown_started | service={name} | duration={RESTART_COOLDOWN}s | non_blocking=True"
                    )

            # Autonomic Vitals Check (The Doctor)
            try:
                with self._processes_lock:
                    process_snapshot = list(self.processes.values())
                active_pids = [d["process"].pid for d in process_snapshot if d["process"].is_alive()]
                self.doctor.update_pids(active_pids)
                self.doctor.perform_vitals_check()
            except Exception as e:
                logger.error("doctor_vital_check_failed", error=str(e))

    def _teardown_servers(self) -> None:
        """Overridable hook to tear down servers owned by a subclass.

        The base :class:`PhoenixSupervisor` owns no HTTP/WS servers, so this is
        a no-op. :class:`DaemonSupervisor` overrides it to stop the IPC bridge
        and the Control_Server. Keeping the hook on the base lets ``stop()``
        perform full teardown without a hard dependency on subclass-only
        attributes (Reqs 14.2, 14.8).
        """
        return None

    def stop(self):
        """Full, fault-isolated teardown of the supervisor (Reqs 14.2, 14.8).

        Each step is independently guarded so that one failure never aborts the
        rest of teardown, and no exception escapes ``stop()``. Order:
          1. mark not-running
          2. terminate + join all child processes
          3. tear down servers (no-op on base, IPC bridge + Control_Server on
             the daemon subclass)
          4. shut down the multiprocessing Manager so its process does not leak
        """
        logger.info("supervisor_teardown_started")

        # 1. Stop the monitor loop.
        try:
            self.running = False
        except Exception as e:
            logger.error("teardown_set_running_failed", error=str(e))

        # 2. Terminate + join all child processes. Snapshot under the lock
        #    (Req 14.6 / task 3.1) so a concurrent restart cannot mutate the
        #    set mid-iteration.
        try:
            with self._processes_lock:
                process_items = list(self.processes.items())
            for name, data in process_items:
                try:
                    p = data["process"]
                    if p.is_alive():
                        p.terminate()
                        p.join(timeout=5)
                    if p.is_alive():
                        p.kill()
                        p.join(timeout=5)
                    logger.info(f"child_terminated | name={name}")
                except Exception as e:
                    logger.error("child_terminate_failed", name=name, error=str(e))
        except Exception as e:
            logger.error("teardown_children_failed", error=str(e))

        # 3. Tear down subclass-owned servers (IPC bridge + Control_Server).
        try:
            self._teardown_servers()
        except Exception as e:
            logger.error("teardown_servers_failed", error=str(e))

        # 4. Shut down the multiprocessing Manager so its process is released
        #    (Req 14.2) — without this the manager process leaks.
        try:
            if getattr(self, "_manager", None):
                self._manager.shutdown()
                logger.info("manager_shutdown_complete")
        except Exception as e:
            logger.error("manager_shutdown_failed", error=str(e))

        logger.info("supervisor_teardown_complete")

    def restart_subsystem(self, name: str) -> bool:
        """Restart a single managed subsystem by role name.

        Serializes access to the process set (Req 14.6) and confirms the
        predecessor process is fully dead before spawning the successor so no
        orphaned predecessor for the same role remains running (Req 14.7).

        Returns True on success, False if the subsystem name is unknown.
        """
        # Terminate the predecessor under the lock, then release before calling
        # start_process (which re-acquires the lock for its own mutation). The
        # RLock makes nested acquisition safe regardless.
        with self._processes_lock:
            data = self.processes.get(name)
            if data is None:
                logger.warning(f"restart_subsystem_unknown | name={name}")
                return False

            target = data["target"]
            restarts = data["restarts"]
            p = data["process"]

            # Confirm the predecessor is dead before respawning.
            if p.is_alive():
                p.terminate()
                p.join(timeout=5)
                if p.is_alive():
                    p.kill()
                    p.join(timeout=5)

            if p.is_alive():
                # Could not confirm termination — refuse to spawn a successor so
                # we never orphan the predecessor.
                logger.error(f"restart_subsystem_failed | name={name} | predecessor_still_alive")
                return False

        logger.info(f"restart_subsystem | name={name} | predecessor_terminated")
        # start_process re-acquires the lock to record the successor entry.
        self.start_process(name, target, restarts=restarts)
        return True

    def reboot(self):
        """Full system restart by replacing the current process."""
        self.running = False
        logger.info("executing_os_reboot")

        # Kill all managed processes
        with self._processes_lock:
            process_items = list(self.processes.items())
        for name, data in process_items:
            p = data["process"]
            if p.is_alive():
                p.terminate()
                p.join(timeout=1)
                if p.is_alive():
                    p.kill()

        # Replace the current process with a fresh one
        # sys.executable is the python interpreter
        # sys.argv contains the script and arguments
        # Use subprocess.Popen for cross-platform compatibility
        # os.execv doesn't work properly on Windows
        try:
            subprocess.Popen([sys.executable] + sys.argv)
            sys.exit(0)
        except Exception as e:
            logger.error(f"reboot_failed | error={e}")
            # Fallback to os.execv for non-Windows platforms
            if platform.system() != "Windows":
                os.execv(sys.executable, [sys.executable] + sys.argv)
            else:
                logger.critical("reboot_fallback_unavailable")
                sys.exit(1)

    def get_status_report(self):
        """Returns a formatted string of current system health for the Brain."""
        vitals = self.doctor.obs.get_vitals()
        stats = []
        with self._processes_lock:
            process_items = list(self.processes.items())
        for name, data in process_items:
            p = data["process"]
            restarts = data["restarts"]
            pid = p.pid
            if pid in vitals["processes"]:
                v = vitals["processes"][pid]
                stats.append(f"{name}: CPU {v['cpu']:.1f}% | RAM {v['ram_mb']:.1f}MB | Restarts: {restarts}")
            else:
                stats.append(f"{name}: Offline/Initializing | Restarts: {restarts}")

        load_str = f"System Load: CPU {vitals['system_cpu']}% | RAM {vitals['system_ram_percent']}%"
        return "SYSTEM STATUS:\n" + "\n".join(stats) + "\n" + load_str

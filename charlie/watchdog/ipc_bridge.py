"""
charlie/watchdog/ipc_bridge.py

IPCBridge — bridges multiprocessing.Queue messages to WebSocket clients.
Runs in the daemon process. Reads from status_q, pushes to ControlServer WS.
Translates WS commands back to queue messages for Brain.
"""

import queue
import threading
import time

from charlie.utils.logger import get_logger
from charlie.watchdog.status_events import STATUS_EVENT_MAP, extract_ws_data

logger = get_logger("IPCBridge")


class IPCBridge:
    """
    Bridges multiprocessing.Queue messages to WebSocket clients.

    - Reads from status_q (produced by Brain, Audio, Vision)
    - Forwards matching messages to ControlServer WS clients
    - Can receive WS commands and translate to queue messages for Brain
    """

    def __init__(self, status_q=None, brain_task_q=None, control_server=None, log_q=None):
        self.status_q = status_q
        self.brain_task_q = brain_task_q
        self.control_server = control_server
        # Optional log queue. When provided, a dedicated thread drains it
        # and forwards each entry as a "log" WS event to the dashboard.
        # The thread is only started when log_q is not None.
        self.log_q = log_q
        self._running = False
        self._thread = None
        self._log_thread = None
        self._stats = {
            "messages_forwarded": 0,
            "messages_dropped": 0,
            "last_forward_time": 0,
        }
        # Latest voice booleans captured from VOICE_ACTIVITY events. Stale
        # entries are ignored (see get_voice_state) so the REST endpoint can
        # report real TTS/STT state instead of a hardcoded False.
        self._voice_state = {
            "is_speaking": False,
            "is_listening": False,
            "muted": False,
            "updated_at": 0.0,
        }
        self._voice_lock = threading.Lock()

    def get_voice_state(self, max_age: float = 1.5):
        """Return (is_speaking, is_listening) booleans, treating stale as False."""
        with self._voice_lock:
            if time.time() - self._voice_state["updated_at"] > max_age:
                return False, False
            return self._voice_state["is_speaking"], self._voice_state["is_listening"]

    def get_voice_state_full(self):
        """Return the full cached voice state dict (is_speaking, is_listening, muted).

        Unlike get_voice_state, this does NOT treat stale entries as False.
        Mute is a sticky user setting; it should remain True even when no
        recent VOICE_ACTIVITY event has arrived (e.g. on dashboard page load).
        Returns a shallow copy so callers cannot mutate the cache.
        """
        with self._voice_lock:
            return {
                "is_speaking": self._voice_state["is_speaking"],
                "is_listening": self._voice_state["is_listening"],
                "muted": self._voice_state["muted"],
            }

    def get_mute_state(self, max_age: float = 1.5) -> bool:
        """Return the most recent muted bool, or False if no recent event.

        Mute is sticky: a missing key on a recent VOICE_ACTIVITY does not
        flip the cached value, so a brief event with no `muted` field will
        not silently unmute. If the cache is older than `max_age`, we return
        the last cached value rather than False, so the dashboard still sees
        a "muted=True" set in a previous session even before a fresh event
        arrives. Callers that need a hard "fresh or unknown" signal should
        call get_voice_state_full and inspect `updated_at` themselves.
        """
        with self._voice_lock:
            return bool(self._voice_state.get("muted", False))

    def start(self):
        """Start the bridge in a background thread."""
        self._running = True
        self._thread = threading.Thread(target=self._bridge_loop, daemon=True, name="IPCBridge")
        self._thread.start()
        if self.log_q is not None:
            self._log_thread = threading.Thread(
                target=self._log_loop, daemon=True, name="IPCBridge-Log"
            )
            self._log_thread.start()
            logger.info("ipc_log_bridge_started")
        logger.info("ipc_bridge_started")

    def stop(self):
        """Stop the bridge."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        if self._log_thread:
            self._log_thread.join(timeout=5)
        logger.info("ipc_bridge_stopped")

    def _log_loop(self):
        """Drain log_q and broadcast each entry as a "log" WS event.

        Runs in its own thread so a burst of high-frequency log records
        (e.g. TTS/STT frames) cannot starve status_q forwarding. The
        timeout on ``get`` keeps the thread responsive to ``_running``
        flipping to False on shutdown.
        """
        while self._running:
            try:
                entry = self.log_q.get(timeout=0.1)
            except queue.Empty:
                continue
            except Exception as e:
                logger.error("log_loop_get_error | %s", e)
                time.sleep(0.5)
                continue
            try:
                self._forward_log(entry)
            except Exception as e:
                logger.error("log_loop_forward_error | %s", e)

    def _forward_log(self, entry: dict) -> None:
        """Send a single log entry to WS clients as ``{"type": "log", "data": entry}``.

        Bypasses ``STATUS_EVENT_MAP`` because log entries are pre-formatted
        by the logging handler — there is no inbound ``type`` to map.
        """
        if not self.control_server or not self.control_server.is_running:
            return
        try:
            self.control_server.broadcast_sync("log", entry)
        except Exception as e:
            logger.debug("log_forward_failed | %s", e)

    def _bridge_loop(self):
        """Main loop: drain status_q, forward to WS."""
        while self._running:
            try:
                msg = self.status_q.get(timeout=0.1)
                self._forward_to_ws(msg)
            except queue.Empty:
                continue
            except Exception as e:
                # Use repr() and type() because multiprocessing.queues.Empty
                # and broken-pipe OSError instances render as empty strings
                # in str(), which previously left us debugging with
                # `bridge_loop_error error=""` and no clue what failed.
                logger.error(
                    "bridge_loop_error | type=%s | repr=%r",
                    type(e).__name__,
                    e,
                )
                time.sleep(0.5)

    def _forward_to_ws(self, msg):
        """Forward a status_q message to WS clients.

        Uses the canonical STATUS_EVENT_MAP from status_events.py.
        Unmapped event types are dropped with a debug log (they are frequent
        and not actionable at warning level).
        """
        if not self.control_server or not self.control_server.is_running:
            return

        msg_type = msg.get("type", "")
        ws_event_type = STATUS_EVENT_MAP.get(msg_type)
        if ws_event_type is None:
            logger.debug("status_event_unmapped | type=%s", msg_type)
            return

        ws_data = extract_ws_data(msg)

        if msg_type == "VOICE_ACTIVITY":
            with self._voice_lock:
                self._voice_state["is_speaking"] = bool(ws_data.get("is_speaking", False))
                self._voice_state["is_listening"] = bool(ws_data.get("is_listening", False))
                # Mute is sticky: only flip to True/False when the source reports
                # an explicit boolean. Missing key leaves the previous value intact
                # so a transient VOICE_ACTIVITY (which may not include "muted")
                # does not silently clear a real mute.
                if "muted" in ws_data:
                    self._voice_state["muted"] = bool(ws_data.get("muted", False))
                self._voice_state["updated_at"] = time.time()

        try:
            self.control_server.broadcast_sync(ws_event_type, ws_data)
            self._stats["messages_forwarded"] += 1
            self._stats["last_forward_time"] = time.time()
        except Exception as e:
            self._stats["messages_dropped"] += 1
            logger.debug("forward_failed | type=%s | error=%s", msg_type, e)

    def send_to_brain(self, msg_type: str, content: dict, source: str = "ws_client"):
        """Send a message to brain_task_q from WS client."""
        if not self.brain_task_q:
            return False

        try:
            self.brain_task_q.put(
                {
                    "type": msg_type,
                    "content": content,
                    "source": source,
                }
            )
            return True
        except Exception as e:
            logger.error("send_to_brain_failed", error=str(e))
            return False

    def send_confirmation(self, result: str, source: str = "ws_client"):
        """Send a confirmation result to brain_task_q."""
        return self.send_to_brain("CONFIRMATION_RESULT", result, source)

    @property
    def stats(self) -> dict:
        return {**self._stats}

    @property
    def is_running(self) -> bool:
        return self._running

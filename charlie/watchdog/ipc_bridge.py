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

    def __init__(self, status_q=None, brain_task_q=None, control_server=None):
        self.status_q = status_q
        self.brain_task_q = brain_task_q
        self.control_server = control_server
        self._running = False
        self._thread = None
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
            "updated_at": 0.0,
        }
        self._voice_lock = threading.Lock()

    def get_voice_state(self, max_age: float = 1.5):
        """Return (is_speaking, is_listening) booleans, treating stale as False."""
        with self._voice_lock:
            if time.time() - self._voice_state["updated_at"] > max_age:
                return False, False
            return self._voice_state["is_speaking"], self._voice_state["is_listening"]

    def start(self):
        """Start the bridge in a background thread."""
        self._running = True
        self._thread = threading.Thread(
            target=self._bridge_loop, daemon=True, name="IPCBridge"
        )
        self._thread.start()
        logger.info("ipc_bridge_started")

    def stop(self):
        """Stop the bridge."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("ipc_bridge_stopped")

    def _bridge_loop(self):
        """Main loop: drain status_q, forward to WS."""
        while self._running:
            try:
                msg = self.status_q.get(timeout=0.1)
                self._forward_to_ws(msg)
            except queue.Empty:
                continue
            except Exception as e:
                logger.error("bridge_loop_error", error=str(e))
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
            self.brain_task_q.put({
                "type": msg_type,
                "content": content,
                "source": source,
            })
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

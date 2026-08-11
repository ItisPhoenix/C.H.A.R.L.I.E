"""Shell: consumes SURFACE_* events over the EventBus SUB channel, owns SurfaceWindow instances."""
import json
import logging
import threading
from pathlib import Path
from typing import Dict

import zmq
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication

from charlie.events import EventType
from charlie.hud import placement
from charlie.hud.window import SurfaceWindow
from charlie.ipc import DEFAULT_EVENT_PORT

logger = logging.getLogger("charlie.hud.shell")

_SPIKE_HTML = Path(__file__).parent / "_spike.html"
_POLL_TIMEOUT_MS = 500
_DRAGGABLE_MODES = frozenset({"widget", "notification", "floating"})


class Shell(QObject):
    """Owns active SurfaceWindows, driven by SURFACE_SPAWN/_UPDATE/_DISMISS events."""

    spawn_requested = Signal(str, dict)
    dismiss_requested = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._windows: Dict[str, SurfaceWindow] = {}
        self.spawn_requested.connect(self._on_spawn)
        self.dismiss_requested.connect(self._on_dismiss)

    def _screen_rect(self) -> placement.Rect:
        geo = QApplication.primaryScreen().availableGeometry()
        return (geo.x(), geo.y(), geo.width(), geo.height())

    def _on_spawn(self, surface_id: str, payload: dict) -> None:
        mode = payload.get("presentation", "widget")
        if mode == "background":
            return
        self._on_dismiss(surface_id)
        region = payload.get("region") or "top_right"
        rect = placement.region_to_rect(region, self._screen_rect(), mode)
        window = SurfaceWindow(_SPIKE_HTML, rect, draggable=mode in _DRAGGABLE_MODES)
        window.show()
        self._windows[surface_id] = window
        ttl = payload.get("ttl_seconds")
        if ttl:
            QTimer.singleShot(int(ttl * 1000), lambda: self.dismiss_requested.emit(surface_id))

    def _on_dismiss(self, surface_id: str) -> None:
        window = self._windows.pop(surface_id, None)
        if window is not None:
            window.close()


def sub_loop(shell: Shell, stop_event: threading.Event) -> None:
    """Runs on a background thread; marshals matched events onto the Qt thread via signals."""
    ctx = zmq.Context()
    sock = ctx.socket(zmq.SUB)
    sock.setsockopt(zmq.RCVTIMEO, _POLL_TIMEOUT_MS)
    sock.connect(f"tcp://127.0.0.1:{DEFAULT_EVENT_PORT}")
    sock.setsockopt(zmq.SUBSCRIBE, b"")
    try:
        while not stop_event.is_set():
            try:
                raw = sock.recv_string()
            except zmq.Again:
                continue
            except Exception:
                logger.warning("hud shell sub loop error", exc_info=True)
                continue
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            payload = event.get("payload", {})
            surface_id = payload.get("surface_id")
            if not surface_id:
                continue
            etype = event.get("type")
            if etype in (EventType.SURFACE_SPAWN, EventType.SURFACE_UPDATE):
                shell.spawn_requested.emit(surface_id, payload)
            elif etype == EventType.SURFACE_DISMISS:
                shell.dismiss_requested.emit(surface_id)
    finally:
        sock.close(linger=0)
        ctx.term()

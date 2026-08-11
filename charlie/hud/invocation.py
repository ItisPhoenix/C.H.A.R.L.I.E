"""Manual surface summon: hotkey trigger, PUSHes a command to the voice process."""
import json
import logging

import zmq
from pynput import keyboard

from charlie.ipc import DEFAULT_COMMAND_PORT

logger = logging.getLogger("charlie.hud.invocation")


def _send_invoke_command() -> None:
    ctx = zmq.Context()
    sock = ctx.socket(zmq.PUSH)
    sock.connect(f"tcp://127.0.0.1:{DEFAULT_COMMAND_PORT}")
    try:
        sock.send_string(json.dumps({"type": "hud_invoke", "payload": {}}))
    except Exception:
        logger.warning("failed to send hud_invoke command", exc_info=True)
    finally:
        sock.close(linger=0)
        ctx.term()


def start_hotkey_listener(hotkey: str) -> "keyboard.GlobalHotKeys":
    """Register the HUD summon hotkey, mirroring core.py's panic-hotkey pattern."""
    hotkey_str = "+".join(
        f"<{p}>" if len(p) > 1 else p for p in hotkey.lower().split("+")
    )
    listener = keyboard.GlobalHotKeys({hotkey_str: _send_invoke_command})
    listener.start()
    logger.info("HUD invocation hotkey armed: %s", hotkey)
    return listener

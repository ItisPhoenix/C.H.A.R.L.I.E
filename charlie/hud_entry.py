"""Entry point for Charlie HUD surface shell subprocess."""

import sys
import threading
from pathlib import Path

# Ensure charlie package is importable (must precede charlie imports)
sys.path.insert(0, str(Path(__file__).parent.parent))

from PySide6.QtWidgets import QApplication

from charlie.config import config
from charlie.hud.invocation import start_hotkey_listener
from charlie.hud.shell import Shell, sub_loop


def main() -> None:
    app = QApplication([])
    host = "127.0.0.1" if config.charlie_host == "0.0.0.0" else config.charlie_host
    shell = Shell(base_url=f"http://{host}:{config.charlie_port}")
    stop_event = threading.Event()
    thread = threading.Thread(target=sub_loop, args=(shell, stop_event), daemon=True)
    thread.start()
    hotkey_listener = start_hotkey_listener(config.hud_invoke_hotkey)
    try:
        app.exec()
    finally:
        stop_event.set()
        hotkey_listener.stop()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass

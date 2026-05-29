"""
charlie-daemon.py

Headless daemon entry point for CHARLIE.
Starts all subsystems except HUD. HUD connects as a client via WebSocket.
"""

import os
import sys
import ctypes
import warnings
import multiprocessing

warnings.filterwarnings("ignore", category=FutureWarning, module="torch.cuda")
warnings.filterwarnings("ignore", message=".*pynvml package is deprecated.*")

from charlie.utils.logger import get_logger

logger = get_logger("Daemon")


def _fix_dpi():
    """Ensures high-DPI scaling is handled correctly."""
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass


def main():
    """Daemon entry point. Runs Charlie headless (no HUD)."""
    # ── VENV ENFORCEMENT ──
    root_dir = os.path.dirname(os.path.abspath(__file__))
    venv_python = os.path.abspath(os.path.join(root_dir, ".venv", "Scripts", "python.exe"))
    current_python = os.path.abspath(sys.executable)

    if os.path.exists(venv_python) and current_python.lower() != venv_python.lower():
        print(f"INFO: venv_respawn | switching_to={venv_python}")
        import subprocess
        script_path = os.path.abspath(__file__)
        result = subprocess.run([venv_python, script_path] + sys.argv[1:])
        sys.exit(result.returncode)

    _fix_dpi()

    os.environ["QT_FONT_DPI"] = "96"
    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"

    # ── LOAD .ENV ──
    from dotenv import load_dotenv
    load_dotenv()

    # ── CONFIG INITIALIZATION ──
    from charlie.config import ensure_initialized
    ensure_initialized()

    # ── STARTUP VALIDATION (non-blocking) ──
    try:
        from charlie.utils.startup_validator import StartupValidator
        validator = StartupValidator()
        # Run validation in background to avoid blocking daemon start
        import threading
        threading.Thread(target=validator.validate_all, daemon=True).start()
    except Exception as e:
        logger.warning(f"startup_validation_skipped | {e}")

    # ── DAEMON SUPERVISOR ──
    from charlie.watchdog.daemon_supervisor import DaemonSupervisor

    interrupt_event = multiprocessing.Event()
    reboot_event = multiprocessing.Event()
    supervisor = DaemonSupervisor(interrupt_event, reboot_event)

    # ── QUEUE VALIDATION ──
    try:
        queues = {
            "brain_task_q": supervisor.brain_task_q,
            "status_q": supervisor.status_q,
            "tts_q": supervisor.tts_q,
            "telegram_q": supervisor.telegram_q,
        }
        validator.check_queues(queues)
    except Exception as e:
        logger.warning(f"queue_validation_skipped | {e}")

    # ── CHECK AUTO-START PREFERENCE ──
    from charlie.utils.autostart import sync_shortcut
    try:
        sync_shortcut()
    except Exception as e:
        logger.debug(f"autostart_sync_skipped | {e}")

    # ── START WITH MASCOT ──
    logger.info("charlie_daemon_starting | mode=daemon+mascot")
    print("\n" + "=" * 50)
    print("  C H A R L I E   D A E M O N")
    print("  Daemon Mode")
    print("=" * 50 + "\n")

    try:
        supervisor.start()
    except Exception as e:
        logger.error("daemon_failure", error=str(e), exc_info=True)
    finally:
        supervisor.stop()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()

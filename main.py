import os
import sys

# Triggers Windows DLL search path hardening
import ctypes
import warnings
# Silence pynvml deprecation warning from torch/cuda
warnings.filterwarnings("ignore", category=FutureWarning, module="torch.cuda")
warnings.filterwarnings("ignore", message=".*pynvml package is deprecated.*")

import multiprocessing
from charlie.utils.logger import get_logger

logger = get_logger("Main")


def _fix_dpi():
    """Ensures high-DPI scaling is handled correctly before UI starts."""
    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        logger.info("dpi_awareness_set | mode=v2")
    except Exception as e:
        logger.error("system_font_fallback | error={}", e)
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
            logger.info("dpi_awareness_set | mode=v1")
        except Exception as e:
            logger.debug("dpi_awareness_failed | error={}", e)


def main():
    """Main Entry Point for C.H.A.R.L.I.E. Engine."""
    # ── VENV ENFORCEMENT (Hardened) ──
    # Ensure we are running from the local .venv to prevent ModuleNotFoundErrors in sub-processes
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

    # ── LOAD .ENV BEFORE VALIDATION ──
    from dotenv import load_dotenv
    load_dotenv()

    # We use PhoenixSupervisor to manage all engine sub-processes
    from charlie.watchdog import PhoenixSupervisor

    interrupt_event = multiprocessing.Event()
    reboot_event = multiprocessing.Event()
    supervisor = PhoenixSupervisor(interrupt_event, reboot_event)

    # ── STARTUP & QUEUE VALIDATION (non-blocking) ──
    # Run in background thread to prevent VCAMDS camera loops and IPC Manager queue checks from blocking main thread
    import threading
    from charlie.utils.startup_validator import StartupValidator
    validator = StartupValidator()

    def _run_validation():
        try:
            queues = {
                "brain_task_q": supervisor.brain_task_q,
                "status_q": supervisor.status_q,
                "tts_q": supervisor.tts_q,
                "telegram_q": supervisor.telegram_q,
            }
            if not validator.validate_all(queues):
                logger.warning("startup_validation_partial | check logs for details")
        except Exception as e:
            logger.warning(f"startup_validation_failed | error={e}")

    threading.Thread(target=_run_validation, daemon=True).start()

    try:
        supervisor.start()
    except Exception as e:
        logger.error("engine_failure", error=str(e), exc_info=True)
    finally:
        supervisor.stop()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()

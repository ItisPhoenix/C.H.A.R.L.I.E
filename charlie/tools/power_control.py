import ctypes
import subprocess

from charlie.security.tiers import RiskTier, risk_tier
from charlie.utils.logger import get_logger

logger = get_logger(__name__)

class PowerController:
    """Handles remote PC power states and security lockdown."""

    @risk_tier(RiskTier.TIER_2)
    def lock_pc(self) -> str:
        """Locks the Windows session immediately."""
        try:
            ctypes.windll.user32.LockWorkStation()
            logger.info("system_locked_successfully")
            return "PC Locked, Sir. Session secured."
        except Exception as e:
            logger.error(f"lock_pc_failed | {e}")
            return f"Failed to lock PC: {e}"

    @risk_tier(RiskTier.TIER_2)
    def sleep_pc(self) -> str:
        """Puts the PC into sleep mode (suspend to RAM)."""
        try:
            # S3 sleep: hibernate=0, force=0, disableWakeEvent=0
            if ctypes.windll.powrprof.SetSuspendState(0, 0, 0):
                logger.info("system_sleep_initiated")
                return "Initiating sleep mode, Sir. Connection will be lost."
            else:
                logger.error("system_sleep_failed_via_api")
                return "Failed to put PC to sleep via API."
        except Exception as e:
            logger.error(f"sleep_pc_failed | {e}")
            return f"Failed to put PC to sleep: {e}"

    @risk_tier(RiskTier.TIER_3)
    def shutdown_pc(self) -> str:
        """Performs a full system shutdown."""
        try:
            # /s shutdown, /t 0 immediate
            subprocess.run(["shutdown", "/s", "/t", "0"], check=True)
            logger.info("system_shutdown_initiated")
            return "System shutting down, Sir. Goodbye."
        except Exception as e:
            logger.error(f"shutdown_pc_failed | {e}")
            return f"Failed to shutdown PC: {e}"

    @risk_tier(RiskTier.TIER_2)
    def restart_pc(self) -> str:
        """Performs a system restart."""
        try:
            subprocess.run(["shutdown", "/r", "/t", "0"], check=True)
            logger.info("system_restart_initiated")
            return "System restarting, Sir. Reconnecting shortly."
        except Exception as e:
            logger.error(f"restart_pc_failed | {e}")
            return f"Failed to restart PC: {e}"

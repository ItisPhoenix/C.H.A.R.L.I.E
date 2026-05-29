import comtypes
import win32api
import win32gui
from pycaw.pycaw import AudioUtilities, ISimpleAudioVolume

from charlie.utils.logger import get_logger

logger = get_logger(__name__)

WM_APPCOMMAND = 0x319
APPCOMMAND_VOLUME_DOWN = 9
APPCOMMAND_VOLUME_UP = 10
APPCOMMAND_VOLUME_MUTE = 8

def _send_command(cmd):
    hwnd = win32gui.GetForegroundWindow()
    win32api.SendMessage(hwnd, WM_APPCOMMAND, 0, cmd * 0x10000)

class VolumeController:
    """
    Handles background media ducking using Windows Core Audio API (pycaw).
    Ducks all other applications while keeping the current python process (Charlie) at normal volume.
    """

    def __init__(self, steps=10):
        self.steps = steps  # Kept for API compatibility
        self.is_ducked = False
        self.session_volumes = {}  # PID -> original volume float

    def duck(self):
        if self.is_ducked:
            return

        logger.info("audio_ducking_initiated")
        self.session_volumes.clear()

        try:
            comtypes.CoInitialize()
            sessions = AudioUtilities.GetAllSessions()

            for session in sessions:
                if session.Process:
                    proc_name = session.Process.name().lower()
                    if proc_name not in ["python.exe", "pythonw.exe"]:
                        try:
                            volume = session._ctl.QueryInterface(ISimpleAudioVolume)
                            current_vol = volume.GetMasterVolume()
                            self.session_volumes[session.Process.pid] = current_vol

                            # Calculate target volume (reduce to 20% or half of current, whichever is lower)
                            target_vol = min(0.2, current_vol / 2.0)
                            volume.SetMasterVolume(target_vol, None)
                            logger.debug(f"ducked_pid_{session.Process.pid}_{proc_name}_to_{target_vol:.2f}")
                        except Exception as e:
                            logger.debug(f"failed_to_duck_session_{session.Process.pid} | {e}")

            self.is_ducked = True
        except Exception as e:
            logger.error(f"volume_duck_failed | {e}")

    def unduck(self):
        if not self.is_ducked:
            return

        try:
            self._restore_volumes(self.session_volumes)
            self.is_ducked = False
            self.session_volumes.clear()
            logger.info("audio_ducking_restored")
        except Exception as e:
            logger.error(f"volume_restore_failed | {e}")
            self.is_ducked = False
            self.session_volumes.clear()

    def _restore_volumes(self, saved_volumes: dict):
        """Restore saved volumes for previously ducked sessions."""
        comtypes.CoInitialize()
        sessions = AudioUtilities.GetAllSessions()
        for session in sessions:
            if session.Process and session.Process.pid in saved_volumes:
                try:
                    volume = session._ctl.QueryInterface(ISimpleAudioVolume)
                    volume.SetMasterVolume(saved_volumes[session.Process.pid], None)
                except Exception:
                    pass

    def mute(self):
        _send_command(APPCOMMAND_VOLUME_MUTE)

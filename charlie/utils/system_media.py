from charlie.utils.logger import get_logger

logger = get_logger("SystemMedia")

# Windows AppCommands for Media (Explicit definition as fallback)
WM_APPCOMMAND = 0x0319
APPCOMMAND_MEDIA_PLAY_PAUSE = 14
APPCOMMAND_MEDIA_STOP = 13
APPCOMMAND_MEDIA_NEXTTRACK = 11
APPCOMMAND_MEDIA_PREVTRACK = 12
APPCOMMAND_MEDIA_PLAY = 46
APPCOMMAND_MEDIA_PAUSE = 47


def send_media_command(command: str):
    """Universal Hybrid Media Gate: WinRT Broadcast + VK Injection (Asynchronous)."""
    import threading

    def _execute():
        import os
        import subprocess

        import win32con
        import win32gui

        cmd_lower = command.lower()

        # LAYER 1: WinRT Broadcast (Targeting ALL sessions via PowerShell)
        if cmd_lower in ["play", "pause", "toggle", "stop", "next", "prev", "previous"]:
            try:
                script_path = os.path.join(
                    os.path.dirname(__file__), "media_control.ps1"
                )
                # Map 'prev' to 'previous' for the PS script
                ps_cmd = "previous" if cmd_lower == "prev" else cmd_lower
                subprocess.run(
                    [
                        "powershell",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        script_path,
                        ps_cmd,
                    ],
                    capture_output=True,
                    timeout=5,
                )
            except Exception as e:
                logger.debug(f"media_ps_script_failed | {e}")

        # LAYER 2: Win32 APPCOMMAND (Global Broadcast)
        try:
            target_hwnd = win32con.HWND_BROADCAST
            if cmd_lower == "play":
                win32gui.PostMessage(
                    target_hwnd, WM_APPCOMMAND, 0, APPCOMMAND_MEDIA_PLAY << 16
                )
            elif cmd_lower == "pause":
                win32gui.PostMessage(
                    target_hwnd, WM_APPCOMMAND, 0, APPCOMMAND_MEDIA_PAUSE << 16
                )
            elif cmd_lower == "stop":
                win32gui.PostMessage(
                    target_hwnd, WM_APPCOMMAND, 0, APPCOMMAND_MEDIA_STOP << 16
                )
            elif cmd_lower == "toggle":
                win32gui.PostMessage(
                    target_hwnd, WM_APPCOMMAND, 0, APPCOMMAND_MEDIA_PLAY_PAUSE << 16
                )
        except Exception as e:
            logger.debug(f"media_win32_broadcast_failed | {e}")

        # LAYER 3: Hardware Virtual Key (Global Fallback)
        if cmd_lower == "toggle":
            try:
                import pyautogui

                pyautogui.press("mediaplaypause")
            except Exception:
                import win32api

                win32api.keybd_event(0xB3, 0, 0, 0)  # VK_MEDIA_PLAY_PAUSE
                win32api.keybd_event(0xB3, 0, win32con.KEYEVENTF_KEYUP, 0)
        elif cmd_lower in ["next", "prev"]:
            try:
                import pyautogui

                if cmd_lower == "next":
                    pyautogui.press("medianexttrack")
                else:
                    pyautogui.press("mediprevtrack")
            except Exception:
                import win32api

                vk = 0xB0 if cmd_lower == "next" else 0xB1
                win32api.keybd_event(vk, 0, 0, 0)
                win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)

        logger.info(f"media_gate_broadcast | cmd={cmd_lower}")

    threading.Thread(target=_execute, daemon=True).start()
    return True

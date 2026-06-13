"""Media tools — volume control, media playback, Spotify integration."""

from charlie.tools.tool_decorator import tool, RiskTier


@tool(
    name="set_volume",
    description="Set system volume (0-100)",
    category="media",
)
def set_volume(level: int) -> str:
    """Set system volume to a specific level."""
    from charlie.security.safety_guard import clamp_volume

    level = clamp_volume(level)

    try:
        from ctypes import cast, POINTER
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume = cast(interface, POINTER(IAudioEndpointVolume))
        volume.SetMasterVolumeLevelScalar(level / 100.0, None)
        return f"Volume set to {level}%"
    except ImportError:
        return "pycaw not installed"
    except Exception as e:
        return f"Volume control failed: {e}"


@tool(
    name="get_volume",
    description="Get current system volume (0-100)",
    category="media",
)
def get_volume() -> str:
    """Get current system volume."""
    try:
        from ctypes import cast, POINTER
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume = cast(interface, POINTER(IAudioEndpointVolume))
        level = int(volume.GetMasterVolumeLevelScalar() * 100)
        return f"Volume: {level}%"
    except ImportError:
        return "pycaw not installed"
    except Exception as e:
        return f"Volume check failed: {e}"


@tool(
    name="control_media",
    description="Control media playback (play, pause, next, previous)",
    category="media",
)
def control_media(action: str) -> str:
    """Control media playback."""
    try:
        import pyautogui

        actions = {
            "play": "playpause",
            "pause": "playpause",
            "next": "nexttrack",
            "previous": "prevtrack",
            "stop": "stop",
        }
        key = actions.get(action.lower())
        if not key:
            return f"Unknown action: {action}. Use: play, pause, next, previous, stop"
        pyautogui.press(key)
        return f"Media: {action}"
    except ImportError:
        return "pyautogui not installed"
    except Exception as e:
        return f"Media control failed: {e}"


@tool(
    name="screenshot_save",
    description="Take a screenshot and save to a file",
    category="media",
    risk_tier=RiskTier.TIER_1,
)
def screenshot_save(path: str = "screenshot.png") -> str:
    """Take a screenshot and save it."""
    try:
        import mss

        with mss.mss() as sct:
            sct.shot(output=path)
        return f"Screenshot saved to: {path}"
    except ImportError:
        return "mss not installed"
    except Exception as e:
        return f"Screenshot failed: {e}"


@tool(
    name="capture_webcam",
    description="Capture an image from the webcam",
    category="media",
    risk_tier=RiskTier.TIER_1,
)
def capture_webcam(path: str = "webcam.jpg") -> str:
    """Capture a webcam image."""
    try:
        import cv2

        cap = cv2.VideoCapture(0)
        ret, frame = cap.read()
        cap.release()
        if ret:
            cv2.imwrite(path, frame)
            return f"Webcam capture saved to: {path}"
        return "Failed to capture from webcam"
    except ImportError:
        return "opencv-python not installed"
    except Exception as e:
        return f"Webcam capture failed: {e}"

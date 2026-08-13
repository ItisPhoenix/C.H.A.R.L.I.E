"""Windows Global System Media Transport Controls adapter."""

import base64
import logging
from typing import Any

logger = logging.getLogger("charlie.media_adapter")

try:
    from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager

    _manager_type: Any = GlobalSystemMediaTransportControlsSessionManager
except ImportError:
    _manager_type = None

try:
    from winrt.windows.storage.streams import DataReader
except ImportError:
    DataReader = None

try:
    from pycaw.pycaw import AudioUtilities

    _audio_endpoint = AudioUtilities.GetSpeakers().EndpointVolume
except Exception:
    _audio_endpoint = None


def _seconds(value: Any) -> float:
    if value is None:
        return 0.0
    total_seconds = getattr(value, "total_seconds", None)
    if callable(total_seconds):
        return float(total_seconds())
    duration = getattr(value, "duration", None)
    if duration is not None:
        return float(duration) / 10_000_000
    return 0.0


async def _thumbnail_data_uri(reference: Any) -> str | None:
    if reference is None or DataReader is None:
        return None
    try:
        stream = await reference.open_read_async()
        reader = DataReader(stream)
        await reader.load_async(stream.size)
        buffer = bytearray(stream.size)
        reader.read_bytes(buffer)
        return "data:image/jpeg;base64," + base64.b64encode(buffer).decode("ascii")
    except Exception:
        logger.info("Media thumbnail unavailable", exc_info=True)
        return None


def _volume_snapshot() -> dict:
    if _audio_endpoint is None:
        return {"volume_percent": None, "muted": None}
    try:
        return {
            "volume_percent": round(float(_audio_endpoint.GetMasterVolumeLevelScalar()) * 100),
            "muted": bool(_audio_endpoint.GetMute()),
        }
    except Exception:
        logger.info("Windows volume endpoint unavailable", exc_info=True)
        return {"volume_percent": None, "muted": None}


def _volume_control(action: str) -> dict:
    if _audio_endpoint is None:
        return {"ok": False, "available": False, "reason": "Windows volume controls unavailable"}
    try:
        if action == "mute":
            _audio_endpoint.SetMute(not bool(_audio_endpoint.GetMute()), None)
        elif action in ("volume_up", "volume_down"):
            current = float(_audio_endpoint.GetMasterVolumeLevelScalar())
            delta = 0.05 if action == "volume_up" else -0.05
            _audio_endpoint.SetMasterVolumeLevelScalar(max(0.0, min(1.0, current + delta)), None)
        else:
            return {"ok": False, "available": True, "reason": "Unsupported volume action"}
        return {"ok": True, "available": True, **_volume_snapshot()}
    except Exception:
        logger.warning("Windows volume action failed", exc_info=True)
        return {"ok": False, "available": True, "reason": "Volume action failed"}
class WindowsMediaAdapter:
    async def snapshot(self) -> dict:
        unavailable = {
            "available": False,
            "title": "",
            "artist": "",
            "album": "",
            "app": "",
            "status": "unavailable",
            "position_seconds": 0.0,
            "duration_seconds": 0.0,
            "art_uri": None,
            "volume_percent": None,
            "muted": None,
        }
        if _manager_type is None:
            return unavailable
        try:
            manager = await _manager_type.request_async()
            session = manager.get_current_session()
            if session is None:
                return unavailable
            properties = await session.try_get_media_properties_async()
            playback = session.get_playback_info()
            timeline = session.get_timeline_properties()
            return {
                "available": True,
                "title": properties.title or "",
                "artist": properties.artist or "",
                "album": properties.album_title or "",
                "app": session.source_app_user_model_id or "",
                "status": str(playback.playback_status).split(".")[-1].lower(),
                "position_seconds": _seconds(timeline.position),
                "duration_seconds": _seconds(timeline.end_time),
                "art_uri": await _thumbnail_data_uri(properties.thumbnail),
                **_volume_snapshot(),
            }
        except Exception:
            logger.info("Windows media session unavailable", exc_info=True)
            return unavailable

    async def control(self, action: str) -> dict:
        if action in {"volume_up", "volume_down", "mute"}:
            return _volume_control(action)
        if _manager_type is None:
            return {"ok": False, "available": False, "reason": "Windows media controls unavailable"}
        manager = await _manager_type.request_async()
        session = manager.get_current_session()
        if session is None:
            return {"ok": False, "available": False, "reason": "No controllable media session"}
        actions = {
            "play_pause": "try_toggle_play_pause_async",
            "next_track": "try_skip_next_async",
            "prev_track": "try_skip_previous_async",
            "stop": "try_stop_async",
        }
        method_name = actions.get(action)
        if method_name is None:
            return {"ok": False, "available": True, "reason": "Unsupported media action"}
        try:
            result = await getattr(session, method_name)()
            return {"ok": bool(result), "available": True, "action": action}
        except Exception:
            logger.warning("Windows media action failed", exc_info=True)
            return {"ok": False, "available": True, "reason": "Media action failed"}

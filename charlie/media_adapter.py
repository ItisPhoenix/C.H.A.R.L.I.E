"""Windows Global System Media Transport Controls adapter."""

import asyncio
import base64
import logging
import threading
from typing import Any

logger = logging.getLogger("charlie.media_adapter")

_MEDIA_ACTIONS = frozenset(
    {
        "volume_up",
        "volume_down",
        "set_volume",
        "mute",
        "unmute",
        "play_pause",
        "next_track",
        "prev_track",
        "stop",
    }
)
_PLAYBACK_ACTIONS = {
    "play_pause": "try_toggle_play_pause_async",
    "next_track": "try_skip_next_async",
    "prev_track": "try_skip_previous_async",
    "stop": "try_stop_async",
}

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
except Exception:
    AudioUtilities = None

_audio_endpoint: Any = None


def _get_audio_endpoint() -> Any:
    """Acquire the current speaker endpoint lazily and refresh stale devices."""
    global _audio_endpoint
    if _audio_endpoint is None and AudioUtilities is not None:
        try:
            _audio_endpoint = AudioUtilities.GetSpeakers().EndpointVolume
        except Exception:
            logger.info("Windows volume endpoint acquisition unavailable", exc_info=True)
            _audio_endpoint = None
    return _audio_endpoint


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
    if not _on_media_executor_thread():
        from charlie.media_runtime import get_media_executor

        return get_media_executor().submit(_volume_snapshot).result()
    endpoint = _get_audio_endpoint()
    if endpoint is None:
        return {"volume_percent": None, "muted": None}
    try:
        return {
            "volume_percent": round(float(endpoint.GetMasterVolumeLevelScalar()) * 100),
            "muted": bool(endpoint.GetMute()),
        }
    except Exception:
        logger.info("Windows volume endpoint unavailable", exc_info=True)
        _reset_audio_endpoint()
        endpoint = _get_audio_endpoint()
        if endpoint is None:
            return {"volume_percent": None, "muted": None}
        try:
            return {
                "volume_percent": round(float(endpoint.GetMasterVolumeLevelScalar()) * 100),
                "muted": bool(endpoint.GetMute()),
            }
        except Exception:
            logger.info("Windows volume endpoint reacquisition failed", exc_info=True)
            _reset_audio_endpoint()
            return {"volume_percent": None, "muted": None}


def _reset_audio_endpoint() -> None:
    global _audio_endpoint
    _audio_endpoint = None


def _on_media_executor_thread() -> bool:
    from charlie.media_runtime import media_executor_thread_id

    return media_executor_thread_id() == threading.get_ident()


def _set_volume_percent(percent: float) -> dict:
    if not _on_media_executor_thread():
        from charlie.media_runtime import get_media_executor

        return get_media_executor().submit(_set_volume_percent, percent).result()
    endpoint = _get_audio_endpoint()
    if endpoint is None:
        return {"ok": False, "available": False, "reason": "Windows volume controls unavailable"}
    try:
        scalar = max(0.0, min(1.0, float(percent) / 100.0))
        endpoint.SetMasterVolumeLevelScalar(scalar, None)
        readback = _volume_snapshot()
        return {
            "ok": True,
            "available": True,
            "verified": readback.get("volume_percent") is not None,
            **readback,
        }
    except Exception:
        logger.warning("Windows set volume percent failed", exc_info=True)
        _reset_audio_endpoint()
        _get_audio_endpoint()
        return {"ok": False, "available": True, "reason": "Set volume failed"}


def _volume_control(action: str) -> dict:
    if not _on_media_executor_thread():
        from charlie.media_runtime import get_media_executor

        return get_media_executor().submit(_volume_control, action).result()
    endpoint = _get_audio_endpoint()
    if endpoint is None:
        return {"ok": False, "available": False, "reason": "Windows volume controls unavailable"}
    try:
        if action == "mute":
            endpoint.SetMute(True, None)
        elif action == "unmute":
            endpoint.SetMute(False, None)
        elif action in ("volume_up", "volume_down"):
            current = float(endpoint.GetMasterVolumeLevelScalar())
            delta = 0.05 if action == "volume_up" else -0.05
            endpoint.SetMasterVolumeLevelScalar(max(0.0, min(1.0, current + delta)), None)
        else:
            return {"ok": False, "available": True, "reason": "Unsupported volume action"}
        readback = _volume_snapshot()
        return {
            "ok": True,
            "available": True,
            "verified": readback.get("volume_percent") is not None,
            **readback,
        }
    except Exception:
        logger.warning("Windows volume action failed", exc_info=True)
        _reset_audio_endpoint()
        _get_audio_endpoint()
        return {"ok": False, "available": True, "reason": "Volume action failed"}
class WindowsMediaAdapter:
    async def snapshot(self) -> dict:
        if not _on_media_executor_thread():
            loop = asyncio.get_running_loop()
            from charlie.media_runtime import get_media_executor

            return await loop.run_in_executor(
                get_media_executor(),
                lambda: asyncio.run(self.snapshot()),
            )
        unavailable = {
            "available": False,
            "adapter_available": False,
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
        except Exception:
            logger.info("Windows media adapter unavailable", exc_info=True)
            return unavailable
        try:
            session = manager.get_current_session()
            if session is None:
                return {**unavailable, "adapter_available": True, "status": "no_session"}
            properties = await session.try_get_media_properties_async()
            playback = session.get_playback_info()
            timeline = session.get_timeline_properties()
            return {
                "available": True,
                "adapter_available": True,
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
            logger.info("Windows media session read failed", exc_info=True)
            return {
                **unavailable,
                "adapter_available": True,
                "status": "failed",
                "reason": "Media session read failed",
            }

    async def control(self, action: str, percent: float | None = None) -> dict:
        if not _on_media_executor_thread():
            loop = asyncio.get_running_loop()
            from charlie.media_runtime import get_media_executor

            return await loop.run_in_executor(
                get_media_executor(),
                lambda: asyncio.run(self.control(action, percent=percent)),
            )
        if action not in _MEDIA_ACTIONS:
            return {"ok": False, "available": True, "failure_kind": "unsupported", "reason": "Unsupported media action"}
        if action == "set_volume":
            if (
                percent is None
                or isinstance(percent, bool)
                or not isinstance(percent, (int, float))
                or not 0 <= percent <= 100
            ):
                return {"ok": False, "available": True, "reason": "Volume percent must be between 0 and 100"}
            return _set_volume_percent(float(percent))
        if action in {"volume_up", "volume_down", "mute", "unmute"}:
            return _volume_control(action)
        if _manager_type is None:
            return {"ok": False, "available": False, "reason": "Windows media controls unavailable"}
        try:
            manager = await _manager_type.request_async()
            session = manager.get_current_session()
            if session is None:
                return {"ok": False, "available": False, "reason": "No controllable media session"}
            method_name = _PLAYBACK_ACTIONS[action]
            result = await getattr(session, method_name)()
            if not result:
                return {
                    "ok": False,
                    "available": True,
                    "action": action,
                    "verified": False,
                    "reason": "Media session rejected the action",
                }
            return {
                "ok": True,
                "available": True,
                "action": action,
                "verified": False,
                "reason": "Playback command accepted; playback state readback is unavailable",
            }
        except Exception:
            logger.warning("Windows media action failed", exc_info=True)
            return {"ok": False, "available": True, "reason": "Media action failed"}

from enum import IntEnum
from types import SimpleNamespace

import pytest

from charlie.media_adapter import WindowsMediaAdapter, _thumbnail_data_uri


@pytest.mark.asyncio
async def test_media_adapter_reports_unavailable_without_a_session(monkeypatch):
    class Manager:
        @staticmethod
        async def request_async():
            return Manager()

        def get_current_session(self):
            return None

    monkeypatch.setattr("charlie.media_adapter._manager_type", Manager)
    snapshot = await WindowsMediaAdapter().snapshot()

    assert snapshot["available"] is False
    assert snapshot["adapter_available"] is True
    assert snapshot["status"] == "no_session"
    assert snapshot["title"] == ""


@pytest.mark.asyncio
async def test_media_adapter_uses_winrt_playback_enum_name(monkeypatch):
    class PlaybackStatus(IntEnum):
        PLAYING = 4

    class Session:
        source_app_user_model_id = "Spotify.exe"

        async def try_get_media_properties_async(self):
            return SimpleNamespace(title="Track", artist="Artist", album_title="Album", thumbnail=None)

        def get_playback_info(self):
            return SimpleNamespace(playback_status=PlaybackStatus.PLAYING)

        def get_timeline_properties(self):
            return SimpleNamespace(position=None, end_time=None)

    class Manager:
        @staticmethod
        async def request_async():
            return Manager()

        def get_current_session(self):
            return Session()

    monkeypatch.setattr("charlie.media_adapter._manager_type", Manager)
    monkeypatch.setattr("charlie.media_adapter._on_media_executor_thread", lambda: True)
    monkeypatch.setattr("charlie.media_adapter._volume_snapshot", lambda: {"volume_percent": None, "muted": None})

    snapshot = await WindowsMediaAdapter().snapshot()

    assert snapshot["status"] == "playing"
    assert snapshot["title"] == "Track"


@pytest.mark.asyncio
async def test_play_pause_is_verified_from_session_status_readback(monkeypatch):
    class PlaybackStatus(IntEnum):
        PLAYING = 4
        PAUSED = 5

    class Session:
        def __init__(self):
            self.status = PlaybackStatus.PLAYING

        def get_playback_info(self):
            return SimpleNamespace(playback_status=self.status)

        async def try_toggle_play_pause_async(self):
            self.status = PlaybackStatus.PAUSED
            return True

    session = Session()

    class Manager:
        @staticmethod
        async def request_async():
            return Manager()

        def get_current_session(self):
            return session

    monkeypatch.setattr("charlie.media_adapter._manager_type", Manager)
    monkeypatch.setattr("charlie.media_adapter._on_media_executor_thread", lambda: True)

    result = await WindowsMediaAdapter().control("play_pause")

    assert result["ok"] is True
    assert result["status"] == "paused"
    assert result["verified"] is True


@pytest.mark.asyncio
async def test_play_pause_remains_unverified_when_session_status_does_not_change(monkeypatch):
    class PlaybackStatus(IntEnum):
        PLAYING = 4

    class Session:
        def get_playback_info(self):
            return SimpleNamespace(playback_status=PlaybackStatus.PLAYING)

        async def try_toggle_play_pause_async(self):
            return True

    class Manager:
        @staticmethod
        async def request_async():
            return Manager()

        def get_current_session(self):
            return Session()

    monkeypatch.setattr("charlie.media_adapter._manager_type", Manager)
    monkeypatch.setattr("charlie.media_adapter._on_media_executor_thread", lambda: True)

    result = await WindowsMediaAdapter().control("play_pause")

    assert result["ok"] is True
    assert result["status"] == "playing"
    assert result["verified"] is False
    assert "could not be confirmed" in result["reason"]


@pytest.mark.asyncio
async def test_thumbnail_is_encoded_for_media_results():
    class Stream:
        size = 3

    class Reference:
        async def open_read_async(self):
            return Stream()

    class Reader:
        def __init__(self, _stream):
            pass

        async def load_async(self, _size):
            return None

        def read_bytes(self, buffer):
            buffer[:] = b"abc"

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("charlie.media_adapter.DataReader", Reader)
    assert (await _thumbnail_data_uri(Reference())).endswith("YWJj")
    monkeypatch.undo()


def test_volume_snapshot_degrades_without_windows_audio_endpoint(monkeypatch):
    """A machine with no reachable audio endpoint must report None, not raise.

    Nulling the cached ``_audio_endpoint`` is not enough to create that
    condition: ``_get_audio_endpoint()`` re-fetches from ``AudioUtilities`` on
    the next call, so on a host where pycaw is installed the real endpoint came
    straight back and the snapshot reported a live volume. Neutralize the
    resolver itself so the absence is the thing under test.
    """
    from charlie.media_adapter import _volume_snapshot

    monkeypatch.setattr("charlie.media_adapter._audio_endpoint", None)
    monkeypatch.setattr("charlie.media_adapter.AudioUtilities", None)
    monkeypatch.setattr("charlie.media_adapter._get_audio_endpoint", lambda: None)
    assert _volume_snapshot() == {"volume_percent": None, "muted": None}

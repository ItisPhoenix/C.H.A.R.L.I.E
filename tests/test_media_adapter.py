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
    assert snapshot["title"] == ""


@pytest.mark.asyncio
async def test_thumbnail_is_encoded_for_the_dashboard():
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

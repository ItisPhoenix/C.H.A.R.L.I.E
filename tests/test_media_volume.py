from charlie.media_adapter import _volume_snapshot


def test_volume_snapshot_degrades_without_windows_audio_endpoint(monkeypatch):
    monkeypatch.setattr("charlie.media_adapter._audio_endpoint", None)

    assert _volume_snapshot() == {"volume_percent": None, "muted": None}

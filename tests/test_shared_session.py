import charlie.web_server as web_server


def test_active_session_defaults_to_primary_voice_session(monkeypatch):
    monkeypatch.setattr(web_server, "_active_frontend_session", None)
    monkeypatch.setattr(web_server.config, "charlie_launch_id", "launch-1")

    result = web_server._primary_session_id()

    assert result == "voice_launch-1"

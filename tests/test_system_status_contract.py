from charlie import web_server


def test_system_status_starts_empty_until_real_telemetry_arrives() -> None:
    assert web_server._system_status == {}

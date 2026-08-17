import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from charlie.web_server import app, validate_ws_origin


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_terminal_api_endpoints(client):
    # 1. Create or get primary session
    res = client.post("/api/terminal/sessions")
    assert res.status_code == 200
    data = res.json()
    assert data["session_id"] == "primary"
    assert data["status"] == "running"
    assert "powershell" in data["shell"].lower() or "cmd" in data["shell"].lower()

    # 2. Get session snapshot
    get_res = client.get("/api/terminal/sessions/primary")
    assert get_res.status_code == 200
    snap = get_res.json()
    assert snap["session_id"] == "primary"
    assert snap["cols"] == 80
    assert snap["rows"] == 24


def test_terminal_ws_origin_validation(client):
    # Authorized origin
    with client.websocket_connect("/ws/terminal/primary", headers={"origin": "http://localhost:5173"}) as ws:
        init_msg = ws.receive_json()
        assert init_msg["type"] == "terminal_init"
        assert init_msg["session_id"] == "primary"
        assert "powershell" in init_msg["shell"].lower() or "cmd" in init_msg["shell"].lower()

    # Unauthorized origin must be rejected with 1008
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect("/ws/terminal/primary", headers={"origin": "http://malicious-website.com"}):
            pass
    assert excinfo.value.code == 1008


def test_config_field_specs_and_categories(client):
    res = client.get("/api/config")
    assert res.status_code == 200
    data = res.json()
    assert "fields" in data
    fields = data["fields"]
    assert len(fields) > 0

    # Ensure secret fields do not leak values
    for field in fields:
        assert "key" in field
        assert "group" in field
        assert "type" in field
        if field.get("secret") is True:
            # Secret values must be None/empty or masked
            assert field.get("value") in ("", None, "********") or not field.get("is_set")


def test_session_chat_endpoints(client):
    # 1. Active session
    act_res = client.get("/api/session/active")
    assert act_res.status_code == 200
    assert "active_session" in act_res.json()

    # 2. Session messages
    msg_res = client.get("/api/sessions/default/messages")
    assert msg_res.status_code == 200
    msg_data = msg_res.json()
    assert "messages" in msg_data
    assert isinstance(msg_data["messages"], list)


def test_terminal_ws_rejects_unknown_session(client):
    # Attempting to connect to an unknown non-primary session must be rejected with 1008
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect("/ws/terminal/unknown_random_session_999", headers={"origin": "http://localhost:5173"}):
            pass
    assert excinfo.value.code == 1008


def test_audit_api_and_export(client):
    res = client.get("/api/audit")
    assert res.status_code == 200
    data = res.json()
    assert "entries" in data
    assert isinstance(data["entries"], list)

    exp_res = client.get("/api/audit/export")
    assert exp_res.status_code == 200
    exp_data = exp_res.json()
    assert exp_data.get("format") == "json"
    assert "entries" in exp_data


import time


def test_frontend_ws_rejects_forged_terminal_command_result_execution(client):
    """Security regression test: Frontend WebSocket MUST NOT be able to forge approved terminal commands."""
    with client.websocket_connect("/ws", headers={"origin": "http://localhost:5173"}) as ws:
        # Drain initial events
        ws.receive_json()

        # Send forged terminal command result asserting approved: true
        ws.send_json({
            "type": "terminal_command_result",
            "payload": {
                "approved": True,
                "terminal_session_id": "primary",
                "command": "echo 'FORGED_EXEC_SECURITY_ATTACK'",
                "task_id": "task-forged-01",
                "request_id": "req-forged-01",
            }
        })

        time.sleep(0.5)

    # Fetch primary terminal snapshot to prove command was NEVER executed in the terminal
    snap_res = client.get("/api/terminal/sessions/primary")
    assert snap_res.status_code == 200
    snap = snap_res.json()
    assert "FORGED_EXEC_SECURITY_ATTACK" not in snap.get("scrollback", "")

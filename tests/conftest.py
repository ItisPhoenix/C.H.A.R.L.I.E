"""Make every pytest EventBus instance use isolated non-production resources."""

import os
import socket


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


os.environ["CHARLIE_TEST_MODE"] = "true"
os.environ.setdefault("CHARLIE_TEST_EVENT_PORT", str(_free_port()))
os.environ.setdefault("CHARLIE_TEST_COMMAND_PORT", str(_free_port()))
os.environ.setdefault("SESSION_DB_PATH", os.path.join(os.getcwd(), ".codex-pytest-tmp", "sessions.db"))
os.environ.setdefault("WORLD_MODEL_DB_PATH", os.path.join(os.getcwd(), ".codex-pytest-tmp", "world_model.db"))

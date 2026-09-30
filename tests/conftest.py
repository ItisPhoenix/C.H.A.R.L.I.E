"""Make every pytest EventBus instance use isolated non-production resources."""

import os
import socket
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


os.environ["CHARLIE_TEST_MODE"] = "true"
os.environ.setdefault("CHARLIE_TEST_EVENT_PORT", str(_free_port()))
os.environ.setdefault("CHARLIE_TEST_COMMAND_PORT", str(_free_port()))
_TEST_STATE_DIR = os.path.join(os.getcwd(), ".codex-pytest-tmp")
os.makedirs(_TEST_STATE_DIR, exist_ok=True)
for name, filename in {
    "SESSION_DB_PATH": "sessions.db",
    "WORLD_MODEL_DB_PATH": "world_model.db",
    "MEMORY_DB_PATH": "charlie_memory_db",
    "MEMORY_GRAPH_DB": "charlie_memory_graph.db",
    "MEMORY_FILE": "MEMORY.md",
    "USER_FILE": "USER.md",
    "OPINIONS_FILE": "OPINIONS.md",
    "CHARLIE_TASK_JOURNAL_PATH": ".charlie_task_journal.json",
    "CHARLIE_BACKGROUND_TASK_STATE_PATH": ".charlie_background_task_state.json",
    "CHARLIE_RECOVERY_CACHE_PATH": ".charlie_recovery_cache.json",
}.items():
    os.environ[name] = os.path.join(_TEST_STATE_DIR, filename)


@pytest.fixture
def workspace_path():
    """Return a factory for paths that live inside an approved Charlie root.

    ``charlie.tools.get_path_gate_reason`` only treats the workspace, Documents,
    Downloads and Desktop as approved roots, so tests that assert "an ordinary
    user file needs no approval" must not derive their path from ``tmp_path``
    (which lands under %TEMP% on Windows). This builds the path from the repo
    root instead of hardcoding a directory, and never creates a file.
    """

    def _resolve(*parts: str) -> Path:
        return REPO_ROOT.joinpath(*parts)

    return _resolve

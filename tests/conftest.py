"""Make every pytest EventBus instance use isolated non-production resources."""

import os
import socket
import sys
from importlib import import_module
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
os.makedirs(os.path.join(_TEST_STATE_DIR, "logs"), exist_ok=True)
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
    # A browser profile is a live credential store (Cookies / Login Data /
    # Local Storage / IndexedDB). Leaving it unredirected let the suite launch
    # real Chromium against the operator's authenticated profile.
    "BROWSER_PROFILE_PATH": "browser_profile",
    "MCP_CONFIG_PATH": "mcp_config.json",
}.items():
    os.environ[name] = os.path.join(_TEST_STATE_DIR, filename)

# Isolation is initialised above, before Charlie configuration is imported.
# Both ``charlie.config`` and ``main`` skip ``load_dotenv`` when
# ``CHARLIE_TEST_MODE`` is true, so these values survive configuration import.
# Importing Config here makes that ordering explicit and lets the guard below
# assert the *resolved* paths rather than the requested environment variables.
from charlie.config import Config as _Config  # noqa: E402

# Paths that must never resolve outside the per-run state directory. Each entry
# mutates durable user state if it leaks, so a future configuration field of the
# same kind must be added here deliberately rather than by accident.
_ISOLATED_FIELDS = (
    "session_db_path",
    "world_model_db_path",
    "memory_db_path",
    "memory_graph_db",
    "memory_file",
    "user_file",
    "opinions_file",
    "browser_profile_path",
    "mcp_config_path",
)

_resolved = _Config()
_state_root = os.path.realpath(_TEST_STATE_DIR)
_leaked = [
    f"  {name} -> {getattr(_resolved, name)!r}"
    for name in _ISOLATED_FIELDS
    if not os.path.realpath(str(getattr(_resolved, name))).startswith(_state_root)
]
if _leaked:
    sys.stderr.write(
        "Refusing to run the test suite: these configuration paths resolve "
        f"outside {_TEST_STATE_DIR!r} and would mutate real user state.\n"
        + "\n".join(_leaked)
        + "\nFix the path override in tests/conftest.py before running tests.\n"
    )
    raise SystemExit(1)


# ---------------------------------------------------------------------------
# Process-global state isolation
# ---------------------------------------------------------------------------
# ``charlie.resource_locks`` and ``charlie.config`` both hold module-level
# mutable state that outlives any single test. Neither exposes a reset, and a
# test that touches one leaves it changed for every test that runs after it in
# the same process. Under pytest's default file order that is invisible; under
# any random order it surfaces as a test that passes alone and fails in a full
# run, with a *different* failing set each time.
#
# The two that have actually been observed leaking:
#
#   resource_locks._owners  -- a fixture acquired a capability lease and never
#       released it, so the lease stayed held by that owner for the rest of the
#       process. Every later ``shell_execute`` then waits for the ``terminal``
#       lease, which is never granted, and finally returns the bounded
#       ``"Error: Tool 'shell_execute' timed out after 30.0s"`` envelope
#       (``capabilities.py`` gives shell_execute ``required_leases=("terminal",)``
#       and ``timeout_sec=30.0``). Cascaded into several unrelated files, so the
#       failure looked like a different bug in each one.
#
#   config._TRUST_ENV_RISK_WARNED -- a deliberate warn-once latch, so that a real
#       security signal does not read as repeated noise across the several
#       ``Config`` instances a real startup builds. Under a test run it means the
#       first test to construct a risky ``Config`` consumes the only warning, so
#       any test asserting the warning *is* emitted is inherently order-dependent.
#
# The reset is a clear-to-empty rather than a snapshot/restore on purpose: a
# snapshot taken at the start of test N already contains whatever test N-1
# leaked, and restoring it would carry the leak forward -- which is exactly why a
# file-local snapshot/restore fixture cannot fix a process-global leak.


def _reset_process_global_state() -> None:
    """Drop capability-lease ownership and the warn-once latch to defaults."""
    # ``charlie/__init__.py`` does ``from .config import config``, which rebinds
    # the ``config`` attribute of the package to the Config *singleton instance*.
    # So both ``from charlie import config`` and ``import charlie.config as x``
    # hand back that instance, and assigning ``config._TRUST_ENV_RISK_WARNED``
    # would silently set an attribute on the instance instead of the module
    # global this is meant to reset. Resolve both modules through the import
    # system, which always yields the module object.
    config_module = import_module("charlie.config")
    resource_locks = import_module("charlie.resource_locks")

    with resource_locks._lock:
        resource_locks._owners.clear()
        resource_locks._active_leases.clear()
        resource_locks._lease_objects.clear()
        resource_locks._revocations.clear()
        resource_locks._takeover_listeners.clear()
        # Waiter events are bound to the event loop of the test that registered
        # them. Once that loop is closed they can never be woken, so keeping them
        # only grows a set of dead events that every later ``_wake`` must walk.
        resource_locks._waiters.clear()
    config_module._TRUST_ENV_RISK_WARNED = False


@pytest.fixture(autouse=True)
def _isolate_process_global_state():
    """Keep one test's global mutations out of the next test, whichever runs next."""
    _reset_process_global_state()
    yield
    _reset_process_global_state()


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


@pytest.fixture
def outside_approved_root_path():
    """Return a factory for paths that are deliberately outside every root.

    The inverse of ``workspace_path``. Tests asserting "this needs approval"
    must not derive their path from ``tmp_path`` either: ``basetemp`` is
    configurable, so a run that pins it inside the workspace would silently flip
    an outside-root assertion into an inside-root one. A sibling of the repo
    root is outside the workspace, Documents, Downloads and Desktop on every
    supported platform, and nothing is created on disk.
    """

    def _resolve(*parts: str) -> Path:
        return REPO_ROOT.parent.joinpath("outside-approved-roots", *parts)

    return _resolve

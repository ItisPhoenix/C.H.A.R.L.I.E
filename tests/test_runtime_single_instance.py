"""Runtime single-instance ownership and test-mode log isolation contracts.

Both items here guard the same failure mode: a second Charlie process running
quietly beside the first. For logging it is a test process that silently rotates
real diagnostic history; for the instance lock it is a production process that
reports ``telegram`` healthy while its poller receives nothing.

The lock tests deliberately drive a *real* second process rather than a mock.
An in-process double-handle would still pass if the implementation regressed
into something that only looks cross-process, and the "no stale lock after a
crash" guarantee is specifically a property of kernel-released OS handles, which
no in-process double can demonstrate.
"""

import asyncio
import json
import logging.handlers
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

import main
from charlie.single_instance import SingleInstanceError, SingleInstanceLock

REPO_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_LOG = Path("logs") / "charlie.log"
TEST_STATE_DIR = ".codex-pytest-tmp"
MODULE_PATH = REPO_ROOT / "charlie" / "single_instance.py"

# The venv's python.exe on Windows is a redirector: launching it spawns the
# real interpreter as a *grandchild*, so Popen.pid is not the pid that ends up
# holding the lock and Popen.kill() leaves the grandchild alive still holding
# it. The base executable is not a shim, which makes Popen.pid authoritative and
# kill() actually reach the lock owner. The child loads single_instance.py by
# file path rather than importing the package, because charlie/__init__.py
# pulls in config -> dotenv, which only exists in the venv. single_instance.py
# itself is stdlib-only, so the base interpreter is sufficient.
CHILD_INTERPRETER = getattr(sys, "_base_executable", None) or sys.executable

# The import probe imports modules and prints one RESULT line; it never boots the
# runtime, so tens of seconds is already a generous ceiling. The old 600s budget
# meant a single hung probe could park one of these tests for ten minutes, and
# four tests share the helper. A hang must surface as a fast, named failure.
PROBE_TIMEOUT = 30
# A lock holder polls a stop marker every 50ms and exits on the first sight of
# it, so it leaves in well under a second. These budgets exist to detect a
# *wedged* holder; a long wait would hide the failure and leak the child, which
# still owns the OS lock.
HOLDER_PID_TIMEOUT = 15
HOLDER_EXIT_TIMEOUT = 15
# The holder's own self-exit, so an abandoned child (parent crash, CI kill)
# cannot outlive the suite by minutes while holding the lock.
HOLDER_SELF_EXIT_SECONDS = 30

CHILD_BOOTSTRAP = textwrap.dedent(
    """
    import importlib.util, sys
    spec = importlib.util.spec_from_file_location("charlie_single_instance_under_test", sys.argv[1])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    """
)


def _file_handler_targets() -> list[str]:
    """Absolute paths the root logger is currently writing to."""
    return [
        str(Path(handler.baseFilename).resolve())
        for handler in logging.getLogger().handlers
        if isinstance(handler, logging.handlers.RotatingFileHandler)
    ]


def _probe_reported_pid(pid_file: Path) -> int | None:
    """The pid the probe published, which need not be the pid that was launched."""
    try:
        raw = pid_file.read_text().strip()
    except OSError:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _kill_probe_tree(pid: int | None) -> None:
    """Terminate a timed-out probe interpreter and anything it spawned.

    ``subprocess.run`` kills only the pid it launched, which on Windows is the
    venv's redirector rather than the interpreter that actually imported main.
    A timed-out probe would therefore leave a live interpreter behind. The pid
    used here is the one the child reported about itself, so it names the real
    process, and ``/T`` reaches its descendants too.
    """
    if pid is None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT,
            check=False,
        )
        return
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def _run_main_import_probe(env_overrides: dict, pid_file: Path) -> dict:
    """Import main from a clean interpreter and report its logging wiring.

    The in-process assertions observe whatever the test session already did at
    import time. This re-derives that decision from scratch, which is the
    behaviour that used to rotate real history.

    Contention on the real ``data/charlie.lock`` is not possible here: main.py
    acquires the instance lock inside ``main()``, and this probe stops at
    ``import main``. There is consequently no env var or config seam to redirect
    the lock -- ``single_instance.DEFAULT_LOCK_PATH`` is a plain constant -- and
    none is needed. The probe publishes its own pid before importing so that a
    timeout can name and kill the exact interpreter that hung.
    """
    probe = textwrap.dedent(
        """
        import json, logging, logging.handlers, os, sys
        with open(sys.argv[1], "w") as handle:
            handle.write(str(os.getpid()))
        import main
        print("RESULT" + json.dumps({
            "log_file": main.LOG_FILE,
            "targets": [
                h.baseFilename
                for h in logging.getLogger().handlers
                if isinstance(h, logging.handlers.RotatingFileHandler)
            ],
        }))
        """
    )
    try:
        completed = subprocess.run(
            [sys.executable, "-c", probe, str(pid_file)],
            cwd=REPO_ROOT,
            env={**os.environ, "CHARLIE_TEST_MODE": "true", **env_overrides},
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        _kill_probe_tree(_probe_reported_pid(pid_file))
        pytest.fail(
            f"the main import probe exceeded {PROBE_TIMEOUT}s and was killed. It only imports "
            f"modules and prints one RESULT line, so this is a hang, not slow work. "
            f"data/charlie.lock left behind: {os.path.exists(REPO_ROOT / 'data' / 'charlie.lock')}. "
            f"stdout: {(exc.stdout or '')[-500:]!r} stderr: {(exc.stderr or '')[-1000:]!r}"
        )
    assert completed.returncode == 0, completed.stderr
    result_line = next(
        (line for line in completed.stdout.splitlines() if line.startswith("RESULT")),
        None,
    )
    assert result_line is not None, f"probe exited 0 but printed no RESULT line: {completed.stdout!r}"
    return json.loads(result_line[len("RESULT") :])


# ---------------------------------------------------------------- item 0.1


def test_importing_main_does_not_route_records_to_the_production_log():
    """The import-time handler must not point at logs/charlie.log."""
    production = str((REPO_ROOT / PRODUCTION_LOG).resolve())

    assert main.LOG_FILE != "logs/charlie.log"
    assert production not in _file_handler_targets()


def test_importing_main_keeps_a_rotating_handler_below_the_test_state_dir():
    """Redirecting the log must not simply delete the file handler.

    Dropping the handler would be the easy way to stop touching production logs,
    but it would also silently remove the DEBUG-level file record the console
    policy is designed around, so the handler has to survive -- just not in
    production.
    """
    targets = _file_handler_targets()

    assert targets, "import main must still install a rotating file handler"
    test_state = str((REPO_ROOT / TEST_STATE_DIR).resolve())
    assert all(target.startswith(test_state) for target in targets)
    assert all(os.path.exists(target) for target in targets)


def test_fresh_import_under_test_mode_never_opens_the_production_log(tmp_path):
    payload = _run_main_import_probe({}, tmp_path / "probe.pid")

    assert payload["targets"], "no rotating handler was installed"
    production = str((REPO_ROOT / PRODUCTION_LOG).resolve())
    assert production not in [str(Path(t).resolve()) for t in payload["targets"]]
    assert not Path(payload["log_file"]).is_absolute()
    assert payload["log_file"].replace("\\", "/").startswith(f"{TEST_STATE_DIR}/")


def test_explicit_log_file_override_still_wins_under_test_mode(tmp_path):
    """CHARLIE_LOG_FILE is the seam, so the test location is never hardcoded twice."""
    override = tmp_path / "explicit.log"

    payload = _run_main_import_probe({"CHARLIE_LOG_FILE": str(override)}, tmp_path / "probe.pid")

    assert payload["log_file"] == str(override)
    assert override.exists()


# ---------------------------------------------------------------- item 0.5


def test_second_acquire_is_refused_and_names_the_holder(tmp_path):
    path = str(tmp_path / "charlie.lock")
    first = SingleInstanceLock(path).acquire()
    try:
        with pytest.raises(SingleInstanceError) as excinfo:
            SingleInstanceLock(path).acquire()

        assert excinfo.value.holder_pid == os.getpid()
        assert "already running" in str(excinfo.value)
        assert str(os.getpid()) in str(excinfo.value)
    finally:
        first.release()


def test_release_frees_the_lock_for_the_next_caller(tmp_path):
    path = str(tmp_path / "charlie.lock")
    SingleInstanceLock(path).acquire().release()

    second = SingleInstanceLock(path)
    second.acquire()
    assert second.acquired is True
    second.release()


def test_release_is_idempotent_so_shutdown_cannot_fail_on_it(tmp_path):
    lock = SingleInstanceLock(str(tmp_path / "charlie.lock"))
    lock.release()  # never acquired

    lock.acquire()
    lock.release()
    lock.release()
    assert lock.acquired is False


def test_context_manager_releases_when_the_body_raises(tmp_path):
    path = str(tmp_path / "charlie.lock")
    with pytest.raises(ValueError):
        with SingleInstanceLock(path):
            raise ValueError("boom")

    SingleInstanceLock(path).acquire().release()


def _spawn_lock_holder(lock_path: str, pid_file: str, stop_marker: str) -> subprocess.Popen:
    """Start a real second process that owns the lock and holds it until stopped.

    The child reports its *own* os.getpid() rather than trusting the parent's
    Popen.pid, and it also waits on a stop marker so the caller can choose
    between a graceful exit and a hard kill.
    """
    probe = CHILD_BOOTSTRAP + textwrap.dedent(
        """
        import os, time
        lock_path, pid_file, stop_marker = sys.argv[2], sys.argv[3], sys.argv[4]
        module.SingleInstanceLock(lock_path).acquire()
        with open(pid_file, "w") as handle:
            handle.write(str(os.getpid()))
        deadline = time.time() + %d
        while not os.path.exists(stop_marker) and time.time() < deadline:
            time.sleep(0.05)
        """
        % HOLDER_SELF_EXIT_SECONDS
    )
    return subprocess.Popen(
        [CHILD_INTERPRETER, "-c", probe, str(MODULE_PATH), lock_path, pid_file, stop_marker],
        cwd=REPO_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _wait_for_holder_pid(pid_file: str, holder: subprocess.Popen) -> int:
    deadline = time.time() + HOLDER_PID_TIMEOUT
    while time.time() < deadline:
        if os.path.exists(pid_file):
            with open(pid_file) as handle:
                reported = handle.read().strip()
            if reported:
                return int(reported)
        if holder.poll() is not None:
            pytest.fail(
                f"lock holder exited early with returncode {holder.returncode} before "
                f"reporting its pid; pid file present: {os.path.exists(pid_file)}"
            )
        time.sleep(0.05)
    pytest.fail(
        f"lock holder pid {holder.pid} never reported its pid within {HOLDER_PID_TIMEOUT}s "
        f"(still running: {holder.poll() is None}, pid file present: {os.path.exists(pid_file)})"
    )


def _reap_holder(holder: subprocess.Popen, timeout: int) -> None:
    """Wait for a lock holder to exit, killing it if it refuses.

    A holder that has to be killed is a wedged child, and a wedged child still
    owns the OS lock, so waiting indefinitely would turn a clear failure into a
    hung suite *and* a leaked process.
    """
    try:
        holder.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        holder.kill()
        try:
            holder.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            pytest.fail(f"lock holder pid {holder.pid} survived kill() after {timeout}s")


def test_a_live_second_process_is_refused_and_reports_its_real_pid(tmp_path):
    lock_path = str(tmp_path / "charlie.lock")
    pid_file = str(tmp_path / "holder.pid")
    stop_marker = str(tmp_path / "stop")

    holder = _spawn_lock_holder(lock_path, pid_file, stop_marker)
    try:
        holder_pid = _wait_for_holder_pid(pid_file, holder)

        with pytest.raises(SingleInstanceError) as excinfo:
            SingleInstanceLock(lock_path).acquire()

        assert excinfo.value.holder_pid == holder_pid
        assert str(holder_pid) in str(excinfo.value)
    finally:
        with open(stop_marker, "w"):
            pass
        _reap_holder(holder, HOLDER_EXIT_TIMEOUT)

    # A holder that exited on its own must not leave the lock stuck.
    SingleInstanceLock(lock_path).acquire().release()


def test_hard_killed_holder_does_not_leave_a_stale_lock(tmp_path):
    """The crash case: the kernel must release the lock, not our cleanup code.

    The holder is terminated rather than asked to exit, which is the situation a
    real Charlie crash produces. If the implementation relied on writing a
    sentinel file and checking its contents, or trusted a PID liveness probe,
    this test would hang or refuse forever.
    """
    lock_path = str(tmp_path / "charlie.lock")
    pid_file = str(tmp_path / "holder.pid")
    stop_marker = str(tmp_path / "stop")

    holder = _spawn_lock_holder(lock_path, pid_file, stop_marker)
    try:
        _wait_for_holder_pid(pid_file, holder)
        with pytest.raises(SingleInstanceError):
            SingleInstanceLock(lock_path).acquire()
    finally:
        holder.kill()
        _reap_holder(holder, HOLDER_EXIT_TIMEOUT)

    # The lock file still exists and still names the dead holder. That leftover
    # is exactly what a crashed Charlie leaves behind, and it must not be read
    # as a held lock.
    assert os.path.exists(lock_path)
    recovered = SingleInstanceLock(lock_path)
    recovered.acquire()
    assert recovered.acquired is True
    recovered.release()


def test_main_exits_cleanly_and_names_the_holder_pid(monkeypatch, capsys, tmp_path):
    """main() must report the conflict and return, not raise or half-start."""

    # main.py calls SingleInstanceLock() with no argument, so the real lock
    # would land on the production data/charlie.lock path. The double stands in
    # before the call is ever made and names a tmp-scoped path instead, which
    # means this test cannot leave a lock file behind for any other test to trip
    # over even if the real implementation regressed.
    lock_path = str(tmp_path / "charlie.lock")

    class ContendedLock:
        path = lock_path

        def acquire(self):
            raise SingleInstanceError(self.path, 4242)

    monkeypatch.setattr(main, "SingleInstanceLock", lambda *a, **k: ContendedLock())

    exit_code = asyncio.run(main.main())

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "already running" in captured.err
    assert "4242" in captured.err
    assert "Traceback" not in captured.err

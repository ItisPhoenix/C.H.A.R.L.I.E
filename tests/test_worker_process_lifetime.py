"""ASR worker process-lifetime proof.

charlie/voice.py spawns the ASR worker with ``mp.Process(..., daemon=True)``.
On Windows that child is an independent process created by ``CreateProcess``
with no job object, so nothing in the OS ties it to the parent. ``daemon=True``
only buys cleanup through ``multiprocessing.util._exit_function`` in the
*parent*, and ``TerminateProcess`` (``Stop-Process -Force`` / ``taskkill /F``)
skips that path entirely -- leaving the worker looping on its input queue with
a dead parent. These tests use real Windows processes to pin the contract:

1. the child does not survive a hard parent kill (the reported defect);
2. detection is bounded, not "eventually";
3. without the watchdog the orphan really does happen (control case);
4. a normal parent exit still reaps the child;
5. a live parent is never mistaken for a dead one, so the worker stays stoppable;
6. the parent-side ``finally`` reclaims the worker when the capture loop dies.

Every wait is bounded and every straggler this module creates is reclaimed in
``finally``/fixture teardown, so it can neither hang nor leak.
"""

import multiprocessing as mp
import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import psutil
import pytest

from charlie.asr_worker import PARENT_DEATH_EXIT_CODE, install_parent_death_watchdog

REPO_ROOT = Path(__file__).resolve().parents[1]
# Long enough that a worker would still be alive well past detection if the
# watchdog were removed, short enough that teardown stays bounded.
PARENT_SLEEP_SECONDS = 180
HARD_KILL_DEADLINE_S = 20.0
NORMAL_EXIT_DEADLINE_S = 30.0
CHILD_READY_DEADLINE_S = 60.0
# The harness filename carries this marker so _reap can prove a pid belongs to
# this test module before killing anything.
ATTRIBUTION_MARKER = "test_worker_process_lifetime"


# --- the spawned child ---------------------------------------------------------
# This mirrors asr_worker_process's unbounded `while True: input_queue.get(...)`
# loop. The real worker cannot be driven directly because it loads a Whisper
# model before it polls anything; the property under test is "spawn child that
# nothing can stop", so that shape is reproduced exactly. The watchdog itself is
# the production object imported from charlie.asr_worker, not a stand-in.


def _worker_body(watchdog: bool, ready_path: str) -> None:
    if watchdog:
        install_parent_death_watchdog()
    with open(ready_path, "w", encoding="utf-8") as handle:
        handle.write(str(os.getpid()))
    while True:
        time.sleep(0.25)


# A standalone, importable module so both pytest's spawn children and the
# spawned harness can resolve the target by name (tests/ has no __init__.py, so
# `tests.test_worker_process_lifetime` is not importable).
_HARNESS_SOURCE = textwrap.dedent(
    '''
    """Parent process that spawns a worker exactly the way charlie/voice.py does."""

    import json
    import multiprocessing as mp
    import os
    import sys
    import time

    REPO_ROOT = {repo!r}
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)

    # Production watchdog, not a stand-in.
    from charlie.asr_worker import install_parent_death_watchdog


    def worker_body(watchdog, ready_path):
        if watchdog:
            install_parent_death_watchdog()
        with open(ready_path, "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
        while True:
            time.sleep(0.25)


    def main():
        watchdog = sys.argv[1] == "watchdog"
        ready_path = sys.argv[2]
        sleep_seconds = float(sys.argv[3])
        if os.path.exists(ready_path):
            os.remove(ready_path)
        process = mp.Process(
            target=worker_body, args=(watchdog, ready_path), daemon=True
        )
        process.start()
        print(json.dumps({{"parent_pid": os.getpid(), "child_pid": process.pid}}))
        sys.stdout.flush()
        # The reported shape: an app that just keeps running until it is killed.
        time.sleep(sleep_seconds)


    if __name__ == "__main__":
        main()
    '''
).format(repo=str(REPO_ROOT))


# --- helpers -------------------------------------------------------------------


def _alive(pid: int) -> bool:
    try:
        return psutil.pid_exists(pid) and psutil.Process(pid).is_running()
    except psutil.NoSuchProcess:  # pragma: no cover
        return False


def _wait_until_dead(pid: int, deadline_s: float) -> bool:
    """Return True once the pid is observed gone. False means it outlived the wait."""

    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return not _alive(pid)


def _cmdline(pid: int) -> str:
    try:
        return " ".join(psutil.Process(pid).cmdline())
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return ""


def _reap(pid: int) -> None:
    """Kill a straggler, refusing to touch anything this module did not spawn."""

    if not _alive(pid):
        return
    cmdline = _cmdline(pid)
    if ATTRIBUTION_MARKER not in cmdline and "spawn_main" not in cmdline:
        pytest.fail(f"refusing to kill unattributable pid {pid}: {cmdline}")
    try:
        proc = psutil.Process(pid)
        proc.kill()
        proc.wait(timeout=10)
    except psutil.NoSuchProcess:  # pragma: no cover
        pass


def _hard_kill(parent: subprocess.Popen) -> None:
    """Windows TerminateProcess: no atexit, no finally, no flush. Same as Stop-Process -Force."""

    if parent.poll() is None:
        parent.kill()
    try:
        parent.wait(timeout=20)
    except subprocess.TimeoutExpired:  # pragma: no cover
        pytest.fail("hard-killed parent was never reaped")


@pytest.fixture
def harness(tmp_path):
    script = tmp_path / f"{ATTRIBUTION_MARKER}_harness.py"
    script.write_text(_HARNESS_SOURCE, encoding="utf-8")
    created: list[int] = []

    def _launch(watchdog: bool) -> tuple[subprocess.Popen, int]:
        ready = tmp_path / f"child_pid_{'wd' if watchdog else 'no'}.txt"
        parent = subprocess.Popen(
            [sys.executable, str(script), "watchdog" if watchdog else "nowatchdog",
             str(ready), str(PARENT_SLEEP_SECONDS)],
            cwd=str(REPO_ROOT),
        )
        created.append(parent.pid)
        try:
            child_pid = _wait_for_child_pid(ready, parent)
        except BaseException:
            _hard_kill(parent)
            raise
        created.append(child_pid)
        return parent, child_pid

    yield _launch

    for pid in created:
        _reap(pid)


def _wait_for_child_pid(ready_path: Path, parent: subprocess.Popen) -> int:
    deadline = time.monotonic() + CHILD_READY_DEADLINE_S
    while time.monotonic() < deadline:
        if parent.poll() is not None:
            raise AssertionError(f"spawn parent exited early rc={parent.returncode}")
        if ready_path.exists():
            raw = ready_path.read_text(encoding="utf-8").strip()
            if raw:
                return int(raw)
        time.sleep(0.05)
    raise AssertionError("spawned worker never reported its pid")


# --- tests ---------------------------------------------------------------------


def test_worker_does_not_survive_hard_parent_kill(harness):
    """The reported defect: a hard-killed parent must not strand the worker."""

    parent, child_pid = harness(watchdog=True)
    assert _alive(child_pid), "worker was not alive before the parent was killed"

    _hard_kill(parent)
    assert not _alive(parent.pid), "parent should be gone"

    assert _wait_until_dead(child_pid, HARD_KILL_DEADLINE_S), (
        f"ORPHAN: worker pid={child_pid} still alive {HARD_KILL_DEADLINE_S}s "
        "after its parent was hard-killed"
    )


def test_hard_parent_kill_is_detected_within_the_poll_window(harness):
    """Detection is bounded by the watchdog poll, not by operator patience."""

    parent, child_pid = harness(watchdog=True)
    _hard_kill(parent)

    started = time.monotonic()
    died = _wait_until_dead(child_pid, HARD_KILL_DEADLINE_S)
    elapsed = time.monotonic() - started
    assert died, f"ORPHAN: worker pid={child_pid} survived the hard kill"
    # Production poll is 1.0s; slack absorbs a loaded host, but a worker that
    # only dies by accident would blow straight through this.
    assert elapsed < 15.0, f"worker took {elapsed:.1f}s to notice the dead parent"


def test_hard_parent_kill_without_watchdog_leaves_an_orphan(harness):
    """Control case: daemon=True alone does NOT prevent the orphan.

    This is what makes the watchdog tests meaningful. It deliberately creates a
    real orphan for a few seconds and reaps it in fixture teardown.
    """

    parent, child_pid = harness(watchdog=False)
    assert _alive(child_pid)

    _hard_kill(parent)

    assert not _wait_until_dead(child_pid, 5.0), (
        "control case no longer reproduces: daemon=True now kills the child, so "
        "the watchdog tests above would prove nothing"
    )


def test_worker_exits_when_parent_exits_normally(harness):
    """A parent that terminates normally must still reap the worker."""

    parent, child_pid = harness(watchdog=True)
    assert _alive(child_pid)

    parent.terminate()
    try:
        parent.wait(timeout=20)
    except subprocess.TimeoutExpired:  # pragma: no cover
        _hard_kill(parent)

    assert _wait_until_dead(child_pid, NORMAL_EXIT_DEADLINE_S), (
        f"worker pid={child_pid} outlived a parent that exited normally"
    )


def test_watchdog_stays_inert_while_parent_is_alive(harness):
    """A live parent must never be mistaken for a dead one.

    Guards the other direction: a watchdog that fires while the parent is running
    would make the ASR worker unstoppable.
    """

    parent, child_pid = harness(watchdog=True)

    # Well past several production poll intervals (1.0s each).
    time.sleep(5.0)

    assert _alive(parent.pid), "harness parent should still be running"
    assert _alive(child_pid), (
        f"worker pid={child_pid} exited while its parent was alive -- the "
        "watchdog would have made the ASR worker unstoppable"
    )


def test_watchdog_is_inert_without_a_multiprocessing_parent():
    """install_parent_death_watchdog() must not arm in a plain process.

    tests/test_asr_worker.py drives asr_worker_process in-process, so arming
    here would put a watchdog thread into the pytest runner itself.
    """

    assert PARENT_DEATH_EXIT_CODE != 0, "parent-death exit must be distinguishable"

    watchdog = install_parent_death_watchdog()
    assert watchdog is not None
    assert watchdog.armed is False, "must not arm without a multiprocessing parent"
    assert watchdog.parent_pid is None
    watchdog.stop()  # must be safe on a disarmed watchdog


def test_capture_loop_failure_reclaims_asr_worker():
    """The parent-side finally must terminate the worker when the loop dies.

    A real mp.Process stands in for the ASR worker so the assertion is a real
    process kill, not a mock call count.
    """

    from charlie import voice as voice_module

    class _CaptureBoom(RuntimeError):
        pass

    worker = mp.Process(
        target=_worker_body, args=(False, os.devnull), daemon=True
    )
    worker.start()
    try:
        assert worker.is_alive(), "stand-in worker did not start"

        engine = voice_module.VoiceEngine.__new__(voice_module.VoiceEngine)
        engine.stop_event = threading.Event()
        engine.asr_process = worker

        def _explode():
            raise _CaptureBoom("capture loop failed")

        engine._run_capture_loop = _explode

        with pytest.raises(_CaptureBoom):
            voice_module.VoiceEngine._run(engine)

        assert not worker.is_alive(), (
            "capture-loop failure left the ASR worker running"
        )
    finally:
        _reap(worker.pid)
        worker.join(timeout=10)

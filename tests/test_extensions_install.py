"""Tests for the shared extension-install logic and skill-script runner."""

import sys

from charlie.extensions.install import run_skill_script


def test_run_skill_script_executes_and_returns_output():
    script = [sys.executable, "-c", "print('hello from skill script')"]
    result = run_skill_script(script[0], script[1:])
    assert "hello from skill script" in result


def test_run_skill_script_blocks_hard_blocked_keyword():
    result = run_skill_script("shutdown", ["-s"])
    assert "blocked" in result.lower()


def test_run_skill_script_gates_risky_keyword():
    result = run_skill_script("rm", ["-rf", "/"])
    assert "blocked" in result.lower()
    assert "unsupervised" in result.lower()


def test_run_skill_script_reports_nonzero_exit():
    script = [sys.executable, "-c", "import sys; sys.exit(3)"]
    result = run_skill_script(script[0], script[1:])
    assert "exited with code 3" in result

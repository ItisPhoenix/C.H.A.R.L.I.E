"""Command-line entry point for C.H.A.R.L.I.E.

Exposes the same operational modes as ``main.py`` but in a tidy
argparse-driven shape, plus a couple of diagnostic subcommands that
were previously hidden behind the ``doctor`` keyword and a hard-coded
``--daemon`` flag.

Subcommands
-----------

* ``charlie`` (no subcommand)         — run in phoenix (foreground) mode
* ``charlie daemon``                  — headless daemon
* ``charlie doctor``                  — self-check report
* ``charlie status``                  — hit the control server's /api/status
* ``charlie audit``                   — automation subsystem health check
* ``charlie --version`` / ``--help``  — meta

Adding new subcommands: drop a ``cmd_<name>`` function below and add
the matching ``sub_parsers.add_parser(...)`` block in ``build_parser``.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Sequence

__all__ = ["main", "build_parser"]

# Pulled from pyproject.toml so ``charlie --version`` always agrees
# with the package metadata.
_VERSION = "0.1.0"


# ── Subcommand handlers ─────────────────────────────────────────────────

def _ensure_venv() -> None:
    """Re-exec into the local .venv interpreter if we aren't already.

    Mirrors the behaviour of ``main.main`` so a system-level
    ``charlie`` install still finds the project's local dependencies.
    Silently does nothing when the venv interpreter cannot be located.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    venv_python = os.path.abspath(os.path.join(root, ".venv", "Scripts", "python.exe"))
    current = os.path.abspath(sys.executable)
    if os.path.exists(venv_python) and current.lower() != venv_python.lower():
        import subprocess

        result = subprocess.run([venv_python, "-m", "charlie.cli", *sys.argv[1:]])
        sys.exit(result.returncode)


def cmd_run_pheonix(_args: argparse.Namespace) -> int:
    """Run the foreground (phoenix) supervisor. Default mode."""
    import multiprocessing

    from charlie.watchdog import PhoenixSupervisor

    interrupt = multiprocessing.Event()
    reboot = multiprocessing.Event()
    supervisor = PhoenixSupervisor(interrupt, reboot)
    try:
        supervisor.start()
    except KeyboardInterrupt:
        pass
    finally:
        supervisor.stop()
    return 0


def cmd_run_daemon(_args: argparse.Namespace) -> int:
    """Run the headless daemon supervisor."""
    import multiprocessing

    from charlie.watchdog.daemon_supervisor import DaemonSupervisor

    interrupt = multiprocessing.Event()
    reboot = multiprocessing.Event()
    supervisor = DaemonSupervisor(interrupt, reboot)
    try:
        supervisor.start()
    except KeyboardInterrupt:
        pass
    finally:
        supervisor.stop()
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """Run the doctor self-check and print the report."""
    from charlie.utils.doctor import run_self_check

    report = run_self_check()
    status_icons = {"pass": "✓", "warn": "⚠", "fail": "✗"}

    print("\n╔══════════════════════════════════════════════════════════╗")
    print("║          C.H.A.R.L.I.E. Doctor Self-Check               ║")
    print("╚══════════════════════════════════════════════════════════╝\n")
    for check in report.checks:
        icon = status_icons.get(check.status, "?")
        print(f"  [{icon}] {check.name:20s} — {check.status.upper():4s} — {check.message}")
        if check.cause:
            print(f"      Cause: {check.cause}")
        if check.remediation:
            print(f"      Fix:   {check.remediation}")
    print(f"\n  Overall: {report.overall.upper()}")
    if args.verbose:
        print(f"  Generated at: {report.generated_at}")
    print()
    return 0 if report.overall == "pass" else 1


def cmd_status(args: argparse.Namespace) -> int:
    """Hit the local control server's /api/status endpoint.

    Falls back to a friendly message when the server isn't running
    (rather than raising — this is a diagnostic tool).
    """
    import json
    import urllib.error
    import urllib.request

    url = f"http://127.0.0.1:{args.port}/api/status"
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            print(json.dumps(data, indent=2))
            return 0
    except urllib.error.URLError as e:
        print(f"control_server_unreachable | url={url} | reason={e.reason}", file=sys.stderr)
        return 2


def cmd_audit(_args: argparse.Namespace) -> int:
    """Quick health check of the automation subsystem.

    Reports whether each piece of wiring is present and started. This
    is a subset of the doctor's checks, focused on the pieces that
    usually go silently unwired.
    """
    from charlie.utils.logger import get_logger

    logger = get_logger("charlie.cli.audit")
    checks: list[tuple[str, bool, str]] = []

    # 1. Tool registry
    try:
        from charlie.tools.tool_registry import ToolRegistry

        reg = ToolRegistry()
        n = len(reg.list_all())
        checks.append(("tool_registry", True, f"{n} tools registered"))
    except Exception as e:
        checks.append(("tool_registry", False, str(e)))

    # 2. Agent registry
    try:
        from charlie.brain.agent_factory import AgentFactory

        factory = AgentFactory(agents_dir="charlie/agents")
        agents = factory.list_agents()
        checks.append(("agent_registry", True, f"{len(agents)} agents loaded"))
    except Exception as e:
        checks.append(("agent_registry", False, str(e)))

    # 3. Memory coordinator
    try:
        from charlie.memory.memory_coordinator import MemoryCoordinator

        _ = MemoryCoordinator()
        checks.append(("memory_coordinator", True, "instantiable"))
    except Exception as e:
        checks.append(("memory_coordinator", False, str(e)))

    # 4. Risk gate
    try:
        from charlie.automation.risk_gate import RiskGate

        _ = RiskGate()
        checks.append(("risk_gate", True, "instantiable"))
    except Exception as e:
        checks.append(("risk_gate", False, str(e)))

    # 5. Confidence gate
    try:
        from charlie.security.confidence_gate import ConfidenceGate

        _ = ConfidenceGate()
        checks.append(("confidence_gate", True, "instantiable"))
    except Exception as e:
        checks.append(("confidence_gate", False, str(e)))

    print("\n╔══════════════════════════════════════════════════════════╗")
    print("║          C.H.A.R.L.I.E. Automation Audit                ║")
    print("╚══════════════════════════════════════════════════════════╝\n")
    failed = 0
    for name, ok, detail in checks:
        icon = "✓" if ok else "✗"
        print(f"  [{icon}] {name:20s} — {detail}")
        if not ok:
            failed += 1
    print(f"\n  {'PASS' if failed == 0 else 'FAIL'} ({len(checks) - failed}/{len(checks)} ok)\n")

    if failed:
        logger.warning("audit_partial | failed=%d", failed)
    return 0 if failed == 0 else 1


# ── Parser plumbing ─────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="charlie",
        description="C.H.A.R.L.I.E. — Completely Helpful And Rather Local Intelligent Engine",
    )
    parser.add_argument("--version", action="version", version=f"charlie {_VERSION}")
    parser.add_argument(
        "--no-venv-respawn",
        action="store_true",
        help="Skip the auto-respawn into the local .venv (for tests/CI)",
    )

    sub = parser.add_subparsers(dest="command", title="subcommands")

    p_run = sub.add_parser("run", help="Run in foreground phoenix mode (default)")
    p_run.set_defaults(func=cmd_run_pheonix)

    p_daemon = sub.add_parser("daemon", help="Run in headless daemon mode")
    p_daemon.set_defaults(func=cmd_run_daemon)

    p_doctor = sub.add_parser("doctor", help="Run the doctor self-check")
    p_doctor.add_argument("-v", "--verbose", action="store_true", help="Show timestamps")
    p_doctor.set_defaults(func=cmd_doctor)

    p_status = sub.add_parser("status", help="Query the control server's status endpoint")
    p_status.add_argument("--port", type=int, default=9742, help="Control server port (default: 9742)")
    p_status.set_defaults(func=cmd_status)

    p_audit = sub.add_parser("audit", help="Audit the automation subsystem wiring")
    p_audit.set_defaults(func=cmd_audit)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if not args.no_venv_respawn:
        _ensure_venv()

    # No subcommand → run in phoenix mode (default behaviour).
    if args.command is None:
        return cmd_run_pheonix(args)

    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())

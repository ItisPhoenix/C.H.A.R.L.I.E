"""
Pure-function code worker for CODE_SMALL extensions.

Security model:
  - Static enforcement is at the AST level: only imports from _ALLOWED_IMPORTS
    pass static analysis.  Any other import, or any disallowed call pattern,
    is rejected before a byte is written to disk.
  - Execution happens in a subprocess the OS contains: a Windows job object
    (kill-on-close, one active process, no breakaway, job memory and CPU
    ceilings), a minimal environment, a private temporary working directory,
    and bytecode writing disabled.
  - The job object is the OS boundary; it does not deny filesystem writes or
    network access, so the AST allow-list stays load-bearing.

Allowed stdlib scope (pure-computation only — no filesystem mutation, no
network, no OS mutation, no process spawning):

    math, cmath, decimal, fractions, statistics, numbers
    random                      (seed-able, deterministic)
    json, re, struct, binascii, base64, codecs
    datetime, calendar, time    (read-only: sleep is allowed but capped by timeout)
    typing, types, abc, copy, functools, itertools, operator, contextlib
    collections, heapq, bisect, queue, array
    string, textwrap, difflib, unicodedata
    enum, dataclasses, weakref, pprint, reprlib
    hashlib, hmac, secrets      (pure crypto utilities)
    from io import BytesIO / StringIO only (validated by AST import check)

IMPORTANT: os, sys, subprocess, socket, shutil, glob, pathlib, importlib,
inspect, gc, ctypes, cffi, and all third-party packages are NOT on the
allow-list and are rejected at the AST level.
"""

from __future__ import annotations

import ast
import ctypes
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from ctypes import wintypes
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("charlie.self_extension.code_worker")

# ── Allow-list: only these top-level module roots may be imported ──────────

_ALLOWED_IMPORTS: frozenset[str] = frozenset(
    {
        # Numeric
        "math",
        "cmath",
        "decimal",
        "fractions",
        "statistics",
        "numbers",
        "random",
        # Serialisation
        "json",
        "re",
        "struct",
        "binascii",
        "base64",
        "codecs",
        # Date/time (read-only)
        "datetime",
        "calendar",
        "time",
        # Type system / functional
        "typing",
        "types",
        "abc",
        "copy",
        "functools",
        "itertools",
        "operator",
        "contextlib",
        # Data structures
        "collections",
        "heapq",
        "bisect",
        "queue",
        "array",
        # String helpers
        "string",
        "textwrap",
        "difflib",
        "unicodedata",
        # Class utilities
        "enum",
        "dataclasses",
        "weakref",
        "pprint",
        "reprlib",
        # Crypto (pure)
        "hashlib",
        "hmac",
        "secrets",
    }
)

# io submodules allowed via from io import ...
_ALLOWED_IO_NAMES: frozenset[str] = frozenset({"BytesIO", "StringIO"})

# ── Calls that are always rejected regardless of import ────────────────────

_DISALLOWED_CALLS: frozenset[str] = frozenset(
    {
        "exec",
        "eval",
        "compile",
        "__import__",
        "open",
        "input",
        "print",
        "getattr",
        "setattr",
        "delattr",
        "globals",
        "locals",
        "vars",
        "dir",
        "type",
        "object",
        "super",
    }
)

_DISALLOWED_NAMES: frozenset[str] = _DISALLOWED_CALLS | {
    "__builtins__",
    "__loader__",
    "__spec__",
}

# ── Execution timeout ──────────────────────────────────────────────────────

_EXEC_TIMEOUT_SECONDS: int = 10

# ── Windows job object containment ─────────────────────────────────────────
# Flag values are from winnt.h / MS Learn "JOBOBJECT_BASIC_LIMIT_INFORMATION".

_JOB_OBJECT_LIMIT_JOB_TIME = 0x00000004
_JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
_JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
# JOB_OBJECT_LIMIT_BREAKAWAY_OK (0x800) / SILENT_BREAKAWAY_OK (0x1000) are
# deliberately NOT set: CREATE_BREAKAWAY_FROM_JOB only succeeds when the job
# carries BREAKAWAY_OK, so children cannot break away.  There is no
# JOB_OBJECT_LIMIT_NO_BREAKAWAY constant — omitting both flags *is* the
# secure state.
_JOB_LIMIT_FLAGS = (
    _JOB_OBJECT_LIMIT_ACTIVE_PROCESS
    | _JOB_OBJECT_LIMIT_JOB_TIME
    | _JOB_OBJECT_LIMIT_JOB_MEMORY
    | _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
)
_INFOCLASS_BASIC_LIMIT = 2
_INFOCLASS_EXTENDED_LIMIT = 9
_JOB_MEMORY_LIMIT_BYTES = 1024 * 1024 * 1024  # committed memory for the whole job


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_ulonglong)
        for name in (
            "ReadOperationCount",
            "WriteOperationCount",
            "OtherOperationCount",
            "ReadTransferCount",
            "WriteTransferCount",
            "OtherTransferCount",
        )
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _kernel32() -> Any:
    """kernel32 with 64-bit-safe handle restypes."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.SetInformationJobObject.restype = wintypes.BOOL
    k32.AssignProcessToJobObject.restype = wintypes.BOOL
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k32.QueryInformationJobObject.restype = wintypes.BOOL
    k32.TerminateJobObject.restype = wintypes.BOOL
    k32.CloseHandle.restype = wintypes.BOOL
    return k32


def _open_contained_job(k32: Any, timeout: int) -> int:
    """
    Create the containing job and verify every limit actually took effect.

    JOB_TIME is a user-mode CPU ceiling in 100ns ticks; the default
    end-of-job-time action terminates the job.
    """
    job = k32.CreateJobObjectW(None, None)
    if not job:
        raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
    info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = _JOB_LIMIT_FLAGS
    info.BasicLimitInformation.ActiveProcessLimit = 1
    info.BasicLimitInformation.PerJobUserTimeLimit = int(timeout) * 10_000_000
    info.JobMemoryLimit = _JOB_MEMORY_LIMIT_BYTES
    if not k32.SetInformationJobObject(
        job,
        _INFOCLASS_EXTENDED_LIMIT,
        ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        err = ctypes.get_last_error()
        k32.CloseHandle(job)
        raise OSError(err, "SetInformationJobObject failed")

    # Read the limits back so a struct layout mistake fails closed instead of
    # silently leaving the child uncontained.
    readback = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    returned = wintypes.DWORD()
    ok = k32.QueryInformationJobObject(
        job,
        _INFOCLASS_EXTENDED_LIMIT,
        ctypes.byref(readback),
        ctypes.sizeof(readback),
        ctypes.byref(returned),
    )
    if not ok or (
        readback.BasicLimitInformation.LimitFlags & _JOB_LIMIT_FLAGS
        != _JOB_LIMIT_FLAGS
        or readback.BasicLimitInformation.ActiveProcessLimit != 1
        or readback.BasicLimitInformation.PerJobUserTimeLimit
        != info.BasicLimitInformation.PerJobUserTimeLimit
        or readback.JobMemoryLimit != _JOB_MEMORY_LIMIT_BYTES
    ):
        err = ctypes.get_last_error()
        k32.CloseHandle(job)
        raise OSError(err, "job object limits did not take effect")
    return int(job)


# ─────────────────────────────────────────────────────────────────────────────
# Public exception types
# ─────────────────────────────────────────────────────────────────────────────


class ASTAllowListError(ValueError):
    """Raised when source code references disallowed imports or calls."""


# ─────────────────────────────────────────────────────────────────────────────
# AST allow-list validation
# ─────────────────────────────────────────────────────────────────────────────


def validate_ast_allow_list(source: str, tool_name: str) -> ast.AST:
    """
    Parse *source* and verify it conforms to the pure-function allow-list.

    Raises ASTAllowListError on the first violation found.
    Returns the parsed AST tree on success.
    """
    try:
        tree = ast.parse(source, filename=f"<{tool_name}>", mode="exec")
    except SyntaxError as exc:
        raise ASTAllowListError(f"Syntax error in generated code: {exc}") from exc

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and (
            node.id.startswith("__") or node.id in _DISALLOWED_NAMES
        ):
            raise ASTAllowListError(
                f"Disallowed reflective name '{node.id}' in CODE_SMALL extension."
            )

        # ── Import statements ──────────────────────────────────────────────
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root not in _ALLOWED_IMPORTS:
                    raise ASTAllowListError(
                        f"Disallowed import '{alias.name}': not on the pure-function allow-list. "
                        f"Extensions needing OS/network/filesystem access must use "
                        f"Charlie's policy-controlled capabilities instead."
                    )

        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root == "io":
                for alias in node.names:
                    if alias.name not in _ALLOWED_IO_NAMES:
                        raise ASTAllowListError(
                            f"Disallowed import 'io.{alias.name}': only BytesIO/StringIO permitted "
                            f"in CODE_SMALL extensions."
                        )
            elif root not in _ALLOWED_IMPORTS:
                raise ASTAllowListError(
                    f"Disallowed import '{node.module}': not on the pure-function allow-list. "
                    f"Extensions needing OS/network/filesystem access must use "
                    f"Charlie's policy-controlled capabilities instead."
                )

        # ── Call expressions ───────────────────────────────────────────────
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id in _DISALLOWED_CALLS:
                    raise ASTAllowListError(
                        f"Disallowed call '{node.func.id}': not permitted in "
                        f"CODE_SMALL extensions."
                    )

        # ── Attribute access (non-call) ─────────────────────────────────
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__") or node.attr in _DISALLOWED_CALLS:
                raise ASTAllowListError(
                    f"Reference to disallowed attribute '.{node.attr}' "
                    f"is not permitted in CODE_SMALL extensions."
                )

    return tree


# ─────────────────────────────────────────────────────────────────────────────
# Subprocess runner
# ─────────────────────────────────────────────────────────────────────────────


def run_worker(
    module_path: Path,
    func_name: str,
    test_inputs: Optional[Dict[str, Any]],
    expected_output: Optional[Any] = None,
    timeout: int = _EXEC_TIMEOUT_SECONDS,
) -> Tuple[bool, Any, str]:
    """
    Execute *func_name* from *module_path* in an isolated subprocess.

    If *test_inputs* is None or empty, a zero-argument smoke call is performed
    to verify the module imports and the function is callable.  No CODE_SMALL
    extension may pass on static analysis alone.

    Returns (success, actual_output, error_message).
    """
    try:
        source = module_path.read_text(encoding="utf-8")
    except OSError as exc:
        return False, None, f"Cannot read module for validation: {exc}"

    try:
        validate_ast_allow_list(source, func_name)
    except ASTAllowListError as exc:
        return False, None, f"Pre-execution validation failed: {exc}"

    # Build the test call — zero-arg smoke test when no inputs provided.
    if not test_inputs:
        call_expr = "func()"
        inputs_repr = "{}"
    else:
        call_expr = "func(**inputs)"
        inputs_repr = json.dumps(test_inputs)

    # The child re-reads and validates the file immediately before execution.
    # It then executes that exact validated source string, eliminating the
    # parent-validation/write/execution TOCTOU gap.
    repo_root = str(Path(__file__).resolve().parents[2])
    runner = textwrap.dedent(
        f"""\
import sys, json, traceback, types
sys.path.insert(0, {repo_root!r})
from charlie.self_extension.code_worker import validate_ast_allow_list

module_path = {str(module_path)!r}
func_name   = {func_name!r}
inputs      = json.loads({inputs_repr!r})

try:
    with open(module_path, "r", encoding="utf-8") as source_file:
        source = source_file.read()
    validate_ast_allow_list(source, func_name)
except Exception as exc:
    sys.stdout.write(json.dumps({{"error": f"Validation failed: {{exc}}"}}))
    sys.exit(1)

try:
    mod = types.ModuleType("_ext_worker")
    mod.__file__ = module_path
    exec(compile(source, module_path, "exec"), mod.__dict__)
except Exception as exc:
    sys.stdout.write(json.dumps({{"error": f"Module load failed: {{exc}}"}}))
    sys.exit(1)

func = getattr(mod, func_name, None)
if not callable(func):
    sys.stdout.write(json.dumps({{"error": f"No callable '{{func_name}}' in module"}}))
    sys.exit(1)

try:
    result = {call_expr}
    sys.stdout.write(json.dumps({{"result": result}}))
except Exception as exc:
    sys.stdout.write(json.dumps({{"error": str(exc), "trace": traceback.format_exc()}}))
    sys.exit(1)
"""
    )

    # Minimal environment — no HOME, no CHARLIE_*, no secrets.
    minimal_env: Dict[str, str] = {
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "PYTHONPATH": "",
    }

    k32 = _kernel32()
    try:
        job = _open_contained_job(k32, timeout)
    except (AttributeError, OSError) as exc:
        return False, None, f"OS containment unavailable, refusing to execute: {exc}"

    workdir = tempfile.mkdtemp(prefix="charlie-code-worker-")
    proc: Optional[subprocess.Popen] = None
    try:
        proc = subprocess.Popen(
            # -B: the contained child never writes .pyc files anywhere.
            [sys.executable, "-B", "-c", runner],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=minimal_env,
            cwd=workdir,
        )
        # Pre-assignment window: stdlib Popen closes the primary thread handle,
        # so CREATE_SUSPENDED + ResumeThread needs a hand-rolled CreateProcessW
        # and is not reachable here.  Until the assignment below succeeds the
        # child is uncontained; in that window it runs only interpreter startup
        # plus the charlie / charlie.self_extension import, and reaches the
        # extension source only afterwards.
        handle = int(getattr(proc, "_handle", 0) or 0)
        if not handle or not k32.AssignProcessToJobObject(job, handle):
            err = ctypes.get_last_error()
            proc.kill()
            proc.wait()
            logger.warning(
                "Job assignment failed (%s): killed the direct child only. Any "
                "grandchild it spawned during the pre-assignment window is "
                "orphaned with no job containment.",
                err,
            )
            return (
                False,
                None,
                f"Containment unavailable (job assignment failed: {err}), "
                f"refusing to execute.",
            )
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            k32.TerminateJobObject(job, 1)
            try:
                proc.communicate()
            except Exception:
                proc.wait()
            return False, None, f"Worker timed out after {timeout}s."
    except Exception as exc:
        return False, None, f"Worker launch failed: {exc}"
    finally:
        if proc is not None and proc.poll() is None:
            k32.TerminateJobObject(job, 1)
            proc.wait()
        k32.CloseHandle(job)
        shutil.rmtree(workdir, ignore_errors=True)

    stdout = (stdout or "").strip()[:65536]   # cap at 64 KB
    stderr = (stderr or "").strip()[:4096]

    if proc.returncode != 0 or not stdout:
        detail = stderr or stdout or "(no output)"
        return False, None, f"Worker exited {proc.returncode}: {detail}"

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return False, None, f"Worker returned non-JSON: {stdout[:200]!r}"

    if "error" in payload:
        return False, None, f"Worker raised: {payload['error']}"

    actual = payload.get("result")
    if expected_output is not None and actual != expected_output:
        return (
            False,
            actual,
            f"Expected {expected_output!r}, got {actual!r}",
        )

    return True, actual, ""

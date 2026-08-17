"""
Pure-function code worker for CODE_SMALL extensions.

Security model:
  - Primary enforcement is at the AST level: only imports from _ALLOWED_IMPORTS
    pass static analysis.  Any other import, or any disallowed call pattern,
    is rejected before a byte is written to disk.
  - Execution happens in an isolated subprocess with a minimal environment.
  - The worker does NOT monkeypatch builtins or use RestrictedPython.
    AST analysis is the authoritative boundary; the subprocess merely confirms
    runtime behaviour under test inputs.

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
    pathlib.PurePath *names only* — PurePath is safe; Path mutation is NOT
    io                          (BytesIO / StringIO only — validated by AST)

IMPORTANT: os, sys, subprocess, socket, shutil, glob, pathlib.Path (write
mode), importlib, inspect, gc, ctypes, cffi, and all third-party packages are
NOT on the allow-list and are rejected at the AST level.
"""

from __future__ import annotations

import ast
import json
import logging
import os
import subprocess
import sys
import textwrap
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
        # Pure-path (no mutation)
        "pathlib",
        # In-memory I/O only (guarded by AST call check)
        "io",
    }
)

# ── Calls that are always rejected regardless of import ────────────────────

_DISALLOWED_CALLS: frozenset[str] = frozenset(
    {
        "exec",
        "eval",
        "compile",
        "__import__",
        "open",           # filesystem open — must not appear at call level
        "input",          # stdin
        "print",          # stdout — generator code should return values
    }
)

# ── pathlib attributes that mutate the filesystem ──────────────────────────
# Accessing Path(<anything>).<attr> where attr is in this set → rejected.

_PATHLIB_MUTATION_ATTRS: frozenset[str] = frozenset(
    {
        "write_text",
        "write_bytes",
        "unlink",
        "rmdir",
        "mkdir",
        "rename",
        "replace",
        "symlink_to",
        "hardlink_to",
        "touch",
        "chmod",
    }
)

# ── io attributes that open real files ────────────────────────────────────

_IO_DISALLOWED_ATTRS: frozenset[str] = frozenset({"FileIO", "open"})

# ── Execution timeout ──────────────────────────────────────────────────────

_EXEC_TIMEOUT_SECONDS: int = 10


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
            if root not in _ALLOWED_IMPORTS:
                raise ASTAllowListError(
                    f"Disallowed import '{node.module}': not on the pure-function allow-list. "
                    f"Extensions needing OS/network/filesystem access must use "
                    f"Charlie's policy-controlled capabilities instead."
                )
            # Guard pathlib mutation attributes
            if root == "pathlib":
                for alias in node.names:
                    if alias.name in _PATHLIB_MUTATION_ATTRS:
                        raise ASTAllowListError(
                            f"Disallowed import 'pathlib.{alias.name}': filesystem-mutation operation "
                            f"is not permitted in CODE_SMALL extensions."
                        )
            # Guard io real-file open
            if root == "io":
                for alias in node.names:
                    if alias.name in _IO_DISALLOWED_ATTRS:
                        raise ASTAllowListError(
                            f"Disallowed import 'io.{alias.name}': opens real files and is not permitted "
                            f"in CODE_SMALL extensions."
                        )

        # ── Call expressions ───────────────────────────────────────────────
        elif isinstance(node, ast.Call):
            # Direct builtin call: exec(...), eval(...), open(...), etc.
            if isinstance(node.func, ast.Name):
                if node.func.id in _DISALLOWED_CALLS:
                    raise ASTAllowListError(
                        f"Disallowed call '{node.func.id}': not permitted in "
                        f"CODE_SMALL extensions."
                    )

            # Attribute access: path_obj.write_text(...), io.open(...), etc.
            elif isinstance(node.func, ast.Attribute):
                attr = node.func.attr
                if attr in _PATHLIB_MUTATION_ATTRS:
                    raise ASTAllowListError(
                        f"Disallowed call '.{attr}': filesystem mutation is not permitted "
                        f"in CODE_SMALL extensions."
                    )
                if attr in _IO_DISALLOWED_ATTRS:
                    raise ASTAllowListError(
                        f"Disallowed call 'io.{attr}': real file I/O is not permitted "
                        f"in CODE_SMALL extensions."
                    )

        # ── Attribute access (non-call) ─────────────────────────────────
        elif isinstance(node, ast.Attribute):
            if node.attr in _PATHLIB_MUTATION_ATTRS:
                raise ASTAllowListError(
                    f"Reference to filesystem-mutation attribute '.{node.attr}' "
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
    # Build the test call — zero-arg smoke test when no inputs provided.
    if not test_inputs:
        call_expr = "func()"
        inputs_repr = "{}"
    else:
        call_expr = "func(**inputs)"
        inputs_repr = json.dumps(test_inputs)

    # The runner script uses importlib to load the module by file path.
    # This is safe because: (a) the module has already passed AST allow-list
    # validation; (b) the subprocess runs with a stripped environment.
    runner = textwrap.dedent(
        f"""\
import sys, json, importlib.util, traceback

module_path = {str(module_path)!r}
func_name   = {func_name!r}
inputs      = json.loads({inputs_repr!r})

try:
    spec = importlib.util.spec_from_file_location("_ext_worker", module_path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
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

    try:
        proc = subprocess.run(
            [sys.executable, "-c", runner],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=minimal_env,
        )
    except subprocess.TimeoutExpired:
        return False, None, f"Worker timed out after {timeout}s."
    except Exception as exc:
        return False, None, f"Worker launch failed: {exc}"

    stdout = (proc.stdout or "").strip()[:65536]   # cap at 64 KB
    stderr = (proc.stderr or "").strip()[:4096]

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

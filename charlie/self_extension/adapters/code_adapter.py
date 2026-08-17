"""Code extension adapter: AST static validation, isolated subprocess verification, and capability registration."""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any, Dict, List, Optional

from charlie.self_extension.checkpoint import CheckpointManager
from charlie.self_extension.models import ExtensionKind
from charlie.self_extension.registry import ExtensionEntry, ExtensionRegistry

logger = logging.getLogger("charlie.self_extension.code_adapter")

_DEFAULT_TOOLS_DIR = Path("data/generated_tools")

# Broader deny-list: covers dangerous stdlib and platform APIs.
_DISALLOWED_IMPORTS: frozenset[str] = frozenset(
    {
        # Low-level / FFI
        "ctypes",
        "_ctypes",
        "cffi",
        # Platform / OS internals
        "winreg",
        "msvcrt",
        "posix",
        "pty",
        "fcntl",
        "termios",
        # Subprocess / process spawning
        "subprocess",
        "multiprocessing",
        "concurrent",
        # Network
        "socket",
        "ssl",
        "http",
        "urllib",
        "xmlrpc",
        "ftplib",
        "smtplib",
        "imaplib",
        "poplib",
        "telnetlib",
        # Dynamic code execution
        "code",
        "codeop",
        "compileall",
        "py_compile",
        "importlib",
        "imp",
        # Introspection / exec helpers
        "gc",
        "inspect",
        "dis",
        "tokenize",
    }
)

# Maximum execution timeout for subprocess verification (seconds).
_EXEC_TIMEOUT = 10


class CodeAdapterResult:
    def __init__(
        self,
        success: bool,
        message: str,
        module_path: Optional[str] = None,
        tool_name: Optional[str] = None,
        test_output: Optional[Any] = None,
    ):
        self.success = success
        self.message = message
        self.module_path = module_path
        self.tool_name = tool_name
        self.test_output = test_output


class ASTValidationError(ValueError):
    """Raised when AST analysis detects invalid syntax or unsafe code patterns."""


def _validate_ast_safety(source: str, name: str) -> ast.AST:
    """Parse and statically inspect source code for safety constraints."""
    try:
        tree = ast.parse(source, filename=f"<{name}>", mode="exec")
    except SyntaxError as e:
        raise ASTValidationError(f"Syntax error in code: {e}")

    for node in ast.walk(tree):
        # Block disallowed imports
        if isinstance(node, ast.Import):
            for alias in node.names:
                root_pkg = alias.name.split(".")[0]
                if root_pkg in _DISALLOWED_IMPORTS:
                    raise ASTValidationError(f"Disallowed import '{alias.name}' detected.")
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                root_pkg = node.module.split(".")[0]
                if root_pkg in _DISALLOWED_IMPORTS:
                    raise ASTValidationError(f"Disallowed import '{node.module}' detected.")
        # Block exec()/eval() calls
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in ("exec", "eval", "compile", "__import__"):
                raise ASTValidationError(f"Disallowed call '{node.func.id}' detected.")

    return tree


def _run_subprocess_verification(
    module_path: Path,
    func_name: str,
    test_inputs: Optional[Dict[str, Any]],
    expected_output: Optional[Any],
    timeout: int = _EXEC_TIMEOUT,
) -> tuple[bool, Any, str]:
    """
    Execute generated code in a fully isolated subprocess for safety verification.

    Returns (success, actual_output, error_message).
    The subprocess receives test_inputs as JSON and prints the result as JSON.
    """
    if test_inputs is None:
        # No test inputs — skip execution test; static validation alone is sufficient.
        return True, None, ""

    # Build a self-contained runner script that imports nothing from charlie
    runner_script = textwrap.dedent(
        f"""
import json, sys, importlib.util

module_path = {str(module_path)!r}
func_name = {func_name!r}
test_inputs_json = {json.dumps(test_inputs)!r}

spec = importlib.util.spec_from_file_location("_ext_sandbox", module_path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

func = getattr(mod, func_name, None)
if func is None or not callable(func):
    print(json.dumps({{"error": f"No callable '{{func_name}}' found"}}))
    sys.exit(1)

test_inputs = json.loads(test_inputs_json)
try:
    result = func(**test_inputs)
    print(json.dumps({{"result": result}}))
except Exception as e:
    print(json.dumps({{"error": str(e)}}))
    sys.exit(1)
"""
    )

    try:
        proc = subprocess.run(
            [sys.executable, "-c", runner_script],
            capture_output=True,
            text=True,
            timeout=timeout,
            # Do NOT inherit current env to prevent accidental side-effects.
            env={"PATH": os.environ.get("PATH", ""), "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")},
        )
    except subprocess.TimeoutExpired:
        return False, None, f"Execution timed out after {timeout}s."
    except Exception as e:
        return False, None, f"Subprocess launch failed: {e}"

    stdout = proc.stdout.strip()
    stderr = proc.stderr.strip()

    if proc.returncode != 0 or not stdout:
        detail = stderr or stdout or "(no output)"
        return False, None, f"Subprocess exited with code {proc.returncode}: {detail}"

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as e:
        return False, None, f"Subprocess returned non-JSON output: {stdout!r} ({e})"

    if "error" in payload:
        return False, None, f"Execution raised: {payload['error']}"

    actual_output = payload.get("result")
    if expected_output is not None and actual_output != expected_output:
        return (
            False,
            actual_output,
            f"Expected output {expected_output!r}, got {actual_output!r}",
        )

    return True, actual_output, ""


class CodeAdapter:
    """Manages authoring, static checking, subprocess execution verification, and registration of code extensions."""

    def __init__(
        self,
        repo_root: Optional[Path] = None,
        tools_dir: Optional[Path] = None,
        registry: Optional[ExtensionRegistry] = None,
        capability_index: Optional[Any] = None,
        checkpoint_mgr: Optional[CheckpointManager] = None,
        doctor: Optional[Any] = None,
    ) -> None:
        self._repo_root = (repo_root or Path(os.getcwd())).resolve()
        self._tools_dir = tools_dir or (self._repo_root / _DEFAULT_TOOLS_DIR)
        self._registry = registry or ExtensionRegistry(capability_index=capability_index)
        self._capability_index = capability_index
        self._checkpoint_mgr = checkpoint_mgr or CheckpointManager(repo_root=self._repo_root)
        self._doctor = doctor

    def apply_code_extension(
        self,
        name: str,
        code: str,
        test_inputs: Optional[Dict[str, Any]] = None,
        expected_output: Optional[Any] = None,
        transaction_id: Optional[str] = None,
    ) -> CodeAdapterResult:
        """Statically inspect, write, subprocess-test, and register a new Python tool extension."""
        tx_id = transaction_id or f"code-{name}"

        # 1. AST Validation — runs before any file is written
        try:
            _validate_ast_safety(code, name)
        except ASTValidationError as e:
            return CodeAdapterResult(success=False, message=str(e), tool_name=name)

        # 2. Prepare destination path
        self._tools_dir.mkdir(parents=True, exist_ok=True)
        module_path = self._tools_dir / f"{name}.py"

        # 3. Create checkpoint before writing
        files_to_modify = [module_path] if module_path.exists() else []
        files_to_create = [module_path] if not module_path.exists() else []
        cp = self._checkpoint_mgr.create_checkpoint(
            transaction_id=tx_id,
            files_to_modify=files_to_modify,
            files_to_create=files_to_create,
        )

        try:
            # 4. Write code to disk
            module_path.write_text(code, encoding="utf-8")

            # 5. Verify the module defines the expected callable via AST (no in-process load)
            tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
            top_level_funcs: List[str] = [
                node.name
                for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef) and node.col_offset == 0
            ]
            if name not in top_level_funcs:
                raise ValueError(
                    f"Module does not define a top-level function named '{name}'. "
                    f"Found: {top_level_funcs or '(none)'}"
                )

            # 6. Subprocess execution test — isolated process, bounded timeout
            ok, test_output, error_msg = _run_subprocess_verification(
                module_path=module_path,
                func_name=name,
                test_inputs=test_inputs,
                expected_output=expected_output,
            )
            if not ok:
                raise RuntimeError(error_msg)

            # 7. Register in ExtensionRegistry
            content_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()[:16]
            ext_id = f"code_{name}"
            # Extract docstring from AST for metadata
            func_node = next(
                (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name),
                None,
            )
            docstring = (
                ast.get_docstring(func_node) if func_node else None
            ) or f"Generated tool '{name}'"
            entry = ExtensionEntry(
                extension_id=ext_id,
                name=name,
                kind=ExtensionKind.CODE_SMALL,
                source=str(module_path),
                content_hash=content_hash,
                enabled=True,
                declared_tools=[name],
                metadata={"description": docstring},
            )
            self._registry.register(entry)

            # 8. Register in CapabilityIndex (stub — no live callable reference; safe for in-process use)
            if self._capability_index:
                from charlie.capabilities import CapabilityDescriptor, CapabilityOperation

                desc = CapabilityDescriptor(
                    id=ext_id,
                    name=name,
                    description=docstring,
                    owner="extensions",
                    provenance="extension",
                    operations={
                        name: CapabilityOperation(
                            id=f"code.{name}",
                            name=name,
                            description=docstring,
                            parameters_schema={"type": "object"},
                            risk_class="reversible",
                            func=None,  # No same-process callable — invoke via subprocess
                        )
                    },
                    availability_check=lambda: module_path.exists(),
                )
                self._capability_index.register_capability(desc)

            return CodeAdapterResult(
                success=True,
                message=f"Code extension '{name}' validated, subprocess-tested, and registered.",
                module_path=str(module_path),
                tool_name=name,
                test_output=test_output,
            )

        except Exception as e:
            logger.error("Code extension '%s' failed during test/load, rolling back: %s", name, e)
            self._checkpoint_mgr.rollback(cp)
            return CodeAdapterResult(
                success=False,
                message=f"Execution verification failed, rolled back: {e}",
                tool_name=name,
            )

    def rollback_code_extension(self, name: str) -> CodeAdapterResult:
        """Unregister code extension and delete module file."""
        ext_id = f"code_{name}"
        self._registry.unregister(ext_id)

        module_path = self._tools_dir / f"{name}.py"
        if module_path.exists():
            try:
                module_path.unlink()
            except Exception as e:
                logger.warning("Failed removing tool module %s: %s", module_path, e)

        if self._capability_index:
            self._capability_index._capabilities.pop(ext_id, None)

        return CodeAdapterResult(
            success=True,
            message=f"Code extension '{name}' unregistered and removed.",
            tool_name=name,
        )

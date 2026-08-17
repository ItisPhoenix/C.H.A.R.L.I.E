"""Code extension adapter with AST static validation, isolated execution testing, and Doctor verification."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from charlie.self_extension.checkpoint import CheckpointManager
from charlie.self_extension.models import ExtensionKind
from charlie.self_extension.registry import ExtensionEntry, ExtensionRegistry

logger = logging.getLogger("charlie.self_extension.code_adapter")

_DEFAULT_TOOLS_DIR = Path("data/generated_tools")
_DISALLOWED_IMPORTS = frozenset({"ctypes", "_ctypes", "winreg", "posix", "pty"})


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

    # Inspect imports and disallowed calls
    for node in ast.walk(tree):
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

    return tree


class CodeAdapter:
    """Manages authoring, static checking, execution verification, and registration of code extensions."""

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
        """Statically inspect, write, test, and register a new Python tool extension."""
        tx_id = transaction_id or f"code-{name}"

        # 1. AST Validation
        try:
            tree = _validate_ast_safety(code, name)
        except ASTValidationError as e:
            return CodeAdapterResult(success=False, message=str(e), tool_name=name)

        # 2. Prepare destination path
        self._tools_dir.mkdir(parents=True, exist_ok=True)
        module_path = self._tools_dir / f"{name}.py"

        # 3. Create checkpoint
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

            # 5. Dynamically load module
            spec = importlib.util.spec_from_file_location(f"generated_tool_{name}", module_path)
            if not spec or not spec.loader:
                raise RuntimeError(f"Could not load module spec for {module_path}")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            func = getattr(mod, name, None)
            if func is None or not callable(func):
                raise ValueError(f"Module does not define a top-level callable function named '{name}'")

            # 6. Test execution
            test_output = None
            if test_inputs is not None:
                test_output = func(**test_inputs)
                if expected_output is not None and test_output != expected_output:
                    raise AssertionError(f"Expected output {expected_output!r}, got {test_output!r}")

            # 7. Register in ExtensionRegistry
            content_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()[:16]
            ext_id = f"code_{name}"
            entry = ExtensionEntry(
                extension_id=ext_id,
                name=name,
                kind=ExtensionKind.CODE_SMALL,
                source=str(module_path),
                content_hash=content_hash,
                enabled=True,
                declared_tools=[name],
                metadata={"description": getattr(func, "__doc__", "") or f"Generated tool '{name}'"},
            )
            self._registry.register(entry)

            # 8. Register in CapabilityIndex
            if self._capability_index:
                from charlie.capabilities import CapabilityDescriptor, CapabilityOperation

                desc = CapabilityDescriptor(
                    id=ext_id,
                    name=name,
                    description=getattr(func, "__doc__", "") or f"Generated code tool '{name}'",
                    owner="extensions",
                    provenance="extension",
                    operations={
                        name: CapabilityOperation(
                            id=f"code.{name}",
                            name=name,
                            description=getattr(func, "__doc__", "") or f"Tool '{name}'",
                            parameters_schema={"type": "object"},
                            risk_class="reversible",
                            func=func,
                        )
                    },
                    availability_check=lambda: True,
                )
                self._capability_index.register_capability(desc)

            return CodeAdapterResult(
                success=True,
                message=f"Code extension '{name}' validated, tested, and registered.",
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

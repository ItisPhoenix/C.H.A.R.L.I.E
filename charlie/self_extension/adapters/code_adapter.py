"""Code extension adapter: AST allow-list validation, subprocess worker verification, and capability registration."""

from __future__ import annotations

import ast
import hashlib
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from charlie.self_extension.checkpoint import CheckpointManager
from charlie.self_extension.code_worker import (
    ASTAllowListError,
    run_worker,
    validate_ast_allow_list,
)
from charlie.self_extension.models import ExtensionCheckpoint, ExtensionKind
from charlie.self_extension.registry import ExtensionEntry, ExtensionRegistry
from charlie.self_extension.worktree_guard import WorktreeConflictError, WorktreeGuard

logger = logging.getLogger("charlie.self_extension.code_adapter")

_DEFAULT_TOOLS_DIR = Path("data/generated_tools")

ASTValidationError = ASTAllowListError
_validate_ast_safety = validate_ast_allow_list


class CodeAdapterResult:
    def __init__(
        self,
        success: bool,
        message: str,
        module_path: Optional[str] = None,
        tool_name: Optional[str] = None,
        test_output: Optional[Any] = None,
        checkpoint: Optional[ExtensionCheckpoint] = None,
    ):
        self.success = success
        self.message = message
        self.module_path = module_path
        self.tool_name = tool_name
        self.test_output = test_output
        self.checkpoint = checkpoint


def _run_subprocess_verification(
    module_path: Path,
    func_name: str,
    test_inputs: Optional[Dict[str, Any]],
    expected_output: Optional[Any] = None,
    timeout: int = 10,
) -> tuple[bool, Any, str]:
    return run_worker(
        module_path=module_path,
        func_name=func_name,
        test_inputs=test_inputs,
        expected_output=expected_output,
        timeout=timeout,
    )


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

    def _check_baseline_before_checkpoint(self, module_path: Path, name: str) -> None:
        ext_id = f"code_{name}"
        existing = self._registry.get(ext_id)
        if not module_path.exists():
            return
        WorktreeGuard.check_repository_baseline(
            module_path,
            self._repo_root,
            fallback_hash=existing.content_hash if existing is not None else None,
        )

    def apply_code_extension(
        self,
        name: str,
        code: str,
        test_inputs: Optional[Dict[str, Any]] = None,
        expected_output: Optional[Any] = None,
        transaction_id: Optional[str] = None,
    ) -> CodeAdapterResult:
        """Validate (AST allow-list), checkpoint, guard, write, worker test, and register."""
        tx_id = transaction_id or f"code-{name}"

        try:
            tree = validate_ast_allow_list(code, name)
        except ASTAllowListError as exc:
            return CodeAdapterResult(success=False, message=str(exc), tool_name=name)

        self._tools_dir.mkdir(parents=True, exist_ok=True)
        module_path = self._tools_dir / f"{name}.py"

        try:
            self._check_baseline_before_checkpoint(module_path, name)
        except WorktreeConflictError as exc:
            return CodeAdapterResult(success=False, message=str(exc), tool_name=name)

        files_to_modify = [module_path] if module_path.exists() else []
        files_to_create = [module_path] if not module_path.exists() else []
        cp = self._checkpoint_mgr.create_checkpoint(
            transaction_id=tx_id,
            files_to_modify=files_to_modify,
            files_to_create=files_to_create,
        )

        try:
            self._checkpoint_mgr.check_write_conflict(module_path, cp)
            module_path.write_text(code, encoding="utf-8")
            self._checkpoint_mgr.record_postimage(tx_id, module_path, checkpoint=cp)

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

            ok, test_output, error_msg = run_worker(
                module_path=module_path,
                func_name=name,
                test_inputs=test_inputs,
                expected_output=expected_output,
            )
            if not ok:
                raise RuntimeError(error_msg)

            content_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()[:16]
            ext_id = f"code_{name}"
            func_node = next(
                (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name),
                None,
            )
            docstring = (ast.get_docstring(func_node) if func_node else None) or f"Generated tool '{name}'"
            entry = ExtensionEntry(
                extension_id=ext_id,
                name=name,
                kind=ExtensionKind.CODE_SMALL,
                source=str(module_path),
                content_hash=content_hash,
                enabled=True,
                declared_tools=[name],
                metadata={"description": docstring, "module_path": str(module_path)},
            )
            self._registry.register(entry)

            if self._capability_index:
                from charlie.capabilities import CapabilityDescriptor, CapabilityOperation

                _mp = module_path
                _fn = name

                def _dispatch(**kwargs: Any) -> Any:
                    ok2, out, err = run_worker(
                        module_path=_mp,
                        func_name=_fn,
                        test_inputs=kwargs or None,
                    )
                    if not ok2:
                        raise RuntimeError(err)
                    return out

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
                            func=_dispatch,
                        )
                    },
                    availability_check=lambda: module_path.exists(),
                )
                self._capability_index.register_capability(desc)

            return CodeAdapterResult(
                success=True,
                message=f"Code extension '{name}' validated, worker-tested, and registered.",
                module_path=str(module_path),
                tool_name=name,
                test_output=test_output,
                checkpoint=cp,
            )

        except Exception as exc:
            logger.error("Code extension '%s' failed, rolling back: %s", name, exc)
            rollback = self._checkpoint_mgr.rollback(cp)
            rollback_suffix = "" if rollback.success else f" rollback_failed: {rollback.message}"
            return CodeAdapterResult(
                success=False,
                message=f"Verification failed, rolled back: {exc}.{rollback_suffix}",
                tool_name=name,
                checkpoint=cp,
            )

    def rollback_code_extension(
        self,
        name: str,
        checkpoint: Optional[ExtensionCheckpoint] = None,
    ) -> CodeAdapterResult:
        ext_id = f"code_{name}"
        self._registry.unregister(ext_id)

        rollback = None
        if checkpoint is not None:
            rollback = self._checkpoint_mgr.rollback(checkpoint)
        else:
            module_path = self._tools_dir / f"{name}.py"
            if module_path.exists():
                try:
                    module_path.unlink()
                except Exception as exc:
                    logger.warning("Failed removing tool module %s: %s", module_path, exc)

        if self._capability_index:
            self._capability_index.unregister_capability(ext_id)

        return CodeAdapterResult(
            success=rollback.success if rollback is not None else True,
            message=(
                f"Code extension '{name}' rolled back."
                if rollback is None or rollback.success
                else f"Code extension '{name}' rollback_failed: {rollback.message}"
            ),
            tool_name=name,
            checkpoint=checkpoint,
        )

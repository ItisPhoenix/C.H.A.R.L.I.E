"""Worktree-safe checkpoint manager and scoped rollback engine."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from charlie.self_extension.models import ExtensionCheckpoint

logger = logging.getLogger("charlie.self_extension.checkpoint")


class CheckpointResult:
    def __init__(self, success: bool, message: str, restored_files: Optional[List[str]] = None, deleted_files: Optional[List[str]] = None):
        self.success = success
        self.message = message
        self.restored_files = restored_files or []
        self.deleted_files = deleted_files or []


class CheckpointManager:
    """Creates point-in-time pre-images of declared change paths and safely rolls back mutations."""

    def __init__(self, repo_root: Optional[Path] = None) -> None:
        self._repo_root = (repo_root or Path(os.getcwd())).resolve()

    def _resolve_safe_path(self, path: Union[Path, str]) -> Path:
        """Resolve path and verify it stays strictly within the repository boundary."""
        p = Path(path)
        if not p.is_absolute():
            p = (self._repo_root / p).resolve()
        else:
            p = p.resolve()

        try:
            p.relative_to(self._repo_root)
        except ValueError:
            raise ValueError(f"Path '{path}' escapes outside repository boundary '{self._repo_root}'")

        return p

    def create_checkpoint(
        self,
        transaction_id: str,
        files_to_modify: Optional[List[Union[Path, str]]] = None,
        files_to_create: Optional[List[Union[Path, str]]] = None,
        config_preimage: Optional[Dict[str, Any]] = None,
    ) -> ExtensionCheckpoint:
        """Capture byte pre-images of existing files to be modified and list new files."""
        preimages: Dict[str, bytes] = {}
        new_files: List[str] = []

        if files_to_modify:
            for f in files_to_modify:
                safe_path = self._resolve_safe_path(f)
                if safe_path.exists() and safe_path.is_file():
                    try:
                        preimages[str(safe_path)] = safe_path.read_bytes()
                    except Exception as e:
                        logger.error("Failed reading pre-image for %s: %s", safe_path, e)

        if files_to_create:
            for f in files_to_create:
                safe_path = self._resolve_safe_path(f)
                new_files.append(str(safe_path))

        return ExtensionCheckpoint(
            checkpoint_id=transaction_id,
            created_at=time.time(),
            affected_files_preimage=preimages,
            new_files_created=new_files,
            config_preimage=dict(config_preimage or {}),
        )

    def rollback(self, checkpoint: ExtensionCheckpoint) -> CheckpointResult:
        """Restore modified files from pre-images and delete newly created files."""
        restored: List[str] = []
        deleted: List[str] = []
        errors: List[str] = []

        # 1. Restore modified files
        for path_str, original_bytes in checkpoint.affected_files_preimage.items():
            try:
                p = Path(path_str)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(original_bytes)
                restored.append(path_str)
            except Exception as e:
                msg = f"Failed restoring file {path_str}: {e}"
                logger.error(msg)
                errors.append(msg)

        # 2. Remove newly created files
        for path_str in checkpoint.new_files_created:
            try:
                p = Path(path_str)
                if p.exists() and p.is_file():
                    p.unlink()
                    deleted.append(path_str)
            except Exception as e:
                msg = f"Failed removing created file {path_str}: {e}"
                logger.error(msg)
                errors.append(msg)

        if errors:
            return CheckpointResult(
                success=False,
                message="Rollback completed with errors: " + "; ".join(errors),
                restored_files=restored,
                deleted_files=deleted,
            )

        return CheckpointResult(
            success=True,
            message="Scoped rollback restored all affected files successfully.",
            restored_files=restored,
            deleted_files=deleted,
        )

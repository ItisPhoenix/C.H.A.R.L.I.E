"""Worktree-safe checkpoint manager and scoped rollback engine."""

from __future__ import annotations

import hashlib
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from charlie.self_extension.models import ExtensionCheckpoint
from charlie.self_extension.worktree_guard import WorktreeConflictError, WorktreeGuard

logger = logging.getLogger("charlie.self_extension.checkpoint")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CheckpointResult:
    def __init__(
        self,
        success: bool,
        message: str,
        restored_files: Optional[List[str]] = None,
        deleted_files: Optional[List[str]] = None,
        skipped_files: Optional[List[str]] = None,
    ):
        self.success = success
        self.message = message
        self.restored_files = restored_files or []
        self.deleted_files = deleted_files or []
        self.skipped_files = skipped_files or []   # files skipped due to worktree conflict


class CheckpointManager:
    """Creates point-in-time pre-images of declared change paths and safely rolls back mutations."""

    def __init__(self, repo_root: Optional[Path] = None) -> None:
        self._repo_root = (repo_root or Path(os.getcwd())).resolve()
        # transaction_id → {str(path): postimage_hash}  (populated after writes)
        self._postimages: Dict[str, Dict[str, str]] = {}

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
                    except Exception as exc:
                        logger.error("Failed reading pre-image for %s: %s", safe_path, exc)

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

    def record_postimage(self, transaction_id: str, path: Path) -> None:
        """Record the SHA-256 of a file just written by a transaction.

        Called immediately after each file write so that rollback can detect
        whether the file was externally modified after the transaction applied.
        """
        if not path.exists():
            return
        self._postimages.setdefault(transaction_id, {})[str(path)] = _sha256(path)

    def get_postimage_hash(self, transaction_id: str, path: Path) -> Optional[str]:
        return self._postimages.get(transaction_id, {}).get(str(path))

    def check_write_conflict(
        self,
        path: Path,
        checkpoint: ExtensionCheckpoint,
    ) -> None:
        """Raise WorktreeConflictError if path was modified externally since checkpoint.

        Delegates to WorktreeGuard with the captured pre-image hash.
        """
        preimage_bytes = checkpoint.affected_files_preimage.get(str(path))
        preimage_hash = (
            hashlib.sha256(preimage_bytes).hexdigest() if preimage_bytes is not None else None
        )
        WorktreeGuard.check_before_write(path, preimage_hash)

    def rollback(self, checkpoint: ExtensionCheckpoint) -> CheckpointResult:
        """Restore modified files from pre-images and delete newly created files.

        Skips (does NOT overwrite) any file that was modified after the transaction
        completed, preserving user edits.
        """
        restored: List[str] = []
        deleted: List[str] = []
        skipped: List[str] = []
        errors: List[str] = []

        tx_id = checkpoint.checkpoint_id

        # 1. Restore modified files — skip if post-image conflict detected
        for path_str, original_bytes in checkpoint.affected_files_preimage.items():
            p = Path(path_str)
            postimage_hash = self._postimages.get(tx_id, {}).get(path_str)
            try:
                WorktreeGuard.check_before_rollback(p, postimage_hash)
            except WorktreeConflictError as exc:
                logger.warning("Rollback skipping '%s' (external edit): %s", path_str, exc)
                skipped.append(path_str)
                continue

            try:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(original_bytes)
                restored.append(path_str)
            except Exception as exc:
                msg = f"Failed restoring file {path_str}: {exc}"
                logger.error(msg)
                errors.append(msg)

        # 2. Remove newly created files — skip if externally modified
        for path_str in checkpoint.new_files_created:
            p = Path(path_str)
            postimage_hash = self._postimages.get(tx_id, {}).get(path_str)
            try:
                WorktreeGuard.check_before_rollback(p, postimage_hash)
            except WorktreeConflictError as exc:
                logger.warning("Rollback skipping new file '%s' (external edit): %s", path_str, exc)
                skipped.append(path_str)
                continue

            try:
                if p.exists() and p.is_file():
                    p.unlink()
                    deleted.append(path_str)
            except Exception as exc:
                msg = f"Failed removing created file {path_str}: {exc}"
                logger.error(msg)
                errors.append(msg)

        if errors:
            return CheckpointResult(
                success=False,
                message="Rollback completed with errors: " + "; ".join(errors),
                restored_files=restored,
                deleted_files=deleted,
                skipped_files=skipped,
            )

        return CheckpointResult(
            success=True,
            message="Rollback completed. "
            + (f"{len(skipped)} file(s) skipped (user edits preserved)." if skipped else "All files restored."),
            restored_files=restored,
            deleted_files=deleted,
            skipped_files=skipped,
        )

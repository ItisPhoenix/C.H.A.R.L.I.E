"""Worktree conflict detection for safe extension writes and rollbacks."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("charlie.self_extension.worktree_guard")


class WorktreeConflictError(RuntimeError):
    """Raised when a target file has been modified outside this transaction."""


def _sha256_bytes(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class WorktreeGuard:
    """Detects external modifications to files before write or rollback."""

    @staticmethod
    def check_before_write(
        path: Path,
        preimage_hash: Optional[str],
    ) -> None:
        """Refuse write if an existing file was modified since checkpoint.

        *preimage_hash* is the SHA-256 of the file content at checkpoint time.
        If the file did not exist at checkpoint time, pass ``None``.

        Raises WorktreeConflictError if the file was externally modified.
        """
        if not path.exists():
            return  # new file — no conflict possible
        if preimage_hash is None:
            # File was not in the checkpoint — unexpected existing file.
            raise WorktreeConflictError(
                f"Target file '{path}' exists but was not captured in the checkpoint. "
                "Cannot overwrite — this may be unrelated user work."
            )
        actual = _sha256_bytes(path)
        if actual != preimage_hash:
            raise WorktreeConflictError(
                f"Target file '{path}' was modified externally since the checkpoint was taken. "
                "Refusing to overwrite. Resolve the conflict manually or discard the extension."
            )

    @staticmethod
    def check_before_rollback(
        path: Path,
        postimage_hash: Optional[str],
    ) -> None:
        """Refuse rollback restore if the file was edited after the transaction applied.

        *postimage_hash* is the SHA-256 of the content that was written by the
        transaction.  If the current content differs, the user edited the file
        after the transaction completed — rollback must NOT overwrite those edits.

        Raises WorktreeConflictError on conflict (caller should skip this file
        and mark rollback as partial).
        """
        if postimage_hash is None:
            return  # no post-image recorded — allow rollback unconditionally
        if not path.exists():
            return  # file was already gone — nothing to protect
        actual = _sha256_bytes(path)
        if actual != postimage_hash:
            raise WorktreeConflictError(
                f"File '{path}' was modified after the transaction completed. "
                "Rollback will NOT overwrite user edits. "
                "Manually review the file to complete rollback."
            )

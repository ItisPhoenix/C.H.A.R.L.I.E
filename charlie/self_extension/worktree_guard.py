"""Worktree conflict detection for safe extension writes and rollbacks."""

from __future__ import annotations

import hashlib
import logging
import subprocess
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
    def check_repository_baseline(
        path: Path,
        repo_root: Path,
        fallback_hash: Optional[str] = None,
    ) -> None:
        """Reject an existing target already dirty before checkpoint capture.

        Tracked files use ``HEAD`` as repository baseline. Untracked generated
        extensions use their registry hash as fallback; without either baseline,
        an existing target is treated as unrelated user work.
        """
        if not path.exists():
            return

        try:
            relative = path.resolve().relative_to(repo_root.resolve()).as_posix()
            tracked = subprocess.run(
                ["git", "ls-files", "--error-unmatch", "--", relative],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=False,
            ).returncode == 0
            if tracked:
                baseline = subprocess.run(
                    ["git", "show", f"HEAD:{relative}"],
                    cwd=repo_root,
                    capture_output=True,
                    check=False,
                )
                if baseline.returncode == 0:
                    actual = _sha256_bytes(path)
                    expected = hashlib.sha256(baseline.stdout).hexdigest()
                    if actual != expected:
                        raise WorktreeConflictError(
                            f"Target file '{path}' is dirty relative to repository baseline. "
                            "Refusing to capture it as a rollback preimage."
                        )
                    return
        except WorktreeConflictError:
            raise
        except (OSError, subprocess.SubprocessError):
            logger.debug("Repository baseline unavailable for %s", path, exc_info=True)

        if fallback_hash is None:
            raise WorktreeConflictError(
                f"Target file '{path}' exists but has no known repository or extension baseline. "
                "Cannot overwrite unrelated user work."
            )
        actual = _sha256_bytes(path)
        if not (actual == fallback_hash or actual.startswith(fallback_hash)):
            raise WorktreeConflictError(
                f"Target file '{path}' differs from its recorded extension baseline. "
                "Refusing to capture it as a rollback preimage."
            )

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

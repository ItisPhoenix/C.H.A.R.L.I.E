"""Runtime identity helpers for Charlie."""

from __future__ import annotations

import subprocess
from pathlib import Path


def git_build_identity(root: Path) -> tuple[str | None, bool | None]:
    """Inspect Git metadata to determine commit SHA and dirty state."""
    try:
        git_sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True, stderr=subprocess.DEVNULL
        ).strip()
        dirty = bool(
            subprocess.check_output(
                ["git", "status", "--porcelain", "--untracked-files=no"],
                cwd=root,
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        )
        return git_sha, dirty
    except (OSError, subprocess.CalledProcessError):
        return None, None

"""Runtime build and cache identity helpers for Charlie."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path


def persistent_frontend_dist(root: Path) -> Path:
    """Return the stable per-user cache used when repository dist is inaccessible."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        base = Path(local_app_data)
    else:
        base = Path.home() / "AppData" / "Local"
    return base / "C.H.A.R.L.I.E" / "frontend-dist"


def temporary_frontend_dist(root: Path) -> Path:
    """Return a stable user-owned fallback when the preferred cache is ACL-protected."""
    return Path(tempfile.gettempdir()) / "C.H.A.R.L.I.E" / "frontend-dist"


def frontend_runtime_dist_candidates(root: Path) -> tuple[Path, ...]:
    """Return prioritized candidate directories for the runtime frontend cache."""
    preferred = persistent_frontend_dist(root)
    fallback = temporary_frontend_dist(root)
    return (preferred,) if preferred == fallback else (preferred, fallback)


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

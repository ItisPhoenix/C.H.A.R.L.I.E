import os
import re
import subprocess

from charlie.security.tiers import TIER_2, risk_tier
from charlie.utils.logger import get_logger

logger = get_logger(__name__)

# Commit hashes are 4-40 lowercase hex characters. Anything else (including
# ref names like ``HEAD~1``, branches, or shell metacharacters) is rejected.
_COMMIT_HASH_RE = re.compile(r"^[0-9a-f]{4,40}$")


def _is_valid_commit_hash(commit_hash: str) -> bool:
    """Return True if *commit_hash* is a syntactically valid git SHA."""
    if not isinstance(commit_hash, str):
        return False
    return bool(_COMMIT_HASH_RE.match(commit_hash))


class SnapshotManager:
    def __init__(self, root_dir: str = None):
        # Default to one level above 'charlie' directory
        if root_dir is None:
            self.root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        else:
            self.root_dir = root_dir

        self.repo_ready = self._check_git()

    def _check_git(self) -> bool:
        """Verifies git is initialized in the project root."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=self.root_dir,
                capture_output=True,
                text=True,
                check=False,
            )
            return result.returncode == 0
        except Exception as e:
            logger.error("git_check_failed", error=str(e))
            return False

    def pre_edit_snapshot(self, description: str) -> str:
        """Stages all current changes and commits with description. Returns commit hash."""
        if not self.repo_ready:
            logger.warning("snapshot_failed | git_not_ready")
            return "GIT_NOT_READY"

        try:
            # Add all changes
            subprocess.run(["git", "add", "."], cwd=self.root_dir, check=True)

            # Commit with description
            msg = f"CHARLIE_SNAPSHOT: {description}"
            subprocess.run(["git", "commit", "-m", msg], cwd=self.root_dir, check=True)

            # Get hash
            res = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.root_dir,
                capture_output=True,
                text=True,
                check=True,
            )
            commit_hash = res.stdout.strip()
            logger.info("snapshot_created", hash=commit_hash, desc=description)
            return commit_hash
        except subprocess.CalledProcessError as e:
            # Handle cases where nothing changed
            output = str(e.output or "") + str(getattr(e, "stderr", "") or "")
            if "nothing to commit" in output or "nothing added to commit" in output:
                logger.debug("snapshot_skipped | nothing_to_commit")
                return "CLEAN_TREE"
            logger.error("snapshot_failed", error=str(e))
            return "ERROR"
        except Exception as e:
            logger.error("snapshot_failed", error=str(e))
            return "ERROR"

    @risk_tier(TIER_2)
    def rollback_to(self, commit_hash: str) -> bool:
        """Hard-resets working tree to the given commit and cleans untracked files."""
        if not self.repo_ready:
            return False

        # Validate the commit hash to prevent argument-injection (e.g.
        # ``HEAD; rm -rf /`` into the git command line).
        if not _is_valid_commit_hash(commit_hash):
            logger.error("rollback_blocked | invalid_commit_hash=%r", commit_hash)
            return False

        try:
            # 1. Hard reset
            subprocess.run(["git", "reset", "--hard", commit_hash], cwd=self.root_dir, check=True)
            # 2. Clean untracked files/directories
            self.clean_orphaned_files()

            logger.info("rollback_successful", hash=commit_hash)
            return True
        except Exception as e:
            logger.error("rollback_failed", hash=commit_hash, error=str(e))
            return False

    def clean_orphaned_files(self):
        """Removes untracked files and directories created by failed edits."""
        if not self.repo_ready:
            return
        try:
            subprocess.run(["git", "clean", "-fd"], cwd=self.root_dir, check=True)
            logger.info("orphaned_files_cleaned")
        except Exception as e:
            logger.error("git_clean_failed", error=str(e))

    def get_recent_snapshots(self, n: int = 5) -> list[dict]:
        """Returns last N CHARLIE_SNAPSHOT commits."""
        if not self.repo_ready:
            return []

        try:
            # Filter for commits with CHARLIE_SNAPSHOT in message
            res = subprocess.run(
                [
                    "git",
                    "log",
                    "--grep=CHARLIE_SNAPSHOT",
                    "-n",
                    str(n),
                    "--pretty=format:%H|%at|%s",
                ],
                cwd=self.root_dir,
                capture_output=True,
                text=True,
                check=True,
            )
            snapshots = []
            for line in res.stdout.strip().split("\n"):
                if not line:
                    continue
                parts = line.split("|")
                if len(parts) == 3:
                    snapshots.append(
                        {
                            "hash": parts[0],
                            "timestamp": int(parts[1]),
                            "description": parts[2].replace("CHARLIE_SNAPSHOT: ", ""),
                        }
                    )
            return snapshots
        except Exception as e:
            logger.error("get_recent_snapshots_failed", error=str(e))
            return []

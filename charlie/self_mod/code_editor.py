import logging
import subprocess
import tempfile
from pathlib import Path

from charlie.security.snapshot import SnapshotManager

logger = logging.getLogger("charlie.self_mod.code")

class CodeEditor:
    def __init__(self, brain=None):
        self.brain = brain
        self.snapshot = SnapshotManager()

    def simulate_edit(self, path: str, content: str) -> tuple[bool, str]:
        """Performs dry-run of a code edit: compile check + basic safety."""
        p = Path(path).resolve()
        if not p.is_relative_to(Path(".").resolve()):
            return False, "Access Denied: Path outside project root."

        # Write to temp file for compilation check
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as tf:
            tf.write(content.encode("utf-8"))
            temp_path = tf.name

        try:
            # 1. Compile Check
            res = subprocess.run(["python", "-m", "py_compile", temp_path],
                                capture_output=True, text=True)
            if res.returncode != 0:
                return False, f"Syntax Error: {res.stderr}"

            # 2. Pytest Dry-run (optional/collection only)
            # res = subprocess.run(["pytest", "--co", temp_path], capture_output=True, text=True)

            return True, "Simulation passed."
        finally:
            if Path(temp_path).exists():
                Path(temp_path).unlink()

    def apply_edit(self, path: str, content: str, description: str = "Self-mod edit") -> tuple[bool, str]:
        """Applies the edit after taking a snapshot and verifying."""
        p = Path(path)

        # 1. Take Snapshot
        commit_hash = self.snapshot.pre_edit_snapshot(description)
        if not commit_hash:
            return False, "Failed to take pre-edit snapshot."

        try:
            # 2. Write File
            p.write_text(content)

            # 3. Verify Final Write
            res = subprocess.run(["python", "-m", "py_compile", str(p)],
                                capture_output=True, text=True)
            if res.returncode != 0:
                logger.error(f"post_edit_failure | rolling_back | {res.stderr}")
                self.snapshot.rollback_to(commit_hash)
                return False, f"Post-edit verification failed: {res.stderr}. Rolled back."

            return True, f"Successfully applied edit to {p.name}. Snapshot: {commit_hash[:7]}"
        except Exception as e:
            logger.error(f"apply_edit_exception | {e}")
            self.snapshot.rollback_to(commit_hash)
            return False, f"Exception during apply: {str(e)}. Rolled back."

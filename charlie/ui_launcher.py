"""Launches the Charlie Buddy Electron UI."""
import subprocess
import os
import sys
import pathlib
import logging

logger = logging.getLogger("charlie.ui")


class UILauncher:
    """Manages the Electron buddy UI process."""

    def __init__(self):
        self.process = None
        self.buddy_dir = pathlib.Path(__file__).parent.parent / 'charlie-buddy'

    def start(self):
        if not self.buddy_dir.exists():
            logger.warning("charlie-buddy not found. Skipping UI launch.")
            return

        node_modules = self.buddy_dir / 'node_modules'
        if not node_modules.exists():
            logger.warning("charlie-buddy/node_modules not found. Run 'npm install' first.")
            return

        if sys.platform == 'win32':
            logger.info("Starting Charlie Buddy UI...")
            self.process = subprocess.Popen(
                ['npm', 'run', 'dev'],
                cwd=str(self.buddy_dir),
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            logger.info(f"Charlie Buddy started (PID: {self.process.pid})")
        else:
            logger.info("UI launcher only supports Windows in dev mode.")

    def start_built(self):
        """Launch the built EXE (for distribution)."""
        exe_path = self.buddy_dir / 'dist-electron' / 'Charlie Buddy.exe'
        if exe_path.exists():
            self.process = subprocess.Popen([str(exe_path)])
            logger.info(f"Charlie Buddy (built) started (PID: {self.process.pid})")
        else:
            logger.warning(f"Built exe not found: {exe_path}")

    def stop(self):
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
                logger.info("Charlie Buddy stopped.")
            except subprocess.TimeoutExpired:
                self.process.kill()
                logger.warning("Charlie Buddy force-killed.")
"""
build.py — Build script for CHARLIE daemon executable.

Usage:
    python build.py           # Build charlie-daemon.exe
"""

import subprocess
import sys
import os


def build():
    """Build CHARLIE daemon executable."""
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    print("Building charlie-daemon.exe...")
    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller",
         "--clean", "--noconfirm",
         "--specpath=.",
         "Charlie.spec"],
        capture_output=False,
    )
    if result.returncode != 0:
        print("ERROR: charlie-daemon.exe build failed")
        return False

    print("\nBuild complete!")
    print("  dist/charlie-daemon.exe  — Headless daemon (dashboard at localhost:3000)")
    return True


if __name__ == "__main__":
    success = build()
    sys.exit(0 if success else 1)

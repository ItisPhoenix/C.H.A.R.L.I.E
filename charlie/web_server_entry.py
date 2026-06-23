"""Entry point for Charlie web server subprocess."""

import sys
from pathlib import Path

# Ensure charlie package is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from charlie.web_server import start_server

if __name__ == "__main__":
    start_server()

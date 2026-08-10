"""Entry point for Charlie desktop companion subprocess."""

import sys
from pathlib import Path

# Ensure charlie package is importable (must precede charlie imports)
sys.path.insert(0, str(Path(__file__).parent.parent))

from charlie.pet_window import main

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass

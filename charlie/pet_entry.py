"""Entry point for Charlie desktop companion subprocess."""

import sys
from pathlib import Path

# Ensure charlie package is importable (must precede charlie imports)
sys.path.insert(0, str(Path(__file__).parent.parent))

from charlie.pet_window import QT_AVAILABLE, main

if __name__ == "__main__":
    if not QT_AVAILABLE:
        main()
        raise SystemExit(2)
    try:
        main()
    except KeyboardInterrupt:
        pass

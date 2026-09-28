"""Canonical Charlie runtime launcher."""

# ruff: noqa: E402, I001

import asyncio
import sys

from charlie.runtime import configure as _configure_platform

_configure_platform()

from main import main


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(0)

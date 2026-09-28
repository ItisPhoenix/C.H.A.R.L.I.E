"""Platform-specific runtime fixes.

Centralizes Windows event-loop policy and warning suppression that
the runtime entry points need before importing asyncio-heavy libraries.

Call ``configure()`` as the very first thing in each entry point,
before any other asyncio or zmq import.
"""

import asyncio
import sys
import warnings


def configure() -> None:
    """Apply Windows-specific event-loop policy and warning filters.

    Safe to call multiple times; no-op on non-Windows platforms.
    """
    if sys.platform != "win32":
        return
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    warnings.filterwarnings(
        "ignore", message=".*add_reader.*", category=RuntimeWarning
    )

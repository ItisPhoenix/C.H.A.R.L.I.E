"""
charlie/tools/_vision_bridge.py

Helper to access the brain's vision handler from tool functions.
"""

import logging

logger = logging.getLogger("charlie.tools._vision_bridge")

_brain = None


def get_brain():
    """Get the brain instance via queue_bridge."""
    global _brain
    if _brain is not None:
        return _brain
    try:
        from charlie.utils.queue_bridge import get_brain as _get_brain
        _brain = _get_brain()
        return _brain
    except Exception as e:
        logger.debug(f"_vision_bridge_get_brain_failed | {e}")
        return None

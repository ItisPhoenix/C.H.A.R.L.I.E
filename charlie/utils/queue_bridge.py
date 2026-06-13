"""
charlie/utils/queue_bridge.py
Global reference to the live Brain instance for tools and helper
modules that don't have the brain in their constructor signature
(e.g. LLM-tool-callable functions, lazy-loaded helpers).
"""

import threading

_lock = threading.Lock()
_brain = None


def set_brain(b):
    global _brain
    with _lock:
        _brain = b


def get_brain():
    with _lock:
        return _brain

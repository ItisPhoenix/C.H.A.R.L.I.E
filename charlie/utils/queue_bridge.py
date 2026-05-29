"""
charlie/utils/queue_bridge.py
Global bridge for sharing multiprocessing queues across tools and modules.
Thread-safe via module-level lock.
"""

import threading

_lock = threading.Lock()
_status_q = None
_telegram_q = None
_tts_q = None
_brain = None

def set_status_q(q):
    global _status_q
    with _lock:
        _status_q = q

def get_status_q():
    with _lock:
        return _status_q

def set_telegram_q(q):
    global _telegram_q
    with _lock:
        _telegram_q = q

def get_telegram_q():
    with _lock:
        return _telegram_q

def set_tts_q(q):
    global _tts_q
    with _lock:
        _tts_q = q

def get_tts_q():
    with _lock:
        return _tts_q

def set_brain(b):
    global _brain
    with _lock:
        _brain = b

def get_brain():
    with _lock:
        return _brain

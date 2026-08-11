"""Per-capability resource lock: two callers touching the same capability (desktop, browser,
...) must serialize, not race. Non-blocking test-and-set with an owner id (callers poll on
failure) rather than a real blocking lock -- the same primitive has to work from both plain
async code and desktop control's own worker-thread callers without risking a cross-thread
deadlock, and generalizes charlie/desktop/session.py's original desktop-only mutex exactly.
"""

import threading
from typing import Dict, Optional

_lock = threading.Lock()
_owners: Dict[str, str] = {}


def acquire(capability: str, owner_id: str) -> bool:
    with _lock:
        current = _owners.get(capability)
        if current is None or current == owner_id:
            _owners[capability] = owner_id
            return True
        return False


def release(capability: str, owner_id: str) -> None:
    with _lock:
        if _owners.get(capability) == owner_id:
            _owners.pop(capability, None)


def current_owner(capability: str) -> Optional[str]:
    with _lock:
        return _owners.get(capability)

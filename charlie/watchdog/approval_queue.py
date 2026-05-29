"""
charlie/watchdog/approval_queue.py

ApprovalQueue — thread-safe pending approval queue for CHARLIE daemon.
Manages PendingApproval objects with approve/deny/promote operations.
"""

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable

from charlie.utils.logger import get_logger

logger = get_logger("ApprovalQueue")


@dataclass
class PendingApproval:
    """A pending action awaiting user approval."""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    action: str = ""
    args: dict = field(default_factory=dict)
    risk_tier: int = 1
    description: str = ""
    source: str = "unknown"  # "tool_handler", "risk_gate", "guardian"
    timestamp: float = field(default_factory=time.time)
    timeout: float = 60.0  # seconds until auto-deny
    status: str = "pending"  # "pending", "approved", "denied", "expired"
    result_event: threading.Event = field(default_factory=threading.Event, repr=False)
    result_value: bool = False

    @property
    def is_expired(self) -> bool:
        return self.status == "pending" and (time.time() - self.timestamp) > self.timeout

    @property
    def remaining_seconds(self) -> float:
        if self.status != "pending":
            return 0.0
        return max(0.0, self.timeout - (time.time() - self.timestamp))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "action": self.action,
            "args": self.args,
            "risk_tier": self.risk_tier,
            "description": self.description,
            "source": self.source,
            "timestamp": self.timestamp,
            "timeout": self.timeout,
            "status": self.status,
            "remaining": self.remaining_seconds,
        }


class ApprovalQueue:
    """
    Thread-safe approval queue.

    - add() → creates PendingApproval, notifies listeners
    - approve() / deny() → resolves, fires result_event
    - promote() → creates permanent policy rule
    - wait_for_result() → blocks until resolved or timeout
    """

    def __init__(self):
        self._pending: dict[str, PendingApproval] = {}
        self._lock = threading.Lock()
        self._on_change: list[Callable] = []
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_loop, daemon=True, name="ApprovalCleanup"
        )
        self._cleanup_thread.start()

    def add(self, approval: PendingApproval) -> str:
        """Add a pending approval. Returns approval ID."""
        with self._lock:
            self._pending[approval.id] = approval
        self._notify("added", approval)
        logger.info(f"approval_added | id={approval.id} action={approval.action} tier={approval.risk_tier}")
        return approval.id

    def approve(self, approval_id: str) -> bool:
        """Approve a pending action. Returns True if found."""
        with self._lock:
            approval = self._pending.get(approval_id)
            if not approval or approval.status != "pending":
                return False
            approval.status = "approved"
            approval.result_value = True
            approval.result_event.set()
        self._notify("approved", approval)
        logger.info(f"approval_approved | id={approval_id}")
        return True

    def deny(self, approval_id: str) -> bool:
        """Deny a pending action. Returns True if found."""
        with self._lock:
            approval = self._pending.get(approval_id)
            if not approval or approval.status != "pending":
                return False
            approval.status = "denied"
            approval.result_value = False
            approval.result_event.set()
        self._notify("denied", approval)
        logger.info(f"approval_denied | id={approval_id}")
        return True

    def promote(self, approval_id: str, policy: str = "always_allow") -> bool:
        """Promote to permanent policy. Returns True if found."""
        with self._lock:
            approval = self._pending.get(approval_id)
            if not approval or approval.status != "pending":
                return False
            approval.status = "approved"
            approval.result_value = True
            approval.result_event.set()
        self._notify("promoted", approval)
        logger.info(f"approval_promoted | id={approval_id} policy={policy}")
        return True

    def wait_for_result(self, approval_id: str, timeout: float = 60.0) -> bool:
        """Block until approval is resolved or timeout. Returns True if approved."""
        with self._lock:
            approval = self._pending.get(approval_id)
            if not approval:
                return False
            if approval.status != "pending":
                return approval.result_value

        # Wait outside lock
        approval.result_event.wait(timeout=timeout)

        # Check if expired
        if approval.is_expired:
            with self._lock:
                if approval.status == "pending":
                    approval.status = "expired"
                    approval.result_value = False
                    approval.result_event.set()
            self._notify("expired", approval)
            logger.info(f"approval_expired | id={approval_id}")

        return approval.result_value

    def get_pending(self) -> list[PendingApproval]:
        """Get all pending approvals."""
        with self._lock:
            return [a for a in self._pending.values() if a.status == "pending"]

    def get_all(self) -> list[PendingApproval]:
        """Get all approvals (including resolved)."""
        with self._lock:
            return list(self._pending.values())

    def on_change(self, callback: Callable):
        """Register a change listener. callback(event_type, approval)."""
        self._on_change.append(callback)

    def _notify(self, event_type: str, approval: PendingApproval):
        """Notify all listeners."""
        for cb in self._on_change:
            try:
                cb(event_type, approval)
            except Exception:
                pass

    def _cleanup_loop(self):
        """Background cleanup of expired approvals."""
        while True:
            time.sleep(10)
            with self._lock:
                expired = [
                    a for a in self._pending.values()
                    if a.is_expired and a.status == "pending"
                ]
                for a in expired:
                    a.status = "expired"
                    a.result_value = False
                    a.result_event.set()
                    self._notify("expired", a)
                    logger.info(f"approval_auto_expired | id={a.id}")

    @property
    def pending_count(self) -> int:
        with self._lock:
            return sum(1 for a in self._pending.values() if a.status == "pending")

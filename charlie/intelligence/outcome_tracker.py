"""Outcome tracking for the Learning Engine.

Tracks tool call success/failure, user signals (thanks, no, wrong),
task completion, and agent selection outcomes in SQLite.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from collections import deque
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional


SCRATCH_DIR = Path("scratch")
OUTCOMES_DB = SCRATCH_DIR / "outcomes.db"


@dataclass
class Outcome:
    id: Optional[int] = None
    timestamp: float = 0.0
    event_type: str = ""       # tool_call | user_response | task_complete | agent_selection
    outcome_type: str = ""     # success | failure | positive | negative | correction
    tool_name: Optional[str] = None
    agent_name: Optional[str] = None
    user_signal: Optional[str] = None
    details: Optional[str] = None
    confidence: float = 1.0

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


class OutcomeTracker:
    """SQLite-backed outcome tracker."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or OUTCOMES_DB
        SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
        # Bounded in-memory ring buffer of recent tool gate/execution decisions
        # for the dashboard (RPC GET_TOOL_LOG). Newest entries are appended last.
        self.tool_exec_log: deque[dict] = deque(maxlen=200)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    event_type TEXT NOT NULL,
                    outcome_type TEXT NOT NULL,
                    tool_name TEXT,
                    agent_name TEXT,
                    user_signal TEXT,
                    details TEXT,
                    confidence REAL DEFAULT 1.0
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_event_type ON outcomes(event_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON outcomes(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tool_name ON outcomes(tool_name)")
            conn.commit()

    # --- Implicit signal detection ---

    POSITIVE_SIGNALS = re.compile(
        r"\b(thanks|thank you|perfect|exactly|exactly right|that.?s? (it|right|perfect)|great|godus|awesome|nice)\b",
        re.IGNORECASE
    )
    NEGATIVE_SIGNALS = re.compile(
        r"\b(no|wrong|not right|not correct|that.?s? (not |no)|nope|nah|don.?t want|never mind|cancel)\b",
        re.IGNORECASE
    )
    CORRECTION_SIGNALS = re.compile(
        r"\b(rephrase|try again|instead|another way|different|rewrite|redo|retry|redo)\b",
        re.IGNORECASE
    )

    @classmethod
    def detect_signal(cls, text: str) -> Optional[str]:
        """Detect implicit user signal from raw message text."""
        if not text:
            return None
        if cls.POSITIVE_SIGNALS.search(text):
            return "positive"
        if cls.NEGATIVE_SIGNALS.search(text):
            return "negative"
        if cls.CORRECTION_SIGNALS.search(text):
            return "correction"
        return None

    # --- Record ---

    def record_outcome(
        self,
        event_type: str,
        outcome_type: str,
        tool_name: Optional[str] = None,
        agent_name: Optional[str] = None,
        details: Optional[dict] = None,
        user_signal: Optional[str] = None,
        confidence: float = 1.0,
    ) -> None:
        """Insert an outcome record."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO outcomes
                    (timestamp, event_type, outcome_type, tool_name, agent_name,
                     user_signal, details, confidence)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    time.time(),
                    event_type,
                    outcome_type,
                    tool_name,
                    agent_name,
                    user_signal,
                    json.dumps(details) if details else None,
                    confidence,
                ),
            )
            conn.commit()

    def record_tool(self, tool_name: str, success: bool, details: Optional[dict] = None) -> None:
        """Convenience: record a tool call outcome."""
        self.record_outcome(
            event_type="tool_call",
            outcome_type="success" if success else "failure",
            tool_name=tool_name,
            details=details,
        )

    def record_user_signal(self, signal: str, details: Optional[dict] = None) -> None:
        """Convenience: record an implicit user signal."""
        self.record_outcome(
            event_type="user_response",
            outcome_type=signal,
            user_signal=signal,
            details=details,
        )

    def record_agent(self, agent_name: str, outcome: str = "success", details: Optional[dict] = None) -> None:
        """Convenience: record an agent selection outcome."""
        self.record_outcome(
            event_type="agent_selection",
            outcome_type=outcome,
            agent_name=agent_name,
            details=details,
        )

    # --- Tool execution / gate-decision log ---

    def record_tool_decision(
        self,
        tool_name: str,
        risk_tier,
        decision: str,
        outcome: Optional[str] = None,
        details: Optional[dict] = None,
    ) -> None:
        """Record a single tool gate/execution decision.

        ``decision`` is one of "gated" | "confirmed" | "cancelled" | "executed".
        ``risk_tier`` may be a :class:`RiskTier` enum or a string; either way its
        string name (e.g. ``"TIER_2"``) is stored.

        Exactly-once: a single call appends exactly one ring-buffer entry and
        writes exactly one SQLite row, so the in-memory dashboard view and the
        persisted learning record stay in lockstep.
        """
        # Normalize the risk tier to its string name (accept enum or string).
        if risk_tier is None:
            tier_name = None
        elif hasattr(risk_tier, "name"):
            tier_name = risk_tier.name
        else:
            tier_name = str(risk_tier)

        # 1) Append to the in-memory ring buffer (newest last).
        self.tool_exec_log.append(
            {
                "timestamp": time.time(),
                "tool_name": tool_name,
                "risk_tier": tier_name,
                "decision": decision,
                "outcome": outcome,
                "details": details,
            }
        )

        # 2) Persist a single SQLite row so the decision survives restart and
        #    feeds the learning subsystem. The outcome_type is the explicit
        #    outcome when provided, otherwise the decision itself.
        persisted_details = dict(details) if details else {}
        persisted_details.setdefault("decision", decision)
        persisted_details.setdefault("risk_tier", tier_name)
        self.record_outcome(
            event_type="tool_call",
            outcome_type=outcome or decision,
            tool_name=tool_name,
            details=persisted_details,
        )

    def get_tool_exec_log(self, limit: int = 50) -> list[dict]:
        """Return the most recent ``limit`` tool-decision entries, newest first."""
        if limit <= 0:
            return []
        entries = list(self.tool_exec_log)[-limit:]
        entries.reverse()
        return entries



    # --- Queries ---

    def get_tool_success_rate(self, tool_name: str, since_hours: int = 168) -> float:
        """Return success rate (0-1) for a tool over the given time window."""
        since = time.time() - (since_hours * 3600)
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                """
                SELECT outcome_type, COUNT(*) as cnt FROM outcomes
                WHERE tool_name = ? AND event_type = 'tool_call' AND timestamp > ?
                GROUP BY outcome_type
                """,
                (tool_name, since),
            )
            rows = {r["outcome_type"]: r["cnt"] for r in cur.fetchall()}
        total = rows.get("success", 0) + rows.get("failure", 0)
        return rows.get("success", 0) / total if total > 0 else 0.0

    def get_user_signal_count(self, signal: str, since_hours: int = 168) -> int:
        """Count occurrences of a user signal over the given time window."""
        since = time.time() - (since_hours * 3600)
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "SELECT COUNT(*) FROM outcomes WHERE user_signal = ? AND timestamp > ?",
                (signal, since),
            )
            return cur.fetchone()[0]

    def get_recent_outcomes(
        self, event_type: Optional[str] = None, limit: int = 50
    ) -> list[Outcome]:
        """Return recent outcomes, optionally filtered by event_type."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            if event_type:
                cur = conn.execute(
                    "SELECT * FROM outcomes WHERE event_type = ? ORDER BY timestamp DESC LIMIT ?",
                    (event_type, limit),
                )
            else:
                cur = conn.execute(
                    "SELECT * FROM outcomes ORDER BY timestamp DESC LIMIT ?",
                    (limit,),
                )
            return [Outcome(**dict(r)) for r in cur.fetchall()]

    def get_event_counts(
        self, event_type: str, outcome_type: Optional[str] = None, since_hours: int = 168
    ) -> dict[str, int]:
        """Get counts grouped by outcome_type for a given event type and time window."""
        since = time.time() - (since_hours * 3600)
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            if outcome_type:
                cur = conn.execute(
                    """
                    SELECT outcome_type, COUNT(*) as cnt FROM outcomes
                    WHERE event_type = ? AND outcome_type = ? AND timestamp > ?
                    GROUP BY outcome_type
                    """,
                    (event_type, outcome_type, since),
                )
            else:
                cur = conn.execute(
                    """
                    SELECT outcome_type, COUNT(*) as cnt FROM outcomes
                    WHERE event_type = ? AND timestamp > ?
                    GROUP BY outcome_type
                    """,
                    (event_type, since),
                )
            return {r["outcome_type"]: r["cnt"] for r in cur.fetchall()}

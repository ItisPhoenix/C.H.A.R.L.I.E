"""Charlie's world model: SQLite-backed store for open threads and machine
state, same boundary shape as charlie/session_store.py.
"""

import logging
import re
import sqlite3
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple, TypeVar

from charlie.utils import make_id, utc_now_iso

T = TypeVar("T")

logger = logging.getLogger("charlie.world_model")

# Reader hard budget: keeps the world-model slice inside the ~1s TTFA budget.
_DEFAULT_CHAR_BUDGET = 800

# Rules: confidence starts here, moves by these steps, decays out below this floor.
_RULE_INITIAL_CONFIDENCE = 0.6
_RULE_REINFORCE_STEP = 0.15
_RULE_DECAY_STEP = 0.1
_RULE_DECAY_FLOOR = 0.1


class WorldModel:
    """Threads (open work) + machine events (observed activity/errors) +
    learned rules (confidence-scored behavior preferences). Patterns/entities
    tables land with their own writers when the learning phase needs them.
    """

    def __init__(self, db_path: str = "world_model.db"):
        self.db_path = db_path
        self._local = threading.local()
        self.init_db()

    @property
    def conn(self):
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = self._get_connection()
        return self._local.conn

    @conn.setter
    def conn(self, value):
        self._local.conn = value

    def _get_connection(self):
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.execute("PRAGMA journal_mode = WAL;")
        return conn

    def _with_retry(self, op: "Callable[[], T]", op_name: str, reraise: bool = True) -> "Optional[T]":
        for attempt in range(2):
            try:
                return op()
            except sqlite3.OperationalError as e:
                if "database is locked" in str(e) and attempt == 0:
                    logger.warning("world_model DB locked during %s, retrying...", op_name)
                    time.sleep(0.05)
                    continue
                logger.error("world_model %s failed: %s", op_name, e)
                if reraise:
                    raise
                return None
            except sqlite3.Error as e:
                logger.error("world_model %s failed: %s", op_name, e)
                if reraise:
                    raise
                return None

    def init_db(self) -> None:
        self.conn = self._get_connection()
        with self.conn:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS threads (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '',
                    session_id TEXT NOT NULL DEFAULT 'default',
                    status TEXT NOT NULL DEFAULT 'open',
                    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                );
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS machine_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                );
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS rules (
                    id TEXT PRIMARY KEY,
                    rule_text TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 0.6,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    last_reinforced_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                );
            """)
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_threads_status ON threads(status, updated_at);")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_events_created ON machine_events(created_at);")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_rules_status ON rules(status, confidence);")

    # --- Writers: threads ---

    def open_thread(self, title: str, session_id: str = "default") -> str:
        thread_id = make_id(8)
        now = utc_now_iso()

        def _op():
            with self.conn:
                self.conn.execute(
                    "INSERT INTO threads (id, title, session_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (thread_id, title, session_id, now, now),
                )
        self._with_retry(_op, "open_thread", reraise=False)
        return thread_id

    def update_thread(self, thread_id: str, summary: str, resolved: bool = False) -> None:
        def _op():
            with self.conn:
                self.conn.execute(
                    "UPDATE threads SET summary = ?, status = ?, updated_at = ? WHERE id = ?",
                    (summary, "closed" if resolved else "open", utc_now_iso(), thread_id),
                )
        self._with_retry(_op, "update_thread", reraise=False)

    def close_thread(self, thread_id: str) -> None:
        def _op():
            with self.conn:
                self.conn.execute(
                    "UPDATE threads SET status = 'closed', updated_at = ? WHERE id = ?",
                    (utc_now_iso(), thread_id),
                )
        self._with_retry(_op, "close_thread", reraise=False)

    def list_open_threads(self, limit: int = 5) -> List[Tuple[str, str, str]]:
        """Most recently touched open threads first: (id, title, summary)."""
        def _op():
            cur = self.conn.execute(
                "SELECT id, title, summary FROM threads WHERE status = 'open' ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            )
            return cur.fetchall()
        return self._with_retry(_op, "list_open_threads", reraise=False) or []

    # --- Writers: machine events ---

    def record_event(self, event_type: str, detail: str) -> None:
        def _op():
            with self.conn:
                self.conn.execute(
                    "INSERT INTO machine_events (event_type, detail) VALUES (?, ?)",
                    (event_type, detail),
                )
        self._with_retry(_op, "record_event", reraise=False)

    def recent_events(self, limit: int = 5, event_type: Optional[str] = None) -> List[Tuple[str, str, str]]:
        """Most recent events first: (event_type, detail, created_at)."""
        def _op():
            if event_type:
                cur = self.conn.execute(
                    "SELECT event_type, detail, created_at FROM machine_events "
                    "WHERE event_type = ? ORDER BY created_at DESC LIMIT ?",
                    (event_type, limit),
                )
            else:
                cur = self.conn.execute(
                    "SELECT event_type, detail, created_at FROM machine_events ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                )
            return cur.fetchall()
        return self._with_retry(_op, "recent_events", reraise=False) or []

    def detect_app_sequence_pattern(
        self, min_occurrences: int = 3, window_seconds: int = 300, sample_size: int = 200
    ) -> Optional[Tuple[str, str, int]]:
        """Observed-pattern signal: has the user opened app B shortly after
        app A at least min_occurrences times? Scans the most recent
        sample_size app_open events for consecutive pairs within
        window_seconds. Returns (app_a, app_b, occurrences) for the most
        common pair, or None. App names are extracted from the event detail
        string's leading "I've opened X for you." shape (see router.execute_open_app);
        best-effort, skips events it can't parse.
        """
        def _op():
            cur = self.conn.execute(
                "SELECT detail, created_at FROM machine_events WHERE event_type = 'app_open' "
                "ORDER BY created_at DESC LIMIT ?",
                (sample_size,),
            )
            return cur.fetchall()
        rows = self._with_retry(_op, "detect_app_sequence_pattern", reraise=False) or []
        rows = list(reversed(rows))  # chronological order

        from datetime import datetime

        def _app_name(detail: str) -> Optional[str]:
            m = re.search(r"opened (.+?) for you", detail)
            return m.group(1) if m else None

        def _parse_ts(ts: str) -> Optional[datetime]:
            try:
                return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ")
            except ValueError:
                return None

        pair_counts: Dict[Tuple[str, str], int] = {}
        for i in range(len(rows) - 1):
            app_a, ts_a = _app_name(rows[i][0]), _parse_ts(rows[i][1])
            app_b, ts_b = _app_name(rows[i + 1][0]), _parse_ts(rows[i + 1][1])
            if not app_a or not app_b or app_a == app_b or not ts_a or not ts_b:
                continue
            if (ts_b - ts_a).total_seconds() > window_seconds:
                continue
            pair_counts[(app_a, app_b)] = pair_counts.get((app_a, app_b), 0) + 1

        if not pair_counts:
            return None
        (app_a, app_b), count = max(pair_counts.items(), key=lambda kv: kv[1])
        return (app_a, app_b, count) if count >= min_occurrences else None

    # --- Writers: learned rules ---

    def add_rule(self, rule_text: str, source: str) -> str:
        """Adds a new active rule at the starting confidence. Caller (source-specific
        signal detection) is responsible for not adding an exact duplicate."""
        rule_id = make_id(8)
        now = utc_now_iso()

        def _op():
            with self.conn:
                self.conn.execute(
                    "INSERT INTO rules (id, rule_text, confidence, source, created_at, last_reinforced_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (rule_id, rule_text, _RULE_INITIAL_CONFIDENCE, source, now, now),
                )
        self._with_retry(_op, "add_rule", reraise=False)
        return rule_id

    def propose_rule(self, rule_text: str, source: str) -> str:
        """Observed-pattern signal: stage a candidate rule as 'proposed', not
        'active' -- active_rules()/context_slice() only read 'active', so a
        proposed rule is visible for review but never auto-injected into the
        prompt. Approve via approve_rule(), reject via delete_rule()."""
        rule_id = make_id(8)
        now = utc_now_iso()

        def _op():
            with self.conn:
                self.conn.execute(
                    "INSERT INTO rules (id, rule_text, confidence, source, status, created_at, last_reinforced_at) "
                    "VALUES (?, ?, ?, ?, 'proposed', ?, ?)",
                    (rule_id, rule_text, _RULE_INITIAL_CONFIDENCE, source, now, now),
                )
        self._with_retry(_op, "propose_rule", reraise=False)
        return rule_id

    def approve_rule(self, rule_id: str) -> None:
        """Flip a proposed rule to active -- now it's read by active_rules()/context_slice()."""
        def _op():
            with self.conn:
                self.conn.execute("UPDATE rules SET status = 'active' WHERE id = ?", (rule_id,))
        self._with_retry(_op, "approve_rule", reraise=False)

    def reinforce_rule(self, rule_id: str) -> None:
        """Rule held true again: raise confidence (capped at 1.0), reset the decay clock."""
        def _op():
            with self.conn:
                self.conn.execute(
                    "UPDATE rules SET confidence = MIN(1.0, confidence + ?), last_reinforced_at = ? WHERE id = ?",
                    (_RULE_REINFORCE_STEP, utc_now_iso(), rule_id),
                )
        self._with_retry(_op, "reinforce_rule", reraise=False)

    def decay_rule(self, rule_id: str) -> None:
        """Rule unused or contradicted: lower confidence, auto-retire below the floor."""
        def _op():
            with self.conn:
                self.conn.execute(
                    "UPDATE rules SET confidence = MAX(0.0, confidence - ?) WHERE id = ?",
                    (_RULE_DECAY_STEP, rule_id),
                )
                self.conn.execute(
                    "UPDATE rules SET status = 'decayed' WHERE id = ? AND confidence < ? AND status = 'active'",
                    (rule_id, _RULE_DECAY_FLOOR),
                )
        self._with_retry(_op, "decay_rule", reraise=False)

    def delete_rule(self, rule_id: str) -> None:
        """Explicit user-requested forget: hard delete, not just a status flip."""
        def _op():
            with self.conn:
                self.conn.execute("DELETE FROM rules WHERE id = ?", (rule_id,))
        self._with_retry(_op, "delete_rule", reraise=False)

    def decay_stale_rules(self, stale_days: int = 14) -> List[str]:
        """Active rules unreinforced for stale_days+ lose confidence -- the
        off-ramp for a bad inference that never gets corrected but also never
        gets used again. Returns the decayed rule ids."""
        def _find():
            cur = self.conn.execute(
                "SELECT id FROM rules WHERE status = 'active' "
                "AND julianday('now') - julianday(last_reinforced_at) >= ?",
                (stale_days,),
            )
            return [row[0] for row in cur.fetchall()]
        stale_ids = self._with_retry(_find, "decay_stale_rules_find", reraise=False) or []
        for rule_id in stale_ids:
            self.decay_rule(rule_id)
        return stale_ids

    def find_rules_matching(self, text: str) -> List[Tuple[str, str]]:
        """Rules (active or proposed) whose text contains `text` -- the lookup
        behind a "forget that ..." command. Case-insensitive substring match."""
        def _op():
            cur = self.conn.execute(
                "SELECT id, rule_text FROM rules WHERE status IN ('active', 'proposed') "
                "AND lower(rule_text) LIKE ?",
                (f"%{text.lower()}%",),
            )
            return cur.fetchall()
        return self._with_retry(_op, "find_rules_matching", reraise=False) or []

    def list_rules(self, include_decayed: bool = False) -> List[Tuple[str, str, float, str, str]]:
        """All rules for the review command: (id, rule_text, confidence, source, status)."""
        def _op():
            if include_decayed:
                cur = self.conn.execute(
                    "SELECT id, rule_text, confidence, source, status FROM rules ORDER BY confidence DESC"
                )
            else:
                cur = self.conn.execute(
                    "SELECT id, rule_text, confidence, source, status FROM rules "
                    "WHERE status = 'active' ORDER BY confidence DESC"
                )
            return cur.fetchall()
        return self._with_retry(_op, "list_rules", reraise=False) or []

    def active_rules(self, limit: int = 5) -> List[Tuple[str, str]]:
        """Highest-confidence active rules for prompt injection: (id, rule_text)."""
        def _op():
            cur = self.conn.execute(
                "SELECT id, rule_text FROM rules WHERE status = 'active' ORDER BY confidence DESC LIMIT ?",
                (limit,),
            )
            return cur.fetchall()
        return self._with_retry(_op, "active_rules", reraise=False) or []

    # --- Reader ---

    def context_slice(self, char_budget: int = _DEFAULT_CHAR_BUDGET) -> str:
        """Open threads + learned rules + recent tool errors, hard-capped at
        char_budget. Not semantic search -- active state is relevant on
        every turn.
        """
        parts: List[str] = []
        threads = self.list_open_threads(limit=5)
        if threads:
            parts.append("Open threads:")
            for _id, title, summary in threads:
                line = f"- {title}" + (f": {summary}" if summary else "")
                parts.append(line)

        rules = self.active_rules(limit=5)
        if rules:
            parts.append("Learned rules:")
            for _id, rule_text in rules:
                parts.append(f"- {rule_text}")

        errors = self.recent_events(limit=3, event_type="tool_error")
        if errors:
            parts.append("Recent errors:")
            for _etype, detail, _created in errors:
                parts.append(f"- {detail}")

        text = "\n".join(parts)
        return text[:char_budget]

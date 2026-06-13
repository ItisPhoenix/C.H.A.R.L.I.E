"""
charlie/intelligence/timeline.py

TimelineIndexer — Unified timeline from multiple CHARLIE data sources.
Indexes: conversation, trust ledger, tasks, memory, automation.
"""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from charlie.utils.logger import get_logger

logger = get_logger("Timeline")


@dataclass
class TimelineEntry:
    """A single timeline event."""

    timestamp: float
    source: str  # "conversation", "trust", "task", "memory", "automation"
    category: str  # "user_input", "charlie_response", "tool_call", "trust_event", ...
    content: str
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "source": self.source,
            "category": self.category,
            "content": self.content,
            "metadata": self.metadata,
        }


class TimelineIndexer:
    """
    Builds a unified timeline from CHARLIE data sources.

    Sources:
    - conversation_history.json — user/charlie messages
    - trust_ledger.jsonl — trust score events
    - memory graph — notes and facts
    - automation_learning.json — automation outcomes

    Usage:
        indexer = TimelineIndexer()
        indexer.build_index()
        results = indexer.search(query="python", limit=10)
    """

    def __init__(self, base_path: str = "."):
        self.base_path = Path(base_path)
        self._index: list[TimelineEntry] = []

    def build_index(self) -> int:
        """Scan all data sources and build unified timeline. Returns entry count."""
        self._index.clear()

        self._index_conversation()
        self._index_trust_ledger()
        self._index_automation()

        self._index.sort(key=lambda e: e.timestamp, reverse=True)
        logger.info(f"timeline_built | entries={len(self._index)}")
        return len(self._index)

    def search(
        self,
        query: str = None,
        date_from: float = None,
        date_to: float = None,
        source: str = None,
        category: str = None,
        limit: int = 50,
    ) -> list[TimelineEntry]:
        """Search timeline with filters."""
        results = self._index

        if date_from:
            results = [e for e in results if e.timestamp >= date_from]
        if date_to:
            results = [e for e in results if e.timestamp <= date_to]
        if source:
            results = [e for e in results if e.source == source]
        if category:
            results = [e for e in results if e.category == category]
        if query:
            query_lower = query.lower()
            results = [e for e in results if query_lower in e.content.lower()]

        return results[:limit]

    def _index_conversation(self):
        """Index conversation_history.json."""
        path = self.base_path / "scratch" / "conversation_history.json"
        if not path.exists():
            return

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                return

            for msg in data:
                if not isinstance(msg, dict):
                    continue
                speaker = msg.get("speaker", msg.get("role", "unknown"))
                content = msg.get("content", "")
                timestamp = msg.get("timestamp", time.time())

                if not content:
                    continue

                category = "user_input" if speaker in ("SIR", "user") else "charlie_response"
                self._index.append(
                    TimelineEntry(
                        timestamp=timestamp,
                        source="conversation",
                        category=category,
                        content=str(content)[:200],
                        metadata={"speaker": speaker},
                    )
                )
        except Exception as e:
            logger.debug(f"conversation_index_failed | {e}")

    def _index_trust_ledger(self):
        """Index trust_ledger.jsonl."""
        path = self.base_path / "charlie" / "personality" / "trust_ledger.jsonl"
        if not path.exists():
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        self._index.append(
                            TimelineEntry(
                                timestamp=entry.get("timestamp", time.time()),
                                source="trust",
                                category="trust_event",
                                content=entry.get("reason", "Trust update"),
                                metadata={
                                    "delta": entry.get("delta", 0),
                                    "new_score": entry.get("new_score", 0),
                                    "event": entry.get("event", ""),
                                },
                            )
                        )
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            logger.debug(f"trust_index_failed | {e}")

    def _index_automation(self):
        """Index automation_learning.json."""
        path = self.base_path / "scratch" / "automation_learning.json"
        if not path.exists():
            return

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return

            outcomes = data.get("outcomes", [])
            for outcome in outcomes:
                if not isinstance(outcome, dict):
                    continue
                self._index.append(
                    TimelineEntry(
                        timestamp=outcome.get("timestamp", time.time()),
                        source="automation",
                        category="automation_outcome",
                        content=outcome.get("action", "Automation event"),
                        metadata={
                            "success": outcome.get("success", False),
                            "user_approved": outcome.get("user_approved", None),
                            "event_type": outcome.get("event_type", ""),
                        },
                    )
                )
        except Exception as e:
            logger.debug(f"automation_index_failed | {e}")

    @property
    def entry_count(self) -> int:
        return len(self._index)

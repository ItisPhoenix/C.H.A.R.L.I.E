"""Agent learning tracker — records task outcomes and computes success rates."""

import json
import threading
import time
from pathlib import Path
from typing import Optional

from charlie.utils.logger import get_logger

logger = get_logger(__name__)


class AgentLearningTracker:
    """Tracks agent task outcomes for learning-based routing."""

    def __init__(self, data_path: str | Path | None = None):
        self._data_path = Path(data_path or "scratch/agent_learning.json")
        self._lock = threading.Lock()
        self._records: list[dict] = []
        self._load()

    def _load(self):
        """Load learning data from disk."""
        try:
            if self._data_path.exists():
                with open(self._data_path, "r", encoding="utf-8") as f:
                    self._records = json.load(f)
                logger.info("learning_loaded | records=%d", len(self._records))
        except Exception as e:
            logger.warning("learning_load_failed | %s", e)
            self._records = []

    def _save(self):
        """Persist learning data to disk."""
        try:
            self._data_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._data_path, "w", encoding="utf-8") as f:
                json.dump(self._records, f, indent=2)
        except Exception as e:
            logger.warning("learning_save_failed | %s", e)

    def record(
        self,
        agent_name: str,
        keywords: list[str],
        success: bool,
        duration_ms: float = 0.0,
    ):
        """Record a task outcome."""
        entry = {
            "agent": agent_name,
            "keywords": keywords,
            "success": success,
            "duration_ms": duration_ms,
            "timestamp": time.time(),
        }
        with self._lock:
            self._records.append(entry)
            # Keep last 1000 records
            if len(self._records) > 1000:
                self._records = self._records[-1000:]
            self._save()
        logger.debug(
            "learning_recorded | agent=%s success=%s duration=%.0fms",
            agent_name,
            success,
            duration_ms,
        )

    def get_score(self, agent_name: str, keywords: Optional[list[str]] = None) -> float:
        """Get success rate for an agent. Returns 0.5 if no data."""
        with self._lock:
            records = [r for r in self._records if r["agent"] == agent_name]
            if keywords:
                keyword_set = set(k.lower() for k in keywords)
                records = [r for r in records if any(k.lower() in keyword_set for k in r.get("keywords", []))]
            if not records:
                return 0.5
            successes = sum(1 for r in records if r["success"])
            return successes / len(records)

    def get_all_scores(self) -> dict[str, float]:
        """Get success rates for all agents."""
        with self._lock:
            agents = set(r["agent"] for r in self._records)
            return {agent: self.get_score(agent) for agent in agents}

    def get_history(self, agent_name: Optional[str] = None, limit: int = 50) -> list[dict]:
        """Get recent task records."""
        with self._lock:
            records = self._records
            if agent_name:
                records = [r for r in records if r["agent"] == agent_name]
            return records[-limit:]

    def get_stats(self) -> dict:
        """Get overall learning statistics."""
        with self._lock:
            if not self._records:
                return {
                    "total_records": 0,
                    "overall_success_rate": 0.0,
                    "agents": {},
                }
            total = len(self._records)
            successes = sum(1 for r in self._records if r["success"])
            agents = {}
            for r in self._records:
                name = r["agent"]
                if name not in agents:
                    agents[name] = {"total": 0, "successes": 0}
                agents[name]["total"] += 1
                if r["success"]:
                    agents[name]["successes"] += 1
            for name in agents:
                agents[name]["success_rate"] = agents[name]["successes"] / agents[name]["total"]
            return {
                "total_records": total,
                "overall_success_rate": successes / total,
                "agents": agents,
            }

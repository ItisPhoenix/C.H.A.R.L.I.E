"""Pattern detection for the Learning Engine.

Analyzes outcome history from OutcomeTracker to detect behavioral patterns.
Requires 3+ occurrences before reporting a pattern (confidence threshold).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from charlie.intelligence.outcome_tracker import OutcomeTracker

SCRATCH_DIR = Path("scratch")
PATTERNS_CACHE_FILE = SCRATCH_DIR / "patterns_cache.json"


@dataclass
class LearnedPattern:
    pattern_type: str   # temporal | behavioral | workflow | agent_routing | preference
    description: str
    confidence: int      # occurrences count (3 = threshold)
    data: dict


class PatternDetector:
    """Detects patterns in outcome history."""

    def __init__(
        self,
        tracker: Optional[OutcomeTracker] = None,
        persist_path: Optional[Path] = None,
    ):
        self.tracker = tracker or OutcomeTracker()
        self._cache: Optional[tuple[float, list[LearnedPattern]]] = None
        self._cache_ttl = 300.0  # 5 minutes
        self._persist_path = persist_path or PATTERNS_CACHE_FILE
        self._load_patterns()

    # --- Cached detect ---

    def detect_patterns(self, min_confidence: int = 3) -> list[LearnedPattern]:
        """Detect all pattern types with at least min_confidence occurrences."""
        now = time.time()
        if self._cache is not None and (now - self._cache[0]) < self._cache_ttl:
            return [p for p in self._cache[1] if p.confidence >= min_confidence]

        patterns = (
            self._detect_temporal()
            + self._detect_behavioral()
            + self._detect_workflow()
            + self._detect_agent_routing()
            + self._detect_preferences()
        )
        self._cache = (now, patterns)
        self._save_patterns()
        return [p for p in patterns if p.confidence >= min_confidence]

    # --- Persistence ---

    def _save_patterns(self) -> None:
        """Persist cached patterns to disk."""
        if self._cache is None:
            return
        try:
            SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
            data = {
                "cached_at": self._cache[0],
                "patterns": [
                    {
                        "pattern_type": p.pattern_type,
                        "description": p.description,
                        "confidence": p.confidence,
                        "data": p.data,
                    }
                    for p in self._cache[1]
                ],
            }
            with open(self._persist_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            from charlie.utils.logger import get_logger
            get_logger(__name__).debug(f"patterns_save_failed | {e}")

    def _load_patterns(self) -> None:
        """Load persisted patterns from disk into cache."""
        try:
            if not self._persist_path.exists():
                return
            with open(self._persist_path, encoding="utf-8") as f:
                data = json.load(f)
            cached_at = data.get("cached_at", 0.0)
            patterns = [
                LearnedPattern(
                    pattern_type=p["pattern_type"],
                    description=p["description"],
                    confidence=p["confidence"],
                    data=p.get("data", {}),
                )
                for p in data.get("patterns", [])
            ]
            self._cache = (cached_at, patterns)
            from charlie.utils.logger import get_logger
            get_logger(__name__).info(f"patterns_loaded | count={len(patterns)}")
        except Exception as e:
            from charlie.utils.logger import get_logger
            get_logger(__name__).debug(f"patterns_load_failed | {e}")

    # --- Pattern detectors ---

    def _detect_temporal(self) -> list[LearnedPattern]:
        """Detect temporal patterns: weekday vs weekend, time-of-day preferences."""
        patterns = []
        outcomes = self.tracker.get_recent_outcomes(event_type="user_response", limit=200)
        if len(outcomes) < 3:
            return patterns

        # Group by day-of-week (0=Mon ... 6=Sun) and signal type
        by_dow: dict[int, dict[str, int]] = {}
        for o in outcomes:
            from datetime import datetime as dt
            dow = dt.fromtimestamp(o.timestamp).weekday()
            by_dow.setdefault(dow, {"positive": 0, "negative": 0, "correction": 0})
            if o.outcome_type in by_dow[dow]:
                by_dow[dow][o.outcome_type] += 1

        # Detect weekday vs weekend sentiment difference
        weekday_pos = sum(by_dow.get(d, {}).get("positive", 0) for d in range(5))
        weekend_pos = sum(by_dow.get(d, {}).get("positive", 0) for d in range(5, 7))
        weekday_neg = sum(by_dow.get(d, {}).get("negative", 0) for d in range(5))
        weekend_neg = sum(by_dow.get(d, {}).get("negative", 0) for d in range(5, 7))

        total_wd = weekday_pos + weekday_neg or 1
        total_we = weekend_pos + weekend_neg or 1
        wd_ratio = weekday_pos / total_wd
        we_ratio = weekend_pos / total_we

        if abs(wd_ratio - we_ratio) > 0.3:
            weekday_better = wd_ratio > we_ratio
            patterns.append(LearnedPattern(
                pattern_type="temporal",
                description=(
                    "User prefers "
                    + ("concise answers on weekdays" if weekday_better else "concise answers on weekends")
                ),
                confidence=5,
                data={"weekday_pos_rate": round(wd_ratio, 2), "weekend_pos_rate": round(we_ratio, 2)},
            ))

        return patterns

    def _detect_behavioral(self) -> list[LearnedPattern]:
        """Detect behavioral patterns: which tools get positive vs negative signals after use."""
        patterns = []
        # For each tool, count positive vs negative user signals that follow it
        recent = self.tracker.get_recent_outcomes(event_type="tool_call", limit=500)
        tool_outcomes: dict[str, dict[str, int]] = {}
        for o in recent:
            if not o.tool_name:
                continue
            tool_outcomes.setdefault(o.tool_name, {"success": 0, "failure": 0})
            if o.outcome_type in tool_outcomes[o.tool_name]:
                tool_outcomes[o.tool_name][o.outcome_type] += 1

        for tool, counts in tool_outcomes.items():
            total = counts["success"] + counts["failure"]
            if total >= 3:
                rate = counts["success"] / total
                if rate >= 0.9:
                    patterns.append(LearnedPattern(
                        pattern_type="behavioral",
                        description=f"Tool {tool} has {int(rate*100)}% success rate",
                        confidence=total,
                        data={"tool": tool, "success_rate": rate, "total": total},
                    ))
                elif rate <= 0.3 and total >= 3:
                    patterns.append(LearnedPattern(
                        pattern_type="behavioral",
                        description=f"Tool {tool} frequently fails — consider deprecating",
                        confidence=total,
                        data={"tool": tool, "success_rate": rate, "total": total},
                    ))

        return patterns

    def _detect_workflow(self) -> list[LearnedPattern]:
        """Detect workflow patterns: which sources or approaches fail for certain task types."""
        patterns = []
        recent = self.tracker.get_recent_outcomes(event_type="tool_call", limit=500)
        # Look for tool + details combinations that fail repeatedly
        tool_failures: dict[str, int] = {}
        for o in recent:
            if o.outcome_type == "failure" and o.tool_name:
                tool_failures[o.tool_name] = tool_failures.get(o.tool_name, 0) + 1

        for tool, failures in tool_failures.items():
            if failures >= 3:
                patterns.append(LearnedPattern(
                    pattern_type="workflow",
                    description=f"Tool {tool} has failed {failures} times — investigate",
                    confidence=failures,
                    data={"tool": tool, "failure_count": failures},
                ))

        return patterns

    def _detect_agent_routing(self) -> list[LearnedPattern]:
        """Detect agent routing patterns: which tasks go to which agents."""
        patterns = []
        recent = self.tracker.get_recent_outcomes(event_type="agent_selection", limit=200)
        agent_outcomes: dict[str, dict[str, int]] = {}
        for o in recent:
            if not o.agent_name:
                continue
            agent_outcomes.setdefault(o.agent_name, {"success": 0, "failure": 0})
            if o.outcome_type in agent_outcomes[o.agent_name]:
                agent_outcomes[o.agent_name][o.outcome_type] += 1

        for agent, counts in agent_outcomes.items():
            total = counts["success"] + counts["failure"]
            if total >= 3:
                rate = counts["success"] / total
                if rate >= 0.8:
                    patterns.append(LearnedPattern(
                        pattern_type="agent_routing",
                        description=f"Agent {agent} succeeds {int(rate*100)}% of the time",
                        confidence=total,
                        data={"agent": agent, "success_rate": rate, "total": total},
                    ))

        return patterns

    def _detect_preferences(self) -> list[LearnedPattern]:
        """Detect generic preference patterns from user response signals."""
        patterns = []
        signals = self.tracker.get_user_signal_count("positive", since_hours=168)
        negatives = self.tracker.get_user_signal_count("negative", since_hours=168)
        corrections = self.tracker.get_user_signal_count("correction", since_hours=168)

        total = signals + negatives + corrections
        if total < 3:
            return patterns

        neg_rate = negatives / total

        if neg_rate > 0.3:
            patterns.append(LearnedPattern(
                pattern_type="preference",
                description="User frequently expresses dissatisfaction — consider slower/more careful responses",
                confidence=total,
                data={"negative_rate": round(neg_rate, 2), "total": total},
            ))

        return patterns

    # --- Query helpers for Brain ---

    def get_user_preferences(self) -> list[str]:
        """Return learned preferences as bullet strings for system prompt injection."""
        patterns = self.detect_patterns(min_confidence=3)
        return [p.description for p in patterns]

    def get_tool_recommendations(self) -> dict[str, list[str]]:
        """Return tools to promote or deprecate based on outcome history."""
        patterns = self.detect_patterns(min_confidence=3)
        promote = []
        deprecate = []
        for p in patterns:
            if p.pattern_type == "behavioral":
                if p.data.get("success_rate", 0) >= 0.9:
                    promote.append(p.data["tool"])
                elif p.data.get("success_rate", 0) <= 0.3:
                    deprecate.append(p.data["tool"])
            if p.pattern_type == "workflow" and p.confidence >= 5:
                deprecate.append(p.data["tool"])
        return {"promote": promote, "deprecate": deprecate}

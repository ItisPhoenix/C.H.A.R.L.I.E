"""Iteration budget and tool-turn accounting for Charlie."""

import logging
from dataclasses import dataclass, field
from typing import Dict

logger = logging.getLogger("charlie.budget")


@dataclass
class IterationBudget:
    """Tracks tool-turn budget per user utterance."""

    max_turns: int = 12
    turns_used: int = 0
    turn_cost: Dict[str, int] = field(
        default_factory=lambda: {
            "web_search": 1,
            "shell_execute": 1,
            "file_read": 1,
            "file_write": 1,
            "delegate_task": 3,
        }
    )

    def try_spend(self, tool_name: str) -> bool:
        """Attempt to spend budget for a tool. Returns True if allowed."""
        cost = self.turn_cost.get(tool_name, 1)
        if self.turns_used + cost > self.max_turns:
            logger.warning(
                "Budget exhausted for tool %s (used %d/%d)",
                tool_name,
                self.turns_used,
                self.max_turns,
            )
            return False
        self.turns_used += cost
        return True

    def is_exhausted(self) -> bool:
        return self.turns_used >= self.max_turns

    @property
    def remaining(self) -> int:
        """Number of budget units left."""
        return max(0, self.max_turns - self.turns_used)

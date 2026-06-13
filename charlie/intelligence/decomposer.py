"""
charlie/intelligence/decomposer.py

Goal Decomposition Engine — breaks complex goals into executable task graphs.

The decomposer takes a complex user goal and uses the LLM to generate
a TaskGraph with properly ordered subtasks and dependencies.
"""

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Optional

from charlie.intelligence.task_state import SubTask, TaskGraph, TaskStatus

logger = logging.getLogger("charlie.intelligence.decomposer")


# Default tool registry for LLM context (used when no dynamic registry provided)
_FALLBACK_TOOL_REGISTRY = """
Available tools:
- search: Web search via DuckDuckGo
- browser_fetch: Fetch and parse a URL
- get_news: Retrieve news briefings
- read_file: Read files from filesystem
- write_file: Write files to filesystem
- list_files: List files in a directory
- search_files: Search for files by name
- run_command: Execute shell commands
- get_system_status: Get CPU/RAM/VRAM stats
- get_pc_status: Get full system status
- open_app: Launch applications
- analyze_screen: Look at the current screen
- describe_image: Analyze an image file
- get_calendar_events: Get calendar events
- calculate: Evaluate math expressions
- weather: Get weather information
- time: Get current time
"""


@dataclass
class DecompositionResult:
    """Result of goal decomposition."""

    success: bool
    task_graph: Optional[TaskGraph] = None
    error: Optional[str] = None
    validation_errors: list = field(default_factory=list)


class GoalDecomposer:
    """
    [DEPRECATED] Use charlie.brain.orchestrator.TaskOrchestrator instead.

    Decomposes complex user goals into executable task graphs.
    Kept for backward compatibility. New code should use TaskPlanner
    from charlie.brain.orchestrator.
    """


# Mock LLM client for testing
def mock_llm_client(system_prompt: str, messages: list) -> str:
    """Mock LLM client that returns a valid task graph."""
    return json.dumps(
        {
            "tasks": [
                {
                    "id": "step1",
                    "description": "Search for relevant information",
                    "tool": "web_search",
                    "args": {"query": "topic research"},
                    "depends_on": [],
                },
                {
                    "id": "step2",
                    "description": "Read search results",
                    "tool": "file_read",
                    "args": {"path": "results.txt"},
                    "depends_on": ["step1"],
                },
                {
                    "id": "step3",
                    "description": "Write summary to file",
                    "tool": "file_write",
                    "args": {"path": "summary.txt", "content": "summary"},
                    "depends_on": ["step2"],
                },
            ],
            "reasoning": "Sequential tasks with step2 depending on step1, and step3 depending on step2",
        }
    )


if __name__ == "__main__":
    # Test the decomposer
    decomposer = GoalDecomposer(mock_llm_client)
    result = decomposer.decompose("Research a topic and write a summary")

    if result.success:
        print(f"Decomposed into {len(result.task_graph.tasks)} tasks:")
        for task in result.task_graph.tasks:
            print(f"  - {task.id}: {task.description} (tool: {task.tool})")
    else:
        print(f"Failed: {result.error}")

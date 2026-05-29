"""
charlie/intelligence/decomposer.py

Goal Decomposition Engine — breaks complex goals into executable task graphs.

Part of Phase A.2 — Core Autonomy Loops (Foundation)

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
- send_gmail: Send an email
- get_gmail_messages: Read emails
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
    Decomposes complex user goals into executable task graphs.

    Uses LLM to:
    1. Understand the goal intent
    2. Break it into ordered subtasks
    3. Identify dependencies between subtasks
    4. Generate a valid TaskGraph

    The LLM prompt includes:
    - Tool registry (names, args, descriptions)
    - Output format specification
    - Validation rules
    """

    def __init__(self, llm_client: Callable[[str, list], str], tool_registry: dict = None):
        """
        Initialize decomposer.

        Args:
            llm_client: Callable that takes (system_prompt, messages) and returns response
            tool_registry: Optional dict of {tool_name: tool_func} from brain.tools_registry.
                          If provided, builds dynamic tool list for LLM context.
        """
        self.llm_client = llm_client
        self.tool_registry = tool_registry or {}
        self._validation_attempts = 0
        self._max_validation_attempts = 3

    def decompose(self, goal: str, context: dict = None) -> DecompositionResult:
        """
        Decompose a complex goal into a TaskGraph.

        Args:
            goal: The user's complex goal
            context: Optional context (user preferences, current state, etc.)

        Returns:
            DecompositionResult with TaskGraph or error details
        """
        context = context or {}
        self._validation_attempts = 0

        system_prompt = self._build_system_prompt()
        user_message = self._build_user_message(goal, context)

        # Try decomposition with validation loop
        while self._validation_attempts < self._max_validation_attempts:
            try:
                response = self.llm_client(system_prompt, [{"role": "user", "content": user_message}])
                task_graph = self._parse_llm_response(response, goal)

                if task_graph is None:
                    return DecompositionResult(
                        success=False,
                        error="Failed to parse LLM response into TaskGraph"
                    )

                # Validate the task graph
                validation_errors = self._validate_task_graph(task_graph)
                if validation_errors:
                    self._validation_attempts += 1
                    user_message = self._build_validation_feedback(goal, validation_errors, context)
                    logger.warning(f"task_graph_validation_failed | attempts={self._validation_attempts}")
                    continue

                logger.info(f"goal_decomposed | goal={goal[:50]} | tasks={len(task_graph.tasks)}")
                return DecompositionResult(success=True, task_graph=task_graph)

            except Exception as e:
                logger.error(f"decomposition_error | {e}")
                return DecompositionResult(success=False, error=str(e))

        return DecompositionResult(
            success=False,
            error=f"Failed to generate valid task graph after {self._max_validation_attempts} attempts",
            validation_errors=validation_errors if 'validation_errors' in locals() else []
        )

    def _build_system_prompt(self) -> str:
        """Build the LLM system prompt with tool registry and output format."""
        tool_list = self._build_tool_list()
        return f"""You are a task decomposition assistant for an AI agent system.

Your job is to break down complex user goals into executable subtasks.

{tool_list}

OUTPUT FORMAT:
Respond ONLY with valid JSON in this exact format:
{{
    "tasks": [
        {{
            "id": "unique_id",
            "description": "What this subtask does",
            "tool": "tool_name",
            "args": {{"arg_name": "value"}},
            "depends_on": ["other_task_id"]  // empty list if no dependencies
        }}
    ],
    "reasoning": "Brief explanation of the decomposition"
}}

RULES:
1. Each task must have a valid tool from the registry above
2. Tasks with dependencies must reference other task IDs
3. No circular dependencies allowed (A depends on B, B depends on A)
4. Order tasks so that dependencies come before dependents
5. Use parallel execution where tasks don't depend on each other
6. Keep task descriptions clear and actionable
7. Maximum 10 tasks per decomposition
8. Each task should be atomic (do one thing well)

Respond with ONLY the JSON, no additional text."""

    def _build_tool_list(self) -> str:
        """Build tool list string from dynamic registry or fallback."""
        if not self.tool_registry:
            return _FALLBACK_TOOL_REGISTRY

        lines = ["Available tools:"]
        for name in sorted(self.tool_registry.keys()):
            func = self.tool_registry[name]
            doc = (func.__doc__ or "").split("\n")[0].strip()
            desc = doc if doc else f"Tool: {name}"
            lines.append(f"- {name}: {desc}")
        return "\n".join(lines)

    def _build_user_message(self, goal: str, context: dict) -> str:
        """Build the user message with goal and context."""
        msg = f"Decompose this goal into subtasks:\n\n{goal}"

        if context.get("deadline"):
            msg += f"\n\nDeadline: {context['deadline']}"

        if context.get("preferred_order"):
            msg += f"\n\nPreferred order: {context['preferred_order']}"

        if context.get("constraints"):
            msg += f"\n\nConstraints: {context['constraints']}"

        return msg

    def _build_validation_feedback(self, goal: str, errors: list, context: dict) -> str:
        """Build feedback message for LLM to fix validation errors."""
        error_list = "\n".join(f"- {e}" for e in errors)
        return f"""Fix the following validation errors in your task graph:

{error_list}

Original goal: {goal}

Provide corrected JSON with the same format."""

    def _parse_llm_response(self, response: str, goal: str) -> Optional[TaskGraph]:
        """Parse LLM response into a TaskGraph."""
        try:
            # Extract JSON from response
            json_str = self._extract_json(response)
            data = json.loads(json_str)

            tasks = []
            for task_data in data.get("tasks", []):
                task = SubTask(
                    id=task_data.get("id", str(uuid.uuid4())[:8]),
                    description=task_data.get("description", ""),
                    tool=task_data.get("tool", ""),
                    args=task_data.get("args", {}),
                    depends_on=task_data.get("depends_on", []),
                )
                tasks.append(task)

            deadline = None
            if context := data.get("context"):
                if deadline_str := context.get("deadline"):
                    try:
                        deadline = datetime.fromisoformat(deadline_str)
                    except (ValueError, TypeError):
                        pass

            return TaskGraph(
                goal=goal,
                tasks=tasks,
                max_parallel=data.get("max_parallel", 3),
                deadline=deadline,
            )

        except json.JSONDecodeError as e:
            logger.error(f"json_parse_error | {e}")
            return None
        except Exception as e:
            logger.error(f"parse_error | {e}")
            return None

    def _extract_json(self, response: str) -> str:
        """Extract JSON from LLM response (handles markdown code blocks)."""
        # Try to find JSON in code blocks
        if "```json" in response:
            start = response.find("```json") + 7
            end = response.find("```", start)
            if end > start:
                return response[start:end].strip()

        if "```" in response:
            start = response.find("```") + 3
            end = response.find("```", start)
            if end > start:
                return response[start:end].strip()

        # Try to find JSON directly (look for { and })
        start = response.find("{")
        end = response.rfind("}") + 1
        if start >= 0 and end > start:
            return response[start:end]

        return response.strip()

    def _validate_task_graph(self, graph: TaskGraph) -> list:
        """
        Validate a task graph.

        Returns list of validation errors (empty if valid).
        """
        errors = []

        # Check for circular dependencies
        if graph.has_circular_dependencies():
            errors.append("Circular dependency detected in task graph")

        # Check that all referenced tools exist
        if self.tool_registry:
            valid_tools = set(self.tool_registry.keys())
        else:
            # Fallback: use default tool names from _FALLBACK_TOOL_REGISTRY
            # Include both current and legacy names for backward compatibility
            valid_tools = {
                "search", "browser_fetch", "get_news", "read_file", "write_file",
                "list_files", "search_files", "run_command", "get_system_status",
                "get_pc_status", "open_app", "analyze_screen", "describe_image",
                "send_gmail", "get_gmail_messages", "get_calendar_events",
                "calculate", "weather", "time",
                "web_search", "file_read", "file_write", "terminal", "code_editor",
                "screenshot", "system_info", "github_api", "gmail_api",
                "calendar_api", "notion_api", "spotify_api",
                "browser_github", "browser_gmail",
            }

        if valid_tools is not None:
            for task in graph.tasks:
                if task.tool not in valid_tools:
                    errors.append(f"Unknown tool '{task.tool}' in task '{task.id}'")

        # Check that all dependencies reference existing tasks
        task_ids = {t.id for t in graph.tasks}
        for task in graph.tasks:
            for dep_id in task.depends_on:
                if dep_id not in task_ids:
                    errors.append(f"Task '{task.id}' depends on non-existent task '{dep_id}'")

        # Check for empty descriptions
        for task in graph.tasks:
            if not task.description.strip():
                errors.append(f"Task '{task.id}' has empty description")

        return errors

    def replan(self, task_graph: TaskGraph, failed_task: SubTask, error: str, context: dict = None) -> DecompositionResult:
        """Re-plan after a subtask failure. Sends failure context to LLM for revised plan.

        Args:
            task_graph: The current task graph
            failed_task: The subtask that failed
            error: The error message
            context: Optional additional context

        Returns:
            DecompositionResult with revised TaskGraph
        """
        context = context or {}

        # Build status summary of completed/failed tasks
        completed = [t for t in task_graph.tasks if t.status == TaskStatus.COMPLETED]
        failed = [t for t in task_graph.tasks if t.status == TaskStatus.FAILED]
        pending = [t for t in task_graph.tasks if t.status == TaskStatus.PENDING]

        completed_str = "\n".join(
            f"  - {t.id}: {t.description} → Result: {(t.result or '')[:200]}"
            for t in completed
        ) or "  (none)"
        failed_str = "\n".join(
            f"  - {t.id}: {t.description} → Error: {t.error or error}"
            for t in failed
        ) or "  (none)"

        replan_prompt = f"""The original goal was: {task_graph.goal}

COMPLETED TASKS:
{completed_str}

FAILED TASKS:
{failed_str}

PENDING TASKS (not yet executed):
{chr(10).join(f'  - {t.id}: {t.description}' for t in pending) or '  (none)'}

The task '{failed_task.id}' failed with error: {error}

Generate a REVISED plan that:
1. Keeps the completed work (don't repeat it)
2. Works around the failure
3. Uses alternative approaches if the original tool failed
4. Still achieves the original goal

Provide the revised plan as JSON with the same format."""

        system_prompt = self._build_system_prompt()

        try:
            response = self.llm_client(system_prompt, [{"role": "user", "content": replan_prompt}])
            new_graph = self._parse_llm_response(response, task_graph.goal)
            if new_graph is None:
                return DecompositionResult(success=False, error="Failed to parse revised plan")

            validation_errors = self._validate_task_graph(new_graph)
            if validation_errors:
                return DecompositionResult(
                    success=False,
                    error=f"Revised plan has validation errors: {validation_errors}",
                    validation_errors=validation_errors
                )

            logger.info(f"replan_success | original_tasks={len(task_graph.tasks)} | new_tasks={len(new_graph.tasks)}")
            return DecompositionResult(success=True, task_graph=new_graph)

        except Exception as e:
            logger.error(f"replan_error | {e}")
            return DecompositionResult(success=False, error=str(e))

    def can_parallelize(self, task_a: SubTask, task_b: SubTask) -> bool:
        """
        Check if two tasks can be executed in parallel.

        Two tasks can be parallelized if neither depends on the other.
        """
        if task_a.id == task_b.id:
            return False

        # A can run in parallel with B if:
        # - A doesn't depend on B
        # - B doesn't depend on A
        return task_a.id not in task_b.depends_on and task_b.id not in task_a.depends_on

    def estimate_duration(self, task: SubTask) -> timedelta:
        """
        Estimate how long a task will take to complete.

        This is a rough estimate based on tool type.
        """
        # Tool duration estimates (in seconds)
        tool_estimates = {
            "github_api": 5,
            "gmail_api": 3,
            "calendar_api": 2,
            "notion_api": 4,
            "spotify_api": 2,
            "browser_github": 10,
            "browser_gmail": 8,
            "web_search": 5,
            "file_read": 1,
            "file_write": 2,
            "terminal": 10,
            "code_editor": 15,
            "screenshot": 2,
            "system_info": 1,
        }

        base_time = tool_estimates.get(task.tool, 5)

        # Adjust based on args complexity
        complexity_factor = 1 + (len(task.args) * 0.1)

        return timedelta(seconds=base_time * complexity_factor)


# Mock LLM client for testing
def mock_llm_client(system_prompt: str, messages: list) -> str:
    """Mock LLM client that returns a valid task graph."""
    return json.dumps({
        "tasks": [
            {
                "id": "step1",
                "description": "Search for relevant information",
                "tool": "web_search",
                "args": {"query": "topic research"},
                "depends_on": []
            },
            {
                "id": "step2",
                "description": "Read search results",
                "tool": "file_read",
                "args": {"path": "results.txt"},
                "depends_on": ["step1"]
            },
            {
                "id": "step3",
                "description": "Write summary to file",
                "tool": "file_write",
                "args": {"path": "summary.txt", "content": "summary"},
                "depends_on": ["step2"]
            }
        ],
        "reasoning": "Sequential tasks with step2 depending on step1, and step3 depending on step2"
    })


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

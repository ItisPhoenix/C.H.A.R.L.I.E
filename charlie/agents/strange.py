"""Doctor Strange - Planner agent.

Reads high-level tasks, creates sub-task graphs, maps dependencies.
"""


from charlie.agents.base import BaseAgent


class StrangeAgent(BaseAgent):
    name = "Doctor Strange"
    role = "planner"
    description = (
        "Reads high-level tasks from J.A.R.V.I.S., creates sub-task graphs, "
        "and maps dependencies onto the blackboard."
    )

    async def _do_action(self, task_name: str, task=None) -> str:
        task_id = task.id if task else None
        parent_id = task.parent_task_id if task and task.parent_task_id else task_id
        self.log(f"Planning: {task_name}")

        # Decompose into worker sub-tasks
        steps = self._decompose(task_name)

        created_ids = []
        for i, (name, agent) in enumerate(steps):
            deps = [created_ids[i - 1]] if i > 0 else []
            sub = self.blackboard.add_task(
                name=name,
                assigned_to=agent,
                parent_task_id=parent_id,
                dependencies=deps,
            )
            created_ids.append(sub.id)
            self.log(f"Created sub-task: {sub.name} -> {agent}")

        return f"Created {len(steps)} sub-tasks"

    def _decompose(self, request: str) -> list:
        """Simple heuristic decomposition. In production, use LLM."""
        lower = request.lower()
        steps = []
        if "api" in lower or "build" in lower:
            steps.append(("Design schema", "Shuri"))
            steps.append(("Implement code", "Shuri"))
            steps.append(("Test endpoints", "Vision"))
        elif "research" in lower or "find" in lower or "search" in lower:
            steps.append(("Search web", "E.D.I.T.H."))
            steps.append(("Summarize findings", "K.A.R.E.N."))
        elif "fix" in lower or "debug" in lower:
            steps.append(("Analyze error", "Vision"))
            steps.append(("Apply fix", "Shuri"))
        else:
            steps.append(("Process task", "Shuri"))
            steps.append(("Verify result", "Vision"))
        return steps

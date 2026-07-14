"""Base class for all Charlie agents."""

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Tuple

if __name__ != "__main__":
    from charlie.blackboard import Blackboard


class BaseAgent(ABC):
    """Abstract base for swarm agents. Each agent has a name, role, and tools."""

    name: str = "BaseAgent"
    role: str = "worker"
    description: str = ""
    _action_verb: str = "Working"
    _done_log: str = "Done"
    _fail_log: str = "Failed"
    # Tool names (from charlie.tools.registry) this agent may call via
    # _call_tool. Empty by default -- subclasses that need real tool access
    # override it (see edith.py, karen.py, etc.). Deliberately does NOT
    # include shell_execute/delegate_to_agent/graph_consolidate/memory for
    # any agent: those are supervised-Brain-only (see agents design notes).
    allowed_tools: Tuple[str, ...] = ()

    def __init__(self, blackboard: "Blackboard", llm_client: Any = None) -> None:
        self.blackboard = blackboard
        self.llm_client = llm_client
        self.logger = logging.getLogger(f"charlie.agents.{self.name}")
        self.blackboard.register_agent(self.name)

    async def execute(self, task_id: str) -> Dict[str, Any]:
        """Template method: fetches task, runs _do_action, handles status/error."""
        task = self.blackboard.get_task(task_id)
        if not task:
            return {"status": "failed", "result": "Task not found"}

        self._update_status("working", task_id)
        self.log(f"{self._action_verb}: {task.name}")

        try:
            result = await self._do_action(task.name, task)
            self.blackboard.update_task(task_id, status="done", result=result)
            self.log(self._done_log)
            self._update_status("idle")
            return {"status": "done", "result": result}
        except Exception as exc:
            self.log(f"{self._fail_log}: {exc}")
            self._update_status("error")
            self.blackboard.update_task(task_id, status="failed", result=str(exc))
            return {"status": "failed", "result": str(exc)}

    @abstractmethod
    async def _do_action(self, task_name: str, task) -> str:
        """Override in subclass: perform the actual work. Return result string.

        ``task`` is the live Blackboard Task object, passed so planner agents
        can read fields like ``parent_task_id``.
        """
        ...

    def log(self, message: str) -> None:
        self.logger.info("[%s] %s", self.name, message)
        card = self.blackboard.get_agents().get(self.name)
        if card:
            card.logs.append(message)
            # Keep only last 50 log entries
            if len(card.logs) > 50:
                card.logs = card.logs[-50:]

    def _update_status(self, status: str, task_id: str = "") -> None:
        self.blackboard.update_agent(
            self.name, status=status, current_task=task_id
        )

    async def _call_tool(self, name: str, arguments: Dict[str, Any]) -> str:
        """Call a tool from the shared registry, gated by allowed_tools.

        Swarm agents run unsupervised in the background (no human watching
        turn-by-turn like the main Brain conversation), so each agent gets a
        narrow, explicit tool allowlist instead of open-ended access. The
        registry import is local/lazy: charlie/tools.py imports
        `from charlie.agents import AGENT_REGISTRY` at module level, so a
        top-level `from charlie.tools import registry` here would create a
        circular import.
        """
        if name not in self.allowed_tools:
            self.logger.warning(
                "[%s] Rejected tool call '%s': not in allowed_tools %s",
                self.name, name, self.allowed_tools,
            )
            return f"Error: tool '{name}' is not permitted for agent '{self.name}'."

        from charlie.tools import get_path_gate_reason, is_shell_command_gated, registry

        # Gated shell keywords / sensitive paths (see charlie.tools) normally
        # go through an interactive approve/decline prompt -- see
        # charlie.core.Brain.request_tool_approval. Swarm agents run
        # unsupervised with no human turn to prompt, so for them a gate is
        # a hard block instead.
        gate_reason: Optional[str] = None
        if name == "shell_execute":
            gate_reason = is_shell_command_gated(arguments.get("command", ""))
        elif name in ("file_read", "file_write"):
            gate_reason = get_path_gate_reason(arguments.get("path", ""))
        if gate_reason:
            self.logger.warning(
                "[%s] Rejected tool call '%s': requires approval (%s), no human in the loop for agents",
                self.name, name, gate_reason,
            )
            return (
                f"Error: tool '{name}' blocked -- requires approval ({gate_reason}), "
                "not available to unsupervised agents."
            )

        self.log(f"Calling tool: {name}({arguments})")
        result = await asyncio.get_running_loop().run_in_executor(
            None, registry.execute_tool, name, arguments
        )
        return result

    async def _complete(self, prompt: str, max_tokens: int = 1000) -> str:
        """Call the LLM chat endpoint and return the assistant message text.

        Injects the auth headers carried by the agent's LLM client (built from
        the small-LLM key) so agent calls authenticate the same way the Brain's
        own call sites do. Falls back to a placeholder string when no client is
        wired (e.g. tests), keeping the agent callable without a live LLM.
        """
        if not self.llm_client:
            return f"[{self.name} placeholder - LLM not connected] Would process: {prompt[:80]}..."
        response = await self.llm_client.post(
            "chat/completions",
            json={
                "model": self.llm_client.model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
            },
        )
        response.raise_for_status()
        data = response.json()
        usage = data.get("usage") or {}
        tokens = usage.get("total_tokens", 0)
        if tokens:
            self.blackboard.add_token_cost(self.name, tokens)
        return data["choices"][0]["message"]["content"]

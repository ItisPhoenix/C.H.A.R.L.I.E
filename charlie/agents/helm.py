"""H.E.L.M. - autonomous desktop operator agent.

Runs the see-act-verify loop unattended (charlie/core.py's H.E.L.M. persona
covers the same loop for the foreground chat turn -- this is the swarm
version, dispatched via delegate_to_agent for background desktop work).
"""

import json
import re
from typing import Any, Dict, Tuple

from charlie.agents.base import BaseAgent
from charlie.config import config
from charlie.desktop import session

_MAX_CONSECUTIVE_FAILURES = 3
_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_plan(raw: str) -> Dict[str, Any]:
    """Best-effort JSON extraction -- small local models routinely wrap the
    object in markdown fences or a sentence of preamble."""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        pass
    match = _JSON_BLOCK_RE.search(raw or "")
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return {}


class HELM(BaseAgent):
    name = "H.E.L.M."
    description = "Operates the desktop unattended: windows, apps, mouse, keyboard, vision."
    _action_verb = "Operating desktop"
    _done_log = "Desktop task complete"
    _fail_log = "Desktop task failed"

    @property
    def allowed_tools(self) -> Tuple[str, ...]:
        from charlie.tools import registry

        return tuple(n for n in registry.get_tool_names() if n.startswith("desktop_")) + (
            "shell_execute",
        )

    async def _do_action(self, task_name: str, task=None) -> str:
        owner = self._task_id or self.name
        if not session.acquire_desktop(owner):
            return "Error: Desktop is in use by another task -- try again later."
        if session.user_idle_seconds() < config.desktop_idle_threshold_s:
            session.release_desktop(owner)
            return (
                f"Error: user is active -- waiting for {config.desktop_idle_threshold_s:.0f}s "
                "of idle desktop before running unattended."
            )
        try:
            return await self._operate_loop(task_name)
        finally:
            session.release_desktop(owner)

    async def _operate_loop(self, task_name: str) -> str:
        last_own_tick = session._last_input_tick_ms()
        history: list = []
        consecutive_failures = 0

        for _step in range(config.desktop_max_actions):
            if session._last_input_tick_ms() > last_own_tick:
                return "Paused: you started using the computer."

            # ponytail: text-only planning over desktop_observe's mark/OCR
            # text, not the full vision-LLM screenshot follow-up Brain's
            # chat loop uses (charlie/core.py:_select_followup_route) --
            # unmarked-target (canvas/icon) tasks won't ground correctly
            # here. Upgrade path: give this agent its own vision-capable
            # client (config.vision_llm_*) and route screenshot results
            # through it the same way core.py does.
            observation = await self._call_tool("desktop_observe", {})
            plan = _parse_plan(await self._complete(self._plan_prompt(task_name, observation, history)))

            if plan.get("done"):
                return str(plan.get("result", "Task complete."))

            tool_name = plan.get("tool")
            arguments = plan.get("arguments") or {}
            if tool_name not in self.allowed_tools:
                history.append(f"Rejected plan: tool '{tool_name}' not permitted.")
                consecutive_failures += 1
            else:
                result = await self._call_tool(tool_name, arguments)
                history.append(f"{tool_name}({arguments}) -> {result}")
                consecutive_failures = 0 if not str(result).startswith("Error") else consecutive_failures + 1
                last_own_tick = session._last_input_tick_ms()

            if consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                return f"Failed: {_MAX_CONSECUTIVE_FAILURES} consecutive failed actions."

        return f"Stopped: reached the action budget ({config.desktop_max_actions}) without completing."

    def _plan_prompt(self, task_name: str, observation: str, history: list) -> str:
        recent = "\n".join(history[-5:]) or "(none yet)"
        return f"""You are operating the desktop unattended to accomplish this task: {task_name}

Current screen (desktop_observe):
{observation}

Recent actions:
{recent}

Available tools: {", ".join(self.allowed_tools)}

Decide the single next action. Respond with ONLY a JSON object, no other text:
{{"tool": "<tool_name>", "arguments": {{...}}}}
or, once the task is accomplished:
{{"done": true, "result": "<summary of what was accomplished>"}}"""

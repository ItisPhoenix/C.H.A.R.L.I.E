"""FRIDAY - Code generation and file operations specialist."""

from charlie.agents.base import BaseAgent


class FRIDAY(BaseAgent):
    name = "F.R.I.D.A.Y."
    description = "Writes code, manages files, and executes technical tasks."
    _action_verb = "Coding"
    _done_log = "Completed"
    _fail_log = "Failed"
    _success_msg = "Code task completed"

    async def _do_action(self, task_name: str, task=None) -> str:
        prompt = f"""Write code for: {task_name}

Return ONLY the code, properly formatted and commented.
Do not include markdown fences."""
        code = await self._code(prompt)
        return f"Generated {len(code)} chars of code"

    async def _code(self, prompt: str) -> str:
        return await self._complete(prompt, max_tokens=3000)

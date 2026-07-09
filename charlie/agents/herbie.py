"""HERBIE - Verification and validation specialist."""

from charlie.agents.base import BaseAgent


class HERBIE(BaseAgent):
    name = "H.E.R.B.I.E."
    description = "Verifies completed tasks against acceptance criteria."
    _action_verb = "Verifying"
    _done_log = "Verification passed"
    _fail_log = "Verification failed"
    _success_msg = "Verification completed"

    async def _do_action(self, task_name: str, task=None) -> str:
        prompt = f"""Verify this deliverable: {task_name}

Check for completeness, correctness, and quality.
Return a structured verification report."""
        report = await self._verify(prompt)
        return report

    async def _verify(self, prompt: str) -> str:
        return await self._complete(prompt, max_tokens=1000)

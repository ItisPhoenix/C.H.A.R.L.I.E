"""KAREN - System diagnostics and health monitoring specialist."""

from charlie.agents.base import BaseAgent


class KAREN(BaseAgent):
    name = "K.A.R.E.N."
    description = "Runs system diagnostics, monitors health, and suggests fixes."
    _action_verb = "Running diagnostics"
    _done_log = "Diagnostics complete"
    _fail_log = "Diagnostics failed"
    _success_msg = "Diagnostics completed"

    async def _do_action(self, task_name: str, task=None) -> str:
        prompt = f"""Run diagnostics for: {task_name}

Check system health, identify issues, and recommend fixes.
Return a structured diagnostic report."""
        report = await self._diagnose(prompt)
        return report

    async def _diagnose(self, prompt: str) -> str:
        return await self._complete(prompt, max_tokens=1000)

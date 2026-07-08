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
        if not self.llm_client:
            return f"[KAREN placeholder - LLM not connected] Would diagnose: {prompt[:80]}..."
        response = await self.llm_client.post(
            "/chat/completions",
            json={
                "model": self.llm_client.model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 1000,
            },
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]

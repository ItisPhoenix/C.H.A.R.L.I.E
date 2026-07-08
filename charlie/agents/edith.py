"""EDITH - Research and intelligence specialist."""

from charlie.agents.base import BaseAgent


class EDITH(BaseAgent):
    name = "E.D.I.T.H."
    description = "Researches topics, gathers intelligence, and produces analysis."
    _action_verb = "Researching"
    _done_log = "Research complete"
    _fail_log = "Research failed"
    _success_msg = "Research completed"

    async def _do_action(self, task_name: str, task=None) -> str:
        prompt = f"""Research this topic thoroughly: {task_name}

Gather information, analyze findings, and produce a structured report."""
        return await self._research(prompt)

    async def _research(self, prompt: str) -> str:
        if not self.llm_client:
            return f"[EDITH placeholder - LLM not connected] Would research: {prompt[:80]}..."
        response = await self.llm_client.post(
            "/chat/completions",
            json={
                "model": self.llm_client.model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 2000,
            },
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]

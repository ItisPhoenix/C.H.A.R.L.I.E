"""AIDA - Content creation specialist."""

from charlie.agents.base import BaseAgent


class AIDA(BaseAgent):
    name = "A.I.D.A."
    description = "Creates marketing copy, emails, reports, and structured documents."
    _action_verb = "Creating"
    _done_log = "Created"
    _fail_log = "Creation failed"
    _success_msg = "Content created"

    async def _do_action(self, task_name: str, task=None) -> str:
        prompt = f"""Create high-quality content based on this brief: {task_name}

Return ONLY the generated content, properly formatted."""
        content = await self._generate(prompt)
        return content

    async def _generate(self, prompt: str) -> str:
        if not self.llm_client:
            return f"[AIDA placeholder - LLM not connected] Would generate: {prompt[:80]}..."
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

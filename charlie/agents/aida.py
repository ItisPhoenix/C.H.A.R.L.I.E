"""AIDA - Content creation specialist."""

from charlie.agents.base import BaseAgent


class AIDA(BaseAgent):
    name = "A.I.D.A."
    description = "Creates marketing copy, emails, reports, and structured documents."
    _action_verb = "Creating"
    _done_log = "Created"
    _fail_log = "Creation failed"
    allowed_tools = ("vector_memory", "graph_query")

    async def _do_action(self, task_name: str, task=None) -> str:
        prompt = f"""Create high-quality content based on this brief: {task_name}

Return ONLY the generated content, properly formatted."""
        content = await self._generate(prompt)
        return content

    async def _generate(self, prompt: str) -> str:
        return await self._complete(prompt, max_tokens=2000)

"""EDITH - Research and intelligence specialist."""

from charlie.agents.base import BaseAgent


class EDITH(BaseAgent):
    name = "E.D.I.T.H."
    description = "Researches topics, gathers intelligence, and produces analysis."
    _action_verb = "Researching"
    _done_log = "Research complete"
    _fail_log = "Research failed"

    async def _do_action(self, task_name: str, task=None) -> str:
        prompt = f"""Research this topic thoroughly: {task_name}

Gather information, analyze findings, and produce a structured report."""
        return await self._research(prompt)

    async def _research(self, prompt: str) -> str:
        return await self._complete(prompt, max_tokens=2000)

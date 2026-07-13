"""EDITH - Research and intelligence specialist."""

from charlie.agents.base import BaseAgent


class EDITH(BaseAgent):
    name = "E.D.I.T.H."
    description = "Researches topics, gathers intelligence, and produces analysis."
    _action_verb = "Researching"
    _done_log = "Research complete"
    _fail_log = "Research failed"
    allowed_tools = ("web_search", "vector_memory", "session_search", "graph_query")

    async def _do_action(self, task_name: str, task=None) -> str:
        search_results = await self._call_tool("web_search", {"query": task_name})

        prompt = f"""Research this topic thoroughly: {task_name}

Web search results:
{search_results}

Using the search results above, gather information, analyze findings, and
produce a structured report."""
        return await self._research(prompt)

    async def _research(self, prompt: str) -> str:
        return await self._complete(prompt, max_tokens=2000)

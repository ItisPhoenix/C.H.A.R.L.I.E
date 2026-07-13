"""KAREN - System diagnostics and health monitoring specialist."""

from charlie.agents.base import BaseAgent

# Maps task-name keywords to the matching system_diagnostics check. Falls
# back to "cpu" (a reasonable general-health signal) when nothing matches.
_CHECK_KEYWORDS = {
    "disk": ("disk", "storage", "space"),
    "memory": ("memory", "ram"),
    "processes": ("process",),
    "network": ("network", "connection", "internet"),
    "cpu": ("cpu", "processor", "load"),
}


def _select_check(task_name: str) -> str:
    lower = task_name.lower()
    for check, keywords in _CHECK_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            return check
    return "cpu"


class KAREN(BaseAgent):
    name = "K.A.R.E.N."
    description = "Runs system diagnostics, monitors health, and suggests fixes."
    _action_verb = "Running diagnostics"
    _done_log = "Diagnostics complete"
    _fail_log = "Diagnostics failed"
    allowed_tools = ("system_diagnostics",)

    async def _do_action(self, task_name: str, task=None) -> str:
        check = _select_check(task_name)
        diagnostic_output = await self._call_tool("system_diagnostics", {"check": check})

        prompt = f"""Run diagnostics for: {task_name}

Diagnostic ({check}) output:
{diagnostic_output}

Using the diagnostic output above, check system health, identify issues, and
recommend fixes. Return a structured diagnostic report."""
        report = await self._diagnose(prompt)
        return report

    async def _diagnose(self, prompt: str) -> str:
        return await self._complete(prompt, max_tokens=1000)

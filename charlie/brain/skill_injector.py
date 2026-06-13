"""
Skill Injector — Injects skill content into agent system prompts.

Loads skill content via SkillLoader and appends it as delimited sections
to system prompts. Supports always-mode and on-demand injection with a
configurable token budget.
"""

from __future__ import annotations

from charlie.utils.logger import get_logger

logger = get_logger(__name__)

# Rough approximation: 1 token ~= 4 characters
_CHARS_PER_TOKEN = 4


class SkillInjector:
    """Injects skill content into agent system prompts."""

    def __init__(self, skill_loader=None):
        self.skill_loader = skill_loader
        self._token_budget = 4000  # Max tokens for skill content

    def inject_skills(self, system_prompt: str, skill_names: list[str]) -> str:
        """Inject skill content into a system prompt.

        1. Load each skill's content via SkillLoader
        2. Append as "## Skill: <name>\\n<content>" sections
        3. Respect token budget (always-mode skills first, then on-demand)
        """
        if not self.skill_loader or not skill_names:
            return system_prompt

        sections: list[str] = []
        total_chars = 0
        max_chars = self._token_budget * _CHARS_PER_TOKEN

        for skill_name in skill_names:
            skill = self.skill_loader.get_skill(skill_name)
            if not skill or not skill.enabled:
                continue
            if not skill.content:
                continue

            section = f"## Skill: {skill.name}\n{skill.content}"
            if total_chars + len(section) > max_chars:
                logger.warning(
                    "skill_inject_budget_exceeded | skill=%s | remaining_budget=%d",
                    skill_name,
                    max_chars - total_chars,
                )
                break
            sections.append(section)
            total_chars += len(section)

        if not sections:
            return system_prompt

        return system_prompt + "\n\n" + "\n\n---\n\n".join(sections)

    def inject_on_demand(self, system_prompt: str, task: str, available_skills: list) -> str:
        """Inject skills whose tags match the task content.

        1. Check task text against skill tags
        2. Inject matching on-demand skills
        """
        if not self.skill_loader:
            return system_prompt

        task_lower = task.lower()
        matching_skills: list[str] = []

        for skill in available_skills:
            if not skill.enabled or skill.inject_mode != "on_demand":
                continue
            if any(tag in task_lower for tag in skill.tags):
                matching_skills.append(skill.name)

        if matching_skills:
            return self.inject_skills(system_prompt, matching_skills)
        return system_prompt

    def get_always_skills(self) -> list[str]:
        """Get names of skills with inject_mode='always'."""
        if not self.skill_loader:
            return []
        return [s.name for s in self.skill_loader.get_skills_by_mode("always")]

    def set_token_budget(self, tokens: int) -> None:
        """Set the maximum token budget for skill injection."""
        self._token_budget = tokens

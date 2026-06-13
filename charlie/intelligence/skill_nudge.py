"""
charlie/intelligence/skill_nudge.py

Auto-create skills after complex tasks.
Reviews session data and decides if an approach is reusable.
"""

import logging
import os
import time
from typing import Optional

logger = logging.getLogger("charlie.intelligence.skill_nudge")

# Threshold for triggering a skill nudge
NUDGE_THRESHOLD = 5

# Minimum tools used to consider a skill
MIN_TOOLS_FOR_SKILL = 3


class SkillNudgeEngine:
    """Reviews complex sessions and auto-creates reusable skills."""

    def __init__(self, skills_dir: str = "charlie/skills"):
        self._skills_dir = skills_dir
        os.makedirs(skills_dir, exist_ok=True)
        logger.info("skill_nudge_initialized | dir=%s", skills_dir)

    def should_nudge(self, step_count: int) -> bool:
        """Check if a session should trigger a skill nudge."""
        return step_count >= NUDGE_THRESHOLD

    def review_session(
        self,
        session_summary: dict,
        llm_client=None,
    ) -> Optional[dict]:
        """Review a session and decide if a skill should be created.

        Args:
            session_summary: Dict with steps, tools_used, args, outcomes.
            llm_client: LLM client for analysis.

        Returns:
            Dict with skill details if skill should be created, None otherwise.
        """
        tools_used = session_summary.get("tools_used", [])

        if len(tools_used) < MIN_TOOLS_FOR_SKILL:
            logger.debug("nudge_skip | too few tools (%d)", len(tools_used))
            return None

        if llm_client:
            try:
                return self._llm_review(session_summary, llm_client)
            except Exception as e:
                logger.warning("llm_nudge_failed | %s", e)

        return self._heuristic_review(session_summary)

    def _llm_review(self, session_summary: dict, llm_client) -> Optional[dict]:
        """Use LLM to analyze if the session represents a reusable skill."""
        import asyncio

        steps_text = "\n".join(
            f"  Step {s.get('step', i + 1)}: {s.get('tool', '?')}({s.get('args', {})}) → {str(s.get('output', ''))[:200]}"
            for i, s in enumerate(session_summary.get("steps", []))
        )

        prompt = f"""Analyze this tool usage session and decide if it represents a reusable skill.

STEPS:
{steps_text}

TOOLS USED: {", ".join(session_summary.get("tools_used", []))}

Rules for creating a skill:
1. The steps must form a coherent, repeatable workflow
2. The workflow should be useful for future similar tasks
3. NOT every complex session is a skill — one-off debugging or exploration is NOT a skill
4. Good skills: research workflows, file processing pipelines, deployment checklists, code review patterns
5. Bad skills: "debugged a specific error", "explored a codebase", "fixed a typo"

Respond with JSON (no markdown):
{{"create_skill": true/false, "name": "skill-name", "category": "category", "description": "one-line description", "steps": ["step 1 description", "step 2 description"]}}"""

        response = asyncio.run(
            llm_client.complete(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=500,
            )
        )

        if not response or not response.content:
            return None

        import json

        content = response.content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        try:
            result = json.loads(content)
            if result.get("create_skill"):
                logger.info("nudge_approved | name=%s", result.get("name"))
                return result
        except json.JSONDecodeError:
            logger.warning("nudge_json_failed | content=%s", content[:200])

        return None

    def _heuristic_review(self, session_summary: dict) -> Optional[dict]:
        """Simple heuristic-based review without LLM."""
        tools_used = session_summary.get("tools_used", [])
        steps = session_summary.get("steps", [])

        # Check for common skill patterns
        # Pattern: search → read → write (research workflow)
        if "search" in tools_used and "read_file" in tools_used and len(steps) >= 4:
            return {
                "create_skill": True,
                "name": "research-workflow",
                "category": "research",
                "description": "Automated research and documentation workflow",
                "steps": [f"Use {s.get('tool', '?')}" for s in steps],
            }

        # Pattern: multiple file operations (file processing)
        file_ops = [t for t in tools_used if t in ("read_file", "write_file", "edit_file")]
        if len(file_ops) >= 3:
            return {
                "create_skill": True,
                "name": "file-processing",
                "category": "automation",
                "description": "File processing and transformation workflow",
                "steps": [f"Use {s.get('tool', '?')}" for s in steps],
            }

        return None

    def create_skill(self, skill_data: dict) -> str:
        """Create a Hermes-compatible skill directory.

        Returns the path to the created skill.
        """
        name = skill_data.get("name", "unnamed-skill")
        category = skill_data.get("category", "general")
        description = skill_data.get("description", "")
        steps = skill_data.get("steps", [])

        skill_dir = os.path.join(self._skills_dir, category, name)
        os.makedirs(skill_dir, exist_ok=True)
        os.makedirs(os.path.join(skill_dir, "references"), exist_ok=True)

        # Write SKILL.md
        skill_md = f"""---
name: {name}
description: >
  {description}
---

# {name.replace("-", " ").title()}

## Purpose
{description}

## Process
{chr(10).join(f"{i + 1}. {step}" for i, step in enumerate(steps))}

## Boundaries
- Use when the task matches this workflow pattern
- Do not use for one-off or exploratory tasks

## Created
- Auto-generated by CHARLIE's skill nudge system
- Created: {time.strftime("%Y-%m-%d %H:%M")}
"""

        with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(skill_md)

        logger.info("skill_created | path=%s | name=%s", skill_dir, name)
        return skill_dir

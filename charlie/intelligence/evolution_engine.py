"""
charlie/intelligence/evolution_engine.py

Self-Evolution Engine — Optimizes skills and prompts using real conversation data.
Inspired by Hermes Agent's DSPy/GEPA optimization.

Runs as a weekly background task. Builds evaluation datasets from
OutcomeTracker records, reviews skill performance, and deploys improvements.

Uses free cloud APIs (OpenRouter, NVIDIA NIM, Groq) — not local LLMs.
"""

import json
import logging
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("charlie.intelligence.evolution")

EVOLUTION_LOG = Path("scratch/evolution_log.json")


class EvolutionEngine:
    """Optimizes skills and prompts using real usage data."""

    def __init__(self, log_path: Path = EVOLUTION_LOG):
        self._log_path = log_path
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._runs: list[dict] = []
        self._load_log()

    def _load_log(self) -> None:
        """Load evolution history from disk."""
        try:
            if self._log_path.exists():
                self._runs = json.loads(self._log_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("evolution_log_load_failed | %s", e)
            self._runs = []

    def _save_log(self) -> None:
        """Save evolution history to disk."""
        try:
            self._log_path.write_text(
                json.dumps(self._runs, indent=2, default=str),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error("evolution_log_save_failed | %s", e)

    def run_evolution(
        self,
        outcome_tracker=None,
        session_search=None,
        skills_dir: Path = Path("charlie/skills"),
        llm_client=None,
        status_q=None,
    ) -> dict:
        """Run a self-evolution cycle.

        Reviews skill performance, identifies underperforming skills,
        and generates improved versions.

        Returns:
            Dict with evolution results
        """
        result = {
            "timestamp": time.time(),
            "skills_reviewed": 0,
            "skills_improved": 0,
            "improvements": [],
        }

        if not llm_client:
            logger.info("evolution_skip | no LLM client")
            return result

        # Find skills to review
        skills = self._find_skills(skills_dir)
        if not skills:
            logger.info("evolution_skip | no skills found")
            return result

        # Review each skill
        for skill_path in skills:
            try:
                improvement = self._review_skill(skill_path, outcome_tracker, session_search, llm_client)
                result["skills_reviewed"] += 1
                if improvement:
                    result["skills_improved"] += 1
                    result["improvements"].append(improvement)
            except Exception as e:
                logger.warning("evolution_skill_failed | path=%s | %s", skill_path, e)

        # Log the run
        self._runs.append(result)
        self._save_log()

        # Notify dashboard
        if status_q and result["skills_improved"] > 0:
            try:
                status_q.put_nowait(
                    {
                        "type": "EVOLUTION_COMPLETE",
                        "content": {
                            "reviewed": result["skills_reviewed"],
                            "improved": result["skills_improved"],
                            "improvements": result["improvements"],
                        },
                    }
                )
            except Exception:
                pass

        logger.info(
            "evolution_complete | reviewed=%d improved=%d", result["skills_reviewed"], result["skills_improved"]
        )
        return result

    def _find_skills(self, skills_dir: Path) -> list[Path]:
        """Find all SKILL.md files in the skills directory."""
        if not skills_dir.exists():
            return []
        return list(skills_dir.rglob("SKILL.md"))

    def _review_skill(
        self,
        skill_path: Path,
        outcome_tracker,
        session_search,
        llm_client,
    ) -> Optional[dict]:
        """Review a single skill and suggest improvements."""
        current_content = skill_path.read_text(encoding="utf-8")

        # Gather performance data
        perf_data = self._gather_performance(skill_path, outcome_tracker, session_search)

        # Ask LLM to review
        prompt = (
            "You are reviewing a skill for potential improvement.\n\n"
            f"Current SKILL.md:\n{current_content[:3000]}\n\n"
            f"Performance data:\n{json.dumps(perf_data, default=str)[:1000]}\n\n"
            "If the skill could be improved, answer with ONLY valid JSON:\n"
            '{"should_improve": true, "improved_content": "...", "reason": "..."}\n\n'
            "If the skill is fine as-is:\n"
            '{"should_improve": false}'
        )

        try:
            import asyncio

            response = asyncio.run(
                llm_client.complete(
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.2,
                    max_tokens=2000,
                )
            )
            content = response.content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            data = json.loads(content)

            if data.get("should_improve") and data.get("improved_content"):
                improved = data["improved_content"]

                # Validate: must be non-trivial markdown with YAML frontmatter
                if not improved.startswith("---"):
                    logger.warning("evolution_reject | no YAML frontmatter in improved content")
                    return None
                if len(improved) < 100:
                    logger.warning("evolution_reject | improved content too short (%d chars)", len(improved))
                    return None
                if len(improved) > len(current_content) * 5:
                    logger.warning(
                        "evolution_reject | improved content suspiciously large (%d vs %d)",
                        len(improved),
                        len(current_content),
                    )
                    return None

                # Write improved version
                skill_path.write_text(improved, encoding="utf-8")
                logger.info("skill_improved | path=%s | reason=%s", skill_path, data.get("reason", ""))
                return {
                    "path": str(skill_path),
                    "reason": data.get("reason", ""),
                    "timestamp": time.time(),
                }
        except Exception as e:
            logger.warning("skill_review_failed | path=%s | %s", skill_path, e)

        return None

    def _gather_performance(self, skill_path: Path, outcome_tracker, session_search) -> dict:
        """Gather performance data for a skill."""
        perf = {"skill_path": str(skill_path)}

        # Get tool outcomes related to this skill's tools
        if outcome_tracker and hasattr(outcome_tracker, "get_recent_outcomes"):
            try:
                outcomes = outcome_tracker.get_recent_outcomes(limit=50)
                perf["recent_outcomes"] = len(outcomes)
                perf["success_rate"] = sum(1 for o in outcomes if o.outcome_type == "success") / max(len(outcomes), 1)
            except Exception as e:
                logger.warning("gather_performance_outcomes_failed | %s", e)
                perf["recent_outcomes"] = 0
                perf["success_rate"] = 0.0

        # Get session context
        if session_search and hasattr(session_search, "get_recent"):
            try:
                recent = session_search.get_recent(limit=10)
                perf["recent_sessions"] = len(recent)
            except Exception as e:
                logger.warning("gather_performance_sessions_failed | %s", e)

        return perf

    def get_last_run(self) -> Optional[dict]:
        """Get the last evolution run result."""
        return self._runs[-1] if self._runs else None

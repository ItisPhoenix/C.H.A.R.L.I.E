"""
charlie/intelligence/user_model.py

Dialectic User Modeling — Builds a deepening understanding of the user.
Inspired by Hermes Agent's Honcho integration.

After each session, reviews conversation turns and extracts user insights.
Maintains a persistent USER.md profile that evolves over time.
"""

import logging
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("charlie.intelligence.user_model")

USER_MD_PATH = Path("scratch/USER.md")


class UserModelEngine:
    """Builds and maintains a persistent user profile through dialectic reasoning."""

    def __init__(self, user_md_path: Path = USER_MD_PATH):
        self._path = user_md_path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._cached_profile: Optional[str] = None
        self._cache_time: float = 0

    def get_user_context(self, max_chars: int = 2000) -> str:
        """Get the current user profile for system prompt injection.

        Returns cached version if recent (within 60 seconds).
        """
        now = time.time()
        if self._cached_profile and (now - self._cache_time) < 60:
            return self._cached_profile[:max_chars]

        try:
            if self._path.exists():
                content = self._path.read_text(encoding="utf-8").strip()
                self._cached_profile = content
                self._cache_time = now
                return content[:max_chars]
        except Exception as e:
            logger.error("user_model_read_failed | %s", e)

        return ""

    def review_session(
        self,
        session_turns: list[dict],
        llm_client=None,
    ) -> Optional[str]:
        """Review a session and extract user insights.

        Args:
            session_turns: List of message dicts (role, content)
            llm_client: LLM client for analysis

        Returns:
            Updated USER.md content, or None if no update needed
        """
        if not session_turns or len(session_turns) < 2:
            return None

        if not llm_client:
            logger.debug("user_model_skip | no LLM client")
            return None

        # Build session text for analysis
        lines = []
        total = 0
        for t in session_turns:
            content = t.get("content", "")[:300]
            line = f"{t.get('role', '?')}: {content}"
            if total + len(line) > 4000:
                break
            lines.append(line)
            total += len(line)

        session_text = "\n".join(lines)
        current_profile = self.get_user_context(max_chars=3000)

        # LLM dialectic review
        prompt = (
            "You are a user modeling system. Analyze this conversation and extract insights about the user.\n\n"
            f"Current User Profile:\n{current_profile or '(empty - first analysis)'}\n\n"
            f"Recent Conversation:\n{session_text}\n\n"
            "Extract insights in these categories:\n"
            "- Communication Style: tone, detail level, format preferences\n"
            "- Work Patterns: focus areas, tools used, work habits\n"
            "- Technical Profile: skill level, languages, frameworks\n"
            "- Preferences: learned preferences from interactions\n"
            "- Interaction History: key moments, corrections, positive signals\n\n"
            "Answer with ONLY valid JSON (no markdown):\n"
            '{"insights": {"communication_style": "...", "work_patterns": "...", '
            '"technical_profile": "...", "preferences": ["..."], '
            '"interaction_history": ["..."]}, '
            '"should_update": true/false, "summary": "one-line summary of new insights"}'
        )

        try:
            import asyncio

            response = asyncio.run(
                llm_client.complete(
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1,
                    max_tokens=500,
                )
            )
            content = response.content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

            import json

            data = json.loads(content)

            if data.get("should_update"):
                insights = data.get("insights", {})
                updated = self._merge_insights(current_profile, insights)
                self._write_profile(updated)
                logger.info("user_model_updated | summary=%s", data.get("summary", ""))
                return updated

        except Exception as e:
            logger.warning("user_model_review_failed | %s", e)

        return None

    def _merge_insights(self, current: str, insights: dict) -> str:
        """Merge new insights into existing profile."""
        sections = []
        sections.append("# User Profile")
        sections.append("")
        sections.append(f"_Last updated: {time.strftime('%Y-%m-%d %H:%M')}_")
        sections.append("")

        for key, label in [
            ("communication_style", "Communication Style"),
            ("work_patterns", "Work Patterns"),
            ("technical_profile", "Technical Profile"),
        ]:
            value = insights.get(key, "")
            if value:
                sections.append(f"## {label}")
                sections.append(str(value))
                sections.append("")

        # Preferences (list)
        prefs = insights.get("preferences", [])
        if prefs:
            sections.append("## Preferences")
            for p in prefs:
                sections.append(f"- {p}")
            sections.append("")

        # Interaction history (list)
        history = insights.get("interaction_history", [])
        if history:
            sections.append("## Interaction History")
            for h in history:
                sections.append(f"- {h}")
            sections.append("")

        return "\n".join(sections)

    def _write_profile(self, content: str) -> None:
        """Write the user profile to disk."""
        try:
            self._path.write_text(content, encoding="utf-8")
            self._cached_profile = content
            self._cache_time = time.time()
            logger.info("user_model_written | path=%s", self._path)
        except Exception as e:
            logger.error("user_model_write_failed | %s", e)

    def get_profile_summary(self, max_chars: int = 500) -> str:
        """Get a condensed version of the user profile."""
        full = self.get_user_context(max_chars=max_chars * 2)
        if len(full) <= max_chars:
            return full
        return full[:max_chars] + "..."

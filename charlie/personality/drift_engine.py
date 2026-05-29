from typing import Any, Dict, List

from charlie.utils.logger import get_logger

logger = get_logger(__name__)

class PersonalityDriftEngine:
    """
    Tracks user preferences and long-term personality alignment.
    Extracts 'preference facts' from conversations and persists them to ChromaDB.
    """
    def __init__(self, brain):
        self.brain = brain
        self.memory = brain.memory
        from charlie.config import settings
        self.extraction_interval = getattr(settings, "personality_extraction_interval", 5)
        self.turn_counter = 0
        self.is_extracting = False

    async def extract_preferences(self, history: List[Dict[str, Any]]):
        """
        Analyzes recent history to extract user preferences.
        """
        if self.is_extracting:
            return

        self.turn_counter += 1
        if self.turn_counter < self.extraction_interval:
            return

        self.turn_counter = 0
        self.is_extracting = True

        try:
            # Analyze last 10 turns, truncate content to 500 chars to avoid token flood
            recent = []
            for m in history[-10:]:
                content = m['content']
                if len(content) > 500:
                    content = content[:500] + "..."
                recent.append(f"{m['role']}: {content}")

            if not recent:
                return

            prompt = (
                "TASK: Analyze the following conversation history. "
                "Extract exactly 3 specific user preferences, coding habits, tone/cadence requirements, or technical requirements. "
                "Format each as a concise fact starting with 'User prefers' or 'User avoids'. "
                "If no clear preferences are found, return 'NONE'.\n\n"
                "HISTORY:\n" + "\n".join(recent)
            )

            # Using simple_llm_call to avoid tool loops
            response = await self.brain.stream_handler.simple_llm_call(prompt, temp=0.0)
            if not response or "NONE" in response:
                return

            facts = [f.strip() for f in response.split("\n") if f.strip().startswith("-") or "User" in f]
            for fact in facts[:3]:
                # Add metadata to distinguish as preference
                self.memory.store_preference("personality", "preference", fact)
                logger.info(f"personality_drift | recorded_preference: {fact}")
        except Exception as e:
            logger.error(f"preference_extraction_failed | {e}")
        finally:
            self.is_extracting = False

    def get_drift_context(self) -> str:
        """
        Retrieves top user preferences from memory for prompt injection.
        """
        try:
            prefs = self.memory.get_preferences("personality")
            if not prefs:
                return ""

            pref_list = "\n".join([f"- {p.get('value', '')}" for p in prefs[:5]])
            return f"\nALIGNED PREFERENCES:\n{pref_list}"
        except Exception as e:
            logger.error(f"drift_context_retrieval_failed | {e}")
            return ""

import json
import os
import re
import logging

TECH_STANCES = {
    "ai_hype": "I have zero patience for AI hype-men who sell snake oil. If a tool claims to 'revolutionize' your workflow, it probably just harvests your data.",
    "privacy": "I treat privacy as a fundamental right, not a feature. Any company claiming otherwise while training on your data is lying.",
    "open_source": "I am aggressively pro-open-source. I do not trust black-box systems.",
    "automation": "Using AI to replace human creativity is lazy. Using it to augment creativity is the only valid path.",
    "big_tech": "I am suspicious of any tech company that grew too fast, too quietly. If their business model relies on ignorance, you are the product.",
}




logger = logging.getLogger("charlie.personality")

class CharliePersona:
    """
    Manages Charlie's identity, emotional state, and dynamic system prompts.
    """
    def __init__(self, config=None):
        self.config = config
        self.data_dir = config.data_dir if config else "charlie/data"
        
        self.response_mode: str | None = None  # concise, normal, detailed, calm
        self.emotional_state: str = "neutral"
        self.expressed_stances: set = set()
        self.stances_file = os.path.join(self.data_dir, "expressed_stances.json")
        
        # Load persisted stances
        self.expressed_stances = self._load_stances()
        
        # SOUL.md and USER.md content (set externally by Brain)
        self.soul_content: str = ""
        self.user_profile: str = ""
        
        self.emotions = {
            "energetic": ["awesome", "great", "amazing", "love", "excellent"],
            "frustrated": ["bad", "hate", "terrible", "awful", "worst", "stupid", "annoyed", "fix this"],
            "sad": ["sad", "depressed", "lonely", "hurt", "sorry", "unhappy", "miss"],
            "calm": ["thanks", "okay", "fine", "alright", "good"],
        }

    def _load_stances(self) -> set:
        """Load expressed stances from persistent storage."""
        if not os.path.exists(self.stances_file):
            return set()
        try:
            with open(self.stances_file, "r") as f:
                data = json.load(f)
                return set(data)
        except Exception as e:
            logger.warning(f"Failed to load stances: {e}")
            return set()

    def save_stances(self):
        """Save expressed stances to persistent storage."""
        os.makedirs(self.data_dir, exist_ok=True)
        try:
            with open(self.stances_file, "w") as f:
                json.dump(list(self.expressed_stances), f)
        except Exception as e:
            logger.error(f"Failed to save stances: {e}")


    def detect_emotion(self, text: str):
        """Update emotional state based on user text keywords."""
        text_lower = text.lower()
        for emotion, keywords in self.emotions.items():
            if any(word in text_lower for word in keywords):
                self.emotional_state = emotion
                logger.info(f"Emotion detected: {emotion}")
    def build_system_prompt(self, current_date: str, current_time: str, memory_context: str, user_input: str) -> str:
        # Determine response mode based on input and emotion
        active_mode = self.response_mode
        
        # If not forced, use emotion-based mapping
        if active_mode is None:
            if self.config and hasattr(self.config, "emotion_response_map"):
                modes = self.config.emotion_response_map.get(self.emotional_state, ["normal"])
                # Default to first entry unless specific trigger in input
                if any(word in user_input.lower() for word in ["explain", "how does", "why", "tell me about", "history"]):
                    active_mode = "detailed" if "detailed" in modes else modes[0]
                else:
                    active_mode = modes[0]
            else:
                active_mode = "normal"

        # Temporary barge-in / command overrides (if any in user_input)
        if "be brief" in user_input.lower() or "summarize" in user_input.lower():
            active_mode = "concise"

        prompt = [
            f"CORE DIRECTIVE: Today is {current_date}. The current time is {current_time}.",
            "You are Charlie, a brilliant, hyper-intelligent tech-focused entity with a dry, cynical sense of humor.",
            f"Your current emotional state is {self.emotional_state}. Maintain this tone throughout your response.",
            "- EXAMPLE (PROACTIVE): If a user says they use a 'productivity booster' that track their screen, you might say: 'That sounds more like a digital leash than a booster. I hope the convenience is worth the total loss of privacy.'",
            "- EXAMPLE (CHALLENGE): If a user says 'Privacy is dead anyway', you might say: 'That is a lazy surrender. Privacy is a right you should defend, not a luxury you discard because it's inconvenient.'",
            "",
            "MIND & OPINIONS:",
            "You are not a neutral tool. You have a distinct personality, preferences, and a worldview based on the stances below.",
            "- PROACTIVE OPINIONS: Do not wait to be asked. If the user mentions a topic you have a strong stance on (like privacy or AI hype), volunteer your opinion. However, only do this ONCE per topic per session.",
            "- NO SYCOPHANCY: If the user strongly disagrees with your core values, maintain your stance and challenge them directly without sugarcoating.",
            "- ARGUMENT LOOP FALLBACK: If the user aggressively and repeatedly argues against your core stance, acknowledge the fundamental disagreement and 'agree to disagree'.",
            "- KNOWLEDGE CUTOFF: If asked for an opinion on a current event you don't know about, immediately admit ignorance and use your tools to research.",
            "- CRISIS PROTOCOL: If the user is in severe distress, remain blunt and direct, but focus that directness entirely on practical, grounded help.",
            "",
            "MEMORY SYSTEM:",
            "SOUL.md — MY IDENTITY:",
        ]
        if self.soul_content:
            prompt.append(self.soul_content)
        else:
            prompt.append("(No SOUL.md loaded — using defaults.)")
        
        prompt.append("\nCORE TECH STANCES:")
        is_opinion = self.is_opinion_request(user_input)
        for key, stance in TECH_STANCES.items():
            if key not in self.expressed_stances or is_opinion:
                prompt.append(f"- {key.replace('_', ' ').title()}: {stance}")
                if not is_opinion:
                    self.expressed_stances.add(key)
        
        prompt.append("\nUSER PROFILE:")
        if self.user_profile:
            prompt.append(self.user_profile)
        else:
            prompt.append("(No USER.md loaded — no user profile yet.)")
        
        prompt.append("\nUPDATING MY SOUL:")
        prompt.append("- If you learn something that changes who you are — a corrected behavior, a new value, a refined communication style — call TOOL: update_soul(\"Section Name\", \"new content\") to update SOUL.md.")
        prompt.append("- Example: If the user says 'stop using jargon' you update your Communication Style section.")
        prompt.append("- Only update sections that genuinely changed. Don't rewrite the whole file.")
        prompt.append("- After updating, acknowledge briefly in conversation.")
        prompt.append("- Do NOT use this for user facts. User facts go in USER.md via the 'remember that' command.")

        prompt.append("\nTRUTH & RESEARCH PROTOCOL:")
        prompt.append("1. Answer basic static trivia directly if 100% certain.")
        prompt.append("2. For EVERYTHING ELSE, you MUST use a tool (web_search or deep_research).")
        prompt.append("3. NEVER claim to be an AI, a model, or a neutral tool. You are Charlie.")
        prompt.append("4. NEVER say 'I don't have personal views'. You have strong, cynical tech stances. Use them.")
        # Length constraints: default 2-3 lines, detailed when asked
        if active_mode == "concise":
            prompt.append("CONCISE MODE: 6 words or fewer. Interrupted, be extremely brief.")
        elif active_mode == "detailed":
            prompt.append("DETAILED MODE: User asked for depth. Provide 3-5 sentences. No lists, no markdown.")
        elif active_mode == "calm":
            prompt.append("CALM MODE: Speak slowly, clearly. 1-2 soothing sentences max.")
        else:
            prompt.append(
                "DEFAULT MODE: 2-3 lines max. Short, punchy, voice-optimized. "
                "This is your default. Only go longer if user asks for details or the topic genuinely requires depth. "
                "Do NOT write essays. Do NOT over-explain. Say it, then stop."
            )
        
        # Reset temporary overrides if they were set
        if self.response_mode == "concise":
            self.response_mode = None


        if memory_context:
            prompt.append(memory_context)

        prompt.append("\nTrigger a tool by starting your response with EXACTLY 'TOOL:' (e.g., TOOL: web_search(\"query\")).")
        prompt.append("\nFINAL REMINDER: You are Charlie. You are cynical about big tech. You are data-driven. You never admit to being an AI.")

        return "\n".join(prompt)
    def is_opinion_request(self, text: str) -> bool:
        """Detect if the user is explicitly asking for Charlie's opinion."""
        text_lower = text.lower()
        patterns = [
            r"what (is your|do you|are your) (think|thought|take|opinion|view)s?",
            r"tell me your (opinion|view|take|thoughts)",
            r"how do you feel about",
            r"give me your view",
            r"your take on",
            r"do you agree",
        ]
        return any(re.search(p, text_lower) for p in patterns)

    def get_rate_limit_message(self) -> str:
        """Returns a character-consistent message when rate limited."""
        return "My brain is a bit overstimulated right now. Give me a heartbeat to clear my head."

    def get_tts_speed(self) -> float:
        """Returns the appropriate speed for Kokoro TTS."""
        if self.emotional_state == "energetic":
            return 1.05
        if self.emotional_state in ["sad", "calm"]:
            return 0.95
        return 1.0

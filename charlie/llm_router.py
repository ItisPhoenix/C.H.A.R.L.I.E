import logging
import re
from enum import Enum

logger = logging.getLogger("charlie.llm_router")


class QueryCategory(str, Enum):
    TRIVIAL = "trivial"
    SIMPLE = "simple"
    COMPLEX = "complex"
    CREATIVE = "creative"
    TOOL = "tool"


class RouterHeuristic:
    TRIVIAL_PATTERNS = (
        r"^hi\b",
        r"^hello\b",
        r"^hey\b",
        r"^bye\b",
        r"^goodbye\b",
        r"^thanks?\b",
        r"^thank you\b",
        r"^ok(?:ay)?\b",
        r"^yes\b",
        r"^no\b",
        r"^what['\s]?s up\b",
        r"^how are you\b",
    )

    TOOL_PREFIXES = (
        "search",
        "research",
        "find",
        "look up",
        "web search",
        "deep dive",
    )

    CREATIVE_PREFIXES = (
        "write",
        "draft",
        "create",
        "make",
        "compose",
        "tell me a story",
        "poem",
        "joke",
        "song",
    )

    SIMPLE_PREFIXES = (
        "what is",
        "who is",
        "where is",
        "when",
        "how many",
        "how much",
        "define",
        "calculate",
        "convert",
        "translate",
        "spell",
    )

    COMPLEX_PREFIXES = (
        "why",
        "how to",
        "compare",
        "contrast",
        "analyze",
        "explain",
        "evaluate",
        "explain in detail",
        "elaborate",
    )

    @staticmethod
    def _word_count(user_input: str) -> int:
        return len(user_input.split())

    @staticmethod
    def _is_trivial(user_input: str) -> bool:
        stripped = user_input.strip()
        if not stripped:
            return True
        return any(re.match(pattern, stripped) for pattern in RouterHeuristic.TRIVIAL_PATTERNS)

    @staticmethod
    def _starts_with_any(user_input: str, prefixes: tuple[str, ...]) -> bool:
        return any(user_input.startswith(prefix) for prefix in prefixes)

    @classmethod
    def classify(cls, user_input: str) -> QueryCategory:
        normalized = " ".join(user_input.strip().lower().split())

        # Regex patterns first (most specific)
        if cls._is_trivial(normalized):
            return QueryCategory.TRIVIAL
        # Prefix checks (more specific before less)
        if cls._starts_with_any(normalized, cls.TOOL_PREFIXES):
            return QueryCategory.TOOL
        if cls._starts_with_any(normalized, cls.CREATIVE_PREFIXES):
            return QueryCategory.CREATIVE
        if cls._starts_with_any(normalized, cls.SIMPLE_PREFIXES):
            return QueryCategory.SIMPLE
        if cls._starts_with_any(normalized, cls.COMPLEX_PREFIXES):
            return QueryCategory.COMPLEX
        # Word count fallback
        if cls._word_count(normalized) < 12:
            return QueryCategory.SIMPLE
        return QueryCategory.COMPLEX


class LLMRouter:
    """Select LLM backends from query intent and complexity."""

    def __init__(self, config=None):
        self.config = config

    def select_backends(
        self,
        user_input: str,
        fast_backend: tuple,
        main_backend: tuple,
        has_fast: bool = False,
    ) -> list:
        category = RouterHeuristic.classify(user_input)
        logger.info(f"router | query='{user_input}' | category={category.value}")

        if not has_fast:
            return [(main_backend[0], main_backend[1], "main")]

        fast_client, fast_model = fast_backend
        main_client, main_model = main_backend

        if category in {QueryCategory.TRIVIAL, QueryCategory.SIMPLE}:
            return [
                (fast_client, fast_model, "fast"),
                (main_client, main_model, "main"),
            ]

        return [
            (main_client, main_model, "main"),
            (fast_client, fast_model, "fast"),
        ]

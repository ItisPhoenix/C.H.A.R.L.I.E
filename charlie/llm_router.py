import logging
import re
from typing import AsyncGenerator, Callable

logger = logging.getLogger("charlie.llm_router")


class LLMRouter:
    """Routes user input to either local or cloud LLM based on rules.

    Supports prefix overrides (``!local`` / ``!cloud``), keyword-based routing,
    and a regex pattern for simple factual questions suited to a smaller local model.
    """

    # Simple factual / utility questions well-suited to a smaller local model
    LOCAL_KEYWORDS = frozenset({
        "time", "date", "weather", "joke", "how are you",
        "hello", "goodbye", "thanks", "thank you", "hi",
    })

    LOCAL_PATTERN = re.compile(
        r"^(what|who|where|when|how)\s+(is|are|was|were)\s",
        re.IGNORECASE,
    )

    def __init__(self, config):
        self.config = config

    async def route(
        self,
        user_input: str,
        local_chat_fn: Callable,
        cloud_chat_fn: Callable,
    ) -> AsyncGenerator[str, None]:
        """Route ``user_input`` to the local or cloud LLM.

        ``local_chat_fn(text)`` and ``cloud_chat_fn(text)`` are async
        generators yielding text chunks.
        """
        stripped = user_input.strip()

        # 1. Prefix overrides
        if stripped.lower().startswith("!local "):
            logger.info("Router: forced local via !local prefix")
            async for chunk in local_chat_fn(stripped[len("!local "):]):
                yield chunk
            return

        if stripped.lower().startswith("!cloud "):
            logger.info("Router: forced cloud via !cloud prefix")
            async for chunk in cloud_chat_fn(stripped[len("!cloud "):]):
                yield chunk
            return

        # 2. Keyword / pattern match → local
        lower = stripped.lower()
        if self._should_route_local(lower):
            logger.info("Router: routing to local LLM")
            async for chunk in local_chat_fn(stripped):
                yield chunk
            return

        # 3. Default → cloud
        logger.info("Router: routing to cloud LLM")
        async for chunk in cloud_chat_fn(stripped):
            yield chunk

    def _should_route_local(self, lower: str) -> bool:
        """Determine if input is simple enough for a local LLM."""
        # Check exact keyword match with word boundaries
        for kw in self.LOCAL_KEYWORDS:
            pattern = r'(?<!\w)' + re.escape(kw) + r'(?!\w)'
            if re.search(pattern, lower):
                return True
        # Check simple factual pattern: "what is X", "where are Y", etc.
        # Reject long/complex questions (more than 5 words after the pattern)
        m = self.LOCAL_PATTERN.match(lower)
        if m:
            after_pattern = lower[m.end():].strip()
            word_count = len(after_pattern.split())
            if word_count <= 5:
                return True
        return False

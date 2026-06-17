import logging
from typing import AsyncGenerator, Callable

logger = logging.getLogger("charlie.llm_router")


class LLMRouter:
    """Passthrough router — fast/main fallback is handled inside chat_fn."""

    def __init__(self, config):
        self.config = config

    async def route(
        self,
        user_input: str,
        chat_fn: Callable,
    ) -> AsyncGenerator[str, None]:
        """Stream response through chat_fn."""
        async for chunk in chat_fn(user_input):
            yield chunk

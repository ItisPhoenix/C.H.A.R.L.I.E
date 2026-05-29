"""
Model Manager — Thin wrapper around ModelRouter.

Provides backward-compatible API (nim_chat, nim_health, load_text_model, etc.)
while delegating actual LLM routing to ModelRouter.

VRAM monitoring stays here — it's resource management, not routing.
"""

import asyncio
import time
from typing import Optional

from charlie.brain.model_router import LLMResponse, ModelRouter, TaskType
from charlie.utils.logger import get_logger

logger = get_logger(__name__)


class ModelManager:
    """
    Manages LLM model lifecycle via ModelRouter.

    Backward-compatible API — existing callers (chain_executor, core, etc.)
    can keep using nim_chat() and it just works through the router.

    .env / settings keys consumed:
        NIM_BASE_URL        → settings.llm.nim_base_url
        NIM_API_KEY         → settings.llm.nim_api_key
        NIM_PRIMARY_MODEL   → settings.llm.primary_model
        VISION_MODEL        → settings.llm.vision_model
        VISION_LLM_URL      → settings.llm.vision_url
        EMBEDDING_URL       → settings.llm.embedding_url
        EMBEDDING_MODEL     → settings.llm.embedding_model
        VRAM_THRESHOLD_MB   → settings.resources.vram_threshold_mb
    """

    def __init__(self, settings):
        self.settings = settings
        self._router = ModelRouter.from_settings(settings)

        # Expose for callers that need direct access
        self.nim_base_url: str = settings.llm.nim_base_url.rstrip("/")
        self.nim_api_key: str = getattr(settings.llm, "nim_api_key", "")
        self.primary_model: str = settings.llm.primary_model
        self.vision_model: str = settings.llm.vision_model
        self.vision_url: str = getattr(settings.llm, "vision_url", "")
        self.embedding_url: str = getattr(
            settings.llm, "embedding_url", "http://127.0.0.1:1234/api/embeddings"
        )
        self.embedding_model: str = getattr(settings.llm, "embedding_model", "")

        self.current_model: Optional[str] = None
        self._lock = asyncio.Lock()

        # Health Cache (delegates to router, but kept for backward compat)
        self._last_health_check = 0.0
        self._health_cache = False

    @property
    def router(self) -> ModelRouter:
        """Direct access to the underlying ModelRouter."""
        return self._router

    # ------------------------------------------------------------------
    # Public API — backward compatible
    # ------------------------------------------------------------------

    async def load_text_model(self):
        """Activate NIM primary model."""
        async with self._lock:
            if self.current_model != "text":
                await self._activate_nim()

    async def load_vision_model(self):
        """Activate vision model (non-NIM endpoint)."""
        async with self._lock:
            if self.current_model != "vision":
                await self._activate_vision()

    async def unload_vision_model(self):
        """Release vision model, return to NIM primary."""
        async with self._lock:
            if self.current_model == "vision":
                logger.info("unloading_vision_model | returning_to_nim")
                await self._activate_nim()

    def get_current_model(self) -> Optional[str]:
        return self.current_model

    async def check_vram_pressure(self) -> bool:
        """True if VRAM usage exceeds configured threshold."""
        try:
            import torch

            if torch.cuda.is_available():
                allocated = torch.cuda.memory_allocated() / 1024**3
                reserved = torch.cuda.memory_reserved() / 1024**3
                pressure = allocated / reserved if reserved > 0 else 0
                threshold = self.settings.resources.vram_threshold_mb / 8192
                if pressure > threshold:
                    logger.warning(
                        f"vram_pressure_critical | {pressure:.2%} | threshold={threshold:.2%}"
                    )
                    return True
        except Exception as e:
            logger.debug("vram_check_failed | %s", e)
        return False

    # ------------------------------------------------------------------
    # LLM calls — delegates to ModelRouter
    # ------------------------------------------------------------------

    async def nim_chat(
        self,
        messages: list[dict],
        *,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        stream: bool = False,
        **kwargs,
    ) -> dict:
        """
        Send chat/completions request via ModelRouter.
        Returns raw JSON response dict (backward compatible).
        """
        logger.info("nim_chat | model=%s | msgs=%d", self.primary_model, len(messages))
        try:
            response = await self._router.complete(
                messages,
                task_type=TaskType.CHAT,
                max_tokens=max_tokens,
                temperature=temperature,
                stream=stream,
                **kwargs,
            )
            # Return as raw dict for backward compatibility
            return {
                "choices": [
                    {
                        "message": {
                            "content": response.content,
                            "tool_calls": response.tool_calls,
                        },
                        "finish_reason": response.finish_reason,
                    }
                ],
                "model": response.model,
                "usage": response.usage,
            }
        except Exception as e:
            logger.error("nim_chat_failed | %s", e)
            # Invalidate health cache so next call re-checks
            self._health_cache = False
            self._last_health_check = 0.0
            raise

    async def nim_chat_response(
        self,
        messages: list[dict],
        *,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        tools: list | None = None,
        **kwargs,
    ) -> LLMResponse:
        """
        Like nim_chat() but returns typed LLMResponse instead of raw dict.
        Preferred for new code.
        """
        return await self._router.complete(
            messages,
            task_type=TaskType.CHAT,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
            **kwargs,
        )

    async def nim_health(self) -> bool:
        """Check NIM endpoint health via router."""
        now = time.time()
        if now - self._last_health_check < 300.0 and self._health_cache:
            return True

        healthy = await self._router.health_check(TaskType.CHAT)
        self._health_cache = healthy
        self._last_health_check = time.time()
        return healthy

    async def vision_chat(
        self,
        messages: list[dict],
        *,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        **kwargs,
    ) -> LLMResponse:
        """Send vision request via router (OpenRouter → LM Studio fallback)."""
        return await self._router.complete(
            messages,
            task_type=TaskType.VISION,
            max_tokens=max_tokens,
            temperature=temperature,
            **kwargs,
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _activate_nim(self):
        """Mark NIM primary as active; verify endpoint if switching from vision."""
        prev = self.current_model
        logger.info(
            f"activating_nim | from={prev} | model={self.primary_model} | endpoint={self.nim_base_url}"
        )
        healthy = await self.nim_health()
        if not healthy:
            logger.error("nim_endpoint_unreachable | check NIM_BASE_URL in .env")
            return
        self.current_model = "text"
        logger.info("nim_active | model=%s", self.primary_model)

    async def _activate_vision(self):
        """Switch to vision model on separate endpoint."""
        logger.info(
            f"activating_vision | from={self.current_model} | model={self.vision_model}"
        )
        if not self.vision_url:
            logger.error("vision_url_not_set | set VISION_LLM_URL in .env")
            return
        self.current_model = "vision"

    async def close(self):
        """Close router and all provider sessions."""
        await self._router.close()

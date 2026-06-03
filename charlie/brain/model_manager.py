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
from charlie.utils.system import get_vram_used_mb

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Standalone STT fallback utility (Task 14.2)
# ---------------------------------------------------------------------------
# This is a standalone function so the AudioEngine (separate process) can call
# it without needing the full Brain or ModelManager instance.


def load_stt_with_fallback(
    primary_model: str = "distil-large-v3",
    device: str = "cuda",
) -> str:
    """Try to load the configured STT model; fall back on CUDA OOM.

    Returns the model name that was successfully loaded, or an error string
    prefixed with "Error:" if all attempts fail.
    """
    try:
        import faster_whisper  # noqa: F401 — verify importable

        from faster_whisper import WhisperModel

        logger.info("stt_load_attempt | model=%s | device=%s", primary_model, device)
        _model = WhisperModel(primary_model, device=device, compute_type="float16")  # noqa: F841
        logger.info("stt_loaded | model=%s | device=%s", primary_model, device)
        return primary_model
    except (RuntimeError, Exception) as exc:
        exc_str = str(exc)
        is_oom = "CUDA out of memory" in exc_str or "OutOfMemoryError" in type(exc).__name__
        if not is_oom:
            # Not an OOM — propagate as error
            logger.error("stt_load_failed | model=%s | error=%s", primary_model, exc_str)
            return f"Error: STT load failed ({exc_str})"

        logger.warning("stt_oom | model=%s | falling_back_to_tiny.en_cpu", primary_model)

    # Fallback: tiny.en on CPU
    fallback_model = "tiny.en"
    try:
        from faster_whisper import WhisperModel

        _model = WhisperModel(fallback_model, device="cpu", compute_type="int8")  # noqa: F841
        logger.info("stt_fallback_loaded | model=%s | device=cpu", fallback_model)
        return fallback_model
    except Exception as exc2:
        logger.error(
            "stt_fallback_also_failed | model=%s | error=%s", fallback_model, exc2
        )
        return f"Error: STT fallback also failed ({exc2})"


class ModelManager:
    """
    Manages LLM model lifecycle via ModelRouter.

    Backward-compatible API — existing callers (chain_executor, core, etc.)
    can keep using nim_chat() and it just works through the router.

    .env / settings keys consumed:
        LLM_URL             → settings.llm.llm_url
        LLM_API_KEY         → settings.llm.llm_api_key
        LLM_MODEL           → settings.llm.llm_model
        VRAM_THRESHOLD_MB   → settings.resources.vram_threshold_mb
    """

    def __init__(self, settings):
        self.settings = settings
        self._router = ModelRouter.from_settings(settings)

        # Expose for callers that need direct access
        self.llm_url: str = settings.llm.llm_url.rstrip("/")
        self.llm_api_key: str = getattr(settings.llm, "llm_api_key", "")
        self.llm_model: str = settings.llm.llm_model
        self.llm_vision_url: str = getattr(settings.llm, "llm_vision_url", "").rstrip("/")
        self.llm_vision_api_key: str = getattr(settings.llm, "llm_vision_api_key", "")
        self.llm_vision_model: str = getattr(settings.llm, "llm_vision_model", "")

        self.current_model: Optional[str] = None
        self._lock = asyncio.Lock()

        # Health Cache (delegates to router, but kept for backward compat)
        self._last_health_check = 0.0
        self._health_cache = False

        # VRAM governor settings (Task 14.1)
        self._vram_budget_mb: float = getattr(
            settings.resources, "vram_budget_mb", 7168
        )
        self._vram_warning_mb: float = getattr(
            settings.resources, "vram_warning_mb", 6500
        )
        self._model_unload_delay_s: int = getattr(
            settings.resources, "model_unload_delay_s", 30
        )
        self._model_priority: dict = getattr(
            settings.resources, "model_priority", {"text": "primary", "vision": "on_demand"}
        )

        # Track loaded on-demand models for unloading
        self._loaded_models: dict[str, object] = {}  # name → model reference
        self._vision_last_used: float = 0.0

    @property
    def router(self) -> ModelRouter:
        """Direct access to the underlying ModelRouter."""
        return self._router

    # ------------------------------------------------------------------
    # VRAM Governor (Task 14.1)
    # ------------------------------------------------------------------

    def check_vram_budget(self, model_size_mb: float) -> bool:
        """Check if loading a model of *model_size_mb* fits within the VRAM budget.

        If projected usage would exceed the budget, attempts to unload
        lower-priority on-demand models (e.g. vision) first. Returns True if
        the load can proceed, False if still over budget after unloading.
        """
        current_used = get_vram_used_mb()
        projected = current_used + model_size_mb

        if projected <= self._vram_budget_mb:
            return True

        # Attempt to free VRAM by unloading on-demand models
        logger.warning(
            "vram_over_budget | current=%.0f | model_size=%.0f | budget=%.0f | attempting_unload",
            current_used,
            model_size_mb,
            self._vram_budget_mb,
        )

        # Unload on-demand models (vision first)
        on_demand_models = [
            name
            for name, priority in self._model_priority.items()
            if priority == "on_demand" and name in self._loaded_models
        ]
        for model_name in on_demand_models:
            self.unload_model(model_name)

        # Re-check after unloading
        current_used = get_vram_used_mb()
        projected = current_used + model_size_mb
        if projected <= self._vram_budget_mb:
            logger.info(
                "vram_freed | current=%.0f | projected=%.0f | budget=%.0f",
                current_used,
                projected,
                self._vram_budget_mb,
            )
            return True

        logger.error(
            "vram_still_over_budget | current=%.0f | projected=%.0f | budget=%.0f",
            current_used,
            projected,
            self._vram_budget_mb,
        )
        return False

    def unload_model(self, model_name: str) -> None:
        """Release a model's GPU memory.

        Sets the reference to None and calls torch.cuda.empty_cache() if
        available. STT/TTS models (priority "primary") are never unloaded.
        """
        # Guard: never unload primary models
        priority = self._model_priority.get(model_name, "on_demand")
        if priority == "primary":
            logger.debug("unload_skipped | model=%s | reason=primary_priority", model_name)
            return

        if model_name in self._loaded_models:
            logger.info("unloading_model | name=%s", model_name)
            self._loaded_models[model_name] = None
            del self._loaded_models[model_name]

        # Also clear the current_model state if it matches
        if self.current_model == model_name:
            self.current_model = None

        # Release GPU cache
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                logger.debug("torch_cuda_cache_cleared | after_unload=%s", model_name)
        except ImportError:
            pass
        except Exception as e:
            logger.debug("cuda_empty_cache_failed | %s", e)

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
        logger.info("llm_chat | model=%s | msgs=%d", self.llm_model, len(messages))
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
            f"activating_llm | from={prev} | model={self.llm_model} | endpoint={self.llm_url}"
        )
        healthy = await self.nim_health()
        if not healthy:
            logger.error("llm_endpoint_unreachable | check LLM_URL in .env")
            return
        self.current_model = "text"
        logger.info("llm_active | model=%s", self.llm_model)

    async def _activate_vision(self):
        """Switch to the vision model on its own endpoint."""
        logger.info(
            f"activating_vision | from={self.current_model} | model={self.llm_vision_model}"
        )
        if not self.llm_vision_url:
            logger.error("vision_url_not_set | set LLM_VISION_URL in .env")
            return
        self.current_model = "vision"

    async def close(self):
        """Close router and all provider sessions."""
        await self._router.close()

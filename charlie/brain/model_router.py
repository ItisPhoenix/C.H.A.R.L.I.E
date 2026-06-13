"""
Model Router — Provider abstraction layer for LLM requests.

Routes requests to the right provider based on task type (reasoning, vision, chat).
Handles failover, health checks, and config-driven provider registry.

All providers expose OpenAI-compatible /v1/chat/completions endpoints.
"""

import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator, Dict, List, Optional

import aiohttp

from charlie.utils.logger import get_logger

logger = get_logger("ModelRouter")


# ---------------------------------------------------------------------------
# Task types — used to route requests to the right provider
# ---------------------------------------------------------------------------


class TaskType(str, Enum):
    REASONING = "reasoning"  # Complex thought, planning, code
    CHAT = "chat"  # General conversation
    TOOLS = "tools"  # Tool-calling responses
    VISION = "vision"  # Image analysis
    EMBEDDING = "embedding"  # Vector embeddings
    VOICE = "voice"  # Real-time voice (Gemini Live)


# ---------------------------------------------------------------------------
# Provider config
# ---------------------------------------------------------------------------


@dataclass
class ProviderConfig:
    name: str
    base_url: str
    model: str
    api_key: str = ""
    roles: List[str] = field(default_factory=lambda: ["chat"])
    priority: int = 1
    timeout: int = 60
    max_retries: int = 2


# ---------------------------------------------------------------------------
# Health cache — avoids hammering endpoints
# ---------------------------------------------------------------------------


class _HealthCache:
    """TTL-based health cache per provider."""

    def __init__(self, ttl_seconds: int = 300):
        self._ttl = ttl_seconds
        self._cache: Dict[str, tuple[bool, float]] = {}  # name -> (healthy, timestamp)

    def get(self, name: str) -> Optional[bool]:
        entry = self._cache.get(name)
        if entry is None:
            return None
        healthy, ts = entry
        if time.monotonic() - ts > self._ttl:
            return None
        return healthy

    def set(self, name: str, healthy: bool):
        self._cache[name] = (healthy, time.monotonic())

    def invalidate(self, name: str):
        self._cache.pop(name, None)


# ---------------------------------------------------------------------------
# LLM Response — normalized across providers
# ---------------------------------------------------------------------------


@dataclass
class LLMResponse:
    content: str
    tool_calls: List[Dict[str, Any]]
    finish_reason: str
    model: str
    provider: str
    usage: Dict[str, int]


# ---------------------------------------------------------------------------
# Provider base + implementations
# ---------------------------------------------------------------------------


class BaseProvider:
    """Async OpenAI-compatible chat completions client."""

    def __init__(self, config: ProviderConfig):
        self.config = config
        self.name = config.name
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.config.timeout))
        return self._session

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers

    def _chat_url(self) -> str:
        """Build the chat-completions URL idempotently.

        If the user already included ``/v1`` in the base URL (common with
        NIM, Ollama, LM Studio, vLLM — any OpenAI-compatible server), do
        not append a second ``/v1``. Otherwise add it.
        """
        base = self.config.base_url.rstrip("/")
        if base.endswith("/v1"):
            return f"{base}/chat/completions"
        return f"{base}/v1/chat/completions"

    def _models_url(self) -> str:
        """Build the /models URL idempotently (see ``_chat_url``)."""
        base = self.config.base_url.rstrip("/")
        if base.endswith("/v1"):
            return f"{base}/models"
        return f"{base}/v1/models"

    async def complete(
        self,
        messages: List[Dict[str, Any]],
        *,
        tools: Optional[List[Dict[str, Any]]] = None,
        stream: bool = False,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        **kwargs,
    ) -> LLMResponse:
        """Send chat completion request. Returns normalized LLMResponse."""
        url = self._chat_url()
        payload: Dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": stream,
        }
        if tools:
            payload["tools"] = tools
        payload.update(kwargs)

        session = await self._get_session()
        logger.info(f"provider_request | name={self.name} | model={self.config.model} | msgs={len(messages)}")

        async with session.post(url, json=payload, headers=self._headers()) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return self._parse_response(data)

    async def stream_complete(
        self,
        messages: List[Dict[str, Any]],
        *,
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        **kwargs,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Stream SSE chunks from chat completion."""
        url = self._chat_url()
        payload: Dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
        payload.update(kwargs)

        session = await self._get_session()
        async with session.post(url, json=payload, headers=self._headers()) as resp:
            resp.raise_for_status()
            buffer = ""
            async for chunk in resp.content.iter_any():
                buffer += chunk.decode("utf-8", errors="replace")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line or line == "data: [DONE]":
                        continue
                    if line.startswith("data: "):
                        try:
                            import json

                            yield json.loads(line[6:])
                        except Exception:
                            continue

    async def health_check(self) -> bool:
        """Ping /v1/models to verify endpoint is live."""
        url = self._models_url()
        try:
            session = await self._get_session()
            async with session.get(url, headers=self._headers(), timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return False
                data = await resp.json()
                models = [m.get("id", "") for m in data.get("data", []) if isinstance(m, dict)]
                # Accept exact match or stripped vendor prefix
                model_ok = self.config.model in models or self.config.model.split("/")[-1] in models
                return model_ok
        except Exception as e:
            logger.debug(f"health_check_fail | provider={self.name} | {e}")
            return False

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    def _parse_response(self, data: Dict[str, Any]) -> LLMResponse:
        """Parse OpenAI-format response into LLMResponse."""
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message", {})
        usage = data.get("usage", {})
        return LLMResponse(
            content=message.get("content", ""),
            tool_calls=message.get("tool_calls", []),
            finish_reason=choice.get("finish_reason", "stop"),
            model=data.get("model", self.config.model),
            provider=self.name,
            usage={
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
        )


# ---------------------------------------------------------------------------
# Gemini Provider — translates OpenAI format to Gemini API
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# Model Router — the main entry point
# ---------------------------------------------------------------------------


class ModelRouter:
    """
    Routes LLM requests to the right provider based on task type.

    Usage:
        router = ModelRouter.from_config(charlie_config)
        response = await router.complete(messages, task_type=TaskType.REASONING)
    """

    def __init__(self):
        self._providers: Dict[str, BaseProvider] = {}
        self._role_map: Dict[str, List[str]] = {}  # role -> [provider_names] sorted by priority
        self._health = _HealthCache(ttl_seconds=300)
        self._fallback_order: Dict[str, List[str]] = {}  # role -> ordered fallback list

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> "ModelRouter":
        """Build router from charlie_config.json providers section."""
        router = cls()
        providers_cfg = config.get("providers", {})

        for name, pcfg in providers_cfg.items():
            # Resolve $ENV_VAR references in all fields
            def _resolve(val: str) -> str:
                if isinstance(val, str) and val.startswith("$"):
                    return os.getenv(val[1:], "")
                return val

            api_key = _resolve(pcfg.get("api_key", ""))
            base_url = _resolve(pcfg["base_url"])
            model = _resolve(pcfg["model"])

            if not base_url:
                logger.warning(f"provider_skip | name={name} | base_url unresolved ({pcfg['base_url']})")
                continue

            provider_config = ProviderConfig(
                name=name,
                base_url=base_url.rstrip("/"),
                model=model or "unknown",
                api_key=api_key,
                roles=pcfg.get("roles", ["chat"]),
                priority=pcfg.get("priority", 1),
                timeout=pcfg.get("timeout", 60),
            )
            provider = BaseProvider(provider_config)
            router.register(provider)

        return router

    @classmethod
    def from_settings(cls, settings) -> "ModelRouter":
        """
        Build router from charlie.config.settings.

        Prefers providers config (from charlie_config.json) when available,
        falling back to hardcoded settings/env vars.
        """
        # If providers config exists, use it (resolves $ENV_VAR references)
        providers_cfg = getattr(settings, "providers", {})
        if providers_cfg:
            logger.info(f"loading_from_config | providers={list(providers_cfg.keys())}")
            return cls.from_config({"providers": providers_cfg})

        # Fallback: build from settings attributes (backward compat)
        logger.info("loading_from_settings | no providers config, using env vars")
        router = cls()

        # Universal LLM endpoint — one OpenAI-compatible server for chat/reasoning/tools
        if settings.llm.llm_url:
            llm_config = ProviderConfig(
                name="llm",
                base_url=settings.llm.llm_url.rstrip("/"),
                model=settings.llm.llm_model or "default",
                api_key=getattr(settings.llm, "llm_api_key", "") or "",
                roles=["reasoning", "chat", "tools"],
                priority=1,
                timeout=60,
            )
            router.register(BaseProvider(llm_config))
        else:
            logger.warning("llm_url_empty | no_provider_registered | set LLM_URL in .env")

        # Vision — separate endpoint, optional
        if getattr(settings.llm, "llm_vision_url", ""):
            vision_config = ProviderConfig(
                name="vision",
                base_url=settings.llm.llm_vision_url.rstrip("/"),
                model=getattr(settings.llm, "llm_vision_model", "") or "default",
                api_key=getattr(settings.llm, "llm_vision_api_key", "") or "",
                roles=["vision"],
                priority=1,
                timeout=90,
            )
            router.register(BaseProvider(vision_config))
        else:
            logger.info("vision_disabled | set LLM_VISION_URL and LLM_VISION_MODEL to enable")

        return router

    def register(self, provider: BaseProvider):
        """Register a provider and update role mappings."""
        self._providers[provider.name] = provider
        for role in provider.config.roles:
            if role not in self._role_map:
                self._role_map[role] = []
            self._role_map[role].append(provider.name)
        # Sort by priority (lower = higher priority)
        for role in self._role_map:
            self._role_map[role].sort(key=lambda n: self._providers[n].config.priority)
        # Build fallback order
        self._fallback_order = dict(self._role_map)
        logger.info(
            f"provider_registered | name={provider.name} | roles={provider.config.roles} | priority={provider.config.priority}"
        )

    def unregister(self, name: str):
        """Remove a provider."""
        provider = self._providers.pop(name, None)
        if provider:
            for role in self._role_map:
                if name in self._role_map[role]:
                    self._role_map[role].remove(name)
            self._health.invalidate(name)
            logger.info(f"provider_unregistered | name={name}")

    def get_provider_for_task(self, task_type: TaskType) -> Optional[BaseProvider]:
        """Get the best healthy provider for a given task type."""
        role = task_type.value
        candidates = self._fallback_order.get(role, [])
        if not candidates:
            # Fall back to chat
            candidates = self._fallback_order.get("chat", [])

        for name in candidates:
            cached = self._health.get(name)
            if cached is False:
                continue  # Known unhealthy
            return self._providers.get(name)

        # If all cached as unhealthy, try first anyway (cache might be stale)
        if candidates:
            return self._providers.get(candidates[0])
        return None

    async def complete(
        self,
        messages: List[Dict[str, Any]],
        *,
        task_type: TaskType = TaskType.CHAT,
        tools: Optional[List[Dict[str, Any]]] = None,
        stream: bool = False,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        **kwargs,
    ) -> LLMResponse:
        """
        Send a chat completion request with automatic failover.

        Routes based on task_type, tries providers in priority order.
        """
        role = task_type.value
        candidates = self._fallback_order.get(role, self._fallback_order.get("chat", []))

        last_error = None
        for name in candidates:
            provider = self._providers.get(name)
            if not provider:
                continue

            try:
                response = await provider.complete(
                    messages,
                    tools=tools,
                    stream=stream,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    **kwargs,
                )
                self._health.set(name, True)
                return response
            except Exception as e:
                last_error = e
                self._health.set(name, False)
                logger.warning(f"provider_fail | name={name} | {e}")
                continue

        raise RuntimeError(f"All providers failed for role '{role}'. Last error: {last_error}")

    async def stream_complete(
        self,
        messages: List[Dict[str, Any]],
        *,
        task_type: TaskType = TaskType.CHAT,
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        **kwargs,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Stream SSE chunks with failover."""
        role = task_type.value
        candidates = self._fallback_order.get(role, self._fallback_order.get("chat", []))

        last_error = None
        for name in candidates:
            provider = self._providers.get(name)
            if not provider:
                continue

            try:
                async for chunk in provider.stream_complete(
                    messages,
                    tools=tools,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    **kwargs,
                ):
                    yield chunk
                self._health.set(name, True)
                return
            except Exception as e:
                last_error = e
                self._health.set(name, False)
                logger.warning(f"stream_provider_fail | name={name} | {e}")
                continue

        raise RuntimeError(f"All streaming providers failed for role '{role}'. Last error: {last_error}")

    async def health_check_all(self) -> Dict[str, bool]:
        """Check health of all registered providers."""
        results = {}
        for name, provider in self._providers.items():
            healthy = await provider.health_check()
            self._health.set(name, healthy)
            results[name] = healthy
            logger.info(f"health_check | name={name} | healthy={healthy}")
        return results

    async def health_check(self, task_type: TaskType = TaskType.CHAT) -> bool:
        """Check if at least one provider for the given task type is healthy."""
        role = task_type.value
        candidates = self._fallback_order.get(role, [])
        for name in candidates:
            cached = self._health.get(name)
            if cached is True:
                return True
            provider = self._providers.get(name)
            if provider:
                healthy = await provider.health_check()
                self._health.set(name, healthy)
                if healthy:
                    return True
        return False

    @property
    def providers(self) -> Dict[str, BaseProvider]:
        return dict(self._providers)

    @property
    def active_roles(self) -> Dict[str, List[str]]:
        return dict(self._role_map)

    async def close(self):
        """Close all provider sessions."""
        for provider in self._providers.values():
            await provider.close()

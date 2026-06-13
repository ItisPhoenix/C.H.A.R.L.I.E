"""
LLM Client -- Shared async LLM HTTP client with retry, rate limiting, and logging.

Wraps ModelRouter to provide:
- Rate limiting (configurable window + limit)
- Retry with exponential backoff (same-provider retries)
- Request/response logging for debugging
- Session lifecycle management
- Simple API: complete() and stream()

ModelRouter handles provider selection, cross-provider failover, and health
checks.  LLMClient adds same-provider retry and rate limiting on top.
"""

import asyncio
import time
from typing import Any, AsyncIterator, Dict, List, Optional

import aiohttp

from charlie.brain.model_router import LLMResponse, ModelRouter, TaskType
from charlie.utils.logger import get_logger

# Transport-level exceptions worth retrying (connection/timeout issues).
# Programming bugs (TypeError, KeyError, etc.) should NOT be retried.
_RETRYABLE = (ConnectionError, TimeoutError, OSError, aiohttp.ClientError)

logger = get_logger("LLMClient")


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_DEFAULT_MAX_RETRIES = 3
_DEFAULT_BACKOFF_BASE = 1.0  # seconds; doubles each attempt


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class RateLimitExceeded(Exception):
    """Raised when the LLM rate limit is exceeded."""


# ---------------------------------------------------------------------------
# LLM Client
# ---------------------------------------------------------------------------


class LLMClient:
    """Shared async LLM client with retry, logging, and ModelRouter integration.

    Usage::

        router = ModelRouter.from_config(config)
        client = LLMClient(router)

        response = await client.complete(
            [{"role": "user", "content": "Hello"}],
            task_type=TaskType.CHAT,
        )

        async for chunk in client.stream(messages, task_type=TaskType.CHAT):
            ...
    """

    def __init__(
        self,
        model_router: ModelRouter,
        *,
        rate_limit: int = 30,
        rate_window: float = 60.0,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        backoff_base: float = _DEFAULT_BACKOFF_BASE,
    ):
        self._router = model_router
        self._rate_limit = rate_limit
        self._rate_window = rate_window
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._call_times: List[float] = []

    # ------------------------------------------------------------------
    # Rate limiting
    # ------------------------------------------------------------------

    def _prune_call_times(self) -> None:
        """Remove timestamps outside the current rate window."""
        now = time.monotonic()
        self._call_times = [t for t in self._call_times if now - t < self._rate_window]

    def check_rate_limit(self) -> bool:
        """Check and record an LLM call against the rate limit.

        Returns ``True`` if the call is allowed, ``False`` if blocked.
        """
        self._prune_call_times()
        if len(self._call_times) >= self._rate_limit:
            logger.warning(
                f"rate_limit_exceeded | calls={len(self._call_times)} "
                f"limit={self._rate_limit} window={self._rate_window}s"
            )
            return False
        self._call_times.append(time.monotonic())
        return True

    @property
    def rate_limit_remaining(self) -> int:
        """Number of calls remaining in the current window."""
        self._prune_call_times()
        return max(0, self._rate_limit - len(self._call_times))

    # ------------------------------------------------------------------
    # Non-streaming completion
    # ------------------------------------------------------------------

    async def complete(
        self,
        messages: List[Dict[str, Any]],
        *,
        task_type: TaskType = TaskType.CHAT,
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        **kwargs: Any,
    ) -> LLMResponse:
        """Send chat completion with automatic failover and retry.

        Retries the same request up to *max_retries* times with exponential
        backoff.  ModelRouter handles cross-provider failover internally.

        Raises:
            RateLimitExceeded: If the rate limit is hit.
            RuntimeError: If all retry attempts fail.
        """
        self._enforce_rate_limit()

        last_error: Optional[Exception] = None

        for attempt in range(1, self._max_retries + 1):
            try:
                logger.info(
                    f"complete_start | attempt={attempt}/{self._max_retries} "
                    f"task={task_type.value} msgs={len(messages)}"
                )
                response = await self._router.complete(
                    messages,
                    task_type=task_type,
                    tools=tools,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    **kwargs,
                )
                logger.info(
                    f"complete_ok | provider={response.provider} "
                    f"model={response.model} "
                    f"tokens={response.usage.get('total_tokens', 0)}"
                )
                return response

            except _RETRYABLE as exc:
                last_error = exc
                if attempt < self._max_retries:
                    delay = self._backoff_base * (2 ** (attempt - 1))
                    logger.warning(f"complete_retry | attempt={attempt} error={exc} backoff={delay:.1f}s")
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"complete_failed | exhausted {self._max_retries} attempts | last_error={exc}")

        raise RuntimeError(f"LLM complete failed after {self._max_retries} attempts: {last_error}") from last_error

    # ------------------------------------------------------------------
    # Streaming completion
    # ------------------------------------------------------------------

    async def stream(
        self,
        messages: List[Dict[str, Any]],
        *,
        task_type: TaskType = TaskType.CHAT,
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        **kwargs: Any,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Stream SSE chunks with failover and retry.

        Yields raw SSE data dicts as parsed by ModelRouter / provider.
        Retries only on connection-level errors *before* any chunks are
        yielded.  Once streaming has started, errors propagate immediately
        to avoid duplicate chunks.

        Raises:
            RateLimitExceeded: If the rate limit is hit.
            RuntimeError: If all retry attempts fail.
        """
        self._enforce_rate_limit()

        last_error: Optional[Exception] = None

        for attempt in range(1, self._max_retries + 1):
            yielded = False
            try:
                logger.info(
                    f"stream_start | attempt={attempt}/{self._max_retries} task={task_type.value} msgs={len(messages)}"
                )
                chunk_count = 0
                async for chunk in self._router.stream_complete(
                    messages,
                    task_type=task_type,
                    tools=tools,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    **kwargs,
                ):
                    yielded = True
                    chunk_count += 1
                    yield chunk

                logger.info(f"stream_done | chunks={chunk_count}")
                return  # success -- exit retry loop

            except _RETRYABLE as exc:
                last_error = exc
                if yielded:
                    # Already yielded chunks to caller -- can't retry
                    # without producing duplicates.  Propagate immediately.
                    logger.error(f"stream_mid_error | chunks_already_yielded={chunk_count} error={exc}")
                    raise
                if attempt < self._max_retries:
                    delay = self._backoff_base * (2 ** (attempt - 1))
                    logger.warning(f"stream_retry | attempt={attempt} error={exc} backoff={delay:.1f}s")
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"stream_failed | exhausted {self._max_retries} attempts | last_error={exc}")

        raise RuntimeError(f"LLM stream failed after {self._max_retries} attempts: {last_error}") from last_error

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self):
        """Close underlying ModelRouter provider sessions."""
        await self._router.close()
        logger.info("client_closed")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _enforce_rate_limit(self):
        """Check rate limit and raise if exceeded."""
        if not self.check_rate_limit():
            raise RateLimitExceeded(f"Rate limit exceeded ({self._rate_limit} calls / {self._rate_window}s)")

"""Semantic relevance for research documents.

Token overlap cannot tell a page about the topic from a page that merely shares
vocabulary. A concrete measured case: for the query "latest ai trends 2026" a
Cybersecurity article scored perfect overlap (1.000) and outranked the Stanford
AI Index report (0.667 overlap, 0.657 credibility). Sweeping every possible
weighting of relevance against credibility shows no weighting fixes both that
case and the opposite case where a credible-but-off-topic page wins - the
defect is the relevance signal, not the formula.

So relevance gains an embedding-based term. This is optional by design: if the
embedding service is absent or slow the caller keeps deterministic token
overlap, and the failure is visible in the returned mode rather than silent.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
from dataclasses import dataclass
from typing import List, Optional, Sequence

import httpx

logger = logging.getLogger("charlie.research.semantics")

# How much leading document text to embed. Headings and the opening summary
# carry the topic; embedding a whole page wastes latency for no ranking gain.
_DOC_CHARS = 1200
_MAX_DOCUMENTS = 16


@dataclass(frozen=True)
class SemanticRelevance:
    """Cosine similarity per document key, plus which mode produced them."""

    scores: dict
    mode: str

    def score_for(self, key: str) -> Optional[float]:
        return self.scores.get(key)


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = 0.0
    left_norm = 0.0
    right_norm = 0.0
    for a, b in zip(left, right):
        dot += a * b
        left_norm += a * a
        right_norm += b * b
    if left_norm <= 0.0 or right_norm <= 0.0:
        return 0.0
    value = dot / (math.sqrt(left_norm) * math.sqrt(right_norm))
    # Embedding backends can drift slightly outside the unit range.
    return max(-1.0, min(1.0, value))


def _endpoint(base_url: str) -> str:
    base = (base_url or "").rstrip("/")
    if not base:
        return ""
    if base.endswith("/api/embed") or base.endswith("/api/embeddings"):
        return base
    if base.endswith("/v1/embeddings") or base.endswith("/v1"):
        return base
    return f"{base}/api/embed"


def _extract(payload: dict) -> List[List[float]]:
    if isinstance(payload.get("embeddings"), list):
        return payload["embeddings"]
    data = payload.get("data")
    if isinstance(data, list):
        return [item.get("embedding", []) for item in data]
    embedding = payload.get("embedding")
    return [embedding] if isinstance(embedding, list) else []


async def _embed(client: httpx.AsyncClient, url: str, model: str, texts: List[str]) -> List[List[float]]:
    response = await client.post(url, json={"model": model, "input": texts}, timeout=15.0)
    response.raise_for_status()
    return _extract(response.json())


async def compute_semantic_relevance(
    query: str,
    documents: Sequence[tuple],
    *,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    timeout_s: float = 15.0,
) -> SemanticRelevance:
    """Score documents against the query using embeddings.

    ``documents`` is a sequence of ``(key, text)`` pairs. Returns mode
    ``"semantic"`` with one score per key, or mode ``"unavailable"`` with an
    empty mapping when the service cannot be used, so callers can fall back to
    token overlap without treating a failure as "no similarity".
    """
    resolved_base = base_url if base_url is not None else os.getenv(
        "MEMORY_EMBEDDING_URL", ""
    )
    resolved_model = model if model is not None else os.getenv(
        "MEMORY_EMBEDDING_MODEL", ""
    )
    url = _endpoint(resolved_base)
    if not url or not resolved_model or not documents:
        return SemanticRelevance({}, "unavailable")

    usable = list(documents)[:_MAX_DOCUMENTS]
    payloads = [query] + [f"{text[:_DOC_CHARS]}" for _key, text in usable]

    try:
        # trust_env is a client-level option; a local service must not be
        # redirected through proxy environment variables.
        async with httpx.AsyncClient(timeout=timeout_s, trust_env=False) as client:
            vectors = await _embed(client, url, resolved_model, payloads)
    except Exception as exc:
        logger.warning(
            "Embedding service unavailable for semantic relevance (%s: %s); "
            "falling back to token overlap.",
            type(exc).__name__,
            exc,
        )
        return SemanticRelevance({}, "unavailable")

    if len(vectors) < len(payloads):
        logger.warning(
            "Embedding service returned %d of %d vectors; falling back.",
            len(vectors),
            len(payloads),
        )
        return SemanticRelevance({}, "unavailable")

    query_vector = vectors[0]
    if not query_vector:
        return SemanticRelevance({}, "unavailable")

    scores = {
        key: _cosine(query_vector, vector)
        for (key, _text), vector in zip(usable, vectors[1:])
        if vector
    }
    if not scores:
        return SemanticRelevance({}, "unavailable")
    return SemanticRelevance(scores, "semantic")


async def gather_semantic_scores(
    query: str,
    documents: Sequence[tuple],
    **kwargs,
) -> SemanticRelevance:
    """Single entry point so callers never block on a missing service."""
    try:
        return await asyncio.wait_for(
            compute_semantic_relevance(query, documents, **kwargs), timeout=20.0
        )
    except asyncio.TimeoutError:
        logger.warning("Semantic relevance timed out; falling back to token overlap.")
        return SemanticRelevance({}, "unavailable")

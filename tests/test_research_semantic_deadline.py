"""Supporting optional-ranking deadline contract."""

import asyncio

import pytest

from charlie.research import semantics


@pytest.mark.asyncio
async def test_optional_relevance_honors_caller_deadline(monkeypatch):
    cancelled = asyncio.Event()

    async def slow_embedding(*args, **kwargs):
        try:
            await asyncio.sleep(0.03)
            return semantics.SemanticRelevance({"doc": 1.0}, "semantic")
        finally:
            cancelled.set()

    monkeypatch.setattr(semantics, "compute_semantic_relevance", slow_embedding)
    result = await semantics.gather_semantic_scores("question", [("doc", "text")], timeout_s=0.005)
    assert result.mode == "unavailable"
    assert cancelled.is_set()

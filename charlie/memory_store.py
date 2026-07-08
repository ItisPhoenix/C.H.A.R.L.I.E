"""Chroma-backed vector memory store for cross-session fact persistence.

Provides semantic search over past assistant/user facts using local embeddings.
Falls back to sentence-transformers if the primary embedding endpoint is unavailable.
"""

import json
import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("charlie.memory_store")

# --- Constants ---
_COLLECTION_NAME = "charlie_memories"
_DEFAULT_DB_PATH = "charlie_memory_db"
_DEFAULT_EMBEDDING_MODEL = ""
_DEFAULT_EMBEDDING_URL = ""
_DEFAULT_RELEVANCE_THRESHOLD = 0.3
_FACT_EXTRACT_MAX_CHARS = 2000
_FACT_EXTRACT_MODEL = ""


class _RemoteEmbeddingFunction:
    """Embedding function for ChromaDB using a remote embedding service.

    Supports both OpenAI-compatible (/v1/embeddings) and Ollama (/api/embeddings) endpoints.
    Automatically detects the endpoint format from the URL.
    """

    def __init__(self, model: str = _DEFAULT_EMBEDDING_MODEL, base_url: str = _DEFAULT_EMBEDDING_URL):
        self.model = model
        base = base_url.rstrip("/")
        if "/v1" in base:
            self._url = f"{base}/embeddings"
            self._format = "openai"
        else:
            self._url = f"{base}/api/embeddings"
            self._format = "ollama"
        self._name = f"remote-{model}"

    def name(self) -> str:
        return self._name

    def _call_api(self, texts: List[str]) -> List[List[float]]:
        import httpx

        if self._format == "openai":
            payload = {"model": self.model, "input": texts}
            resp = httpx.post(self._url, json=payload, timeout=10.0)
            resp.raise_for_status()
            data = resp.json()
            return [item["embedding"] for item in data["data"]]
        else:
            results: List[List[float]] = []
            for text in texts:
                payload = {"model": self.model, "prompt": text}
                resp = httpx.post(self._url, json=payload, timeout=10.0)
                resp.raise_for_status()
                results.append(resp.json()["embedding"])
            return results

    def __call__(self, input: List[str]) -> List[List[float]]:
        return self._call_api(input)

    def embed_query(self, input: List[str]) -> List[List[float]]:
        # ChromaDB calls embed_query(input=Documents) -> Embeddings
        # Same signature as __call__
        return self._call_api(input)

    def embed_documents(self, input: List[str]) -> List[List[float]]:
        return self._call_api(input)


class _SentenceTransformerEmbeddingFunction:
    """Fallback embedding function using sentence-transformers."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        from sentence_transformers import SentenceTransformer
        self._model = SentenceTransformer(model_name)
        self._name = f"sentence-transformers-{model_name}"

    def name(self) -> str:
        return self._name

    def __call__(self, input: List[str]) -> List[List[float]]:
        return self._model.encode(input).tolist()

    def embed_query(self, input: List[str]) -> List[List[float]]:
        return self._model.encode(input).tolist()

    def embed_documents(self, input: List[str]) -> List[List[float]]:
        return self._model.encode(input).tolist()


def _build_embedding_function(config: Any) -> Any:
    """Build the best available embedding function. Falls back gracefully."""
    url = getattr(config, "memory_embedding_url", _DEFAULT_EMBEDDING_URL)
    model = getattr(config, "memory_embedding_model", _DEFAULT_EMBEDDING_MODEL)

    # Try primary embedding service
    try:
        ef = _RemoteEmbeddingFunction(model=model, base_url=url)
        # Quick smoke test
        ef.embed_query(["test"])
        logger.info("Using embedding service at %s (model=%s)", url, model)
        return ef
    except Exception as e:
        logger.warning("Primary embedding service unavailable: %s", e)

    # Fallback to sentence-transformers
    try:
        ef = _SentenceTransformerEmbeddingFunction()
        logger.info("Using sentence-transformers fallback for embeddings")
        return ef
    except Exception as e:
        logger.error("No embedding backend available: %s", e)
        return None


class MemoryStore:
    """Persistent vector memory backed by ChromaDB with local embeddings.

    Stores facts extracted from conversations and retrieves them semantically.
    """

    def __init__(self, config: Any):
        self.config = config
        self.db_path = getattr(config, "memory_db_path", _DEFAULT_DB_PATH)
        self.relevance_threshold = getattr(config, "memory_relevance_threshold", _DEFAULT_RELEVANCE_THRESHOLD)
        self.auto_extract = getattr(config, "memory_auto_extract", True)

        # Build embedding function (may be None if no backend available)
        self._ef = _build_embedding_function(config)
        if self._ef is None:
            logger.warning(
                "MemoryStore: no embedding backend. Memory features disabled."
            )
            self._collection = None
            return

        try:
            import chromadb

            client = chromadb.PersistentClient(path=self.db_path)
            self._collection = client.get_or_create_collection(
                name=_COLLECTION_NAME,
                embedding_function=self._ef,
                metadata={"hnsw:space": "cosine"},
            )
            logger.info(
                "MemoryStore initialized: %s (%d documents)",
                self.db_path,
                self._collection.count(),
            )
        except Exception as e:
            logger.error("Failed to initialize ChromaDB: %s", e, exc_info=True)
            self._collection = None

    @property
    def is_available(self) -> bool:
        return self._collection is not None

    def add_memory(
        self,
        text: str,
        source: str,
        session_id: str,
        *,
        auto_extract: bool = True,
    ) -> int:
        """Store facts from text. Returns number of facts stored.

        If auto_extract is True, uses a fast LLM to extract factual statements.
        Otherwise stores the raw text as a single document.
        """
        if not self.is_available:
            return 0

        facts: List[str] = []

        if auto_extract and self.auto_extract and len(text) > 50:
            facts = self._extract_facts(text)
        elif text.strip():
            facts = [text.strip()]

        if not facts:
            return 0

        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        documents: List[str] = []
        metadatas: List[Dict[str, str]] = []
        ids: List[str] = []

        for i, fact in enumerate(facts):
            if not fact or len(fact) < 5:
                continue
            doc_id = f"{session_id}_{time.time_ns()}_{i}"
            documents.append(fact)
            metadatas.append(
                {
                    "source": source,
                    "timestamp": now,
                    "session_id": session_id,
                }
            )
            ids.append(doc_id)

        if not documents:
            return 0

        try:
            self._collection.add(
                documents=documents,
                metadatas=metadatas,
                ids=ids,
            )
            logger.info("Stored %d facts from %s", len(documents), source)
            return len(documents)
        except Exception as e:
            logger.warning("Failed to store facts: %s", e, exc_info=True)
            return 0

    def search(
        self,
        query: str,
        n_results: int = 3,
        threshold: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """Semantic search over stored memories.

        Returns list of dicts with keys: text, distance, metadata.
        Only returns results with distance below threshold.
        """
        if not self.is_available or not query.strip():
            return []

        threshold = threshold if threshold is not None else self.relevance_threshold

        try:
            results = self._collection.query(
                query_texts=[query],
                n_results=n_results,
            )
        except Exception as e:
            logger.warning("Memory search failed: %s", e, exc_info=True)
            return []

        if not results or not results.get("documents"):
            return []

        documents = results["documents"][0] if results["documents"] else []
        distances = results["distances"][0] if results.get("distances") else []
        metadatas = results["metadatas"][0] if results.get("metadatas") else []

        filtered: List[Dict[str, Any]] = []
        for doc, dist, meta in zip(documents, distances, metadatas):
            # Chroma cosine distance: lower = more similar. 0 = identical.
            if dist <= threshold:
                filtered.append(
                    {"text": doc, "distance": dist, "metadata": meta or {}}
                )

        logger.debug(
            "Memory search: query=%s -> %d/%d results below threshold %.2f",
            query[:50],
            len(filtered),
            len(documents),
            threshold,
        )
        return filtered

    def _extract_facts(self, text: str) -> List[str]:
        """Use a fast LLM to extract factual statements from text.

        Returns a list of fact strings, or empty list on failure.
        """
        # Truncate to avoid huge payloads
        truncated = text[:_FACT_EXTRACT_MAX_CHARS]

        prompt = (
            "Extract up to 3 factual statements or preferences from this text.\n"
            "Return ONLY a JSON object: {\"facts\": [\"string\", ...]}\n"
            "If none are facts or preferences, return {\"facts\": []}\n\n"
            f"Text:\n{truncated}"
        )

        try:
            import httpx

            # Use the small LLM endpoint (configurable)
            fast_url = getattr(self.config, "small_llm_url", "")
            fast_model = getattr(self.config, "small_llm_model", _FACT_EXTRACT_MODEL)
            fast_key = getattr(self.config, "small_llm_key", "no-key")

            if not fast_url:
                logger.debug("No LLM configured for fact extraction")
                return []

            headers = {"Content-Type": "application/json"}
            if fast_key and fast_key not in ("no-key", "no_key"):
                headers["Authorization"] = f"Bearer {fast_key}"

            payload = {
                "model": fast_model,
                "messages": [
                    {"role": "system", "content": "You extract facts. Return only JSON."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.0,
                "max_tokens": 256,
                "stream": False,
            }

            resp = httpx.post(
                f"{fast_url.rstrip('/')}/chat/completions",
                json=payload,
                headers=headers,
                timeout=10.0,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]

            # Parse JSON from response (handle markdown code blocks)
            content = content.strip()
            if content.startswith("```"):
                lines = content.split("\n")
                content = "\n".join(lines[1:-1])

            data = json.loads(content)
            facts = data.get("facts", [])
            if isinstance(facts, list):
                return [str(f) for f in facts if f]
            return []
        except Exception as e:
            logger.debug("Fact extraction failed: %s", e)
            return []

    def format_for_prompt(self, results: List[Dict[str, Any]]) -> str:
        """Format search results into a prompt injection block."""
        if not results:
            return ""
        lines = ["[Relevant memories from past conversations:]"]
        for r in results:
            lines.append(f"- {r['text']}")
        return "\n".join(lines)

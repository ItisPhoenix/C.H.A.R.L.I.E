"""Shared embedding function singleton — loads the model once, reuses everywhere.

Uses a background thread with timeout so a hung model load doesn't block Brain startup.
Falls back to ChromaDB's default embedding function if SentenceTransformer fails.
"""

import threading

from chromadb.utils import embedding_functions
from charlie.utils.logger import get_logger

logger = get_logger(__name__)

_model_name = "all-MiniLM-L6-v2"
_embedding_fn = None
_lock = threading.Lock()


def _load_sentence_transformer():
    """Attempt to load SentenceTransformerEmbeddingFunction."""
    return embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name=_model_name
    )


def get_embedding_fn():
    """Return the shared SentenceTransformerEmbeddingFunction (singleton).

    Loads in a background thread with a 10-second timeout.  If the model
    fails to load (network issue, corrupted cache, etc.), falls back to
    ChromaDB's default embedding function so Brain can still start.
    """
    global _embedding_fn
    if _embedding_fn is not None:
        return _embedding_fn

    with _lock:
        if _embedding_fn is not None:
            return _embedding_fn

        result = [None]
        error = [None]

        def _worker():
            try:
                result[0] = _load_sentence_transformer()
            except Exception as e:
                error[0] = e

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()
        thread.join(timeout=10)

        if thread.is_alive():
            logger.warning(
                "embedding_load_timeout | model=%s | falling_back=default",
                _model_name,
            )
            _embedding_fn = embedding_functions.DefaultEmbeddingFunction()
        elif error[0] is not None:
            logger.warning(
                "embedding_load_failed | model=%s | error=%s | falling_back=default",
                _model_name,
                error[0],
            )
            _embedding_fn = embedding_functions.DefaultEmbeddingFunction()
        else:
            logger.info("embedding_loaded | model=%s", _model_name)
            _embedding_fn = result[0]

        return _embedding_fn

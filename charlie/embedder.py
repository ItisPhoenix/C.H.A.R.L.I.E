import logging
import os
import threading
import numpy as np
import onnxruntime as ort
from huggingface_hub import hf_hub_download
from tokenizers import Tokenizer

logger = logging.getLogger("charlie.embedder")

HF_MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"


class LocalEmbedder:
    """ONNX-based embedding model. Replaces PyTorch SentenceTransformer for fast, low-memory inference."""
    _session = None
    _tokenizer = None
    _lock = threading.Lock()

    def __init__(self, model_name: str = HF_MODEL_ID, cache_dir: str = "models/onnx-embedder"):
        self.model_name = model_name
        self.cache_dir = cache_dir
        os.makedirs(self.cache_dir, exist_ok=True)

    def _load(self):
        with self._lock:
            if self._session is not None:
                return
            try:
                # Download ONNX model
                model_path = hf_hub_download(
                    self.model_name, "onnx/model.onnx", cache_dir=self.cache_dir
                )
                # Download tokenizer
                tok_path = hf_hub_download(
                    self.model_name, "tokenizer.json", cache_dir=self.cache_dir
                )

                # Create ONNX session (CPU-only, lightweight)
                providers = ["CPUExecutionProvider"]
                self._session = ort.InferenceSession(model_path, providers=providers)
                self._tokenizer = Tokenizer.from_file(tok_path)
                logger.info(f"ONNX embedder loaded: {self.model_name}")
            except Exception as e:
                logger.error(f"Failed to load ONNX embedder: {e}. Semantic memory disabled.")
                self._session = None

    def _tokenize(self, texts: list[str], max_length: int = 128) -> dict:
        """Tokenize texts and return dict of numpy arrays for ONNX input."""
        self._tokenizer.enable_truncation(max_length=max_length)
        self._tokenizer.enable_padding(length=max_length, pad_id=0)

        input_ids = []
        attention_mask = []
        for text in texts:
            encoding = self._tokenizer.encode(text)
            input_ids.append(encoding.ids)
            attention_mask.append(encoding.attention_mask)

        return {
            "input_ids": np.array(input_ids, dtype=np.int64),
            "attention_mask": np.array(attention_mask, dtype=np.int64),
            "token_type_ids": np.zeros_like(np.array(input_ids, dtype=np.int64)),
        }

    def _mean_pooling(self, last_hidden_state: np.ndarray, attention_mask: np.ndarray) -> np.ndarray:
        """Mean pooling — take attention mask into account for correct averaging."""
        mask_expanded = np.expand_dims(attention_mask, -1).astype(np.float32)
        sum_embeddings = np.sum(last_hidden_state * mask_expanded, axis=1)
        sum_mask = np.clip(mask_expanded.sum(axis=1), a_min=1e-9, a_max=None)
        return sum_embeddings / sum_mask

    def embed(self, texts: list[str]) -> np.ndarray:
        self._load()
        if self._session is None:
            # Fallback: return zero vectors so callers don't crash
            return np.zeros((len(texts), 384), dtype=np.float32)

        inputs = self._tokenize(texts)
        outputs = self._session.run(None, inputs)
        # outputs[0] = last_hidden_state (batch, seq_len, hidden)
        embeddings = self._mean_pooling(outputs[0], inputs["attention_mask"])
        # L2 normalize
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        embeddings = embeddings / np.clip(norms, 1e-9, a_max=None)
        return embeddings.astype(np.float32)

    def embed_single(self, text: str) -> np.ndarray:
        return self.embed([text])[0]

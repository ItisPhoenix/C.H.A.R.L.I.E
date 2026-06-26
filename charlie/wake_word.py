"""Wake-word detector using ONNX classification pipeline.

Pipeline: raw audio -> mel spectrogram -> speech embeddings -> classifier.
The charlie.onnx classifier takes [1, 16, 96] embedding features and
returns a probability score. A threshold determines activation.
"""

import logging
import os

import numpy as np

logger = logging.getLogger("charlie.wake_word")

# Pipeline constants (match openWakeWord upstream)
_MEL_MODEL_PATH = os.path.join("charlie", "models", "melspectrogram.onnx")
_EMBED_MODEL_PATH = os.path.join("charlie", "models", "embedding_model.onnx")
_SAMPLE_RATE = 16000
_EMBED_WINDOW = 76   # mel frames per embedding window
_EMBED_STRIDE = 8    # hop between embedding windows
_N_TIMESTEPS = 16    # classifier requires exactly 16 embedding timesteps
def _mel_transform(x: np.ndarray) -> np.ndarray:
    """OpenWakeWord normalization: mel_db / 10 + 2."""
    return x / 10.0 + 2.0


class WakeWordDetector:
    """ONNX-based wake-word detector. Thread-safe for inference calls.

    Loads three ONNX models:
      1. melspectrogram.onnx - raw audio to mel features
      2. embedding_model.onnx - mel features to speech embeddings
      3. classifier (charlie.onnx) - embeddings to probability score

    All model loading errors are caught and logged; the detector
    sets _loaded=False and is_triggered() always returns False.
    """

    def __init__(
        self,
        classifier_path: str,
        melspec_path: str = _MEL_MODEL_PATH,
        embed_path: str = _EMBED_MODEL_PATH,
        threshold: float = 0.6,
        sample_rate: int = _SAMPLE_RATE,
    ):
        self.threshold = threshold
        self.sample_rate = sample_rate
        self._loaded = False
        self._melspec_sess = None
        self._embed_sess = None
        self._classifier_sess = None

        try:
            import onnxruntime as ort

            opts = ort.SessionOptions()
            opts.inter_op_num_threads = 1
            opts.intra_op_num_threads = 1

            # Mel spectrogram model
            if not os.path.exists(melspec_path):
                logger.warning(f"Mel model not found: {melspec_path}")
                return
            self._melspec_sess = ort.InferenceSession(melspec_path, opts)

            # Embedding model
            if not os.path.exists(embed_path):
                logger.warning(f"Embedding model not found: {embed_path}")
                return
            self._embed_sess = ort.InferenceSession(embed_path, opts)

            # Classifier
            if not os.path.exists(classifier_path):
                logger.warning(f"Wake word classifier not found: {classifier_path}")
                return
            self._classifier_sess = ort.InferenceSession(classifier_path, opts)

            self._loaded = True
            logger.info(
                f"Wake word detector loaded | threshold={threshold}"
            )
        except Exception as e:
            logger.warning(f"Wake word detector failed to load: {e}")

    @property
    def is_available(self) -> bool:
        """Whether all models loaded successfully."""
        return self._loaded

    def is_triggered(self, audio_chunk: np.ndarray) -> bool:
        """Run full pipeline on an audio chunk and return True if wake word detected.

        Args:
            audio_chunk: Raw float32 audio samples (16 kHz mono).
                         Any length is accepted; the detector accumulates
                         internally via _run_pipeline.

        Returns:
            True if classifier score >= threshold.
        """
        if not self._loaded:
            return False

        try:
            score = self._run_pipeline(audio_chunk)
            return score >= self.threshold
        except Exception as e:
            logger.debug(f"Wake word inference error: {e}")
            return False

    def _run_pipeline(self, audio: np.ndarray) -> float:
        """Execute mel -> embedding -> classify pipeline. Returns score."""

        # Ensure correct format for mel model
        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)
        if audio.ndim == 1:
            audio = audio[None, :]  # (1, samples)

        # Step 1: Mel spectrogram
        mel_out = self._melspec_sess.run(None, {"input": audio})
        mel_spec = mel_out[0].squeeze()  # (time_frames, 32)
        mel_spec = _mel_transform(mel_spec)

        # Step 2: Sliding window embeddings
        n_frames = mel_spec.shape[0]
        if n_frames < _EMBED_WINDOW:
            # Not enough frames for even one window - pad with zeros
            mel_spec = np.pad(
                mel_spec,
                ((_EMBED_WINDOW - n_frames, 0), (0, 0)),
                mode="constant",
            )
            n_frames = mel_spec.shape[0]

        windows = []
        for i in range(0, n_frames - _EMBED_WINDOW + 1, _EMBED_STRIDE):
            windows.append(mel_spec[i : i + _EMBED_WINDOW])

        if not windows:
            return 0.0

        batch = np.expand_dims(np.array(windows), axis=-1).astype(np.float32)
        emb_out = self._embed_sess.run(None, {"input_1": batch})
        embeddings = emb_out[0].squeeze()  # (n_windows, 96)

        # Step 3: Take last _N_TIMESTEPS, pad if needed
        if embeddings.ndim == 1:
            # Single window - expand to (1, 96)
            embeddings = embeddings[None, :]

        if embeddings.shape[0] >= _N_TIMESTEPS:
            features = embeddings[-_N_TIMESTEPS:]
        else:
            pad_len = _N_TIMESTEPS - embeddings.shape[0]
            features = np.pad(
                embeddings, ((pad_len, 0), (0, 0)), mode="constant"
            )

        # Step 4: Classify
        features_batch = features[None, :].astype(np.float32)
        score_out = self._classifier_sess.run(
            None, {"onnx::Flatten_0": features_batch}
        )
        score = float(score_out[0].item())
        return score

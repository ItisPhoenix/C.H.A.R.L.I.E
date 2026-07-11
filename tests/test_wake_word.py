"""Unit tests for charlie.wake_word.

Mocks onnxruntime to avoid GPU/ONNX model dependencies.
"""
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from charlie.wake_word import WakeWordDetector, _mel_transform


class TestMelTransform:
    """Pure function — no mocking needed."""

    def test_normalizes_mel_values(self):
        result = _mel_transform(np.array([[10.0, 20.0]]))
        expected = np.array([[3.0, 4.0]])  # x/10 + 2
        np.testing.assert_allclose(result, expected)

    def test_handles_negative_values(self):
        result = _mel_transform(np.array([[-10.0]]))
        assert result[0, 0] == 1.0  # -10/10 + 2 = 1

    def test_handles_zero(self):
        result = _mel_transform(np.array([[0.0]]))
        assert result[0, 0] == 2.0  # 0/10 + 2 = 2


class TestWakeWordDetectorInit:
    """Constructor should degrade gracefully when models are absent."""

    def test_returns_not_available_when_classifier_missing(self, tmp_path):
        """All three model paths must exist; missing classifier = not loaded."""
        dummy_mel = tmp_path / "mel.onnx"
        dummy_embed = tmp_path / "embed.onnx"
        dummy_mel.write_bytes(b"\x00")
        dummy_embed.write_bytes(b"\x00")

        detector = WakeWordDetector(
            classifier_path=str(tmp_path / "missing.onnx"),
            melspec_path=str(dummy_mel),
            embed_path=str(dummy_embed),
        )
        assert not detector.is_available
        assert not detector.is_triggered(np.zeros(16000, dtype=np.float32))

    def test_returns_not_available_when_mel_model_missing(self, tmp_path):
        detector = WakeWordDetector(
            classifier_path=str(tmp_path / "clf.onnx"),
            melspec_path=str(tmp_path / "missing.onnx"),
        )
        assert not detector.is_available

    def test_handles_onnx_import_error(self):
        with patch.dict("sys.modules", {"onnxruntime": None}):
            detector = WakeWordDetector(
                classifier_path="nope.onnx",
            )
            assert not detector.is_available


class TestWakeWordDetectorLoaded:
    """Full pipeline with mocked ONNX sessions."""

    @pytest.fixture
    def mock_sessions(self):
        """Replace InferenceSession with a mock that returns canned outputs."""
        with patch("onnxruntime.InferenceSession") as mock_inf:
            mock_mel = MagicMock()
            mock_mel.run.return_value = [np.ones((1, 20, 32), dtype=np.float32)]
            mock_embed = MagicMock()
            # 3 windows × 96 embedding dims
            mock_embed.run.return_value = [
                np.ones((3, 96), dtype=np.float32) * 0.5
            ]
            mock_clf = MagicMock()
            mock_clf.run.return_value = [np.array([[0.85]])]

            def side_effect(path, opts=None):
                if "mel" in str(path):
                    return mock_mel
                if "embed" in str(path):
                    return mock_embed
                return mock_clf

            mock_inf.side_effect = side_effect
            yield mock_inf  # yields the patched object

    @pytest.fixture
    def detector(self, mock_sessions, tmp_path):
        """Build a WakeWordDetector with real file paths (files need not exist)."""
        # Touch files so os.path.exists passes
        for name in ("mel.onnx", "embed.onnx", "clf.onnx"):
            (tmp_path / name).write_bytes(b"\x00")
        return WakeWordDetector(
            classifier_path=str(tmp_path / "clf.onnx"),
            melspec_path=str(tmp_path / "mel.onnx"),
            embed_path=str(tmp_path / "embed.onnx"),
            threshold=0.6,
        )

    def test_loaded_sessions_created(self, detector):
        assert detector.is_available

    def test_is_triggered_above_threshold(self, detector):
        assert detector.is_triggered(np.zeros(16000, dtype=np.float32))

    def test_not_triggered_below_threshold(self, mock_sessions, tmp_path):
        for name in ("mel.onnx", "embed.onnx", "clf.onnx"):
            (tmp_path / name).write_bytes(b"\x00")
        detector = WakeWordDetector(
            classifier_path=str(tmp_path / "clf.onnx"),
            melspec_path=str(tmp_path / "mel.onnx"),
            embed_path=str(tmp_path / "embed.onnx"),
            threshold=0.9,  # higher than mock's 0.85
        )
        assert not detector.is_triggered(np.zeros(16000, dtype=np.float32))

    def test_is_triggered_handles_inference_error(self, detector, mock_sessions):
        """Pipeline exception returns False, doesn't crash."""
        # Make the classifier session raise — this is the actual mock returned
        # by the side_effect callback, NOT mock_sessions.return_value.
        clf = mock_sessions("clf.onnx")  # side_effect returns mock_clf
        clf.run.side_effect = RuntimeError("boom")
        result = detector.is_triggered(np.zeros(16000, dtype=np.float32))
        assert result is False

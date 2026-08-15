"""Unit tests for Charlie V1 Capability Semantic Verifiers."""

import os
import tempfile

from charlie.verifiers import (
    VerificationResult,
    run_verifier_for_match,
    verify_browser_navigate,
    verify_file_write,
    verify_volume,
)


class TestVolumeVerifier:
    def test_verify_volume_returns_result(self):
        # Read-back test
        res = verify_volume()
        assert isinstance(res, VerificationResult)
        assert res.status in ("completed", "unverified")

    def test_verify_volume_pct_tolerance(self, monkeypatch):
        from charlie import media_adapter

        monkeypatch.setattr(
            media_adapter, "_volume_snapshot", lambda: {"volume_percent": 50, "muted": False}
        )

        # Within tolerance (+/- 3%)
        res_ok = verify_volume(expected_pct=52.0, tolerance_pct=3.0)
        assert res_ok.status == "completed"
        assert res_ok.verified is True

        # Outside tolerance
        res_fail = verify_volume(expected_pct=80.0, tolerance_pct=3.0)
        assert res_fail.status == "partially_completed"
        assert res_fail.verified is False

    def test_verify_volume_mute(self, monkeypatch):
        from charlie import media_adapter

        monkeypatch.setattr(
            media_adapter, "_volume_snapshot", lambda: {"volume_percent": 50, "muted": True}
        )

        res = verify_volume(expected_muted=True)
        assert res.status == "completed"
        assert res.verified is True

        res_mismatch = verify_volume(expected_muted=False)
        assert res_mismatch.status == "failed"
        assert res_mismatch.verified is False


class TestFileWriteVerifier:
    def test_verify_file_write_success(self):
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as f:
            f.write("Hello Charlie V1 Semantic Verification")
            temp_path = f.name

        try:
            res = verify_file_write(
                temp_path,
                expected_content="Hello Charlie V1 Semantic Verification",
                expected_min_bytes=10,
            )
            assert res.status == "completed"
            assert res.verified is True
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_verify_file_write_missing(self):
        res = verify_file_write("C:\\nonexistent_charlie_file_xyz_123.tmp")
        assert res.status == "failed"
        assert res.verified is False


class TestBrowserNavigateVerifier:
    def test_verify_browser_navigate(self):
        res = verify_browser_navigate(
            expected_url_or_domain="https://github.com",
            actual_url="https://github.com/astral-sh/uv",
            ready_state="complete",
        )
        assert res.status == "completed"
        assert res.verified is True

        res_mismatch = verify_browser_navigate(
            expected_url_or_domain="https://reddit.com",
            actual_url="https://github.com",
        )
        assert res_mismatch.status == "partially_completed"
        assert res_mismatch.verified is False


class TestVerifierDispatcher:
    def test_run_verifier_for_match(self, monkeypatch):
        from charlie import media_adapter

        monkeypatch.setattr(
            media_adapter, "_volume_snapshot", lambda: {"volume_percent": 40, "muted": False}
        )

        res = run_verifier_for_match(
            verifier_name="verify_volume",
            tool_name="set_volume",
            arguments={"percent": 40},
            result_text="Volume set to 40%.",
        )
        assert res.status == "completed"
        assert res.verified is True

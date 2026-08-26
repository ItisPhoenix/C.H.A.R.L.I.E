from pathlib import Path


def test_launcher_does_not_claim_microphone_listening_before_readiness() -> None:
    source = Path("run.py").read_text(encoding="utf-8")

    assert "Voice Loop: Active (listening to mic)" not in source
    assert "Voice Loop: Starting (microphone readiness pending)" in source

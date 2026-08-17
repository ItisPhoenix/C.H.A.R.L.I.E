import tempfile
from pathlib import Path

import pytest

from charlie.config import Config
from charlie.settings_service import SettingsService, SettingValidationError


def test_settings_service_describes_fields():
    cfg = Config()
    service = SettingsService(cfg, env_path=Path(".env.test-nonexistent"))
    specs = service.get_field_specs()
    assert len(specs) > 40

    by_key = {s["key"]: s for s in specs}
    assert "LLM_URL" in by_key
    assert "LLM_API_KEY" in by_key

    # Check secret masking
    secret_field = by_key["LLM_API_KEY"]
    assert secret_field["secret"] is True
    assert secret_field["value"] is None
    assert isinstance(secret_field["is_set"], bool)

    # Check non-secret
    url_field = by_key["LLM_URL"]
    assert url_field["secret"] is False
    assert "value" in url_field


def test_settings_service_validation():
    cfg = Config()
    service = SettingsService(cfg, env_path=Path(".env.test-nonexistent"))

    # Valid update
    validated = service.validate_updates({
        "KOKORO_LANG": "en-gb",
        "WAKE_WORD_ENABLED": True,
        "CONTEXT_WINDOW": 16000,
        "VAD_THRESHOLD": 0.35,
    })
    assert validated["KOKORO_LANG"] == "en-gb"
    assert validated["WAKE_WORD_ENABLED"] is True
    assert validated["CONTEXT_WINDOW"] == 16000
    assert validated["VAD_THRESHOLD"] == 0.35

    # Invalid int
    with pytest.raises(SettingValidationError):
        service.validate_updates({"CONTEXT_WINDOW": "not-an-int"})

    # Invalid float
    with pytest.raises(SettingValidationError):
        service.validate_updates({"VAD_THRESHOLD": "not-a-float"})

    # Unknown key ignored or filtered
    filtered = service.validate_updates({"NON_EXISTENT_KEY": "value"})
    assert "NON_EXISTENT_KEY" not in filtered


def test_settings_service_atomic_write():
    with tempfile.TemporaryDirectory() as tmpdir:
        env_file = Path(tmpdir) / ".env"
        env_file.write_text("# Existing comment\nEXISTING_VAR=123\nLLM_MODEL=old-model\n", encoding="utf-8")

        cfg = Config()
        service = SettingsService(cfg, env_path=env_file)

        touched = service.update_settings({
            "LLM_MODEL": "new-model",
            "GPU_DEVICE": "cuda:0",
        })

        # Verify in-memory config updated
        assert cfg.llm_model == "new-model"
        assert cfg.gpu_device == "cuda:0"
        assert "voice" in touched  # GPU_DEVICE touches voice restart tier

        # Verify file written atomically and preserved existing comments
        content = env_file.read_text(encoding="utf-8")
        assert "# Existing comment" in content
        assert "EXISTING_VAR=123" in content
        assert "LLM_MODEL=new-model" in content
        assert "GPU_DEVICE=cuda:0" in content

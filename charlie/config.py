import json
import logging
import os
import re
from pathlib import Path

from dotenv import load_dotenv

from charlie.utils.persona import get_system_prompt

logger = logging.getLogger("charlie.config")
load_dotenv(override=True)


class LLMSettings:
    """Universal LLM endpoints. Any OpenAI-compatible server (LM Studio, Ollama, NIM, OpenRouter, vLLM, etc.).

    No defaults — the user MUST set LLM_URL in .env. Charlie will not start without it.
    Vision is optional: if LLM_VISION_URL is empty, vision features are disabled with a warning.
    Embeddings are computed on-device (no env vars for that).
    """

    def __init__(self):
        # --- Primary chat (any OpenAI-compatible server) ---
        self.llm_url: str = os.getenv("LLM_URL", "").rstrip("/")
        self.llm_api_key: str = os.getenv("LLM_API_KEY", "")
        self.llm_model: str = os.getenv("LLM_MODEL", "")
        # --- Vision (separate endpoint, optional) ---
        self.llm_vision_url: str = os.getenv("LLM_VISION_URL", "").rstrip("/")
        self.llm_vision_api_key: str = os.getenv("LLM_VISION_API_KEY", "")
        self.llm_vision_model: str = os.getenv("LLM_VISION_MODEL", "")

    def validate(self) -> list[str]:
        """Return a list of human-readable problems. Empty list = OK."""
        problems = []
        if not self.llm_url:
            problems.append("LLM_URL is empty. Set it in .env to any OpenAI-compatible endpoint.")
        if not self.llm_model:
            problems.append("LLM_MODEL is empty. Set it in .env to a model name served by LLM_URL.")
        if not problems and self.llm_vision_url and not self.llm_vision_model:
            problems.append("LLM_VISION_URL is set but LLM_VISION_MODEL is empty.")
        return problems


class ResourceSettings:
    def __init__(self):
        from charlie.utils.vram import detect_total_vram_mb, calculate_budget_mb
        total = detect_total_vram_mb()
        self.vram_total_mb = total
        self.vram_budget_mb = calculate_budget_mb(total)
        self.model_priority = {"text": "primary", "vision": "on_demand"}
        self.model_unload_delay_s = 30
        self.max_context_tokens = 4096


class AudioSettings:
    def __init__(self):
        self.wakeword_models = ["charlie/models/charlie.onnx"]
        self.stt_model = "distil-large-v3"
        self.mic_index = int(os.getenv("MIC_INDEX", "1"))
        self.output_index = int(os.getenv("OUTPUT_INDEX", "4"))
        self.sample_rate = 16000
        self.target_rate = 16000
        self.conversation_timeout = 300
        self.silence_limit = 0.4
        self.vad_mode = 3
        self.duck_steps = 8
        self.wake_confidence = float(os.getenv("WAKE_CONFIDENCE", "0.5"))


class WatchdogSettings:
    def __init__(self):
        self.telegram_token = os.getenv("TELEGRAM_TOKEN")
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.reports_path = "charlie/reports"


class SecuritySettings:
    def __init__(self):
        self.tier_2_countdown = 30
        self.snapshots_enabled = True
        self.require_confirmation_tier1 = True
        self.restricted_paths = ["charlie/security", "charlie/watchdog"]
        # Dangerous capabilities default to OFF (Reqs 17.1, 17.2). These are the
        # canonical feature flags; the Operator may enable them via
        # charlie_config.json ("safety" block) and changes are persisted back.
        self.self_modify_enabled = False


class AuditSettings:
    def __init__(self):
        self.dep_audit_enabled = os.getenv("DEP_AUDIT_ENABLED", "true").lower() == "true"
        self.dep_audit_interval_hours = 24
        self.log_redaction_enabled = True
        self.max_llm_calls_per_minute = 30
        self.max_telegram_messages_per_minute = 20


class StartupSettings:
    def __init__(self):
        self.play_music = False


class PersonaSettings:
    def __init__(self):
        pass


class Settings:
    def __init__(self):
        self.llm = LLMSettings()
        self.audio = AudioSettings()
        self.supervisor = WatchdogSettings()
        self.security = SecuritySettings()
        self.audit = AuditSettings()
        self.startup = StartupSettings()
        self.persona = PersonaSettings()
        self.resources = ResourceSettings()
        self.integrations = {}
        self.providers = {}
        self.mcp_servers = {}

    def to_dict(self):
        """Serialize all settings to a flat dict for API responses."""
        def _obj_to_dict(obj):
            if hasattr(obj, '__dict__'):
                return {k: v for k, v in obj.__dict__.items() if not k.startswith('_')}
            return obj

        return {
            "llm": _obj_to_dict(self.llm),
            "audio": _obj_to_dict(self.audio),
            "supervisor": _obj_to_dict(self.supervisor),
            "security": _obj_to_dict(self.security),
            "audit": _obj_to_dict(self.audit),
            "startup": _obj_to_dict(self.startup),
            "resources": _obj_to_dict(self.resources),
            "integrations": self.integrations,
            "providers": self.providers,
            "mcp_servers": self.mcp_servers,
        }


def resolve_env_vars(obj):
    """Replace $ENV_VAR references with actual environment variable values."""
    if isinstance(obj, str):
        def replace_var(m):
            var_name = m.group(1)
            return os.environ.get(var_name, m.group(0))
        return re.sub(r'\$([A-Z_][A-Z0-9_]*)', replace_var, obj)
    elif isinstance(obj, dict):
        return {k: resolve_env_vars(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [resolve_env_vars(v) for v in obj]
    return obj


def load_json_overrides():
    """Reads charlie_config.json and overrides default settings."""
    config_path = Path(__file__).parent.parent / "charlie_config.json"
    if not config_path.exists():
        return

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Audio
        if "voice" in data:
            v = data["voice"]
            if "mic_index" in v:
                settings.audio.mic_index = int(v["mic_index"])
            if "output_index" in v:
                settings.audio.output_index = int(v["output_index"])

        # Safety / Self-mod
        if "safety" in data:
            s = data["safety"]
            if "require_confirmation_tier1" in s:
                settings.security.require_confirmation_tier1 = s[
                    "require_confirmation_tier1"
                ]
            if "self_modify_enabled" in s:
                settings.security.self_modify_enabled = s["self_modify_enabled"]

        # Startup
        if "startup" in data:
            st = data["startup"]
            if "play_music" in st:
                settings.startup.play_music = st["play_music"]

        # Providers (LLM model routing — values may reference $ENV_VAR)
        if "providers" in data:
            settings.providers = resolve_env_vars(data["providers"])

        # Resources (VRAM budget, model priority)
        if "resources" in data:
            r = data["resources"]
            if "vram_budget_mb" in r:
                settings.resources.vram_budget_mb = int(r["vram_budget_mb"])
            if "model_unload_delay_s" in r:
                settings.resources.model_unload_delay_s = int(r["model_unload_delay_s"])
            if "model_priority" in r:
                settings.resources.model_priority = r["model_priority"]

        # LLM overrides
        if "llm" in data:
            l = data["llm"]
            if "url" in l and l["url"]:
                settings.llm.llm_url = l["url"].rstrip("/")
            if "api_key" in l and l["api_key"]:
                settings.llm.llm_api_key = l["api_key"]
            if "model" in l and l["model"]:
                settings.llm.llm_model = l["model"]

        # Audio overrides (distinct from "voice" which controls TTS/kokoro)
        if "audio" in data:
            a = data["audio"]
            if "stt_model" in a:
                settings.audio.stt_model = a["stt_model"]
            if "wake_word" in a:
                settings.integrations["wake_word"] = a["wake_word"]

        # Integrations
        if "integrations" in data:
            settings.integrations.update(data["integrations"])

        # Features
        if "features" in data:
            settings.integrations["features"] = data["features"]

        # Security overrides (distinct from "safety" which controls self-mod flags)
        if "security" in data:
            sec = data["security"]
            if "risk_tier" in sec:
                settings.integrations["risk_tier"] = sec["risk_tier"]
            if "guardian_enabled" in sec:
                settings.integrations["guardian_enabled"] = sec["guardian_enabled"]
            if "auto_approve_threshold" in sec:
                settings.integrations["auto_approve_threshold"] = sec["auto_approve_threshold"]

        # MCP Servers
        if "mcp_servers" in data:
            settings.mcp_servers = data["mcp_servers"]

    except Exception as e:
        print(f"Error loading JSON config overrides: {e}")


settings = Settings()


def persist_safety_flags() -> None:
    """Persist the current safety feature flags back to charlie_config.json (Req 17.6).

    Merges the live ``settings.security`` safety flags (``self_modify_enabled``,
    ``require_confirmation_tier1``) into the file's
    ``"safety"`` object so changes survive a restart. Other top-level keys and
    existing ``safety`` keys are preserved. Tolerates the file not existing by
    creating it.
    """
    config_path = Path(__file__).parent.parent / "charlie_config.json"

    data = {}
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                data = {}
        except Exception as e:
            logger.error(f"persist_safety_flags failed to read existing config: {e}")
            data = {}

    safety = data.get("safety")
    if not isinstance(safety, dict):
        safety = {}
    safety["self_modify_enabled"] = settings.security.self_modify_enabled
    safety["require_confirmation_tier1"] = settings.security.require_confirmation_tier1
    data["safety"] = safety

    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"persist_safety_flags failed to write config: {e}")


def setup_windows_vault():
    """Migrates plain text credentials to Windows Credential Manager using DPAPI + Keyring."""
    if os.name != "nt":
        return

    try:
        import ctypes
        from ctypes import wintypes
        import base64
        import keyring
        from keyring.backends.Windows import WinVaultKeyring

        # Explicitly lock to WinVaultKeyring backend
        keyring.set_keyring(WinVaultKeyring())
    except Exception as e:
        logger.error(f"setup_windows_vault failed to load keyring backend: {e}")
        return

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    entropy = ""

    def encrypt_dpapi(data: str) -> bytes:
        data_bytes = data.encode('utf-8')
        entropy_bytes = entropy.encode('utf-8')
        blob_in = DATA_BLOB(len(data_bytes), ctypes.cast(ctypes.create_string_buffer(data_bytes), ctypes.POINTER(ctypes.c_char)))
        blob_entropy = DATA_BLOB(len(entropy_bytes), ctypes.cast(ctypes.create_string_buffer(entropy_bytes), ctypes.POINTER(ctypes.c_char)))
        blob_out = DATA_BLOB()

        # CRYPTPROTECT_UI_FORBIDDEN = 0x01
        res = ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(blob_in),
            None,
            ctypes.byref(blob_entropy),
            None,
            None,
            0x01,
            ctypes.byref(blob_out)
        )
        if not res:
            raise ctypes.WinError()
        out_data = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)
        return out_data

    def decrypt_dpapi(data_bytes: bytes) -> str:
        entropy_bytes = entropy.encode('utf-8')
        blob_in = DATA_BLOB(len(data_bytes), ctypes.cast(ctypes.create_string_buffer(data_bytes), ctypes.POINTER(ctypes.c_char)))
        blob_entropy = DATA_BLOB(len(entropy_bytes), ctypes.cast(ctypes.create_string_buffer(entropy_bytes), ctypes.POINTER(ctypes.c_char)))
        blob_out = DATA_BLOB()

        res = ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(blob_in),
            None,
            ctypes.byref(blob_entropy),
            None,
            None,
            0x01,
            ctypes.byref(blob_out)
        )
        if not res:
            raise ctypes.WinError()
        out_data = ctypes.string_at(blob_out.pbData, blob_out.cbData).decode('utf-8')
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)
        return out_data

    service_name = "charlie_credentials"
    keys_to_migrate = {
        "LLM_API_KEY": "llm_api_key",
        "TELEGRAM_TOKEN": "telegram_token",
        "TELEGRAM_CHAT_ID": "telegram_chat_id",
    }

    for env_var, keyring_key in keys_to_migrate.items():
        try:
            # 1. Try to load from Keyring
            stored_val = keyring.get_password(service_name, keyring_key)
            decrypted_val = ""
            if stored_val:
                try:
                    encrypted_bytes = base64.b64decode(stored_val.encode('utf-8'))
                    decrypted_val = decrypt_dpapi(encrypted_bytes)
                except Exception as ex:
                    logger.debug(f"Failed to decrypt {env_var} from keyring: {ex}")

            # 2. If it is in environment but not in keyring (or mismatched), migrate it
            env_val = os.getenv(env_var, "").strip()
            if env_val and env_val != decrypted_val:
                # Encrypt and save to keyring
                encrypted_bytes = encrypt_dpapi(env_val)
                stored_str = base64.b64encode(encrypted_bytes).decode('utf-8')
                keyring.set_password(service_name, keyring_key, stored_str)
                decrypted_val = env_val
                logger.info(f"Successfully migrated {env_var} to Windows Credential Manager.")

            # 3. Populate back to environment variable and LLM / Watchdog Settings
            if decrypted_val:
                os.environ[env_var] = decrypted_val
                if env_var == "LLM_API_KEY":
                    settings.llm.llm_api_key = decrypted_val
                elif env_var == "TELEGRAM_TOKEN":
                    settings.supervisor.telegram_token = decrypted_val
                elif env_var == "TELEGRAM_CHAT_ID":
                    settings.supervisor.telegram_chat_id = decrypted_val
        except Exception as e:
            logger.debug(f"Fails-closed security error during setup for {env_var}: {e}")


# Lazy initialization — side effects deferred to first explicit call.
# Previously these ran at import time, breaking tests and causing slow imports.
_initialized = False

def ensure_initialized():
    """Run setup side effects once. Call explicitly at startup, not on import."""
    global _initialized
    if _initialized:
        return
    _initialized = True
    setup_windows_vault()
    load_json_overrides()
    settings.system_prompt = get_system_prompt()

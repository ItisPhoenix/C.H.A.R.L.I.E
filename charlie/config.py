import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from charlie.utils.persona import get_system_prompt

logger = logging.getLogger("charlie.config")
load_dotenv()


class LLMSettings:
    def __init__(self):
        # --- Primary LLM (OpenAI-compatible, e.g. LM Studio / Ollama) ---
        self.llm_url = os.getenv("LLM_URL", "http://localhost:1234")
        # --- NIM (primary chat model) ---
        self.nim_base_url = os.getenv("NIM_BASE_URL", "https://integrate.api.nvidia.com")
        self.nim_api_key = os.getenv("NIM_API_KEY")
        self.primary_model = os.getenv("NIM_PRIMARY_MODEL", "meta/llama-3.3-70b-instruct")
        # --- Vision (non-NIM, separate endpoint, e.g. LM Studio) ---
        self.vision_model = os.getenv("VISION_MODEL")
        self.vision_url = os.getenv("VISION_LLM_URL", "http://127.0.0.1:1234/v1")
        # --- Embeddings ---
        self.embedding_url = os.getenv("EMBEDDING_URL", "http://127.0.0.1:1234/api/embeddings")
        self.embedding_model = os.getenv("EMBEDDING_MODEL")
        # --- Gemini (optional, for Gemini Live voice mode and Gemini provider) ---
        self.gemini_api_key = os.getenv("GEMINI_API_KEY")
        # --- Shared inference params ---
        self.keep_alive = -1
        self.sentinel_enabled = False
        self.sentinel_interval = 60
        self.vram_limit_mb = 7168
        self.context_window = 8192
        self.temperature = 0.2


class ResourceSettings:
    def __init__(self):
        self.vram_threshold_mb = 6500
        self.model_priority = {"text": "primary", "vision": "on_demand"}
        self.model_unload_delay = 30
        self.max_context_tokens = 4096


class AudioSettings:
    def __init__(self):
        self.wakeword_models = ["charlie/models/charlie.onnx"]
        self.stt_model = "distil-large-v3"
        self.stt_device = "cuda"
        self.stt_initial_prompt = "Charlie, C.H.A.R.L.I.E., Phoenix protocol, system commands, code, terminal."
        self.stt_language = "en"
        self.kokoro_voice = "af_sarah"
        self.kokoro_speed = 1.0
        self.kokoro_lang = "en-us"
        self.voice_mode = "local"
        self.wake_threshold = 0.02
        self.mic_index = int(os.getenv("MIC_INDEX", "1"))
        self.output_index = int(os.getenv("OUTPUT_INDEX", "4"))
        self.sample_rate = 16000
        self.target_rate = 16000
        self.conversation_timeout = 300
        self.barge_in_sensitivity = 0.3
        self.silence_limit = 0.4
        self.vad_mode = 3
        self.duck_steps = 8


class WatchdogSettings:
    def __init__(self):
        self.telegram_token = os.getenv("TELEGRAM_TOKEN")
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.reports_path = "charlie/reports"
        self.patch_timeout = 60
        self.auto_patch = True


class SecuritySettings:
    def __init__(self):
        self.tier_2_countdown = 30
        self.snapshots_enabled = True
        self.require_confirmation_tier1 = True
        self.restricted_paths = ["charlie/security", "charlie/watchdog"]


class AuditSettings:
    def __init__(self):
        self.dep_audit_enabled = os.getenv("DEP_AUDIT_ENABLED", "true").lower() == "true"
        self.dep_audit_interval_hours = 24
        self.log_redaction_enabled = True
        self.max_llm_calls_per_minute = 30
        self.max_telegram_messages_per_minute = 20


class StartupSettings:
    def __init__(self):
        self.run_news_sweep = False
        self.play_music = False
        self.speak_welcome = True


class PersonaSettings:
    def __init__(self):
        self.address_user_as = "Sir"
        self.response_style = "formal"
        self.verbosity = "concise"


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


def load_json_overrides():
    """Reads charlie_config.json and overrides default settings."""
    config_path = Path(__file__).parent.parent / "charlie_config.json"
    if not config_path.exists():
        return

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Voice / Audio
        if "voice" in data:
            v = data["voice"]
            if "tts_speed" in v:
                settings.audio.kokoro_speed = v["tts_speed"]
            if "kokoro_voice" in v:
                settings.audio.kokoro_voice = v["kokoro_voice"]
            if "voice_mode" in v:
                settings.audio.voice_mode = v["voice_mode"]
            if "mic_index" in v:
                settings.audio.mic_index = int(v["mic_index"])
            if "output_index" in v:
                settings.audio.output_index = int(v["output_index"])
            if "wake_word_sensitivity" in v:
                sens = float(v["wake_word_sensitivity"])
                settings.audio.wake_threshold = max(0.01, round((1.0 - sens) ** 2, 4))
                logger.debug(
                    f"Applied dynamic wake_threshold: {settings.audio.wake_threshold} (from sensitivity {sens})"
                )

        # Safety / Self-mod
        if "safety" in data:
            s = data["safety"]
            if "require_confirmation_tier1" in s:
                settings.security.require_confirmation_tier1 = s[
                    "require_confirmation_tier1"
                ]

        # Startup
        if "startup" in data:
            st = data["startup"]
            if "run_news_sweep" in st:
                settings.startup.run_news_sweep = st["run_news_sweep"]
            if "play_music" in st:
                settings.startup.play_music = st["play_music"]
            if "speak_welcome" in st:
                settings.startup.speak_welcome = st["speak_welcome"]

        # Personality
        if "personality" in data:
            p = data["personality"]
            if "address_user_as" in p:
                settings.persona.address_user_as = p["address_user_as"]
            if "response_style" in p:
                settings.persona.response_style = p["response_style"]
            if "verbosity" in p:
                settings.persona.verbosity = p["verbosity"]
        # Vision / Sentinel
        if "vision" in data:
            v = data["vision"]
            if "sentinel_enabled" in v:
                settings.llm.sentinel_enabled = v["sentinel_enabled"]
            if "sentinel_interval" in v:
                settings.llm.sentinel_interval = int(v["sentinel_interval"])

        # Providers (LLM model routing — values may reference $ENV_VAR)
        if "providers" in data:
            settings.providers = data["providers"]

        # MCP Servers
        if "mcp_servers" in data:
            settings.mcp_servers = data["mcp_servers"]

    except Exception as e:
        print(f"Error loading JSON config overrides: {e}")


settings = Settings()


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

    entropy = "CHARLIE_ENTROPY_KEY"

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
        "NIM_API_KEY": "nim_api_key",
        "OPENROUTER_API_KEY": "openrouter_api_key",
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
                if env_var == "NIM_API_KEY":
                    settings.llm.nim_api_key = decrypted_val
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

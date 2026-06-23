import os
from pathlib import Path
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv(override=True)

@dataclass
class Config:
    llm_url: str = os.getenv("LLM_URL", "")
    llm_key: str = os.getenv("LLM_API_KEY", "no-key")
    llm_model: str = os.getenv("LLM_MODEL", "")
    # Fallback LLM provider (used when primary LLM fails)
    fallback_llm_url: str = os.getenv("FALLBACK_LLM_URL", "")
    fallback_llm_key: str = os.getenv("FALLBACK_LLM_API_KEY", "no-key")
    fallback_llm_model: str = os.getenv("FALLBACK_LLM_MODEL", "")
    
    # -1 = system default input/output device; >=0 = specific device index
    mic_index: int = int(os.getenv("MIC_INDEX", "-1"))
    output_index: int = int(os.getenv("OUTPUT_INDEX", "-1"))
    
    whisper_model: str = os.getenv("WHISPER_MODEL", "distil-large-v3")
    silence_timeout: float = float(os.getenv("SILENCE_TIMEOUT", "1.0"))
    phrase_min_duration: float = float(os.getenv("PHRASE_MIN_DURATION", "0.8"))
    phrase_max_duration: float = float(os.getenv("PHRASE_MAX_DURATION", "30.0"))
    kokoro_voice: str = os.getenv("KOKORO_VOICE", "af_heart")
    kokoro_model_dir: str = os.getenv("KOKORO_MODEL_DIR", "models")
    gpu_device: str = os.getenv("GPU_DEVICE", "cuda")
    kokoro_lang: str = "en-us"
    default_language: str = os.getenv("DEFAULT_LANGUAGE", "en")
    log_file: str = "logs/charlie.log"
    log_level: str = "INFO"
    
    # VAD Configuration
    vad_threshold: float = float(os.getenv("VAD_THRESHOLD", "0.75"))
    vad_silence_timeout: float = float(os.getenv("VAD_SILENCE_TIMEOUT", "1.2"))
    
    # Barge-in Configuration
    enable_barge_in: bool = os.getenv("ENABLE_BARGE_IN", "true").lower() == "true"
    
    llm_disable_reasoning: bool = os.getenv("LLM_DISABLE_REASONING", "true").lower() == "true"

    # Iteration Budget & Context Compression
    iteration_budget_max: int = int(os.getenv("ITERATION_BUDGET_MAX", "12"))
    context_window: int = int(os.getenv("CONTEXT_WINDOW", "8192"))
    compression_threshold: float = float(os.getenv("COMPRESSION_THRESHOLD", "0.8"))
    memory_file: str = os.getenv("MEMORY_FILE", "MEMORY.md")
    user_file: str = os.getenv("USER_FILE", "USER.md")
    prompt_memory_max: int = int(os.getenv("PROMPT_MEMORY_MAX", "2200"))
    session_db_path: str = os.getenv("SESSION_DB_PATH", "sessions.db")
    # Search provider (SearXNG self-hosted)
    searxng_url: str = os.getenv("SEARXNG_URL", "")
    soul: str = ""
config = Config()

# Load SOUL.md into config.soul at startup
_SOUL_PATH = Path("SOUL.md")
_DEFAULT_SOUL = (
    "You are Charlie. You are warm but efficient. You get to the point fast, then offer warmth."
    " No fluff. No sycophancy. You speak like a trusted colleague who actually cares."
)
if not _SOUL_PATH.exists():
    _SOUL_PATH.write_text(_DEFAULT_SOUL, encoding="utf-8")
config.soul = _SOUL_PATH.read_text(encoding="utf-8")
